import json
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from api.database import get_db, init_db
from api.models import Event, UserImage, UserModule
from api.routes import admin, auth, images, scoreboard, verify
from api.routes.auth import get_current_user

REGISTRY_HOST = os.environ.get("REGISTRY_HOST", "localhost:5000")
ROOT_PASSWORD = os.environ.get("ROOT_PASSWORD", "changeme123")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Create default event if none exists
    from api.database import SessionLocal
    db = SessionLocal()
    try:
        if not db.query(Event).first():
            quota = os.environ.get(
                "EVENT_QUOTA",
                '{"vulnerability":{"easy":1,"medium":0,"hard":0},"hardening":{"easy":0,"medium":1,"hard":0}}',
            )
            db.add(Event(name="Default CTF Event", quota=quota, open=True))
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

    event = db.query(Event).first()

    return templates.TemplateResponse(request, "admin.html", {
        "user": user,
        "event": event,
        "event_quota": json.loads(event.quota) if event else {},
    })
