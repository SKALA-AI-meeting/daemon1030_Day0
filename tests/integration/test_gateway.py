from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from services.gateway.main import create_app
from services.gateway.persistence.models import AssessmentSession, Base, Outbox, Report
from services.gateway.seed import seed
from services.gateway.settings import Settings


@pytest.fixture
def app(tmp_path):
    app = create_app(
        Settings(
            database_url=f"sqlite:///{tmp_path}/test.sqlite",
            secret="test-only-" * 8,
            development=True,
            agent_url="http://127.0.0.1:1",
        ),
        background=False,
    )
    Base.metadata.create_all(app.state.engine)
    with app.state.sessions.begin() as db:
        seed(db, True, Path("tests/fixtures/workspace.json"))
    return app


def register(client, email, role="company"):
    result = client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": "test-password-123",
            "name": "테스트",
            "role": role,
            "company_name": "테스트 기업" if role == "company" else None,
        },
    )
    assert result.status_code == 200, result.text
    return result.json()


def project(client, codes=None, scenario_id="fixture-workspace-v1"):
    result = client.post(
        "/api/projects",
        json={
            "title": "기능 확인",
            "competency_ids": codes or ["C01", "C02", "C03"],
            "scenario_version_id": scenario_id,
            "request_id": str(uuid4()),
        },
    )
    assert result.status_code == 200, result.text
    return result.json()


def start_session(app, scenario_id="fixture-workspace-v1", codes=None):
    company = TestClient(app)
    person = TestClient(app)
    register(company, "company@example.com")
    register(person, "person@example.com", "individual")
    p = project(company, codes, scenario_id)
    invitation = company.post(
        f"/api/projects/{p['id']}/invitations",
        json={"alias": "지원자", "email": "person@example.com"},
    ).json()
    row = person.post("/api/invitations/redeem", json={"token": invitation["token"]}).json()
    sid = row["session_id"]
    assert person.post(f"/api/sessions/{sid}/start").status_code == 409
    assert (
        person.post(
            f"/api/sessions/{sid}/consent", json={"accepted": True, "version": "v1"}
        ).status_code
        == 200
    )
    assert person.post(f"/api/sessions/{sid}/start").status_code == 200
    return company, person, p, sid


def test_selection_and_tenant_isolation(app):
    a, b, person = TestClient(app), TestClient(app), TestClient(app)
    register(a, "a@example.com")
    register(b, "b@example.com")
    register(person, "p@example.com", "individual")
    assert (
        a.post("/api/scenario-options", json={"competency_ids": ["C01", "C02"]}).status_code == 422
    )
    assert (
        a.post("/api/scenario-options", json={"competency_ids": ["C01", "C01", "C03"]}).status_code
        == 422
    )
    assert (
        a.post("/api/scenario-options", json={"competency_ids": ["C01", "C02", "BAD"]}).status_code
        == 422
    )
    assert any(
        s["is_fixture"]
        for s in a.post(
            "/api/scenario-options", json={"competency_ids": ["C01", "C02", "C03"]}
        ).json()
    )
    all_six = project(a, [f"C0{i}" for i in range(1, 7)])
    assert b.get(f"/api/projects/{all_six['id']}").status_code == 404
    assert b.get("/api/projects").json() == []
    assert person.get("/api/projects").status_code == 403
    assert (
        a.post(
            "/api/projects",
            json={
                "title": "금지",
                "competency_ids": ["C01", "C02", "C03"],
                "scenario_version_id": "S01",
                "request_id": str(uuid4()),
            },
        ).status_code
        == 422
    )


def test_full_flow_revision_snapshot_and_early_submit(app):
    company, person, p, sid = start_session(app)
    body = {
        "content": "저장할 내용",
        "expected_revision": 0,
        "command_id": str(uuid4()),
    }
    saved = person.put(f"/api/sessions/{sid}/documents/notes", json=body)
    assert saved.status_code == 200, saved.text
    assert person.put(f"/api/sessions/{sid}/documents/notes", json=body).json()["revision"] == 1
    assert (
        person.put(
            f"/api/sessions/{sid}/documents/notes",
            json={**body, "command_id": str(uuid4())},
        ).status_code
        == 409
    )
    assert (
        person.put(
            f"/api/sessions/{sid}/tasks/draft",
            json={"status": "done", "expected_revision": 0, "command_id": str(uuid4())},
        ).status_code
        == 403
    )
    assert person.post(f"/api/sessions/{sid}/submit").json()["state"] == "submitted"
    assert person.post(f"/api/sessions/{sid}/submit").status_code == 200
    assert (
        person.put(
            f"/api/sessions/{sid}/documents/notes",
            json={**body, "expected_revision": 1, "command_id": str(uuid4())},
        ).status_code
        == 409
    )
    with app.state.sessions() as db:
        row = db.get(AssessmentSession, sid)
        assert row.snapshot["workspace"]["sections"][0]["content"] == "저장할 내용"
        assert len(db.scalars(select(Outbox)).all()) == 1
        assert db.get(Report, sid).status == "pending"
    assert company.get(f"/api/reports/{sid}").json()["status"] == "pending"
    assert person.get("/api/me/assessments").json()[0]["state"] == "submitted"


