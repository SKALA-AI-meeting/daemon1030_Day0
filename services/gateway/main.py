import asyncio
import json
import secrets
import time
from contextlib import asynccontextmanager
from copy import deepcopy

import httpx
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import JSONResponse
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError

from contracts.api.models import (
    ApprovalDecision,
    AssignmentDecision,
    Consent,
    DocumentUpdate,
    InvitationCreate,
    Login,
    MessageCreate,
    ProjectCreate,
    Redeem,
    Registration,
    Selection,
    TaskUpdate,
)
from contracts.api.models import DocumentImport as ImportRequest
from services.gateway.domain.assignments import apply_assignment
from services.gateway.domain.security import (
    digest,
    password_hash,
    password_matches,
    service_token,
)
from services.gateway.domain.workspace import (
    event_view,
    finish,
    initial_workspace,
    locked_session,
    pause,
    record,
    tick,
    workspace_view,
)
from services.gateway.persistence.database import database
from services.gateway.persistence.models import (
    AssessmentSession,
    DocumentImport,
    Event,
    Invitation,
    LoginSession,
    Project,
    Report,
    Scenario,
    User,
    uid,
)
from services.gateway.settings import ROOT, Settings

CATALOG = json.loads((ROOT / "competencies/catalog.json").read_text())
CODES = {c["id"] for c in CATALOG}


