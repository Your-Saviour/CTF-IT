import asyncio
import json
import os

import bcrypt
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy.orm import Session

from api.database import get_db
from api.models import Event, User, UserImage, UserModule
from builder.main import build_image_for_user

router = APIRouter(prefix="/auth", tags=["auth"])

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")
serializer = URLSafeTimedSerializer(SECRET_KEY)


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    token = request.cookies.get("session")
    if not token:
        return None
    try:
        user_id = serializer.loads(token, max_age=86400 * 7)
    except Exception:
        return None
    return db.query(User).filter(User.id == user_id).first()


def set_session_cookie(response, user_id: int):
    token = serializer.dumps(user_id)
    response.set_cookie(
        "session", token, httponly=True, samesite="lax", max_age=86400 * 7
    )


async def _run_build(user_id: int, username: str, quota: dict):
    from api.database import SessionLocal
    try:
        result = await asyncio.to_thread(
            build_image_for_user, username, quota
        )
        db = SessionLocal()
        try:
            image = db.query(UserImage).filter(
                UserImage.user_id == user_id
            ).first()
            if image:
                image.image_tag = result["image_tag"]
                image.flag = result["flag"]
                image.build_state = result["build_state"]
                image.status = "ready"

            for m in result["modules"]:
                db.add(UserModule(
                    user_id=user_id,
                    module_id=m.id,
                    module_type=m.type,
                    difficulty=m.difficulty,
                    points=m.points,
                ))
            db.commit()
        finally:
            db.close()
    except Exception as e:
        db = SessionLocal()
        try:
            image = db.query(UserImage).filter(
                UserImage.user_id == user_id
            ).first()
            if image:
                image.status = "failed"
                db.commit()
        finally:
            db.close()
        import logging
        logging.getLogger(__name__).exception("Build failed for user %s", username)


@router.post("/register")
async def register(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    existing = db.query(User).filter(User.username == username).first()
    if existing:
        return RedirectResponse("/?error=username_taken", status_code=303)

    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    user = User(username=username, password_hash=hashed)
    db.add(user)
    db.commit()
    db.refresh(user)

    # Get event quota
    event = db.query(Event).filter(Event.open == True).first()
    if event:
        quota = json.loads(event.quota)
    else:
        quota = json.loads(
            os.environ.get(
                "EVENT_QUOTA",
                '{"vulnerability":{"easy":1,"medium":0,"hard":0},"hardening":{"easy":0,"medium":1,"hard":0}}',
            )
        )

    # Create queued image record
    image = UserImage(user_id=user.id, status="queued")
    db.add(image)
    db.commit()

    # Trigger build in background
    asyncio.create_task(_run_build(user.id, user.username, quota))

    response = RedirectResponse("/dashboard", status_code=303)
    set_session_cookie(response, user.id)
    return response


@router.post("/login")
async def login(
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.username == username).first()
    if not user or not bcrypt.checkpw(password.encode(), user.password_hash.encode()):
        return RedirectResponse("/?error=invalid_credentials", status_code=303)

    response = RedirectResponse("/dashboard", status_code=303)
    set_session_cookie(response, user.id)
    return response


@router.get("/logout")
async def logout():
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie("session")
    return response
