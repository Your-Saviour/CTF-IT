import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from contextlib import suppress
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from api.database import engine, get_db, init_db
from api.models import Event, User, VM, utcnow
from api.routes import admin, ai_agent, ansible_export, auth, caldera_export, caldera_ops, caldera_setup, caldera_tree, event_dashboard, learner, service_credentials, vm, vm_goals
from api.routes.auth import get_current_user

_log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from api import database as database_module
    from api.migrations import upgrade_database
    with database_module.engine.connect() as migration_connection:
        upgrade_database(migration_connection)
    # create_all is retained as an idempotent safety net for plugin-owned
    # tables; the core schema above is versioned by Alembic.
    init_db()
    from api.database import SessionLocal
    from sqlalchemy import inspect, text
    db = SessionLocal()
    try:
        # Versioned migrations own all training-release schema changes. The
        # compatibility block below remains only for installations predating
        # the established Alembic baseline.
        # Schema migrations — run before any ORM queries so new columns exist first
        inspector = inspect(db.bind)

        if not inspector.has_table("service_credentials"):
            db.execute(text("""
                CREATE TABLE service_credentials (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    service_name VARCHAR(64) NOT NULL,
                    credential_type VARCHAR(32) NOT NULL,
                    username VARCHAR(256),
                    password VARCHAR(256) NOT NULL,
                    url VARCHAR(512),
                    description TEXT,
                    created_at DATETIME,
                    updated_at DATETIME,
                    created_by INTEGER
                )
            """))

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
                "cloud_instance_id": "VARCHAR(64)",
                "instance_type": "VARCHAR(64)",
                "cloud_region": "VARCHAR(32)",
                "availability_zone": "VARCHAR(32)",
                "primary_eni_id": "VARCHAR(64)",
                "wan_eni_id": "VARCHAR(64)",
                "lan_eni_id": "VARCHAR(64)",
                "subnet_id": "VARCHAR(64)",
                "security_group_ids_json": "TEXT",
                "eip_allocation_id": "VARCHAR(64)",
                "cloudflare_record_id": "VARCHAR(64)",
                "attack_tree_json": "TEXT",
                "vm_type": "VARCHAR(64)",
                "base_type": "VARCHAR(64)",
                "role": "VARCHAR(24)",
                "site_id": "INTEGER REFERENCES sites(id)",
                "zone_id": "INTEGER REFERENCES zones(id)",
                "public_ip": "VARCHAR(45)",
                "private_ip": "VARCHAR(45)",
                "vpc_ip": "VARCHAR(45)",
                "vpc_mac": "VARCHAR(32)",
                "network_boot_id": "VARCHAR(128)",
                "network_phase": "VARCHAR(32)",
                "admin_password": "VARCHAR(128)",
                "ssh_host_key": "VARCHAR(512)",
                "ust_prompt": "TEXT",
            }.items():
                if col not in existing:
                    db.execute(text(f"ALTER TABLE vms ADD COLUMN {col} {typ}"))

        if inspector.has_table("events"):
            existing = {col["name"] for col in inspector.get_columns("events")}
            for col, typ in {
                "semaphore_project_id": "INTEGER",
                "semaphore_key_id": "INTEGER",
                "infrastructure": "TEXT",
                "started_at": "DATETIME",
                "ends_at": "DATETIME",
                "expo_sync_status": "VARCHAR(24)",
                "expo_sync_last_error": "TEXT",
                "expo_sync_attempts": "INTEGER NOT NULL DEFAULT 0",
                "expo_sync_completed_at": "DATETIME",
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

        if inspector.has_table("sites"):
            existing = {col["name"] for col in inspector.get_columns("sites")}
            for col, typ in {
                "availability_zone": "VARCHAR(32)",
                "public_subnet_id": "VARCHAR(64)",
                "infrastructure_subnet_id": "VARCHAR(64)",
                "internet_gateway_id": "VARCHAR(64)",
                "route_table_ids_json": "TEXT",
                "wan_security_group_id": "VARCHAR(64)",
                "lan_security_group_id": "VARCHAR(64)",
            }.items():
                if col not in existing:
                    db.execute(text(f"ALTER TABLE sites ADD COLUMN {col} {typ}"))

        if inspector.has_table("zones"):
            existing = {col["name"] for col in inspector.get_columns("zones")}
            for col, typ in {
                "subnet_id": "VARCHAR(64)",
                "security_group_id": "VARCHAR(64)",
            }.items():
                if col not in existing:
                    db.execute(text(f"ALTER TABLE zones ADD COLUMN {col} {typ}"))

        if inspector.has_table("opnsense_images"):
            existing = {col["name"] for col in inspector.get_columns("opnsense_images")}
            for col, typ in {
                "ami_id": "VARCHAR(64)",
                "backing_snapshot_ids_json": "TEXT",
                "region": "VARCHAR(32)",
                "availability_zone": "VARCHAR(32)",
                "builder_subnet_id": "VARCHAR(64)",
                "validation_subnet_id": "VARCHAR(64)",
            }.items():
                if col not in existing:
                    db.execute(text(f"ALTER TABLE opnsense_images ADD COLUMN {col} {typ}"))

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

        # In-process provisioning workers cannot survive an API restart. Keep
        # interrupted GameNets closed until an administrator explicitly retries.
        interrupted_events = db.query(Event).filter(Event.status == "provisioning").all()
        for event in interrupted_events:
            event.status = "provision_failed"
            event.open = False
        if interrupted_events:
            db.commit()

        from api.services.opnsense_images import interrupt_running_jobs
        interrupt_running_jobs(db)

        # Create default event if none exists
        if not db.query(Event).first():
            quota = os.environ.get(
                "EVENT_QUOTA",
                '{"vulnerability":{"easy":1,"medium":0,"hard":0},"hardening":{"easy":0,"medium":1,"hard":0}}',
            )
            # A new event must pass through the explicit start transition so
            # timestamps, the legacy open flag, and VM provisioning are set
            # consistently by POST /admin/events/{id}/start.
            db.add(Event(name="Default CTF Event", quota=quota, status="draft"))
            db.commit()

        # Seed default service credentials if none exist
        from api.services.secrets import encrypt_secret
        from api.models import ServiceCredential
        if not db.query(ServiceCredential).count():
            domain = os.environ.get("DOMAIN", "example.com")
            caldera_admin_password = os.environ.get("CALDERA_ADMIN_PASSWORD", "")
            if not caldera_admin_password:
                try:
                    import yaml
                    with open(os.environ.get("CALDERA_CONFIG_PATH", "/caldera-config/local.yml")) as handle:
                        caldera_config = yaml.safe_load(handle) or {}
                    caldera_admin_password = (
                        caldera_config.get("users", {}).get("red", {}).get("admin", "")
                    )
                except (OSError, TypeError, AttributeError):
                    _log.warning("Could not read Caldera admin credentials for initial service catalog")
            credentials = [
                {
                    "service_name": "Caldera Admin",
                    "credential_type": "admin",
                    "username": "admin",
                    "password": caldera_admin_password,
                    "url": f"https://caldera.{domain}",
                    "description": "MITRE Caldera C2 admin credentials"
                },
                {
                    "service_name": "Semaphore Admin",
                    "credential_type": "admin",
                    "username": os.environ.get("SEMAPHORE_ADMIN", "admin"),
                    "password": os.environ.get("SEMAPHORE_ADMIN_PASSWORD", ""),
                    "url": f"https://semaphore.{domain}",
                    "description": "Ansible Semaphore admin credentials"
                },
                {
                    "service_name": "Dockhand",
                    "credential_type": "admin",
                    "username": "admin",
                    "password": os.environ.get("DOCKHAND_ADMIN_PASSWORD", ""),
                    "url": f"https://dockhand.{domain}",
                    "description": "Dockhand container management UI credentials"
                },
                {
                    "service_name": "Traefik Dashboard",
                    "credential_type": "admin",
                    "username": "admin",
                    "password": "",
                    "url": f"https://traefik.{domain}",
                    "description": "Traefik reverse proxy dashboard"
                },
                {
                    "service_name": "AWS Provider",
                    "credential_type": "identity",
                    "username": os.environ.get("AWS_DEFAULT_REGION", "not configured"),
                    "password": None,
                    "url": "https://console.aws.amazon.com/",
                    "description": "Uses the runtime IAM role or standard AWS credential chain"
                },
                {
                    "service_name": "Cloudflare",
                    "credential_type": "token",
                    "username": os.environ.get("CLOUDFLARE_API_TOKEN", ""),
                    "password": None,
                    "url": "https://dash.cloudflare.com/profile/api-tokens",
                    "description": "Cloudflare API token"
                },
                {
                    "service_name": "AI Agent",
                    "credential_type": "token",
                    "username": os.environ.get("AGENT_API_KEY", ""),
                    "password": None,
                    "url": "https://platform.openai.com/api-keys",
                    "description": "AI agent API key"
                }
            ]

            admin_user = db.query(User).filter(User.is_admin == True).first()
            for cred in credentials:
                if cred["password"]:  # Only create credentials with actual values
                    db.add(ServiceCredential(
                        service_name=cred["service_name"],
                        credential_type=cred["credential_type"],
                        username=cred["username"],
                        password=encrypt_secret(cred["password"]),
                        url=cred["url"],
                        description=cred["description"],
                        created_by=admin_user.id if admin_user else None,
                        created_at=utcnow()
                    ))
            db.commit()

        # In-process provisioning workers cannot survive a container restart.
        # Reconcile their durable state so operators can safely retry instead
        # of seeing VMs stuck in an in-progress state forever.
        interrupted = db.query(VM).filter(
            VM.status.in_(("creating", "provisioning", "destroying"))
        ).all()
        for stored_vm in interrupted:
            stored_vm.status = "failed"
            stored_vm.provision_step = "failed"
            stored_vm.provision_error = (
                "The API restarted while this operation was running. "
                "Inspect the cloud provider and Semaphore state, then retry."
            )
            stored_vm.updated_at = utcnow()
        deploying_agents = db.query(VM).filter(VM.agent_status == "deploying").all()
        for stored_vm in deploying_agents:
            stored_vm.agent_status = "failed"
            stored_vm.updated_at = utcnow()
        if interrupted or deploying_agents:
            db.commit()
            _log.warning(
                "Recovered %d interrupted VM operations and %d agent deployments",
                len(interrupted), len(deploying_agents),
            )
    except Exception:
        db.rollback()
        raise
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
            except Exception:
                deadline_db.rollback()
                _log.exception("Event deadline monitor failed; retrying next cycle")
            finally:
                deadline_db.close()

    deadline_task = asyncio.create_task(enforce_event_deadlines())
    verification_task = None
    if os.environ.get("LEARNER_TRAINING_ENABLED", "false").lower() in {"1", "true", "yes"}:
        from api.services.verification_scheduler import scheduler_loop
        verification_task = asyncio.create_task(scheduler_loop())
    try:
        yield
    finally:
        deadline_task.cancel()
        if verification_task:
            verification_task.cancel()
        with suppress(asyncio.CancelledError):
            await deadline_task
        if verification_task:
            with suppress(asyncio.CancelledError):
                await verification_task
        await ai_agent.close_agent_client()
        engine.dispose()


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
app.mount(
    "/static",
    StaticFiles(directory=os.path.join(os.path.dirname(__file__), "..", "frontend", "static")),
    name="static",
)

app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(ai_agent.router)
app.include_router(ansible_export.router)
app.include_router(caldera_export.router)
app.include_router(caldera_setup.router)
app.include_router(caldera_ops.router)
app.include_router(caldera_tree.router)
app.include_router(event_dashboard.router)
app.include_router(learner.router)
app.include_router(service_credentials.router)
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
        # A fresh installation deliberately seeds its first event as a draft so
        # normal lifecycle timestamps and provisioning are only set by the
        # explicit admin start action. The initial administrator still needs an
        # event to bind their account to before that action is available.
        .filter(Event.status.in_(("draft", "open")))
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
    if user and not user.is_admin and learner.enabled():
        return RedirectResponse("/dashboard", status_code=303)
    error = request.query_params.get("error")
    return templates.TemplateResponse(request, "landing.html", {
        "user": user,
        "event": user.event if user else None,
        "error": error,
        "bootstrap_required": db.query(User).count() == 0,
    })


@app.get("/dashboard", response_class=HTMLResponse)
async def learner_dashboard(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or user.is_admin or not learner.enabled():
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "learner.html", {
        "user": user, "page_title": "Workspace", "view": "dashboard",
    })


@app.get("/scoreboard", response_class=HTMLResponse)
async def learner_scoreboard(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or user.is_admin or not learner.enabled():
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "learner.html", {
        "user": user, "page_title": "Scoreboard", "view": "scoreboard",
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

    return templates.TemplateResponse(request, "admin_overview.html", {
        "user": user, "page_title": "Operations overview", "active_nav": "overview",
        "page_description": "Event, infrastructure and offensive-tool health at a glance.",
        "breadcrumbs": [],
    })


async def _admin_resource_page(resource: str, request: Request, db: Session):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse("/", status_code=303)
    config = {
        "events": ("Events", "Configure event quotas, lifecycle and provisioning.", "Create event"),
        "people": ("People & access", "Manage invitations, roles, event access and audit history.", "Invite person"),
        "infrastructure": ("Infrastructure", "Manage teams, virtual machines and provisioning.", "Create resource"),
        "modules": ("Module library", "Inspect training modules, dependencies and verification metadata.", ""),
    }
    title, description, action = config[resource]
    infrastructure_tab = "teams"
    if resource == "infrastructure":
        infrastructure_tab = "vms" if request.query_params.get("tab") == "vms" else "teams"
        action = "Register VM" if infrastructure_tab == "vms" else "Create team"
    return templates.TemplateResponse(request, "admin_resource.html", {
        "user": user, "resource": resource, "page_title": title,
        "page_description": description, "action_label": action,
        "infrastructure_tab": infrastructure_tab,
        "active_nav": resource, "breadcrumbs": [{"label": title}],
    })


@app.get("/admin/events", response_class=HTMLResponse)
async def events_page(request: Request, db: Session = Depends(get_db)):
    return await _admin_resource_page("events", request, db)


@app.get("/admin/people", response_class=HTMLResponse)
async def people_page(request: Request, db: Session = Depends(get_db)):
    return await _admin_resource_page("people", request, db)


@app.get("/admin/infrastructure", response_class=HTMLResponse)
async def infrastructure_page(request: Request, db: Session = Depends(get_db)):
    return await _admin_resource_page("infrastructure", request, db)


@app.get("/admin/modules", response_class=HTMLResponse)
async def modules_page(request: Request, db: Session = Depends(get_db)):
    return await _admin_resource_page("modules", request, db)


@app.get("/admin/module/{module_id}", response_class=HTMLResponse)
async def module_detail_page(module_id: str, request: Request, db: Session = Depends(get_db)):
    return RedirectResponse(f"/admin/modules/{module_id}", status_code=308)


@app.get("/admin/modules/{module_id}", response_class=HTMLResponse)
async def new_module_detail_page(module_id: str, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse("/", status_code=303)

    return templates.TemplateResponse(request, "module_detail.html", {
        "user": user,
        "module_id": module_id,
        "active_nav": "modules",
        "breadcrumbs": [{"label": "Modules", "href": "/admin/modules"}, {"label": module_id}],
    })


@app.get("/admin/vm/{vm_id}", response_class=HTMLResponse)
async def vm_detail_page(vm_id: int, request: Request, db: Session = Depends(get_db)):
    return RedirectResponse(f"/admin/infrastructure/vms/{vm_id}", status_code=308)


@app.get("/admin/infrastructure/vms/{vm_id}", response_class=HTMLResponse)
async def new_vm_detail_page(vm_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse("/", status_code=303)

    return templates.TemplateResponse(request, "vm_detail.html", {
        "user": user,
        "vm_id": vm_id,
        "active_nav": "infrastructure",
        "breadcrumbs": [{"label": "Infrastructure", "href": "/admin/infrastructure?tab=vms"}, {"label": f"VM {vm_id}"}],
    })


@app.get("/admin/topology", response_class=HTMLResponse)
async def topology_page(request: Request, db: Session = Depends(get_db)):
    return RedirectResponse("/admin/infrastructure/topology", status_code=308)


@app.get("/admin/infrastructure/topology", response_class=HTMLResponse)
async def new_topology_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse("/", status_code=303)

    return templates.TemplateResponse(request, "topology.html", {
        "user": user,
        "active_nav": "topology",
        "breadcrumbs": [{"label": "Infrastructure", "href": "/admin/infrastructure"}, {"label": "Topology"}],
    })


@app.get("/admin/caldera", response_class=HTMLResponse)
async def caldera_dashboard_page(request: Request, db: Session = Depends(get_db)):
    return RedirectResponse("/admin/red-team/operations", status_code=308)


@app.get("/admin/red-team/operations", response_class=HTMLResponse)
async def new_caldera_dashboard_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "caldera_dashboard.html", {
        "user": user, "active_nav": "operations",
        "breadcrumbs": [{"label": "Red team"}, {"label": "Operations"}],
    })


@app.get("/admin/caldera/operation/{op_id}", response_class=HTMLResponse)
async def caldera_operation_page(op_id: str, request: Request, db: Session = Depends(get_db)):
    return RedirectResponse(f"/admin/red-team/operations/{op_id}", status_code=308)


@app.get("/admin/red-team/operations/{op_id}", response_class=HTMLResponse)
async def new_caldera_operation_page(op_id: str, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "caldera_dashboard.html", {
        "user": user,
        "op_id": op_id,
        "active_nav": "operations",
        "breadcrumbs": [{"label": "Red team"}, {"label": "Operations", "href": "/admin/red-team/operations"}, {"label": op_id}],
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
        "user": user, "event_id": event_id, "event_name": event.name,
        "active_nav": "events",
        "breadcrumbs": [{"label": "Events", "href": "/admin/events"}, {"label": event.name}, {"label": "Plan"}],
    })


@app.get("/admin/events/{event_id}/dashboard", response_class=HTMLResponse)
async def event_dashboard_page(event_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse("/", status_code=303)
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        return RedirectResponse("/admin", status_code=303)
    return templates.TemplateResponse(request, "event_dashboard.html", {
        "user": user, "event_id": event_id, "event_name": event.name,
        "active_nav": "events",
        "breadcrumbs": [{"label": "Events", "href": "/admin/events"}, {"label": event.name}, {"label": "Dashboard"}],
    })


@app.get("/admin/ai-agent", response_class=HTMLResponse)
async def ai_agent_page(request: Request, db: Session = Depends(get_db)):
    return RedirectResponse("/admin/red-team/agent", status_code=308)


@app.get("/admin/red-team/agent", response_class=HTMLResponse)
async def new_ai_agent_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "ai_agent.html", {
        "user": user, "active_nav": "agent",
        "breadcrumbs": [{"label": "Red team"}, {"label": "AI Agent"}],
    })


@app.get("/admin/ai-agent/session/{session_id}", response_class=HTMLResponse)
async def ai_agent_session_page(session_id: str, request: Request, db: Session = Depends(get_db)):
    return RedirectResponse(f"/admin/red-team/agent/sessions/{session_id}", status_code=308)


@app.get("/admin/red-team/agent/sessions/{session_id}", response_class=HTMLResponse)
async def new_ai_agent_session_page(session_id: str, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "ai_agent_session.html", {
        "user": user, "session_id": session_id, "active_nav": "agent",
        "breadcrumbs": [{"label": "Red team"}, {"label": "AI Agent", "href": "/admin/red-team/agent"}, {"label": session_id[:8]}],
    })


@app.get("/admin/service-credentials")
async def legacy_credentials_page():
    return RedirectResponse("/admin/settings", status_code=308)


@app.get("/admin/settings", response_class=HTMLResponse)
async def settings_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "admin_settings.html", {
        "user": user, "page_title": "Services & credentials", "active_nav": "settings",
        "page_description": "External service links and encrypted platform credentials.",
        "breadcrumbs": [{"label": "Settings"}],
    })
