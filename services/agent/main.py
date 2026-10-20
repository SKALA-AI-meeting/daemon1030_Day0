import asyncio
import os
from pathlib import Path

import httpx
import jwt
from fastapi import Depends, FastAPI, Header, HTTPException
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from contracts.api.models import AgentRun
from services.agent.graphs.conversation import build_graph
from services.agent.graphs.documents import build_import_graph
from services.agent.mcp_clients.client import ToolConnections, token_for
from services.agent.providers.model import OpenAIModel

app = FastAPI(title="Day Zero Agent")
locks = {}


def authenticate(authorization: str = Header(default="")):
    try:
        return jwt.decode(
            authorization.removeprefix("Bearer "),
            os.environ["AUTH_SECRET"],
            algorithms=["HS256"],
            audience="agent",
            issuer="dayzero-gateway",
            options={"require": ["exp", "aud", "iss"]},
        )
    except jwt.PyJWTError:
        raise HTTPException(401, "서비스 인증이 필요합니다.") from None


async def gateway(claims, method, path, body=None, scope="agent"):
    token = token_for(claims, "gateway", scope)
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.request(
            method,
            os.getenv("GATEWAY_URL", "http://127.0.0.1:8000") + path,
            headers={"Authorization": "Bearer " + token},
            json=body,
        )
        response.raise_for_status()
        return response.json()


def checkpoint_path():
    path = Path(os.getenv("AGENT_CHECKPOINT_PATH", "data/agent-checkpoints.sqlite"))
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


@app.get("/health")
def health():
    return {"status": "ok", "model_configured": bool(os.getenv("OPENAI_API_KEY"))}


@app.post("/runs")
async def run(body: AgentRun, claims=Depends(authenticate)):
    if (
        claims.get("scope") != "agent:run"
        or claims.get("session_id") != body.session_id
        or claims.get("actor_role") != body.role
    ):
        raise HTTPException(403, "실행 범위가 일치하지 않습니다.")
    if not os.getenv("OPENAI_API_KEY"):
        raise HTTPException(503, "MODEL_NOT_CONFIGURED")
    thread = f"{body.session_id}:{body.role}:{body.message_id}"
    async with locks.setdefault(thread, asyncio.Lock()):
        context = await gateway(claims, "GET", "/internal/context")
        async with ToolConnections(claims, body.role) as tools:
            async with AsyncSqliteSaver.from_conn_string(checkpoint_path()) as checkpoint:
                graph = build_graph(OpenAIModel(), tools, context, checkpoint)
                config = {"configurable": {"thread_id": thread}}
                saved = await graph.aget_state(config)
                if saved.values and not saved.next:
                    result = saved.values
                else:
                    result = await graph.ainvoke(
                        None if saved.values else {"input_id": body.message_id}, config
                    )
        return await gateway(
            claims,
            "POST",
            "/internal/agent-result",
            {
                "input_id": body.message_id,
                "content": result["output"],
                "citations": result.get("citations", []),
            },
        )


@app.post("/document-runs")
async def document_run(body: dict, claims=Depends(authenticate)):
    import_id = body.get("import_id")
    if (
        claims.get("scope") != "documents:manage"
        or claims.get("actor_role") != "manager"
        or claims.get("import_id") != import_id
    ):
        raise HTTPException(403, "문서 실행 범위가 일치하지 않습니다.")
    thread = "document:" + import_id
    async with locks.setdefault(thread, asyncio.Lock()):

        async def read_document(identifier):
            return await gateway(
                claims,
                "GET",
                "/internal/document-imports/" + identifier,
                scope="documents:manage",
            )

        async with ToolConnections(claims, "manager") as tools:
            async with AsyncSqliteSaver.from_conn_string(checkpoint_path()) as checkpoint:
                graph = build_import_graph(tools, read_document, checkpoint)
                config = {"configurable": {"thread_id": thread}}
                saved = await graph.aget_state(config)
                decision = body.get("decision")
                if decision is not None:
                    if not saved.values or not saved.next:
                        return {"status": "already_finished" if saved.values else "not_prepared"}
                    item = await read_document(import_id)
                    expected = "approved" if decision else "rejected"
                    if item["status"] not in (expected, "indexed"):
                        raise HTTPException(409, "서버 승인 결정이 확인되지 않습니다.")
                    result = await graph.ainvoke(Command(resume=decision), config)
                elif saved.values:
                    return {"status": "waiting_for_approval" if saved.next else "finished"}
                else:
                    result = await graph.ainvoke({"import_id": import_id}, config)
                return {
                    "status": "waiting_for_approval" if result.get("__interrupt__") else "finished"
                }
