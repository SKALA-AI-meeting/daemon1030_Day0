import os
import time
from typing import Any

import httpx
import jwt
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from services.rag_mcp import retrieval

mcp = FastMCP(
    "Day Zero rag-mcp",
    host=os.getenv("MCP_HOST", "127.0.0.1"),
    port=9102,
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(
        allowed_hosts=["127.0.0.1:*", "localhost:*", "rag-mcp:*"],
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*"],
    ),
)
READ = ToolAnnotations(readOnlyHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True)


def auth(ctx, manage=False):
    request = ctx.request_context.request
    token = request.headers.get("authorization", "").removeprefix("Bearer ") if request else ""
    try:
        claims = jwt.decode(
            token,
            os.environ["AUTH_SECRET"],
            algorithms=["HS256"],
            audience="rag-mcp",
            issuer="dayzero-gateway",
            options={"require": ["exp", "aud", "iss"]},
        )
    except jwt.PyJWTError:
        raise ToolError('{"code":"ACCESS_DENIED","retryable":false}') from None
    if manage:
        if claims.get("scope") != "documents:manage" or claims.get("actor_role") != "manager":
            raise ToolError('{"code":"ACCESS_DENIED","retryable":false}')
    elif claims.get("scope") != "rag" or claims.get("actor_role") not in (
        "colleague",
        "assistant",
        "leader",
    ):
        raise ToolError('{"code":"ACCESS_DENIED","retryable":false}')
    return claims


async def gateway(claims, path, body=None):
    claims = {**claims, "aud": "gateway", "exp": int(time.time()) + 60}
    token = jwt.encode(claims, os.environ["AUTH_SECRET"], algorithm="HS256")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.request(
                "GET" if body is None else "POST",
                os.getenv("GATEWAY_URL", "http://127.0.0.1:8000") + path,
                json=body,
                headers={"Authorization": "Bearer " + token},
            )
        if response.is_error:
            raise ToolError('{"code":"ACCESS_DENIED_OR_UNAVAILABLE","retryable":false}')
        return response.json()
    except httpx.HTTPError:
        raise ToolError('{"code":"TIMEOUT_OR_UNAVAILABLE","retryable":true}') from None


@mcp.tool(
    description="현재 기업·평가에서 공개된 승인 문서만 검색합니다. 문서·청크 ID와 근거 본문을 반환합니다.",
    annotations=READ,
)
async def search_company_documents(query: str, ctx: Context, top_k: int = 4) -> dict[str, Any]:
    claims = auth(ctx)
    if not query.strip() or len(query) > 2000 or not 1 <= top_k <= 10:
        raise ToolError('{"code":"INVALID_INPUT","retryable":false}')
    documents = await gateway(claims, "/internal/visible-documents")
    return {"results": await retrieval.search(query, documents, claims["tenant_id"], top_k)}


@mcp.tool(
    description="공개된 회사 문서 원문과 버전을 조회합니다. 비공개 문서의 ID를 직접 알아도 조회할 수 없습니다.",
    annotations=READ,
)
async def get_company_document(document_id: str, ctx: Context) -> dict[str, Any]:
    claims = auth(ctx)
    documents = await gateway(claims, "/internal/visible-documents")
    item = next((d for d in documents if d["id"] == document_id), None)
    if not item:
        raise ToolError('{"code":"DOCUMENT_NOT_AVAILABLE","retryable":false}')
    return item


@mcp.tool(
    description="관리 실행 전용: Gateway의 원문 버전을 검색되지 않는 준비 상태로 저장합니다.",
    annotations=WRITE,
)
async def stage_company_document(
    source_version_id: str, command_id: str, ctx: Context
) -> dict[str, Any]:
    claims = auth(ctx, True)
    item = await gateway(claims, "/internal/document-imports/" + source_version_id)
    if item["status"] not in ("pending", "prepared"):
        raise ToolError('{"code":"INVALID_TRANSITION","retryable":false}')
    retrieval.store_prepared(item, claims["tenant_id"])
    await gateway(
        claims,
        f"/internal/document-imports/{item['id']}/status",
        {"status": "prepared", "content_hash": item["content_hash"]},
    )
    return {
        "staged_id": item["id"],
        "content_hash": item["content_hash"],
        "title": item["title"],
    }


@mcp.tool(
    description="관리 실행 전용: 실제 관리자 승인 ID와 문서 해시를 확인하고 검색용 벡터를 생성합니다.",
    annotations=WRITE,
)
async def index_approved_document(
    staged_id: str, approval_id: str, command_id: str, ctx: Context
) -> dict[str, Any]:
    claims = auth(ctx, True)
    item = await gateway(claims, "/internal/document-imports/" + staged_id)
    if item["status"] not in ("approved", "indexed") or item["approval_id"] != approval_id:
        raise ToolError('{"code":"APPROVAL_REQUIRED","retryable":false}')
    count = await retrieval.index(item, claims["tenant_id"])
    await gateway(
        claims,
        f"/internal/document-imports/{item['id']}/status",
        {"status": "indexed", "content_hash": item["content_hash"]},
    )
    return {"document_id": staged_id, "chunks": count, "status": "indexed"}


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
