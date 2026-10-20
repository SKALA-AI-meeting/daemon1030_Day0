import time
from copy import deepcopy

from fastapi import HTTPException
from sqlalchemy import select

from services.gateway.persistence.models import (
    AssessmentSession,
    Event,
    Outbox,
    Project,
    Report,
    uid,
)


def locked_session(db, session_id):
    row = db.scalar(
        select(AssessmentSession).where(AssessmentSession.id == session_id).with_for_update()
    )
    if not row:
        raise HTTPException(404, "평가를 찾을 수 없습니다.")
    return row


def elapsed(row, now=None):
    return row.accumulated + (
        max(0, (now or time.time()) - row.resumed_at)
        if row.state == "running" and row.resumed_at
        else 0
    )


def record(db, row, kind, payload, command_id=None):
    if command_id:
        old = db.scalar(
            select(Event).where(Event.session_id == row.id, Event.command_id == command_id)
        )
        if old:
            if old.event_type != kind or old.payload != payload:
                raise HTTPException(409, "같은 명령 ID를 다른 내용에 사용할 수 없습니다.")
            return old
    row.revision += 1
    event = Event(
        session_id=row.id,
        seq=row.revision,
        event_type=kind,
        payload=payload,
        elapsed=elapsed(row),
        command_id=command_id,
    )
    db.add(event)
    db.flush()
    return event


def pause(db, row, at=None):
    if row.state == "running":
        row.accumulated = elapsed(row, at)
        row.resumed_at = None
        row.state = "paused"
        row.connection_id = None
        record(db, row, "session.paused", {})


def finish(db, row):
    if row.state == "submitted":
        return
    if row.state not in ("running", "paused"):
        raise HTTPException(409, "시작된 평가만 제출할 수 있습니다.")
    project = db.get(Project, row.project_id)
    row.accumulated = min(elapsed(row), project.config_snapshot["duration_seconds"])
    row.resumed_at = None
    row.connection_id = None
    row.state = "submitted"
    record(db, row, "session.closed", {"state": "submitted"})
    events = db.scalars(select(Event).where(Event.session_id == row.id).order_by(Event.seq)).all()
    row.snapshot_id = uid()
    row.snapshot = {
        "workspace": deepcopy(row.workspace),
        "competencies": project.competencies,
        "rules": project.config_snapshot.get("observation_rules", []),
        "review": deepcopy(project.config_snapshot.get("review")),
        "scenario_version_id": project.scenario_id,
        "scenario_title": project.config_snapshot["title"],
        "brief": project.config_snapshot.get("brief", ""),
        "documents": [
            deepcopy(d)
            for d in project.config_snapshot.get("documents", [])
            if d.get("release_after_seconds", 0) <= row.accumulated
        ],
        "events": [event_view(e) for e in events],
        "elapsed": row.accumulated,
        "is_fixture": project.config_snapshot.get("is_fixture", False),
    }
    db.add(Report(session_id=row.id, snapshot_id=row.snapshot_id, status="pending", content={}))
    db.add(
        Outbox(
            session_id=row.id,
            payload={
                "session_id": row.id,
                "snapshot_id": row.snapshot_id,
                "report_version": "v1",
            },
        )
    )
    db.flush()


def tick(db, row, grace=15, now=None):
    now = now or time.time()
    if row.state != "running":
        return
    config = db.get(Project, row.project_id).config_snapshot
    cutoff = min(now, (row.heartbeat_at or now) + grace)
    used = elapsed(row, cutoff)
    existing = {
        e.command_id for e in db.scalars(select(Event).where(Event.session_id == row.id)).all()
    }
    for item in config.get("events", []):
        command = f"scenario:{item['id']}"
        if used >= item["at_seconds"] and command not in existing:
            record(
                db,
                row,
                "scenario.event",
                {
                    "scenario_event_id": item["id"],
                    "actor": item["actor"],
                    "actor_name": item.get("actor_name", ""),
                    "content": item["content"],
                    "scheduled_elapsed": item["at_seconds"],
                },
                command,
            )
    if used >= config["duration_seconds"]:
        row.accumulated = config["duration_seconds"]
        row.resumed_at = None
        finish(db, row)
    elif now > cutoff:
        pause(db, row, cutoff)


def event_view(event):
    return {
        "id": event.id,
        "seq": event.seq,
        "type": event.event_type,
        "elapsed": event.elapsed,
        "created_at": event.created_at,
        "payload": event.payload,
    }


def workspace_view(db, row):
    project = db.get(Project, row.project_id)
    config = project.config_snapshot
    used = elapsed(row)
    docs = [d for d in config.get("documents", []) if d.get("release_after_seconds", 0) <= used]
    return {
        "id": row.id,
        "project_id": project.id,
        "title": project.title,
        "scenario_title": config["title"],
        "brief": config.get("brief", ""),
        "role_names": config.get("role_names", {}),
        "is_fixture": config.get("is_fixture", False),
        "state": row.state,
        "consented": bool(row.consent_version),
        "elapsed_seconds": used,
        "remaining_seconds": max(0, config["duration_seconds"] - used),
        "duration_seconds": config["duration_seconds"],
        "revision": row.revision,
        "competencies": project.competencies,
        "workspace": row.workspace,
        "documents": docs,
    }


def initial_workspace(config):
    return {
        "sections": [
            {
                "id": section["id"],
                "title": section["title"],
                "assignee": section.get("assignee", "candidate"),
                "content": "",
                "revision": 0,
            }
            for section in config.get("sections", [])
        ],
        "tasks": [
            {
                "id": section["id"],
                "title": section["title"],
                "assignee": section.get("assignee", "candidate"),
                "status": "todo",
                "revision": 0,
            }
            for section in config.get("sections", [])
        ],
    }
