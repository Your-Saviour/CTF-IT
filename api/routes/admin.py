import asyncio
import json
import os

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from api.database import get_db
from api.models import Event, User, UserImage, UserModule
from api.routes.auth import get_current_user

REGISTRY_INTERNAL = os.environ.get("REGISTRY_INTERNAL", "http://registry:5000")

router = APIRouter(prefix="/admin", tags=["admin"])


def require_admin(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return None
    return user


@router.get("/users")
async def list_users(request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    users = db.query(User).all()
    result = []
    for u in users:
        image = (
            db.query(UserImage)
            .filter(UserImage.user_id == u.id)
            .order_by(UserImage.created_at.desc())
            .first()
        )
        total_points = sum(
            m.points for m in db.query(UserModule).filter(
                UserModule.user_id == u.id, UserModule.completed == True
            ).all()
        )
        result.append({
            "id": u.id,
            "username": u.username,
            "is_admin": u.is_admin,
            "build_status": image.status if image else "none",
            "total_points": total_points,
            "event_name": u.event.name if u.event else None,
        })

    return result


@router.post("/rebuild/{user_id}")
async def rebuild_user(
    user_id: int, request: Request, db: Session = Depends(get_db)
):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return JSONResponse({"error": "User not found"}, status_code=404)

    # Reset modules
    db.query(UserModule).filter(UserModule.user_id == user_id).delete()

    # Create new image record
    image = UserImage(user_id=user_id, status="queued")
    db.add(image)
    db.commit()

    if user.event:
        quota = json.loads(user.event.quota)
    else:
        quota = json.loads(os.environ.get(
            "EVENT_QUOTA",
            '{"vulnerability":{"easy":1,"medium":0,"hard":0},"hardening":{"easy":0,"medium":1,"hard":0}}',
        ))

    from api.routes.auth import _run_build
    asyncio.create_task(_run_build(user.id, user.username, quota))

    return {"status": "rebuild_queued"}


@router.get("/modules")
async def list_modules(request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    from builder.module_loader import load_all_modules
    modules = load_all_modules()
    return [
        {
            "id": m.id,
            "name": m.name,
            "description": m.description,
            "type": m.type,
            "difficulty": m.difficulty,
            "points": m.points,
            "category": m.category,
            "tags": m.tags,
            "disabled": m.disabled,
        }
        for m in modules
    ]


@router.get("/modules/{module_id}")
async def get_module(module_id: str, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    from builder.module_loader import load_all_modules, CopyStep, RunStep
    modules = load_all_modules()
    module = next((m for m in modules if m.id == module_id), None)
    if not module:
        return JSONResponse({"error": "module not found"}, status_code=404)

    steps = []
    for step in module.steps:
        if isinstance(step, RunStep):
            steps.append({"type": "run", "script": step.script})
        elif isinstance(step, CopyStep):
            steps.append({"type": "copy", "src": step.src, "dest": step.dest, "mode": step.mode})

    return {
        "id": module.id,
        "name": module.name,
        "description": module.description,
        "type": module.type,
        "difficulty": module.difficulty,
        "points": module.points,
        "category": module.category,
        "tags": module.tags,
        "conflicts": module.conflicts,
        "requires": module.requires,
        "verification": module.verification,
        "hints": module.hints,
        "suggested_fix": module.suggested_fix,
        "caldera": module.caldera,
        "steps": steps,
        "disabled": module.disabled,
    }


@router.put("/modules/{module_id}/disable")
async def toggle_module_disabled(module_id: str, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    body = await request.json()
    disabled = bool(body.get("disabled", False))

    from pathlib import Path
    import yaml

    modules_dir = Path(__file__).resolve().parent.parent.parent / "modules"
    yaml_matches = list(modules_dir.rglob(f"{module_id}.yaml"))
    if not yaml_matches:
        return JSONResponse({"error": "module not found"}, status_code=404)

    yaml_path = yaml_matches[0]
    with open(yaml_path) as f:
        data = yaml.safe_load(f)

    if disabled:
        data["disabled"] = True
    else:
        data.pop("disabled", None)

    with open(yaml_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    return {"id": module_id, "disabled": disabled}


@router.get("/registry")
async def list_registry_images(request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            catalog_resp = await client.get(f"{REGISTRY_INTERNAL}/v2/_catalog")
            catalog_resp.raise_for_status()
            repos = catalog_resp.json().get("repositories", [])

            images = []
            for repo in repos:
                tags_resp = await client.get(
                    f"{REGISTRY_INTERNAL}/v2/{repo}/tags/list"
                )
                tags = tags_resp.json().get("tags", []) if tags_resp.status_code == 200 else []
                for tag in tags:
                    # Get manifest for size/digest info
                    digest = None
                    created = None
                    try:
                        manifest_resp = await client.get(
                            f"{REGISTRY_INTERNAL}/v2/{repo}/manifests/{tag}",
                            headers={"Accept": "application/vnd.docker.distribution.manifest.v2+json"},
                        )
                        if manifest_resp.status_code == 200:
                            digest = manifest_resp.headers.get("Docker-Content-Digest", "")
                    except Exception:
                        pass
                    images.append({
                        "repository": repo,
                        "tag": tag,
                        "full_ref": f"{repo}:{tag}",
                        "digest": digest[:19] + "…" if digest and len(digest) > 19 else digest,
                    })

            return images
    except httpx.ConnectError:
        return JSONResponse({"error": "Cannot connect to registry"}, status_code=502)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/events")
async def list_events(request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    events = db.query(Event).order_by(Event.created_at.desc()).all()
    result = []
    for e in events:
        user_count = db.query(User).filter(User.event_id == e.id).count()
        result.append({
            "id": e.id,
            "name": e.name,
            "status": e.status,
            "description": e.description,
            "welcome_message": e.welcome_message,
            "time_limit_minutes": e.time_limit_minutes,
            "quota": json.loads(e.quota),
            "user_count": user_count,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        })
    return result


@router.get("/events/{event_id}")
async def get_event(event_id: int, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        return JSONResponse({"error": "Event not found"}, status_code=404)

    user_count = db.query(User).filter(User.event_id == event.id).count()
    return {
        "id": event.id,
        "name": event.name,
        "status": event.status,
        "description": event.description,
        "welcome_message": event.welcome_message,
        "time_limit_minutes": event.time_limit_minutes,
        "quota": json.loads(event.quota),
        "user_count": user_count,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


@router.post("/events")
async def create_event(request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    body = await request.json()

    if "quota" in body:
        from builder.quota_validation import validate_quota
        errors = validate_quota(body["quota"])
        if errors:
            return JSONResponse(
                {"error": "Invalid quota", "details": errors},
                status_code=422,
            )

    event = Event(
        name=body.get("name", "CTF Event"),
        quota=json.dumps(body.get("quota", {})),
        status="draft",
        description=body.get("description"),
        welcome_message=body.get("welcome_message"),
        time_limit_minutes=body.get("time_limit_minutes"),
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return {"status": "created", "id": event.id}


@router.put("/events/{event_id}")
async def update_event(
    event_id: int, request: Request, db: Session = Depends(get_db)
):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        return JSONResponse({"error": "Event not found"}, status_code=404)

    body = await request.json()

    if "quota" in body:
        from builder.quota_validation import validate_quota
        errors = validate_quota(body["quota"])
        if errors:
            return JSONResponse(
                {"error": "Invalid quota", "details": errors},
                status_code=422,
            )
        event.quota = json.dumps(body["quota"])

    if "name" in body:
        event.name = body["name"]
    if "description" in body:
        event.description = body["description"]
    if "welcome_message" in body:
        event.welcome_message = body["welcome_message"]
    if "time_limit_minutes" in body:
        event.time_limit_minutes = body["time_limit_minutes"]

    db.commit()
    return {"status": "updated"}


@router.post("/events/{event_id}/start")
async def start_event(event_id: int, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        return JSONResponse({"error": "Event not found"}, status_code=404)

    event.status = "open"
    event.open = True
    db.commit()
    return {"status": "started"}


@router.post("/events/{event_id}/stop")
async def stop_event(event_id: int, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        return JSONResponse({"error": "Event not found"}, status_code=404)

    event.status = "stopped"
    event.open = False
    db.commit()
    return {"status": "stopped"}


@router.delete("/events/{event_id}")
async def delete_event(event_id: int, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        return JSONResponse({"error": "Event not found"}, status_code=404)

    user_count = db.query(User).filter(User.event_id == event.id).count()
    if user_count > 0:
        return JSONResponse(
            {"error": f"Cannot delete event with {user_count} assigned users"},
            status_code=409,
        )

    db.delete(event)
    db.commit()
    return {"status": "deleted"}
