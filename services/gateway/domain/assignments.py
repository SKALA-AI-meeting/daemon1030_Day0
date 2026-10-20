from copy import deepcopy

from fastapi import HTTPException
from sqlalchemy import select

from services.gateway.domain.workspace import record
from services.gateway.persistence.models import Event


def apply_assignment(db, row, proposal):
    if row.state != "running":
        raise HTTPException(409, "진행 중인 평가에서만 분업을 변경할 수 있습니다.")
    decisions = db.scalars(
        select(Event).where(Event.session_id == row.id, Event.event_type == "assignment.decision")
    ).all()
    accepted = any(
        e.payload.get("proposal_id") == proposal.id and e.payload.get("approved") for e in decisions
    )
    if not accepted and proposal.payload.get("adjustment_round", 0) < 3:
        raise HTTPException(403, "지원자 수락 또는 조정 종료 조건이 필요합니다.")
    latest = db.scalar(
        select(Event)
        .where(Event.session_id == row.id, Event.event_type == "assignment.proposed")
        .order_by(Event.seq.desc())
        .limit(1)
    )
    if not latest or latest.id != proposal.id:
        raise HTTPException(409, "최신 분담안이 아닙니다.")
    existing = db.scalar(
        select(Event).where(
            Event.session_id == row.id, Event.command_id == "assignment:" + proposal.id
        )
    )
    if existing:
        return existing.payload
    workspace = deepcopy(row.workspace)
    assignments = proposal.payload["assignments"]
    for group in ("sections", "tasks"):
        for item in workspace[group]:
            if item["id"] in assignments:
                item["assignee"] = assignments[item["id"]]
                item["revision"] += 1
    row.workspace = workspace
    return record(
        db,
        row,
        "assignment.applied",
        {"proposal_id": proposal.id, "assignments": assignments},
        "assignment:" + proposal.id,
    ).payload
