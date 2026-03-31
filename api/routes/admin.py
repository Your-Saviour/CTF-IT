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

    event = db.query(Event).filter(Event.open == True).first()
    if event:
        quota = json.loads(event.quota)
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
            "type": m.type,
            "difficulty": m.difficulty,
            "points": m.points,
            "category": m.category,
        }
        for m in modules
    ]


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


@router.put("/event")
async def update_event(
    request: Request,
    db: Session = Depends(get_db),
):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    body = await request.json()
    event = db.query(Event).first()
    if not event:
        event = Event(
            name=body.get("name", "CTF Event"),
            quota=json.dumps(body.get("quota", {})),
            open=body.get("open", True),
        )
        db.add(event)
    else:
        if "name" in body:
            event.name = body["name"]
        if "quota" in body:
            event.quota = json.dumps(body["quota"])
        if "open" in body:
            event.open = body["open"]

    db.commit()
    return {"status": "updated"}
