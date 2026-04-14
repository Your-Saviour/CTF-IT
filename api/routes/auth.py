import os

import bcrypt
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy.orm import Session

from api.database import get_db
from api.models import Event, User

router = APIRouter(prefix="/auth", tags=["auth"])

SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY environment variable is required")
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


@router.post("/register")
async def register(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    event_id: int = Form(...),
    db: Session = Depends(get_db),
):
    event = db.query(Event).filter(
        Event.id == event_id, Event.status == "open"
    ).first()
    if not event:
        return RedirectResponse("/?error=invalid_event", status_code=303)

    existing = db.query(User).filter(User.username == username).first()
    if existing:
        return RedirectResponse("/?error=username_taken", status_code=303)

    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    user = User(username=username, password_hash=hashed, event_id=event.id)

    # First registered user becomes admin automatically
    if db.query(User).count() == 0:
        user.is_admin = True

    db.add(user)
    db.commit()
    db.refresh(user)

    response = RedirectResponse("/", status_code=303)
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

    response = RedirectResponse("/admin" if user.is_admin else "/", status_code=303)
    set_session_cookie(response, user.id)
    return response


@router.get("/logout")
async def logout():
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie("session")
    return response
