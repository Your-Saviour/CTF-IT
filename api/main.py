import json
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from api.database import get_db, init_db
from api.models import Event, UserImage, UserModule
from api.routes import admin, ansible_export, auth, caldera_export, caldera_setup, images, scoreboard, verify, vm
from api.routes.auth import get_current_user

REGISTRY_HOST = os.environ.get("REGISTRY_HOST", "localhost:5050")
ROOT_PASSWORD = os.environ.get("ROOT_PASSWORD", "changeme123")
API_HOST = os.environ.get("API_HOST", "host.docker.internal:8080")


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
            }.items():
                if col not in existing:
                    db.execute(text(f"ALTER TABLE vms ADD COLUMN {col} {typ}"))

        if inspector.has_table("events"):
            existing = {col["name"] for col in inspector.get_columns("events")}
            for col, typ in {
                "semaphore_project_id": "INTEGER",
                "semaphore_key_id": "INTEGER",
            }.items():
                if col not in existing:
                    db.execute(text(f"ALTER TABLE events ADD COLUMN {col} {typ}"))

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
    yield


app = FastAPI(title="CTF Training Platform", lifespan=lifespan)

templates = Jinja2Templates(
    directory=os.path.join(os.path.dirname(__file__), "..", "frontend", "templates")
)

app.include_router(auth.router)
app.include_router(images.router)
app.include_router(verify.router)
app.include_router(scoreboard.router)
app.include_router(admin.router)
app.include_router(ansible_export.router)
app.include_router(caldera_export.router)
app.include_router(caldera_setup.router)
app.include_router(vm.router)


@app.get("/api/events")
async def list_open_events(db: Session = Depends(get_db)):
    """Public endpoint: returns events open for registration."""
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
    if user:
        return RedirectResponse("/dashboard", status_code=303)
    error = request.query_params.get("error")
    return templates.TemplateResponse(request, "landing.html", {
        "error": error,
    })


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/", status_code=303)

    image = (
        db.query(UserImage)
        .filter(UserImage.user_id == user.id)
        .order_by(UserImage.created_at.desc())
        .first()
    )

    user_modules = (
        db.query(UserModule)
        .filter(UserModule.user_id == user.id)
        .all()
    )

    # Load module details for hints/descriptions
    from builder.module_loader import load_all_modules
    library = {m.id: m for m in load_all_modules()}

    modules_with_details = []
    for um in user_modules:
        if um.completed:
            mod = library.get(um.module_id)
            modules_with_details.append({
                "id": um.module_id,
                "name": mod.name if mod else um.module_id,
                "difficulty": um.difficulty,
                "points": um.points,
                "completed": True,
            })

    total_points = sum(um.points for um in user_modules if um.completed)

    return templates.TemplateResponse(request, "dashboard.html", {
        "user": user,
        "image": image,
        "modules": modules_with_details,
        "module_count": len(user_modules),
        "completed_count": len(modules_with_details),
        "total_points": total_points,
        "registry_host": REGISTRY_HOST,
        "root_password": ROOT_PASSWORD,
        "api_host": API_HOST,
        "event": user.event,
    })


@app.get("/scoreboard", response_class=HTMLResponse)
async def scoreboard_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    return templates.TemplateResponse(request, "scoreboard.html", {
        "user": user,
    })


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse("/", status_code=303)

    return templates.TemplateResponse(request, "admin.html", {
        "user": user,
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
