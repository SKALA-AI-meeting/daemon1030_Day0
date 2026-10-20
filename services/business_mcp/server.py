import os
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from services.business_mcp.gateway_client import call_gateway

mcp = FastMCP(
    "Day Zero business-mcp",
    host=os.getenv("MCP_HOST", "127.0.0.1"),
    port=9101,
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(
        allowed_hosts=["127.0.0.1:*", "localhost:*", "business-mcp:*"],
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*"],
    ),
)
READ = ToolAnnotations(readOnlyHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True)


@mcp.tool(
    description="현재 세션의 공개 과제·단계·남은 시간·업무 revision을 조회합니다. 미래 사건과 평가 결과는 제공하지 않습니다.",
    annotations=READ,
)
async def get_session_context(ctx: Context) -> dict[str, Any]:
    return await call_gateway(ctx, "get_session_context", {})


@mcp.tool(
    description="현재 세션의 태스크·담당자·상태·revision을 조회합니다.",
    annotations=READ,
)
async def list_tasks(ctx: Context) -> dict[str, Any]:
    return await call_gateway(ctx, "list_tasks", {})


@mcp.tool(description="현재 세션의 과제 문서 항목을 조회합니다.", annotations=READ)
async def get_document_section(section_id: str, ctx: Context) -> dict[str, Any]:
    return await call_gateway(ctx, "get_document_section", {"section_id": section_id})


@mcp.tool(
    description="전체 태스크의 candidate/colleague 분담안을 제안합니다. 지원자 수락 없이 확정하지 않습니다.",
    annotations=WRITE,
)
async def propose_task_assignment(
    assignments: dict[str, str], expected_revision: int, command_id: str, ctx: Context
) -> dict[str, Any]:
    return await call_gateway(
        ctx,
        "propose_task_assignment",
        {
            "assignments": assignments,
            "expected_revision": expected_revision,
            "command_id": command_id,
        },
    )


@mcp.tool(
    description="진행자 코드 전용: 지원자 수락 기록을 검증한 분업안을 적용합니다.",
    annotations=WRITE,
)
async def apply_task_assignment(
    proposal_id: str, expected_revision: int, command_id: str, ctx: Context
) -> dict[str, Any]:
    return await call_gateway(
        ctx,
        "apply_task_assignment",
        {
            "proposal_id": proposal_id,
            "expected_revision": expected_revision,
            "command_id": command_id,
        },
    )


@mcp.tool(
    description="AI 동료 자신의 태스크 상태를 todo/doing/done으로 바꿉니다. command_id와 현재 revision이 필요합니다.",
    annotations=WRITE,
)
async def update_task_status(
    task_id: str, status: str, expected_revision: int, command_id: str, ctx: Context
) -> dict[str, Any]:
    return await call_gateway(
        ctx,
        "update_task_status",
        {
            "task_id": task_id,
            "status": status,
            "expected_revision": expected_revision,
            "command_id": command_id,
        },
    )


@mcp.tool(
    description="동료 담당 항목의 초안을 공유합니다. 지원자 담당 내용을 덮어쓸 수 없습니다.",
    annotations=WRITE,
)
async def publish_colleague_draft(
    section_id: str, content: str, expected_revision: int, command_id: str, ctx: Context
) -> dict[str, Any]:
    return await call_gateway(
        ctx,
        "publish_colleague_draft",
        {
            "section_id": section_id,
            "content": content,
            "expected_revision": expected_revision,
            "command_id": command_id,
        },
    )


@mcp.tool(
    description="규칙 실행기 전용: 사전에 등록된 규칙과 실제 원본 근거로 관찰 사실을 저장합니다. 일반 모델은 사용할 수 없습니다.",
    annotations=WRITE,
)
async def record_observation(
    event_id: str,
    competency_id: str,
    rule_version: str,
    evidence_ids: list[str],
    facts: dict,
    command_id: str,
    ctx: Context,
) -> dict[str, Any]:
    return await call_gateway(
        ctx,
        "record_observation",
        {
            "event_id": event_id,
            "competency_id": competency_id,
            "rule_version": rule_version,
            "evidence_ids": evidence_ids,
            "facts": facts,
            "command_id": command_id,
        },
    )


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