def test_pause_and_resume_and_session_ownership(app):
    company, person, p, sid = start_session(app)
    other = TestClient(app)
    register(other, "other@example.com", "individual")
    assert other.get(f"/api/sessions/{sid}").status_code == 404
    with person.websocket_connect(f"/api/sessions/{sid}/ws") as ws:
        ws.send_text("ping")
        assert ws.receive_json()["type"] in (
            "consent.accepted",
            "session.started",
            "session.connected",
            "clock",
        )
    assert person.get(f"/api/sessions/{sid}").json()["state"] == "paused"
    before = person.get(f"/api/sessions/{sid}").json()["elapsed_seconds"]
    assert person.get(f"/api/sessions/{sid}").json()["elapsed_seconds"] == before
    assert person.post(f"/api/sessions/{sid}/start").json()["state"] == "running"


def test_invitation_email_bound_and_auth_revocation(app):
    c, p = TestClient(app), TestClient(app)
    register(c, "co@example.com")
    register(p, "wrong@example.com", "individual")
    project_id = project(c)["id"]
    token = c.post(
        f"/api/projects/{project_id}/invitations",
        json={"email": "right@example.com", "alias": "test"},
    ).json()["token"]
    assert p.post("/api/invitations/redeem", json={"token": token}).status_code == 404
    assert c.post("/api/auth/logout").status_code == 200
    assert c.get("/api/auth/me").status_code == 401
    assert (
        c.post(
            "/api/auth/login",
            json={"email": "co@example.com", "password": "test-password-123"},
        ).status_code
        == 200
    )
    assert c.post("/api/auth/logout", headers={"origin": "https://evil.example"}).status_code == 403


def test_production_options_exclude_drafts_and_fixtures(app):
    app.state.settings.development = False
    c = TestClient(app)
    register(c, "co@example.com")
    # TestClient uses HTTP; production session cookies correctly require HTTPS.
    token = c.cookies.get("dayzero_session")
    c.cookies.clear()
    c.cookies.set("dayzero_session", token)
    result = c.post("/api/scenario-options", json={"competency_ids": ["C01", "C02", "C03"]})
    assert result.status_code == 200, result.text
    assert {s["id"] for s in result.json()} == {"S03-v1", "S03-v2"}
    assert not any(s["is_fixture"] for s in result.json())
    assert (
        c.post(
            "/api/scenario-options", json={"competency_ids": [f"C0{i}" for i in range(1, 7)]}
        ).json()
        == []
    )


def test_stale_heartbeat_pauses_without_consuming_disconnected_time(app):
    import time

    from services.gateway.domain.workspace import tick

    _, person, _, sid = start_session(app)
    now = time.time()
    with app.state.sessions.begin() as db:
        row = db.get(AssessmentSession, sid)
        row.resumed_at = now - 40
        row.heartbeat_at = now - 30
        tick(db, row, grace=15, now=now)
        assert row.state == "paused"
        assert abs(row.accumulated - 25) < 0.01
    state = person.get(f"/api/sessions/{sid}").json()
    assert state["state"] == "paused"
    assert abs(state["elapsed_seconds"] - 25) < 0.01


def test_runtime_auto_submits_only_once(app):
    import time

    from services.gateway.domain.workspace import tick

    _, _, _, sid = start_session(app)
    now = time.time()
    with app.state.sessions.begin() as db:
        row = db.get(AssessmentSession, sid)
        row.accumulated = 7199
        row.resumed_at = now - 2
        row.heartbeat_at = now
        tick(db, row, now=now)
        tick(db, row, now=now + 3)
        assert row.state == "submitted"
        assert row.accumulated == 7200
    with app.state.sessions() as db:
        assert len(db.scalars(select(Outbox)).all()) == 1


def test_malformed_origin_and_incorrect_consent(app):
    _, person, _, sid = start_session(app)
    assert (
        person.post(
            f"/api/sessions/{sid}/consent", json={"accepted": False, "version": "v1"}
        ).status_code
        == 422
    )
    assert (
        person.post(
            f"/api/sessions/{sid}/submit", headers={"origin": "https://untrusted.test"}
        ).status_code
        == 403
    )


