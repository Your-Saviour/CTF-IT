from collections import defaultdict, deque
import hmac
import os
import time

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
ADMIN_BOOTSTRAP_TOKEN = os.environ.get("ADMIN_BOOTSTRAP_TOKEN", "")
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "true").lower() not in {"0", "false", "no"}
_LOGIN_WINDOW_SECONDS = 300
_LOGIN_MAX_ATTEMPTS = 5
_login_attempts: dict[str, deque[float]] = defaultdict(deque)


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
        "session",
        token,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        max_age=86400 * 7,
        path="/",
    )


@router.post("/register")
async def register(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    event_id: int = Form(...),
    admin_bootstrap_token: str = Form(""),
    db: Session = Depends(get_db),
):
    username = username.strip()
    password_bytes = password.encode("utf-8")
    if not 3 <= len(username) <= 64:
        return RedirectResponse("/?error=invalid_username", status_code=303)
    if not 12 <= len(password_bytes) <= 72:
        return RedirectResponse("/?error=invalid_password", status_code=303)

    from api.services.event_lifecycle import expire_due_events
    expire_due_events(db)
    event = db.query(Event).filter(
        Event.id == event_id, Event.status == "open"
    ).first()
    if not event:
        return RedirectResponse("/?error=invalid_event", status_code=303)

    existing = db.query(User).filter(User.username == username).first()
    if existing:
        return RedirectResponse("/?error=username_taken", status_code=303)

    first_user = db.query(User).count() == 0
    if first_user:
        if not ADMIN_BOOTSTRAP_TOKEN:
            return RedirectResponse("/?error=bootstrap_not_configured", status_code=303)
        if not hmac.compare_digest(admin_bootstrap_token, ADMIN_BOOTSTRAP_TOKEN):
            return RedirectResponse("/?error=invalid_bootstrap_token", status_code=303)

    hashed = bcrypt.hashpw(password_bytes, bcrypt.gensalt()).decode()
    user = User(username=username, password_hash=hashed, event_id=event.id)

    # First registered user becomes admin automatically
    if first_user:
        user.is_admin = True

    db.add(user)
    db.commit()
    db.refresh(user)

    response = RedirectResponse("/", status_code=303)
    set_session_cookie(response, user.id)
    return response


@router.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    remote = request.client.host if request.client else "unknown"
    client_id = f"{remote}:{username.casefold()}"
    now = time.monotonic()
    attempts = _login_attempts[client_id]
    while attempts and now - attempts[0] > _LOGIN_WINDOW_SECONDS:
        attempts.popleft()
    if len(attempts) >= _LOGIN_MAX_ATTEMPTS:
        return RedirectResponse("/?error=rate_limited", status_code=303)

    user = db.query(User).filter(User.username == username).first()
    password_bytes = password.encode("utf-8")
    password_matches = False
    if user and len(password_bytes) <= 72:
        password_matches = bcrypt.checkpw(password_bytes, user.password_hash.encode())
    if not user or not password_matches:
        attempts.append(now)
        return RedirectResponse("/?error=invalid_credentials", status_code=303)

    attempts.clear()

    response = RedirectResponse("/admin" if user.is_admin else "/", status_code=303)
    set_session_cookie(response, user.id)
    return response


@router.post("/logout")
async def logout():
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie("session")
    return response
