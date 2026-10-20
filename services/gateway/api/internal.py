"""Gateway-owned authorization for all service-side business commands."""

from copy import deepcopy

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select

from contracts.api.models import ToolCommand
from services.gateway.domain.assignments import apply_assignment
from services.gateway.domain.security import verify_service
from services.gateway.domain.workspace import (
    event_view,
    locked_session,
    record,
    tick,
    workspace_view,
)
from services.gateway.persistence.models import DocumentImport, Event, Project


def register_internal(app, sessions, settings, invoke_agent):
    def credentials(authorization: str = Header(default="")):
        return verify_service(settings.secret, authorization.removeprefix("Bearer "), "gateway")

    def session_context(db, claims):
        if claims.get("scope") not in ("business", "agent", "rag"):
            raise HTTPException(403, "허용되지 않은 범위입니다.")
        row = locked_session(db, claims.get("session_id", ""))
        project = db.get(Project, row.project_id)
        if project.tenant_id != claims.get("tenant_id"):
            raise HTTPException(404, "평가를 찾을 수 없습니다.")
        tick(db, row, settings.heartbeat_grace)
        return row, project

    @app.get("/internal/context")
    def context(claims=Depends(credentials)):
        with sessions.begin() as db:
            row, project = session_context(db, claims)
            if row.state != "running":
                raise HTTPException(409, "진행 중인 평가가 아닙니다.")
            role = claims.get("actor_role")
            if role not in ("colleague", "assistant", "leader"):
                raise HTTPException(403, "역할을 확인할 수 없습니다.")
            channel = "assistant" if role == "assistant" else "team"
            all_events = db.scalars(
                select(Event)
                .where(Event.session_id == row.id)
                .order_by(Event.seq.desc())
                .limit(100)
            ).all()
            messages = [
                event_view(e)
                for e in reversed(all_events)
                if e.event_type in ("message.created", "scenario.event")
                and (
                    e.event_type == "scenario.event"
                    or e.payload.get("channel") == channel
                    or (role != "assistant" and e.payload.get("channel") == "dm")
                )
            ]
            view = workspace_view(db, row)
            delivered = {
                e.payload.get("scenario_event_id")
                for e in db.scalars(
                    select(Event).where(
                        Event.session_id == row.id, Event.event_type == "scenario.event"
                    )
                )
            }
            updates = (
                [
                    e.get("role_updates", {}).get(role, "")
                    for e in project.config_snapshot.get("events", [])
                    if e["id"] in delivered
                ]
                if role != "assistant"
                else []
            )
            return {
                "session_id": row.id,
                "state": row.state,
                "brief": view["brief"],
                "role_instruction": project.config_snapshot.get("role_instructions", {}).get(
                    role, ""
                )
                + "\n현재까지 도착한 업무 정보(이후 정보를 우선):\n"
                + "\n".join(updates),
                "messages": messages[-30:],
                "documents": view["documents"],
                "workspace": row.workspace if role != "assistant" else {},
                "revision": row.revision,
            }

    @app.get("/internal/visible-documents")
    def visible(claims=Depends(credentials)):
        with sessions.begin() as db:
            row, project = session_context(db, claims)
            return workspace_view(db, row)["documents"]

    @app.post("/internal/tool")
    def tool(body: ToolCommand, claims=Depends(credentials)):
        if claims.get("scope") != "business":
            raise HTTPException(403, "업무 도구 범위가 필요합니다.")
        with sessions.begin() as db:
            row, project = session_context(db, claims)
            role, name, args = claims.get("actor_role"), body.tool, body.arguments
            if role not in ("colleague", "leader", "orchestrator", "observer"):
                raise HTTPException(403, "업무 도구를 사용할 수 없습니다.")
            if name == "get_session_context":
                view = workspace_view(db, row)
                return {k: view[k] for k in ("brief", "state", "remaining_seconds", "revision")}
            if name == "list_tasks":
                return {"tasks": row.workspace["tasks"]}
            if name == "get_document_section":
                item = next(
                    (x for x in row.workspace["sections"] if x["id"] == args.get("section_id")),
                    None,
                )
                if not item:
                    raise HTTPException(404, "항목을 찾을 수 없습니다.")
                return item
            if row.state != "running":
                raise HTTPException(409, "SESSION_CLOSED")
            command_id = args.get("command_id", "")
            if not isinstance(command_id, str) or not 8 <= len(command_id) <= 100:
                raise HTTPException(422, "command_id가 필요합니다.")
            old = db.scalar(
                select(Event).where(Event.session_id == row.id, Event.command_id == command_id)
            )
            if old:
                return old.payload
            if name == "propose_task_assignment" and role == "colleague":
                if args.get("expected_revision") != row.revision:
                    raise HTTPException(409, "VERSION_CONFLICT")
                assignments = args.get("assignments", {})
                if (
                    not isinstance(assignments, dict)
                    or set(assignments) != {x["id"] for x in row.workspace["tasks"]}
                    or any(v not in ("candidate", "colleague") for v in assignments.values())
                ):
                    raise HTTPException(422, "모든 태스크의 담당자를 지정해 주세요.")
                previous_proposals = db.scalars(
                    select(Event).where(
                        Event.session_id == row.id, Event.event_type == "assignment.proposed"
                    )
                ).all()
                if len(previous_proposals) >= 4:
                    raise HTTPException(409, "분업 조정 한도에 도달했습니다.")
                proposal = record(
                    db,
                    row,
                    "assignment.proposed",
                    {"assignments": assignments, "adjustment_round": len(previous_proposals)},
                    command_id,
                )
                if len(previous_proposals) == 3:
                    apply_assignment(db, row, proposal)
                return {"proposal_id": proposal.id, "assignments": assignments}
            if name == "apply_task_assignment" and role == "orchestrator":
                proposal = db.get(Event, args.get("proposal_id"))
                if (
                    not proposal
                    or proposal.session_id != row.id
                    or proposal.event_type != "assignment.proposed"
                ):
                    raise HTTPException(404, "분담안을 찾을 수 없습니다.")
                return apply_assignment(db, row, proposal)
            if name == "update_task_status" and role == "colleague":
                group, identifier = "tasks", args.get("task_id")
                if args.get("status") not in ("todo", "doing", "done"):
                    raise HTTPException(422, "올바르지 않은 태스크 상태입니다.")
            elif name == "publish_colleague_draft" and role == "colleague":
                group, identifier = "sections", args.get("section_id")
                if not isinstance(args.get("content"), str) or len(args["content"]) > 60000:
                    raise HTTPException(422, "초안 길이를 확인해 주세요.")
            else:
                # No fabricated observer output or unverified assignment acceptance.
                raise HTTPException(403, "승인된 실행 경로가 필요합니다.")
            workspace = deepcopy(row.workspace)
            item = next((i for i in workspace[group] if i["id"] == identifier), None)
            if not item or item["assignee"] != "colleague":
                raise HTTPException(403, "자신의 담당 항목만 변경할 수 있습니다.")
            if item["revision"] != args.get("expected_revision"):
                raise HTTPException(409, "VERSION_CONFLICT")
            item["status" if group == "tasks" else "content"] = args[
                "status" if group == "tasks" else "content"
            ]
            item["revision"] += 1
            row.workspace = workspace
            result = {"item": item}
            record(
                db,
                row,
                "task.updated" if group == "tasks" else "document.updated",
                result,
                command_id,
            )
            return result

    @app.post("/internal/agent-result")
    def agent_result(body: dict, claims=Depends(credentials)):
        if claims.get("scope") != "agent":
            raise HTTPException(403, "Agent 범위가 필요합니다.")
        with sessions.begin() as db:
            row, project = session_context(db, claims)
            if row.state != "running":
                raise HTTPException(409, "SESSION_CLOSED")
            source = db.get(Event, body.get("input_id"))
            role = claims.get("actor_role")
            expected_channel = "assistant" if role == "assistant" else None
            if (
                not source
                or source.session_id != row.id
                or source.payload.get("actor") != "candidate"
                or (expected_channel and source.payload.get("channel") != expected_channel)
                or (not expected_channel and source.payload.get("channel") == "assistant")
                or (not expected_channel and source.payload.get("recipient", "colleague") != role)
            ):
                raise HTTPException(422, "올바르지 않은 입력 참조입니다.")
            content = body.get("content", "")
            if not isinstance(content, str) or not content or len(content) > 24000:
                raise HTTPException(422, "응답 내용을 확인해 주세요.")
            event = record(
                db,
                row,
                "message.created",
                {
                    "actor": role,
                    "actor_name": project.config_snapshot.get("role_names", {}).get(role, ""),
                    "channel": source.payload["channel"],
                    "content": content,
                    "input_id": source.id,
                    "citations": body.get("citations", []),
                },
                f"agent:{source.id}",
            )
            return event_view(event)

    @app.get("/internal/document-imports/{import_id}")
    def import_context(import_id: str, claims=Depends(credentials)):
        if claims.get("scope") != "documents:manage":
            raise HTTPException(403, "문서 관리 범위가 필요합니다.")
        with sessions.begin() as db:
            item = db.get(DocumentImport, import_id)
            if (
                not item
                or item.tenant_id != claims.get("tenant_id")
                or claims.get("import_id") != item.id
            ):
                raise HTTPException(404, "문서를 찾을 수 없습니다.")
            return {
                "id": item.id,
                "title": item.title,
                "content": item.content,
                "content_hash": item.content_hash,
                "status": item.status,
                "approval_id": item.approval_id,
            }

    @app.post("/internal/document-imports/{import_id}/status")
    def import_status(import_id: str, body: dict, claims=Depends(credentials)):
        if claims.get("scope") != "documents:manage":
            raise HTTPException(403, "문서 관리 범위가 필요합니다.")
        with sessions.begin() as db:
            item = db.scalar(
                select(DocumentImport).where(DocumentImport.id == import_id).with_for_update()
            )
            if (
                not item
                or item.tenant_id != claims.get("tenant_id")
                or claims.get("import_id") != import_id
            ):
                raise HTTPException(404, "문서를 찾을 수 없습니다.")
            status = body.get("status")
            if (
                body.get("content_hash") != item.content_hash
                or (status == "indexed" and item.status not in ("approved", "indexed"))
                or (status == "prepared" and item.status not in ("pending", "prepared"))
            ):
                raise HTTPException(409, "승인된 버전과 일치하지 않습니다.")
            if status not in ("prepared", "indexed"):
                raise HTTPException(422, "상태를 확인해 주세요.")
            item.status = status
            return {"ok": True}