def create_app(settings=None, *, background=True):
    settings = (settings or Settings()).validate()
    engine, sessions = database(settings.database_url)

    @asynccontextmanager
    async def lifespan(app):
        async def runtime_loop():
            while True:
                try:
                    await asyncio.to_thread(tick_all)
                except Exception:
                    import logging

                    logging.getLogger("dayzero.runtime").exception("runtime tick failed")
                await asyncio.sleep(2)

        task = asyncio.create_task(runtime_loop()) if background else None
        yield
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        engine.dispose()

    app = FastAPI(title="Day Zero Gateway", lifespan=lifespan)
    app.state.settings, app.state.engine, app.state.sessions = (
        settings,
        engine,
        sessions,
    )

    def tick_all():
        with sessions.begin() as db:
            rows = db.scalars(
                select(AssessmentSession)
                .where(AssessmentSession.state == "running")
                .with_for_update(skip_locked=True)
            ).all()
            for row in rows:
                tick(db, row, settings.heartbeat_grace)

    @app.middleware("http")
    async def origin_check(request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if (
                origin
                and origin not in settings.origins
                and origin != str(request.base_url).rstrip("/")
            ):
                return JSONResponse({"detail": "허용되지 않은 요청 출처입니다."}, status_code=403)
        return await call_next(request)

    @app.exception_handler(IntegrityError)
    async def conflict_handler(request, exc):
        return JSONResponse(
            {"detail": "이미 처리되었거나 충돌한 요청입니다. 다시 조회해 주세요."},
            status_code=409,
        )

    def db_session():
        with sessions.begin() as db:
            yield db

    def user_from_cookie(db, token):
        auth = db.get(LoginSession, digest(token or ""))
        if not auth or auth.expires_at <= time.time():
            raise HTTPException(401, "로그인이 필요합니다.")
        user = db.get(User, auth.user_id)
        if not user:
            raise HTTPException(401, "로그인이 필요합니다.")
        return user

    def current_user(request: Request, db=Depends(db_session)):
        return user_from_cookie(db, request.cookies.get("dayzero_session"))

    def company(user=Depends(current_user)):
        if user.role != "company":
            raise HTTPException(403, "기업 관리자만 사용할 수 있습니다.")
        return user

    def individual(user=Depends(current_user)):
        if user.role != "individual":
            raise HTTPException(403, "개인 계정으로 로그인해 주세요.")
        return user

    def public_user(user):
        return {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "role": user.role,
            "company_name": user.company_name,
        }

    def login_cookie(db, user, response):
        token = secrets.token_urlsafe(32)
        db.add(
            LoginSession(
                token_hash=digest(token),
                user_id=user.id,
                expires_at=time.time() + 86400,
            )
        )
        # Persist authentication before a client can issue its first authenticated request.
        db.commit()
        response.set_cookie(
            "dayzero_session",
            token,
            httponly=True,
            samesite="strict",
            secure=not settings.development,
            max_age=86400,
            path="/",
        )

    def own_project(db, project_id, user):
        project = db.get(Project, project_id)
        if not project or project.tenant_id != user.tenant_id:
            raise HTTPException(404, "프로젝트를 찾을 수 없습니다.")
        return project

    def own_session(db, session_id, user):
        row = locked_session(db, session_id)
        if row.user_id != user.id:
            raise HTTPException(404, "평가를 찾을 수 없습니다.")
        tick(db, row, settings.heartbeat_grace)
        return row

    def check_selection(selection):
        if not set(selection).issubset(CODES):
            raise HTTPException(422, "등록되지 않은 역량입니다.")

    def selectable(config, selection):
        if config.status != "published" or (config.is_fixture and not settings.development):
            return False
        if not set(selection).issubset(config.config.get("competency_ids", [])):
            return False
        # Real scenarios require actual observation rules; empty names are not a valid evaluation.
        if not config.is_fixture:
            covered = {r.get("competency_id") for r in config.config.get("observation_rules", [])}
            return set(selection).issubset(covered)
        return True

    async def invoke_agent(
        session_id, message_id, role, tenant_id, *, import_id=None, decision=None
    ):
        audience = "agent"
        token = service_token(
            settings.secret,
            audience,
            session_id=session_id,
            tenant_id=tenant_id,
            actor_role=role,
            scope="agent:run" if not import_id else "documents:manage",
            import_id=import_id,
        )
        path = "/runs" if not import_id else "/document-runs"
        payload = (
            {"session_id": session_id, "message_id": message_id, "role": role}
            if not import_id
            else {"import_id": import_id, "decision": decision}
        )
        try:
            async with httpx.AsyncClient(timeout=75) as client:
                result = await client.post(
                    settings.agent_url + path,
                    json=payload,
                    headers={"Authorization": f"Bearer {token}"},
                )
                result.raise_for_status()
        except (httpx.HTTPError, ValueError):
            if not import_id:
                with sessions.begin() as db:
                    row = locked_session(db, session_id)
                    if row.state == "running":
                        record(
                            db,
                            row,
                            "agent.unavailable",
                            {
                                "channel": "assistant" if role == "assistant" else "team",
                                "message": "AI 연결을 확인하지 못했습니다. 작성한 내용은 저장되어 있습니다.",
                            },
                            f"agent-error:{message_id}",
                        )

    @app.get("/api/health")
    def health():
        return {"status": "ok", "development": settings.development}

    @app.post("/api/auth/register")
    def register(body: Registration, response: Response, db=Depends(db_session)):
        if body.role == "company" and not (body.company_name or "").strip():
            raise HTTPException(422, "회사 이름을 입력해 주세요.")
        email = body.email.strip().lower()
        if db.scalar(select(User).where(User.email == email)):
            raise HTTPException(409, "가입할 수 없는 이메일입니다.")
        user = User(
            email=email,
            password_hash=password_hash(body.password),
            name=body.name.strip(),
            role=body.role,
            tenant_id=uid() if body.role == "company" else None,
            company_name=body.company_name if body.role == "company" else None,
        )
        db.add(user)
        db.flush()
        login_cookie(db, user, response)
        return public_user(user)

    @app.post("/api/auth/login")
    def login(body: Login, response: Response, db=Depends(db_session)):
        user = db.scalar(select(User).where(User.email == body.email.strip().lower()))
        if not user or not password_matches(body.password, user.password_hash):
            raise HTTPException(401, "이메일 또는 비밀번호를 확인해 주세요.")
        login_cookie(db, user, response)
        return public_user(user)

    @app.get("/api/auth/me")
    def me(user=Depends(current_user)):
        return public_user(user)

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response, db=Depends(db_session)):
        auth = db.get(LoginSession, digest(request.cookies.get("dayzero_session", "")))
        if auth:
            db.delete(auth)
        response.delete_cookie("dayzero_session", path="/")
        return {"ok": True}

    @app.get("/api/competencies")
    def competencies(user=Depends(current_user)):
        return CATALOG

    @app.post("/api/scenario-options")
    def scenario_options(body: Selection, user=Depends(company), db=Depends(db_session)):
        check_selection(body.competency_ids)
        return [
            {
                "id": s.id,
                "title": s.title,
                "is_fixture": s.is_fixture,
                "review_available": bool(s.config.get("review")),
                "duration_seconds": s.config["duration_seconds"],
            }
            for s in db.scalars(select(Scenario)).all()
            if selectable(s, body.competency_ids)
        ]

    def project_view(p):
        return {
            "id": p.id,
            "title": p.title,
            "competencies": p.competencies,
            "scenario_title": p.config_snapshot["title"],
            "duration_seconds": p.config_snapshot["duration_seconds"],
            "is_fixture": p.config_snapshot.get("is_fixture", False),
            "created_at": p.created_at,
        }

    @app.get("/api/projects")
    def projects(user=Depends(company), db=Depends(db_session)):
        owned = db.scalars(
            select(Project)
            .where(Project.tenant_id == user.tenant_id)
            .order_by(Project.created_at.desc())
        ).all()
        counts = {
            row.project_id: {
                "invited": row.invited,
                "running": row.running,
                "submitted": row.submitted,
                "reports_ready": row.reports_ready,
            }
            for row in db.execute(
                select(
                    Invitation.project_id,
                    func.count(Invitation.id).label("invited"),
                    func.sum(case((AssessmentSession.state == "running", 1), else_=0)).label("running"),
                    func.sum(case((AssessmentSession.state == "submitted", 1), else_=0)).label("submitted"),
                    func.sum(case((Report.status == "ready", 1), else_=0)).label("reports_ready"),
                )
                .outerjoin(AssessmentSession, AssessmentSession.invitation_id == Invitation.id)
                .outerjoin(Report, Report.session_id == AssessmentSession.id)
                .where(Invitation.project_id.in_([p.id for p in owned]))
                .group_by(Invitation.project_id)
            )
        }
        return [
            {**project_view(p), "counts": counts.get(p.id, {
                "invited": 0, "running": 0, "submitted": 0, "reports_ready": 0,
            })}
            for p in owned
        ]

    @app.post("/api/projects")
    def create_project(body: ProjectCreate, user=Depends(company), db=Depends(db_session)):
        check_selection(body.competency_ids)
        old = db.scalar(
            select(Project).where(
                Project.tenant_id == user.tenant_id,
                Project.request_id == body.request_id,
            )
        )
        if old:
            if (
                old.title != body.title
                or old.scenario_id != body.scenario_version_id
                or {c["id"] for c in old.competencies} != set(body.competency_ids)
            ):
                raise HTTPException(409, "같은 요청 ID에 다른 내용을 사용할 수 없습니다.")
            return project_view(old)
        scenario = db.get(Scenario, body.scenario_version_id)
        if not scenario or not selectable(scenario, body.competency_ids):
            raise HTTPException(422, "선택 역량을 지원하는 발행 시나리오가 아닙니다.")
        config = deepcopy(scenario.config)
        # Pin approved company documents now; do not expose later uploads to a running project.
        imports = db.scalars(
            select(DocumentImport).where(
                DocumentImport.tenant_id == user.tenant_id,
                DocumentImport.status == "indexed",
            )
        ).all()
        config["documents"] += [
            {
                "id": i.id,
                "title": i.title,
                "content": i.content,
                "release_after_seconds": 0,
            }
            for i in imports
        ]
        project = Project(
            tenant_id=user.tenant_id,
            title=body.title,
            request_id=body.request_id,
            scenario_id=scenario.id,
            config_snapshot=config,
            competencies=[c for c in CATALOG if c["id"] in body.competency_ids],
        )
        db.add(project)
        db.flush()
        return project_view(project)

    @app.get("/api/projects/{project_id}")
    def project_detail(project_id: str, user=Depends(company), db=Depends(db_session)):
        return project_view(own_project(db, project_id, user))

    @app.post("/api/projects/{project_id}/invitations")
    def invite(
        project_id: str,
        body: InvitationCreate,
        user=Depends(company),
        db=Depends(db_session),
    ):
        own_project(db, project_id, user)
        token = secrets.token_urlsafe(32)
        invitation = Invitation(
            project_id=project_id,
            email=body.email.lower().strip(),
            alias=body.alias,
            token_hash=digest(token),
            expires_at=time.time() + 72 * 3600,
        )
        db.add(invitation)
        db.flush()
        return {
            "id": invitation.id,
            "token": token,
            "path": f"/invite/{token}",
            "expires_at": invitation.expires_at,
        }

    @app.get("/api/projects/{project_id}/candidates")
    def candidates(project_id: str, user=Depends(company), db=Depends(db_session)):
        own_project(db, project_id, user)
        items = []
        for inv in db.scalars(select(Invitation).where(Invitation.project_id == project_id)).all():
            row = db.scalar(
                select(AssessmentSession).where(AssessmentSession.invitation_id == inv.id)
            )
            report = db.get(Report, row.id) if row else None
            items.append(
                {
                    "id": inv.id,
                    "alias": inv.alias,
                    "email": inv.email,
                    "state": row.state if row else ("expired" if inv.expires_at < time.time() else "invited"),
                    "session_id": row.id if row else None,
                    "report_status": report.status if report else None,
                }
            )
        return items

    @app.get("/api/me/assessments")
    def assessments(user=Depends(individual), db=Depends(db_session)):
        result = []
        for inv in db.scalars(select(Invitation).where(Invitation.email == user.email)).all():
            project = db.get(Project, inv.project_id)
            row = db.scalar(
                select(AssessmentSession).where(AssessmentSession.invitation_id == inv.id)
            )
            result.append(
                {
                    "invitation_id": inv.id,
                    "title": project.title,
                    "scenario_title": project.config_snapshot["title"],
                    "duration_seconds": project.config_snapshot["duration_seconds"],
                    "is_fixture": project.config_snapshot.get("is_fixture", False),
                    "session_id": row.id if row else None,
                    "state": row.state
                    if row
                    else ("expired" if inv.expires_at < time.time() else "invited"),
                }
            )
        return result

    def redeem_invitation(db, inv, user):
        if not inv or inv.email != user.email:
            raise HTTPException(404, "본인에게 발급된 초대를 찾을 수 없습니다.")
        if inv.user_id and inv.user_id != user.id:
            raise HTTPException(404, "초대를 찾을 수 없습니다.")
        if inv.user_id:
            return db.scalar(
                select(AssessmentSession).where(AssessmentSession.invitation_id == inv.id)
            )
        if inv.expires_at < time.time():
            raise HTTPException(410, "초대가 만료되었습니다.")
        inv.user_id = user.id
        project = db.get(Project, inv.project_id)
        row = AssessmentSession(
            invitation_id=inv.id,
            project_id=project.id,
            user_id=user.id,
            workspace=initial_workspace(project.config_snapshot),
        )
        db.add(row)
        db.flush()
        return row

    @app.post("/api/invitations/redeem")
    def redeem(body: Redeem, user=Depends(individual), db=Depends(db_session)):
        inv = db.scalar(
            select(Invitation).where(Invitation.token_hash == digest(body.token)).with_for_update()
        )
        return {"session_id": redeem_invitation(db, inv, user).id}

    @app.post("/api/invitations/{invitation_id}/accept")
    def accept(invitation_id: str, user=Depends(individual), db=Depends(db_session)):
        inv = db.scalar(select(Invitation).where(Invitation.id == invitation_id).with_for_update())
        return {"session_id": redeem_invitation(db, inv, user).id}

    @app.get("/api/sessions/{session_id}")
    def session_detail(session_id: str, user=Depends(individual), db=Depends(db_session)):
        return workspace_view(db, own_session(db, session_id, user))

    @app.post("/api/sessions/{session_id}/consent")
    def consent(session_id: str, body: Consent, user=Depends(individual), db=Depends(db_session)):
        row = own_session(db, session_id, user)
        if row.state == "ready" and not row.consent_version:
            row.consent_version, row.consent_at = body.version, time.time()
            record(db, row, "consent.accepted", {"version": body.version})
        return workspace_view(db, row)

    @app.post("/api/sessions/{session_id}/start")
    def start(session_id: str, user=Depends(individual), db=Depends(db_session)):
        row = own_session(db, session_id, user)
        if not row.consent_version:
            raise HTTPException(409, "안내·동의가 필요합니다.")
        if row.state in ("ready", "paused"):
            row.state, row.resumed_at, row.heartbeat_at = (
                "running",
                time.time(),
                time.time(),
            )
            record(db, row, "session.started", {})
            tick(db, row, settings.heartbeat_grace)
        return workspace_view(db, row)

    @app.get("/api/sessions/{session_id}/events")
    def events(
        session_id: str,
        after: int = 0,
        user=Depends(individual),
        db=Depends(db_session),
    ):
        own_session(db, session_id, user)
        return [
            event_view(e)
            for e in db.scalars(
                select(Event)
                .where(Event.session_id == session_id, Event.seq > after)
                .order_by(Event.seq)
                .limit(500)
            ).all()
        ]

    @app.post("/api/sessions/{session_id}/messages")
    def message(
        session_id: str,
        body: MessageCreate,
        tasks: BackgroundTasks,
        user=Depends(individual),
        db=Depends(db_session),
    ):
        row = own_session(db, session_id, user)
        if row.state != "running":
            raise HTTPException(409, "진행 중인 평가에서만 메시지를 보낼 수 있습니다.")
        event = record(
            db,
            row,
            "message.created",
            {
                "actor": "candidate",
                "channel": body.channel,
                "content": body.content,
                "recipient": body.recipient,
            },
            body.command_id,
        )
        project = db.get(Project, row.project_id)
        db.commit()  # Persist before any model request/background task.
        tasks.add_task(
            invoke_agent,
            row.id,
            event.id,
            "assistant" if body.channel == "assistant" else body.recipient,
            project.tenant_id,
        )
        return event_view(event)

    def update_part(db, row, group, item_id, body):
        previous = db.scalar(
            select(Event).where(Event.session_id == row.id, Event.command_id == body.command_id)
        )
        if previous:
            old_item = previous.payload.get("item", {})
            key = "content" if group == "sections" else "status"
            value = body.content if group == "sections" else body.status
            if old_item.get("id") != item_id or old_item.get(key) != value:
                raise HTTPException(409, "같은 명령 ID를 다른 변경에 사용할 수 없습니다.")
            return previous.payload["item"]
        if row.state != "running":
            raise HTTPException(409, "진행 중인 평가에서만 수정할 수 있습니다.")
        workspace = deepcopy(row.workspace)
        item = next((i for i in workspace[group] if i["id"] == item_id), None)
        if not item or item["assignee"] != "candidate":
            raise HTTPException(403, "담당 항목만 수정할 수 있습니다.")
        if item["revision"] != body.expected_revision:
            raise HTTPException(409, "문서가 변경되었습니다. 최신 내용을 다시 불러와 주세요.")
        if group == "sections":
            item["content"] = body.content
        else:
            item["status"] = body.status
        item["revision"] += 1
        row.workspace = workspace
        payload = {"item": item}
        if group == "sections" and body.source_response_id:
            source = db.get(Event, body.source_response_id)
            if (
                not source
                or source.session_id != row.id
                or source.payload.get("actor") != "assistant"
            ):
                raise HTTPException(422, "AI 응답 출처를 확인할 수 없습니다.")
            payload["source_response_id"] = source.id
            payload["usage"] = (
                "unchanged" if source.payload.get("content") == body.content else "modified"
            )
        record(
            db,
            row,
            "document.updated" if group == "sections" else "task.updated",
            payload,
            body.command_id,
        )
        return item

    @app.put("/api/sessions/{session_id}/documents/{section_id}")
    def document(
        session_id: str,
        section_id: str,
        body: DocumentUpdate,
        user=Depends(individual),
        db=Depends(db_session),
    ):
        return update_part(db, own_session(db, session_id, user), "sections", section_id, body)

    @app.put("/api/sessions/{session_id}/tasks/{task_id}")
    def task_update(
        session_id: str,
        task_id: str,
        body: TaskUpdate,
        user=Depends(individual),
        db=Depends(db_session),
    ):
        return update_part(db, own_session(db, session_id, user), "tasks", task_id, body)

    @app.post("/api/sessions/{session_id}/submit")
    def submit(session_id: str, user=Depends(individual), db=Depends(db_session)):
        row = own_session(db, session_id, user)
        finish(db, row)
        return workspace_view(db, row)

    @app.get("/api/reports/{session_id}")
    def report_detail(session_id: str, user=Depends(company), db=Depends(db_session)):
        row = db.get(AssessmentSession, session_id)
        if not row:
            raise HTTPException(404, "리포트를 찾을 수 없습니다.")
        own_project(db, row.project_id, user)
        report = db.get(Report, row.id)
        return {
            "status": report.status if report else "not_started",
            "content": report.content if report else {},
        }

    @app.post("/api/sessions/{session_id}/assignments/{proposal_id}/decision")
    def assignment_decision(
        session_id: str,
        proposal_id: str,
        body: AssignmentDecision,
        user=Depends(individual),
        db=Depends(db_session),
    ):
        row = own_session(db, session_id, user)
        proposal = db.get(Event, proposal_id)
        if (
            not proposal
            or proposal.session_id != row.id
            or proposal.event_type != "assignment.proposed"
        ):
            raise HTTPException(404, "분담안을 찾을 수 없습니다.")
        if row.state != "running":
            raise HTTPException(409, "진행 중인 평가가 아닙니다.")
        record(
            db,
            row,
            "assignment.decision",
            {"proposal_id": proposal.id, "approved": body.approved},
            "decision:" + proposal.id,
        )
        if body.approved:
            apply_assignment(db, row, proposal)
        return workspace_view(db, row)

    @app.websocket("/api/sessions/{session_id}/ws")
    async def socket(websocket: WebSocket, session_id: str):
        origin = websocket.headers.get("origin")
        if origin and origin not in settings.origins:
            await websocket.close(code=4403)
            return
        connection = uid()
        close_code = 1000
        try:
            with sessions.begin() as db:
                user = user_from_cookie(db, websocket.cookies.get("dayzero_session"))
                row = own_session(db, session_id, user)
                if row.state not in ("running", "paused"):
                    raise HTTPException(409, "시작된 평가에서 연결할 수 있습니다.")
                row.state, row.resumed_at = "running", row.resumed_at or time.time()
                row.connection_id, row.heartbeat_at = connection, time.time()
                record(db, row, "session.connected", {})
            await websocket.accept()
            cursor = max(0, int(websocket.query_params.get("after", "0")))
            while True:
                with sessions.begin() as db:
                    # Recheck auth, ownership, and active connection for every batch.
                    user = user_from_cookie(db, websocket.cookies.get("dayzero_session"))
                    row = own_session(db, session_id, user)
                    if row.state == "submitted":
                        await websocket.send_json({"type": "session.closed"})
                        break
                    if row.connection_id != connection:
                        close_code = 4409
                        break
                    pending = db.scalars(
                        select(Event)
                        .where(Event.session_id == session_id, Event.seq > cursor)
                        .order_by(Event.seq)
                        .limit(100)
                    ).all()
                    batch = [event_view(e) for e in pending]
                    status = {
                        "type": "clock",
                        "remaining_seconds": workspace_view(db, row)["remaining_seconds"],
                    }
                for event in batch:
                    await websocket.send_json(event)
                    cursor = event["seq"]
                await websocket.send_json(status)
                try:
                    value = await asyncio.wait_for(websocket.receive_text(), timeout=1)
                    if value == "ping":
                        with sessions.begin() as db:
                            row = locked_session(db, session_id)
                            if row.connection_id == connection:
                                row.heartbeat_at = time.time()
                except asyncio.TimeoutError:
                    pass
        except (WebSocketDisconnect, HTTPException, ValueError):
            pass
        finally:
            with sessions.begin() as db:
                row = db.get(AssessmentSession, session_id)
                if row:
                    row = locked_session(db, session_id)
                    if row.connection_id == connection:
                        pause(db, row)
            try:
                await websocket.close(code=close_code)
            except (RuntimeError, WebSocketDisconnect):
                pass

    @app.get("/api/document-imports")
    def imports_list(user=Depends(company), db=Depends(db_session)):
        return [
            {
                "id": i.id,
                "title": i.title,
                "content_hash": i.content_hash,
                "status": i.status,
                "content": i.content,
            }
            for i in db.scalars(
                select(DocumentImport).where(DocumentImport.tenant_id == user.tenant_id)
            ).all()
        ]

    @app.post("/api/document-imports")
    def import_document(
        body: ImportRequest,
        tasks: BackgroundTasks,
        user=Depends(company),
        db=Depends(db_session),
    ):
        item = DocumentImport(
            tenant_id=user.tenant_id,
            title=body.title,
            content=body.content,
            content_hash=digest(body.content),
        )
        db.add(item)
        db.flush()
        db.commit()
        tasks.add_task(invoke_agent, "", "", "manager", user.tenant_id, import_id=item.id)
        return {"id": item.id, "status": item.status}

    @app.post("/api/document-imports/{import_id}/decision")
    def decide(
        import_id: str,
        body: ApprovalDecision,
        tasks: BackgroundTasks,
        user=Depends(company),
        db=Depends(db_session),
    ):
        item = db.scalar(
            select(DocumentImport).where(DocumentImport.id == import_id).with_for_update()
        )
        if not item or item.tenant_id != user.tenant_id:
            raise HTTPException(404, "문서를 찾을 수 없습니다.")
        if item.status != "prepared" or item.content_hash != body.content_hash:
            raise HTTPException(409, "준비된 문서 버전과 일치하지 않습니다.")
        item.status = "approved" if body.approved else "rejected"
        item.approval_id, item.decided_by = uid(), user.id
        db.commit()
        tasks.add_task(
            invoke_agent,
            "",
            "",
            "manager",
            user.tenant_id,
            import_id=item.id,
            decision=body.approved,
        )
        return {"id": item.id, "status": item.status}

    # Internal service APIs are registered separately to keep authorization boundaries visible.
    from services.gateway.api.internal import register_internal

    register_internal(app, sessions, settings, invoke_agent)
    return app


def app_factory():
    return create_app()