def test_internal_tool_scope_and_assignment_acceptance(app):
    from services.gateway.domain.security import service_token
    from services.gateway.persistence.models import Project

    _, person, p, sid = start_session(app)
    with app.state.sessions() as db:
        tenant_id = db.get(Project, p["id"]).tenant_id

    def headers(role):
        token = service_token(
            app.state.settings.secret,
            "gateway",
            tenant_id=tenant_id,
            session_id=sid,
            actor_role=role,
            scope="business",
        )
        return {"Authorization": "Bearer " + token}

    revision = person.get(f"/api/sessions/{sid}").json()["revision"]
    arguments = {
        "assignments": {"notes": "colleague", "draft": "candidate"},
        "expected_revision": revision,
        "command_id": str(uuid4()),
    }
    assert (
        person.post(
            "/internal/tool",
            json={"tool": "propose_task_assignment", "arguments": arguments},
            headers=headers("assistant"),
        ).status_code
        == 403
    )
    proposal = person.post(
        "/internal/tool",
        json={"tool": "propose_task_assignment", "arguments": arguments},
        headers=headers("colleague"),
    )
    assert proposal.status_code == 200, proposal.text
    before = person.get(f"/api/sessions/{sid}").json()
    assert before["workspace"]["sections"][0]["assignee"] == "candidate"
    proposal_id = proposal.json()["proposal_id"]
    accepted = person.post(
        f"/api/sessions/{sid}/assignments/{proposal_id}/decision", json={"approved": True}
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["workspace"]["sections"][0]["assignee"] == "colleague"
    assert accepted.json()["workspace"]["sections"][1]["assignee"] == "candidate"
    assert (
        person.post(
            f"/api/sessions/{sid}/assignments/{proposal_id}/decision", json={"approved": True}
        ).status_code
        == 200
    )
    assert (
        person.post(
            f"/api/sessions/{sid}/assignments/{proposal_id}/decision", json={"approved": False}
        ).status_code
        == 409
    )


def test_document_release_and_expired_invitation(app):
    import time
    from copy import deepcopy

    from services.gateway.persistence.models import Invitation, Scenario

    with app.state.sessions.begin() as db:
        scenario = db.get(Scenario, "fixture-workspace-v1")
        config = deepcopy(scenario.config)
        config["documents"] = [
            {
                "id": "visible",
                "title": "공개 fixture",
                "content": "visible",
                "release_after_seconds": 0,
            },
            {
                "id": "delayed",
                "title": "지연 fixture",
                "content": "hidden",
                "release_after_seconds": 60,
            },
        ]
        scenario.config = config
    company, person, p, sid = start_session(app)
    response = person.get(f"/api/sessions/{sid}").json()
    assert [d["id"] for d in response["documents"]] == ["visible"]
    # A second, expired invitation never creates another session.
    invited = company.post(
        f"/api/projects/{p['id']}/invitations",
        json={"email": "expired@example.com", "alias": "만료 확인"},
    ).json()
    expired = TestClient(app)
    register(expired, "expired@example.com", "individual")
    with app.state.sessions.begin() as db:
        inv = db.get(Invitation, invited["id"])
        inv.expires_at = time.time() - 1
    assert (
        expired.post("/api/invitations/redeem", json={"token": invited["token"]}).status_code == 410
    )


@pytest.mark.parametrize(
    "scenario_id,codes,late_doc,late_event",
    [
        ("S01-v1", ["C01", "C02", "C04", "C05", "C06"], "S01-v1-change", "capacity"),
        ("S02-v1", ["C01", "C03", "C04", "C05", "C06"], "S02-v1-update", "scope_update"),
        ("S03-v1", ["C01", "C02", "C03", "C04", "C05"], "S03-v1-delay", "delay"),
    ],
)
def test_published_scenario_timeline_and_context(app, scenario_id, codes, late_doc, late_event):
    import time

    from services.gateway.domain.security import service_token
    from services.gateway.domain.workspace import tick
    from services.gateway.persistence.models import Event, Project
    from services.report_worker.observations.report import build_report

    company, person = TestClient(app), TestClient(app)
    register(company, "scenario-company@example.test")
    register(person, "scenario-person@example.test", "individual")
    p = company.post(
        "/api/projects",
        json={
            "title": "시나리오 확인",
            "competency_ids": codes,
            "scenario_version_id": scenario_id,
            "request_id": str(uuid4()),
        },
    ).json()
    invite = company.post(
        f"/api/projects/{p['id']}/invitations",
        json={"alias": "지원자", "email": "scenario-person@example.test"},
    ).json()
    sid = person.post("/api/invitations/redeem", json={"token": invite["token"]}).json()[
        "session_id"
    ]
    person.post(f"/api/sessions/{sid}/consent", json={"accepted": True, "version": "v1"})
    result = person.post(f"/api/sessions/{sid}/start").json()
    assert late_doc not in {d["id"] for d in result["documents"]}
    initial = person.get(f"/api/sessions/{sid}/events").json()
    assert sum(e["type"] == "scenario.event" for e in initial) == 1
    assert all("role_updates" not in e["payload"] for e in initial)
    with app.state.sessions() as db:
        tenant = db.get(Project, p["id"]).tenant_id

    def context(role):
        token = service_token(
            app.state.settings.secret,
            "gateway",
            session_id=sid,
            tenant_id=tenant,
            actor_role=role,
            scope="agent",
        )
        return person.get("/internal/context", headers={"Authorization": "Bearer " + token}).json()

    assert "현재 가용량은 4인일" not in context("colleague")["role_instruction"]
    with app.state.sessions.begin() as db:
        row = db.get(AssessmentSession, sid)
        row.accumulated, row.resumed_at, row.heartbeat_at = 46 * 60, time.time(), time.time()
        tick(db, row)
        tick(db, row)
    view = person.get(f"/api/sessions/{sid}").json()
    assert late_doc in {d["id"] for d in view["documents"]}
    delivered = person.get(f"/api/sessions/{sid}/events").json()
    assert sum(e["payload"].get("scenario_event_id") == late_event for e in delivered) == 1
    assert any(e["type"] == "scenario.event" for e in context("colleague")["messages"])
    assert "role_updates" not in str(context("assistant"))
    if scenario_id == "S01-v1":
        assert "현재 가용량은 4인일" in context("leader")["role_instruction"]
        assert "현재 가용량은 4인일" not in context("assistant")["role_instruction"]
    section = next(s for s in view["workspace"]["sections"] if s["assignee"] == "candidate")
    assert (
        person.put(
            f"/api/sessions/{sid}/documents/{section['id']}",
            json={
                "content": "확인한 사실과 추가 확인 사항",
                "expected_revision": 0,
                "command_id": str(uuid4()),
            },
        ).status_code
        == 200
    )
    assert person.post(f"/api/sessions/{sid}/submit").status_code == 200
    with app.state.sessions() as db:
        row = db.get(AssessmentSession, sid)
        status, report = build_report(row.snapshot)
        assert status == "ready"
        assert {i["competency_id"] for i in report["items"]} == set(codes)
        assert report["analysis_mode"] == "activity_evidence_only"
        assert any(s["status"] == "not_provided" for i in report["items"] for s in i["situations"])
        before = len(db.scalars(select(Event).where(Event.session_id == sid)).all())
        tick(db, row, now=time.time() + 10000)
        assert len(db.scalars(select(Event).where(Event.session_id == sid)).all()) == before


def test_authored_demo_replay_uses_actual_submit_snapshot_and_preserves_sources(app):
    import json

    from services.gateway.demo_report import replay_records
    from services.gateway.domain.workspace import locked_session
    from services.report_worker.observations.report import build_report

    sample = json.loads(Path("docs/reports/S01-sample-records.json").read_text())
    codes = [c["id"] for c in sample["snapshot"]["competencies"]]
    _, person, _, sid = start_session(app, "S01-v1", codes)
    with app.state.sessions.begin() as db:
        replay_records(db, locked_session(db, sid), sample)
        assert db.get(Report, sid) is None
    result = person.post(f"/api/sessions/{sid}/submit")
    assert result.status_code == 200, result.text
    with app.state.sessions.begin() as db:
        row = locked_session(db, sid)
        assert row.state == "submitted"
        assert db.get(Report, sid).status == "pending"
        status, report = build_report(row.snapshot)
        assert status == "ready" and len(report["items"]) == 5
        events = {e["id"]: e for e in row.snapshot["events"]}
        for item in report["items"]:
            assert item["evidence"]
            for evidence in item["evidence"]:
                assert evidence["id"] in events
        usage = next(e for e in events.values() if "source_response_id" in e["payload"])
        source = events[usage["payload"]["source_response_id"]]
        assert source["payload"]["actor"] == "assistant"
        before = row.revision
        replay_records(db, row, sample)
        assert row.revision == before
        assert len(db.scalars(select(Outbox).where(Outbox.session_id == sid)).all()) == 1


def test_v2_freezes_review_criteria_and_worker_is_idempotent(app):
    import json

    from services.gateway.demo_report import replay_records
    from services.gateway.domain.workspace import locked_session
    from services.report_worker.main import process

    sample = json.loads(Path("docs/reports/S01-sample-records.json").read_text())
    sample["provenance"]["scenario_version"] = "S01-v2"
    _, person, _, sid = start_session(
        app, "S01-v2", [c["id"] for c in sample["snapshot"]["competencies"]]
    )
    with app.state.sessions.begin() as db:
        replay_records(db, locked_session(db, sid), sample)
    assert person.post(f"/api/sessions/{sid}/submit").status_code == 200
    with app.state.sessions() as db:
        row = db.get(AssessmentSession, sid)
        assert row.snapshot["review"]["version"] == "S01-review-v1"
        assert row.snapshot["scenario_version_id"] == "S01-v2"
        assert row.snapshot["documents"]
        job = {"session_id": sid, "snapshot_id": row.snapshot_id, "report_version": "v1"}
    calls = []

    def unavailable(payload):
        calls.append(payload)
        raise RuntimeError("test-model-unavailable")

    assert process(app.state.engine, job, unavailable) == "ready"
    assert process(app.state.engine, job, unavailable) == "ready"
    assert len(calls) == 1
    with app.state.sessions() as db:
        report = db.get(Report, sid)
        assert report.content["ai_review"]["status"] == "unavailable"
        assert report.content["final_review"]["criteria"]
        assert db.get(AssessmentSession, sid).state == "submitted"


def test_v2_early_submission_does_not_include_future_reference_documents(app):
    _, person, _, sid = start_session(app, "S01-v2", ["C01", "C04", "C05"])
    assert person.post(f"/api/sessions/{sid}/submit").status_code == 200
    with app.state.sessions() as db:
        snapshot = db.get(AssessmentSession, sid).snapshot
        assert snapshot["review"]
        assert "S01-v1-change" not in {d["id"] for d in snapshot["documents"]}
        assert not any(
            e["payload"].get("scenario_event_id") == "capacity" for e in snapshot["events"]
        )


def test_login_cookie_is_only_issued_after_session_commit(app, monkeypatch):
    from starlette.responses import Response

    from services.gateway.domain.security import digest
    from services.gateway.persistence.models import LoginSession

    original = Response.set_cookie
    issued = []

    def verify_commit(response, key, value="", *args, **kwargs):
        if key == "dayzero_session" and value:
            with app.state.sessions() as db:
                assert db.get(LoginSession, digest(value)) is not None
            issued.append(key)
        return original(response, key, value, *args, **kwargs)

    monkeypatch.setattr(Response, "set_cookie", verify_commit)
    client = TestClient(app)
    register(client, "commit-before-cookie@example.test")
    result = client.post(
        "/api/auth/login",
        json={"email": "commit-before-cookie@example.test", "password": "test-password-123"},
    )
    assert result.status_code == 200
    assert len(issued) == 2


def test_project_overview_counts_are_tenant_scoped(app):
    company, person, p, sid = start_session(app)
    empty = project(company)
    other = TestClient(app)
    register(other, "other-overview@example.com")
    foreign = project(other)
    assert other.post(
        f"/api/projects/{foreign['id']}/invitations",
        json={"alias": "다른 기업 지원자", "email": "foreign@example.com"},
    ).status_code == 200
    assert company.post(
        f"/api/projects/{p['id']}/invitations",
        json={"alias": "대기 지원자", "email": "waiting@example.com"},
    ).status_code == 200
    by_id = {item["id"]: item for item in company.get("/api/projects").json()}
    assert foreign["id"] not in by_id
    assert by_id[p["id"]]["counts"] == {
        "invited": 2, "running": 1, "submitted": 0, "reports_ready": 0,
    }
    assert by_id[empty["id"]]["counts"] == {
        "invited": 0, "running": 0, "submitted": 0, "reports_ready": 0,
    }
    detail = person.get(f"/api/sessions/{sid}").json()
    assessment = person.get("/api/me/assessments").json()[0]
    assert detail["duration_seconds"] == p["duration_seconds"] == assessment["duration_seconds"]
    assert person.post(f"/api/sessions/{sid}/submit").status_code == 200
    # Report readiness must be distinct from submission, even while a report is pending.
    counts = next(item for item in company.get("/api/projects").json() if item["id"] == p["id"])["counts"]
    assert counts == {"invited": 2, "running": 0, "submitted": 1, "reports_ready": 0}
    with app.state.sessions.begin() as db:
        db.get(Report, sid).status = "ready"
    counts = next(item for item in company.get("/api/projects").json() if item["id"] == p["id"])["counts"]
    assert counts["reports_ready"] == 1
