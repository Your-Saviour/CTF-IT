import asyncio
import json
import os
from contextlib import asynccontextmanager
from contextlib import suppress
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from api.database import get_db, init_db
from api.models import Event, User, VM
from api.routes import admin, ansible_export, auth, caldera_export, caldera_ops, caldera_setup, caldera_tree, vm, vm_goals
from api.routes.auth import get_current_user


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    from api.database import SessionLocal
    from sqlalchemy import inspect, text
    db = SessionLocal()
    try:
        # Schema migrations — run before any ORM queries so new columns exist first
        inspector = inspect(db.bind)

        if inspector.has_table("vms"):
            existing = {col["name"] for col in inspector.get_columns("vms")}
            for col, typ in {
                "provision_step": "VARCHAR(64)",
                "provision_error": "TEXT",
                "semaphore_project_id": "INTEGER",
                "semaphore_task_id": "INTEGER",
                "agent_status": "VARCHAR(16)",
                "vultr_id": "VARCHAR(64)",
                "vultr_plan": "VARCHAR(64)",
                "vultr_region": "VARCHAR(16)",
                "cloudflare_record_id": "VARCHAR(64)",
                "attack_tree_json": "TEXT",
                "vm_type": "VARCHAR(64)",
                "base_type": "VARCHAR(64)",
                "vpc_ip": "VARCHAR(45)",
                "admin_password": "VARCHAR(128)",
                "ssh_host_key": "VARCHAR(512)",
            }.items():
                if col not in existing:
                    db.execute(text(f"ALTER TABLE vms ADD COLUMN {col} {typ}"))

        if inspector.has_table("events"):
            existing = {col["name"] for col in inspector.get_columns("events")}
            for col, typ in {
                "semaphore_project_id": "INTEGER",
                "semaphore_key_id": "INTEGER",
                "vm_quota": "TEXT",
                "started_at": "DATETIME",
                "ends_at": "DATETIME",
            }.items():
                if col not in existing:
                    db.execute(text(f"ALTER TABLE events ADD COLUMN {col} {typ}"))

        if inspector.has_table("teams"):
            existing = {col["name"] for col in inspector.get_columns("teams")}
            for col, typ in {
                "vpc_id": "VARCHAR(64)",
                "team_index": "INTEGER",
            }.items():
                if col not in existing:
                    db.execute(text(f"ALTER TABLE teams ADD COLUMN {col} {typ}"))

        if inspector.has_table("users"):
            existing = {col["name"] for col in inspector.get_columns("users")}
            for col, typ in {
                "active": "BOOLEAN NOT NULL DEFAULT 1",
                "session_version": "INTEGER NOT NULL DEFAULT 1",
                "updated_at": "DATETIME",
                "deactivated_at": "DATETIME",
                "password_changed_at": "DATETIME",
            }.items():
                if col not in existing:
                    db.execute(text(f"ALTER TABLE users ADD COLUMN {col} {typ}"))

        if inspector.has_table("vm_modules"):
            existing = {col["name"] for col in inspector.get_columns("vm_modules")}
            if "stage" not in existing:
                db.execute(text("ALTER TABLE vm_modules ADD COLUMN stage VARCHAR(16)"))

        if not inspector.has_table("vm_goals"):
            db.execute(text("""
                CREATE TABLE vm_goals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    vm_id INTEGER NOT NULL REFERENCES vms(id),
                    module_id VARCHAR(64) NOT NULL,
                    status VARCHAR(16) NOT NULL DEFAULT 'pending',
                    red_points INTEGER NOT NULL DEFAULT 0,
                    defend_points INTEGER NOT NULL DEFAULT 0,
                    achievement_count INTEGER NOT NULL DEFAULT 0,
                    defend_count INTEGER NOT NULL DEFAULT 0,
                    achieved_at DATETIME,
                    defended_at DATETIME,
                    created_at DATETIME
                )
            """))

        db.commit()

        # Encrypt legacy OPNsense credentials in place. New credentials are
        # encrypted before they are committed.
        from api.services.secrets import encrypt_secret
        for stored_vm in db.query(VM).filter(VM.admin_password.is_not(None)).all():
            encrypted = encrypt_secret(stored_vm.admin_password)
            if encrypted != stored_vm.admin_password:
                stored_vm.admin_password = encrypted
        db.commit()

        # Migrate existing events: open bool → status field
        for event in db.query(Event).filter(Event.status == "draft").all():
            if event.open:
                event.status = "open"
        db.commit()

        # Create default event if none exists
        if not db.query(Event).first():
            quota = os.environ.get(
                "EVENT_QUOTA",
                '{"vulnerability":{"easy":1,"medium":0,"hard":0},"hardening":{"easy":0,"medium":1,"hard":0}}',
            )
            db.add(Event(name="Default CTF Event", quota=quota, status="open"))
            db.commit()
    finally:
        db.close()
    async def enforce_event_deadlines():
        from api.database import SessionLocal
        from api.services.event_lifecycle import expire_due_events
        while True:
            await asyncio.sleep(30)
            deadline_db = SessionLocal()
            try:
                expire_due_events(deadline_db)
            finally:
                deadline_db.close()

    deadline_task = asyncio.create_task(enforce_event_deadlines())
    try:
        yield
    finally:
        deadline_task.cancel()
        with suppress(asyncio.CancelledError):
            await deadline_task


