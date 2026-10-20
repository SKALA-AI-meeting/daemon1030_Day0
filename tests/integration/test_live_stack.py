"""Opt-in tests against a running development-only stack (no model/API spend)."""

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from uuid import uuid4

import aio_pika
import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from services.gateway.domain.security import service_token

pytestmark = pytest.mark.skipif(
    os.getenv("DAYZERO_LIVE_TESTS") != "1",
    reason="Start development fixture stack and set DAYZERO_LIVE_TESTS=1",
)


@asynccontextmanager
async def authorized_transport(url, headers):
    async with httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(30, read=60)) as client:
        async with streamable_http_client(url, http_client=client) as streams:
            yield streams


def wait_for(read, expected, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = read()
        if expected(data):
            return data
        time.sleep(0.2)
    raise AssertionError(f"Expected state was not reached: {data}")


def test_postgres_real_mcp_approval_vector_search_and_queue():
    base = os.getenv("LIVE_GATEWAY_URL", "http://127.0.0.1:8000")
    tag = uuid4().hex[:12]
    with (
        httpx.Client(base_url=base, timeout=30) as company,
        httpx.Client(base_url=base, timeout=30) as person,
    ):
        company.post(
            "/api/auth/register",
            json={
                "name": "통합검증",
                "email": f"company-{tag}@example.test",
                "password": "integration-password",
                "role": "company",
                "company_name": "합성 테스트 기업",
            },
        ).raise_for_status()
        person.post(
            "/api/auth/register",
            json={
                "name": "통합지원자",
                "email": f"person-{tag}@example.test",
                "password": "integration-password",
                "role": "individual",
            },
        ).raise_for_status()
        imported = company.post(
            "/api/document-imports",
            json={
                "title": "검색 계약 fixture",
                "content": "계약 검증용 합성 문서입니다. 기준 단위는 작업 항목입니다.",
            },
        )
        imported.raise_for_status()
        import_id = imported.json()["id"]
        item = wait_for(
            lambda: company.get("/api/document-imports").json(),
            lambda items: any(i["id"] == import_id and i["status"] == "prepared" for i in items),
        )
        document = next(i for i in item if i["id"] == import_id)
        result = company.post(
            f"/api/document-imports/{import_id}/decision",
            json={"approved": True, "content_hash": document["content_hash"]},
        )
        result.raise_for_status()
        wait_for(
            lambda: company.get("/api/document-imports").json(),
            lambda items: any(i["id"] == import_id and i["status"] == "indexed" for i in items),
        )
        project = company.post(
            "/api/projects",
            json={
                "title": "스택 통합 fixture",
                "competency_ids": ["C01", "C02", "C03"],
                "scenario_version_id": "fixture-workspace-v1",
                "request_id": str(uuid4()),
            },
        )
        project.raise_for_status()
        pid = project.json()["id"]
        invitation = company.post(
            f"/api/projects/{pid}/invitations",
            json={"alias": "통합지원자", "email": f"person-{tag}@example.test"},
        ).json()
        sid = person.post("/api/invitations/redeem", json={"token": invitation["token"]}).json()[
            "session_id"
        ]
        person.post(
            f"/api/sessions/{sid}/consent", json={"accepted": True, "version": "v1"}
        ).raise_for_status()
        person.post(f"/api/sessions/{sid}/start").raise_for_status()
        # The tenant ID is intentionally not exposed by public APIs; read scoped test metadata from the dedicated test setup.
        from sqlalchemy import create_engine, text

        engine = create_engine(os.environ["DATABASE_URL"])
        with engine.connect() as db:
            tenant_id = db.execute(
                text("SELECT tenant_id FROM projects WHERE id=:id"), {"id": pid}
            ).scalar_one()
        claims = dict(tenant_id=tenant_id, session_id=sid, actor_role="colleague")

        async def mcp_check():
            token = service_token(
                os.environ["AUTH_SECRET"], "business-mcp", **claims, scope="business"
            )
            async with authorized_transport(
                "http://127.0.0.1:9101/mcp",
                headers={"Authorization": "Bearer " + token},
            ) as transport:
                async with ClientSession(transport[0], transport[1]) as client:
                    await client.initialize()
                    assert len((await client.list_tools()).tools) >= 8
                    tasks = await client.call_tool("list_tasks", {})
                    assert not tasks.isError
                    changed = await client.call_tool(
                        "update_task_status",
                        {
                            "task_id": "draft",
                            "status": "done",
                            "expected_revision": 0,
                            "command_id": "live-task-" + tag,
                        },
                    )
                    assert not changed.isError, changed
                    denied = await client.call_tool(
                        "update_task_status",
                        {
                            "task_id": "notes",
                            "status": "done",
                            "expected_revision": 0,
                            "command_id": "denied-" + tag,
                        },
                    )
                    assert denied.isError
            token = service_token(os.environ["AUTH_SECRET"], "rag-mcp", **claims, scope="rag")
            async with authorized_transport(
                "http://127.0.0.1:9102/mcp",
                headers={"Authorization": "Bearer " + token},
            ) as transport:
                async with ClientSession(transport[0], transport[1]) as client:
                    await client.initialize()
                    assert len((await client.list_tools()).tools) == 4
                    found = await client.call_tool(
                        "search_company_documents", {"query": "기준 단위", "top_k": 3}
                    )
                    assert not found.isError, found
                    assert found.structuredContent["results"][0]["document_id"] == import_id
                    blocked = await client.call_tool(
                        "get_company_document", {"document_id": "not-visible"}
                    )
                    assert blocked.isError
                    blocked_write = await client.call_tool(
                        "stage_company_document",
                        {
                            "source_version_id": import_id,
                            "command_id": "wrong-role-" + tag,
                        },
                    )
                    assert blocked_write.isError

        asyncio.run(mcp_check())
        workspace = person.get(f"/api/sessions/{sid}").json()
        assert (
            next(t for t in workspace["workspace"]["tasks"] if t["id"] == "draft")["status"]
            == "done"
        )
        person.post(f"/api/sessions/{sid}/submit").raise_for_status()
        result = wait_for(
            lambda: company.get(f"/api/reports/{sid}").json(),
            lambda r: r["status"] == "not_configured",
        )
        assert result["content"]["items"] == []
        assert result["content"]["activity_count"] > 0
        # Duplicate publish/consume must preserve the same finalized report.
        from services.report_worker.main import process

        with engine.connect() as db:
            snapshot_id = db.execute(
                text("SELECT snapshot_id FROM reports WHERE session_id=:id"),
                {"id": sid},
            ).scalar_one()
        assert (
            process(
                engine,
                {"session_id": sid, "snapshot_id": snapshot_id, "report_version": "v1"},
            )
            == "not_configured"
        )

        async def verify_retry_and_dlq():
            connection = await aio_pika.connect_robust(os.environ["RABBITMQ_URL"])
            async with connection:
                channel = await connection.channel(publisher_confirms=True)
                dead = await channel.declare_queue("dayzero.reports.failed", durable=True)
                invalid_job = {
                    "session_id": sid,
                    "snapshot_id": snapshot_id,
                    "report_version": "invalid-fixture-version",
                }
                await channel.default_exchange.publish(
                    aio_pika.Message(
                        body=json.dumps(invalid_job).encode(),
                        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                    ),
                    routing_key="dayzero.reports",
                )
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    message = await dead.get(fail=False)
                    if message:
                        payload = json.loads(message.body)
                        if payload.get("session_id") == sid:
                            assert message.headers["attempt"] == 3
                            await message.ack()
                            return
                        await message.reject(requeue=True)
                    await asyncio.sleep(0.2)
                raise AssertionError("Failed job did not reach DLQ after retry limit")

        asyncio.run(verify_retry_and_dlq())
        assert company.get(f"/api/reports/{sid}").json()["status"] == "not_configured"
        engine.dispose()


@pytest.mark.parametrize(
    "scenario,section,late_doc",
    [
        ("S01-v1", "proposal", "S01-v1-change"),
        ("S02-v1", "situation", "S02-v1-update"),
        ("S03-v1", "coordination", "S03-v1-delay"),
    ],
)
def test_live_published_scenario_evidence_report(scenario, section, late_doc):
    from pathlib import Path

    from sqlalchemy import create_engine, text

    config = json.loads(Path(f"scenarios/versions/{scenario}.json").read_text())
    tag = uuid4().hex[:12]
    base = os.getenv("LIVE_GATEWAY_URL", "http://127.0.0.1:8000")
    with (
        httpx.Client(base_url=base, timeout=30) as company,
        httpx.Client(base_url=base, timeout=30) as person,
    ):
        for client, role in [(company, "company"), (person, "individual")]:
            response = client.post(
                "/api/auth/register",
                json={
                    "name": "시나리오 합성검증",
                    "email": f"{role}-{tag}@example.test",
                    "password": "scenario-integration-password",
                    "role": role,
                    "company_name": "시나리오 합성기업" if role == "company" else None,
                },
            )
            response.raise_for_status()
        created = company.post(
            "/api/projects",
            json={
                "title": f"{scenario} 합성검증",
                "competency_ids": config["competency_ids"],
                "scenario_version_id": scenario,
                "request_id": str(uuid4()),
            },
        )
        created.raise_for_status()
        pid = created.json()["id"]
        invitation = company.post(
            f"/api/projects/{pid}/invitations",
            json={"alias": "합성지원자", "email": f"individual-{tag}@example.test"},
        ).json()
        sid = person.post("/api/invitations/redeem", json={"token": invitation["token"]}).json()[
            "session_id"
        ]
        person.post(
            f"/api/sessions/{sid}/consent", json={"accepted": True, "version": "v1"}
        ).raise_for_status()
        started = person.post(f"/api/sessions/{sid}/start").json()
        assert late_doc not in {d["id"] for d in started["documents"]}
        initial = person.get(f"/api/sessions/{sid}/events").json()
        assert any(e["payload"].get("scenario_event_id") == "kickoff" for e in initial)
        engine = create_engine(os.environ["DATABASE_URL"])
        with engine.begin() as db:
            tenant = db.execute(
                text("SELECT tenant_id FROM projects WHERE id=:id"), {"id": pid}
            ).scalar_one()
            # Explicit opt-in synthetic test clock only; no public clock manipulation endpoint.
            db.execute(
                text(
                    "UPDATE sessions SET accumulated=2760, resumed_at=:now, heartbeat_at=:now WHERE id=:id"
                ),
                {"now": time.time(), "id": sid},
            )
        visible = person.get(f"/api/sessions/{sid}").json()
        assert late_doc in {d["id"] for d in visible["documents"]}

        async def search_published_documents():
            token = service_token(
                os.environ["AUTH_SECRET"],
                "rag-mcp",
                tenant_id=tenant,
                session_id=sid,
                actor_role="colleague",
                scope="rag",
            )
            async with authorized_transport(
                "http://127.0.0.1:9102/mcp", {"Authorization": "Bearer " + token}
            ) as streams:
                async with ClientSession(streams[0], streams[1]) as client:
                    await client.initialize()
                    result = await client.call_tool(
                        "search_company_documents",
                        {"query": config["documents"][0]["title"], "top_k": 10},
                    )
                    assert not result.isError, result
                    assert result.structuredContent["results"]
                    assert {r["document_id"] for r in result.structuredContent["results"]}.issubset(
                        {d["id"] for d in visible["documents"]}
                    )

        asyncio.run(search_published_documents())
        person.put(
            f"/api/sessions/{sid}/documents/{section}",
            json={
                "content": "조건 변경을 확인하고 실행 계획을 갱신했습니다. 구체 근거는 사람이 검토합니다.",
                "expected_revision": 0,
                "command_id": str(uuid4()),
            },
        ).raise_for_status()
        person.post(f"/api/sessions/{sid}/submit").raise_for_status()
        report = wait_for(
            lambda: company.get(f"/api/reports/{sid}").json(), lambda r: r["status"] == "ready"
        )
        assert report["content"]["analysis_mode"] == "activity_evidence_only"
        change = next(i for i in report["content"]["items"] if i["competency_id"] == "C05")
        assert any(e["section_id"] == section for e in change["evidence"])
        assert any(
            s["status"] == "not_provided"
            for i in report["content"]["items"]
            for s in i["situations"]
        )
        engine.dispose()
