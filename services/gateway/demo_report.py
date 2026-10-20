"""Explicit local-only replay of authored sample records, followed by real submission.

No canned report is inserted: the normal Outbox/RabbitMQ worker produces it.
"""

import argparse
import json
import os
import time
from copy import deepcopy
from urllib.parse import urlparse

import httpx
from sqlalchemy import select

from services.gateway.domain.workspace import locked_session, record
from services.gateway.persistence.database import database
from services.gateway.persistence.models import Event, Outbox, Project, Report
from services.gateway.settings import ROOT, Settings


def replay_records(db, row, sample):
    project = db.get(Project, row.project_id)
    if project.scenario_id != sample["provenance"]["scenario_version"]:
        raise ValueError("시나리오 버전이 다릅니다.")
    source = sample["snapshot"]
    if {c["id"] for c in source["competencies"]} != {c["id"] for c in project.competencies}:
        raise ValueError("선택 역량이 다릅니다.")
    if row.state == "submitted":
        return
    existing = db.scalars(select(Event).where(Event.session_id == row.id)).all()
    if any(e.event_type in ("message.created", "document.updated") for e in existing):
        raise ValueError("이미 답변이 있는 응시에 예시 기록을 덮어쓸 수 없습니다.")
    if not row.consent_version or row.state not in ("running", "paused"):
        raise ValueError("동의·시작을 완료한 새 응시가 필요합니다.")
    row.state, row.resumed_at, row.connection_id = "paused", None, None
    row.accumulated = 0
    record(
        db,
        row,
        "demo.replayed",
        {
            "sample_id": sample["provenance"]["sample_id"],
            "notice": "작성한 가상 답변을 누적 시간에 맞춰 재생한 데모입니다. 실제 118분 응시·모델 대화가 아닙니다.",
        },
    )
    workspace, ids = deepcopy(row.workspace), {}
    previous = -1
    for event in source["events"]:
        used = event["elapsed"]
        if not previous <= used <= project.config_snapshot["duration_seconds"]:
            raise ValueError("재생 시간 순서가 올바르지 않습니다.")
        previous, row.accumulated = used, used
        kind, payload = event["type"], deepcopy(event["payload"])
        if kind == "session.closed":
            continue  # Actual submission endpoint creates the immutable snapshot and Outbox.
        if kind not in ("scenario.event", "message.created", "document.updated"):
            raise ValueError("지원하지 않는 재생 사건입니다.")
        for key in ("input_id", "source_response_id"):
            if key in payload:
                payload[key] = ids[payload[key]]
        if kind == "document.updated":
            item = payload["item"]
            target = next(s for s in workspace["sections"] if s["id"] == item["id"])
            if target["assignee"] != "candidate" or item["revision"] != target["revision"] + 1:
                raise ValueError("담당·문서 버전이 올바르지 않습니다.")
            target.update(item)
        command = (
            "scenario:" + payload["scenario_event_id"]
            if kind == "scenario.event"
            else "demo:" + event["id"]
        )
        saved = record(db, row, kind, payload, command)
        ids[event["id"]] = saved.id
    row.workspace = workspace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company-email", default="ui.company@example.test")
    parser.add_argument("--person-email", default="ui.person@example.test")
    parser.add_argument(
        "--refresh-ai",
        action="store_true",
        help="이 개발 샘플의 AI 평가만 새 파이프라인으로 재생성",
    )
    parser.add_argument(
        "--retry-ai", action="store_true", help="검토 실패한 이 개발 샘플만 큐에서 다시 처리"
    )
    parser.add_argument("--scenario-version", choices=["S01-v1", "S01-v2"], default="S01-v1")
    parser.add_argument("--gateway", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    settings = Settings().validate()
    if not settings.development or urlparse(args.gateway).hostname not in (
        "127.0.0.1",
        "localhost",
    ):
        raise RuntimeError("로컬 개발 모드에서만 예시 재생을 허용합니다.")
    password = os.environ["DAYZERO_DEMO_PASSWORD"]
    sample = json.loads((ROOT / "docs/reports/S01-sample-records.json").read_text())
    sample["provenance"]["scenario_version"] = args.scenario_version
    engine, sessions = database(settings.database_url)
    with (
        httpx.Client(base_url=args.gateway, timeout=30) as company,
        httpx.Client(base_url=args.gateway, timeout=30) as person,
    ):
        health = company.get("/api/health")
        health.raise_for_status()
        if not health.json().get("development"):
            raise RuntimeError("대상 서버가 개발 모드가 아닙니다.")
        for client, email in ((company, args.company_email), (person, args.person_email)):
            result = client.post("/api/auth/login", json={"email": email, "password": password})
            result.raise_for_status()
        result = company.post(
            "/api/projects",
            json={
                "title": "샘플 응시 · 신규 기능 개선안 "
                + ("AI 최종 평가" if args.scenario_version == "S01-v2" else "리포트"),
                "competency_ids": [c["id"] for c in sample["snapshot"]["competencies"]],
                "scenario_version_id": args.scenario_version,
                "request_id": "web-authored-demo-" + args.scenario_version,
            },
        )
        result.raise_for_status()
        pid = result.json()["id"]
        rows = company.get(f"/api/projects/{pid}/candidates").json()
        candidate = next((r for r in rows if r["email"] == args.person_email), None)
        if not candidate:
            result = company.post(
                f"/api/projects/{pid}/invitations",
                json={
                    "alias": "가상 지원자 A · 예시 답변",
                    "email": args.person_email,
                },
            )
            result.raise_for_status()
            invitation_id = result.json()["id"]
        else:
            invitation_id = candidate["id"]
        result = person.post(f"/api/invitations/{invitation_id}/accept")
        result.raise_for_status()
        sid = result.json()["session_id"]
        state = person.get(f"/api/sessions/{sid}").json()["state"]
        if state != "submitted":
            person.post(
                f"/api/sessions/{sid}/consent", json={"accepted": True, "version": "v1"}
            ).raise_for_status()
            person.post(f"/api/sessions/{sid}/start").raise_for_status()
            with sessions.begin() as db:
                replay_records(db, locked_session(db, sid), sample)
            person.post(f"/api/sessions/{sid}/submit").raise_for_status()
        if args.retry_ai or args.refresh_ai:
            with sessions.begin() as db:
                locked_session(db, sid)
                stored = db.get(Report, sid)
                if (
                    stored
                    and stored.status == "ready"
                    and (
                        args.refresh_ai
                        or stored.content.get("ai_review", {}).get("status") == "unavailable"
                    )
                ):
                    stored.status = "pending"
                    outbox = db.scalar(select(Outbox).where(Outbox.session_id == sid))
                    outbox.sent = False
        for _ in range(360):
            result = company.get(f"/api/reports/{sid}")
            result.raise_for_status()
            if result.json()["status"] == "ready":
                content = result.json()["content"]
                print(
                    json.dumps(
                        {
                            "project_id": pid,
                            "session_id": sid,
                            "url": f"http://127.0.0.1:5173/projects/{pid}",
                            "status": "ready",
                            "competencies": len(content["items"]),
                            "ai_review_status": content.get("ai_review", {}).get("status"),
                            "evidence_count": sum(len(i["evidence"]) for i in content["items"]),
                        },
                        ensure_ascii=False,
                    )
                )
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("실제 리포트 워커 완료를 확인하지 못했습니다.")
    engine.dispose()


if __name__ == "__main__":
    main()