app = FastAPI(title="CTF Training Platform", lifespan=lifespan)


@app.middleware("http")
async def reject_cross_site_mutations(request: Request, call_next):
    """Reject browser cross-site state changes while preserving API clients."""
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
            return JSONResponse({"error": "cross-site request rejected"}, status_code=403)
        origin = request.headers.get("origin")
        if origin:
            expected_host = request.headers.get("x-forwarded-host") or request.headers.get("host")
            if not expected_host or urlsplit(origin).netloc.lower() != expected_host.lower():
                return JSONResponse({"error": "origin mismatch"}, status_code=403)
    return await call_next(request)

templates = Jinja2Templates(
    directory=os.path.join(os.path.dirname(__file__), "..", "frontend", "templates")
)

app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(ansible_export.router)
app.include_router(caldera_export.router)
app.include_router(caldera_setup.router)
app.include_router(caldera_ops.router)
app.include_router(caldera_tree.router)
app.include_router(vm.router)
app.include_router(vm_goals.router)


@app.get("/api/events")
async def list_open_events(db: Session = Depends(get_db)):
    """Only expose an event for the one-time administrator bootstrap flow."""
    if db.query(User).count() > 0:
        return []
    from api.services.event_lifecycle import expire_due_events
    expire_due_events(db)
    events = (
        db.query(Event)
        .filter(Event.status == "open")
        .order_by(Event.created_at.desc())
        .all()
    )
    return [
        {
            "id": e.id,
            "name": e.name,
            "description": e.description,
            "welcome_message": e.welcome_message,
        }
        for e in events
    ]


@app.get("/", response_class=HTMLResponse)
async def landing(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if user and user.is_admin:
        return RedirectResponse("/admin", status_code=303)
    error = request.query_params.get("error")
    return templates.TemplateResponse(request, "landing.html", {
        "user": user,
        "event": user.event if user else None,
        "error": error,
        "bootstrap_required": db.query(User).count() == 0,
    })


@app.get("/invite/{token}", response_class=HTMLResponse)
async def invitation_page(token: str, request: Request, db: Session = Depends(get_db)):
    from api.routes.auth import _valid_token
    record = _valid_token(db, token, "invitation")
    event = db.query(Event).filter(Event.id == record.event_id).first() if record else None
    return templates.TemplateResponse(request, "account_token.html", {
        "user": None, "mode": "invitation", "token": token,
        "valid": record is not None, "event": event,
        "intended_username": record.intended_username if record else None,
        "error": request.query_params.get("error"),
    })


@app.get("/reset/{token}", response_class=HTMLResponse)
async def password_reset_page(token: str, request: Request, db: Session = Depends(get_db)):
    from api.routes.auth import _valid_token
    return templates.TemplateResponse(request, "account_token.html", {
        "user": None, "mode": "reset", "token": token,
        "valid": _valid_token(db, token, "password_reset") is not None,
        "event": None, "intended_username": None,
        "error": request.query_params.get("error"),
    })


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse("/", status_code=303)

    return templates.TemplateResponse(request, "admin.html", {
        "user": user,
        "domain": os.environ.get("DOMAIN"),
    })


@app.get("/admin/module/{module_id}", response_class=HTMLResponse)
async def module_detail_page(module_id: str, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse("/", status_code=303)

    return templates.TemplateResponse(request, "module_detail.html", {
        "user": user,
        "module_id": module_id,
    })


@app.get("/admin/vm/{vm_id}", response_class=HTMLResponse)
async def vm_detail_page(vm_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse("/", status_code=303)

    return templates.TemplateResponse(request, "vm_detail.html", {
        "user": user,
        "vm_id": vm_id,
    })


@app.get("/admin/topology", response_class=HTMLResponse)
async def topology_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse("/", status_code=303)

    return templates.TemplateResponse(request, "topology.html", {
        "user": user,
    })


@app.get("/admin/caldera", response_class=HTMLResponse)
async def caldera_dashboard_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "caldera_dashboard.html", {"user": user})


@app.get("/admin/caldera/operation/{op_id}", response_class=HTMLResponse)
async def caldera_operation_page(op_id: str, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "caldera_dashboard.html", {
        "user": user,
        "op_id": op_id,
    })


@app.get("/admin/events/{event_id}/plan", response_class=HTMLResponse)
async def event_plan_page(event_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse("/", status_code=303)
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        return RedirectResponse("/admin", status_code=303)
    return templates.TemplateResponse(request, "event_plan.html", {
        "user": user, "event_id": event_id, "event_name": event.name
    })
