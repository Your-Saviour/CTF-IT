from collections import defaultdict, deque
from datetime import datetime, timezone
import hashlib
import hmac
import os
import time

import bcrypt
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy.orm import Session

from api.database import get_db

router = APIRouter(prefix="/auth", tags=["auth"])

# Import models after database import to avoid circular imports
User = None
Event = None
Session = None

SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY environment variable is required")
serializer = URLSafeTimedSerializer(SECRET_KEY)
ADMIN_BOOTSTRAP_TOKEN = os.environ.get("ADMIN_BOOTSTRAP_TOKEN", "")
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "true").lower() not in {"0", "false", "no"}
_LOGIN_WINDOW_SECONDS = 300
_LOGIN_MAX_ATTEMPTS = 5
_login_attempts: dict[str, deque[float]] = defaultdict(deque)


def get_current_user(request: Request, db: Session = Depends(get_db)):
    from api.models import User
    token = request.cookies.get("session")
    if not token:
        return None
    try:
        session_data = serializer.loads(token, max_age=86400 * 7)
    except Exception:
        return None
    if not isinstance(session_data, dict):
        return None
    user = db.query(User).filter(User.id == session_data.get("user_id")).first()
    if not user or not user.active:
        return None
    if user.session_version != session_data.get("session_version"):
        return None
    return user


def set_session_cookie(response, user: User):
    token = serializer.dumps({"user_id": user.id, "session_version": user.session_version})
    response.set_cookie(
        "session",
        token,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        max_age=86400 * 7,
        path="/",
    )


def _token_digest(raw_token: str) -> str:
    from api.models import AccountToken
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _valid_token(db: Session, raw_token: str, purpose: str):
    from api.models import AccountToken
    record = db.query(AccountToken).filter(
        AccountToken.token_hash == _token_digest(raw_token),
        AccountToken.purpose == purpose,
        AccountToken.redeemed_at.is_(None),
    ).first()
    if not record:
        return None
    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return record if expires_at > datetime.now(timezone.utc) else None


def _claim_token(db: Session, record) -> bool:
    """Atomically mark a token used so concurrent redemption cannot replay it."""
    from api.models import AccountToken, utcnow
    claimed_at = utcnow()
    updated = db.query(AccountToken).filter(
        AccountToken.id == record.id,
        AccountToken.redeemed_at.is_(None),
    ).update({AccountToken.redeemed_at: claimed_at}, synchronize_session=False)
    if updated:
        record.redeemed_at = claimed_at
    return updated == 1


@router.post("/register")
async def register(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    event_id: int = Form(...),
    admin_bootstrap_token: str = Form(""),
    db: Session = Depends(get_db),
):
    from api.models import User as UserModel, Event as EventModel
    username = username.strip()
    password_bytes = password.encode("utf-8")
    if not 3 <= len(username) <= 64:
        return RedirectResponse("/?error=invalid_username", status_code=303)
    if not 12 <= len(password_bytes) <= 72:
        return RedirectResponse("/?error=invalid_password", status_code=303)

    first_user = db.query(UserModel).count() == 0
    if not first_user:
        return RedirectResponse("/?error=invitation_required", status_code=303)
    if not ADMIN_BOOTSTRAP_TOKEN:
        return RedirectResponse("/?error=bootstrap_not_configured", status_code=303)
    if not hmac.compare_digest(admin_bootstrap_token, ADMIN_BOOTSTRAP_TOKEN):
        return RedirectResponse("/?error=invalid_bootstrap_token", status_code=303)

    event = db.query(EventModel).filter(EventModel.id == event_id).first()
    if not event:
        return RedirectResponse("/?error=invalid_event", status_code=303)
    if db.query(UserModel).filter(UserModel.username == username).first():
        return RedirectResponse("/?error=username_taken", status_code=303)

    hashed = bcrypt.hashpw(password_bytes, bcrypt.gensalt()).decode()
    user = UserModel(username=username, password_hash=hashed, event_id=event.id)

    user.is_admin = True

    db.add(user)
    db.commit()
    db.refresh(user)

    response = RedirectResponse("/", status_code=303)
    set_session_cookie(response, user)
    return response


@router.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    from api.models import User
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
    if not user or not password_matches or not user.active:
        attempts.append(now)
        return RedirectResponse("/?error=invalid_credentials", status_code=303)

    attempts.clear()

    response = RedirectResponse("/admin" if user.is_admin else "/", status_code=303)
    set_session_cookie(response, user)
    return response


@router.get("/invitations/{token}")
async def validate_invitation(token: str, db: Session = Depends(get_db)):
    record = _valid_token(db, token, "invitation")
    if not record:
        return {"valid": False, "message": "This link is invalid or has expired."}
    event = db.query(Event).filter(Event.id == record.event_id).first()
    return {
        "valid": True,
        "event_name": event.name if event else None,
        "intended_username": record.intended_username,
    }


@router.post("/invitations/{token}")
async def redeem_invitation(
    token: str,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    generic = "/invite/" + token + "?error=invalid_or_expired"
    record = _valid_token(db, token, "invitation")
    if not record:
        return RedirectResponse(generic, status_code=303)
    username = username.strip()
    password_bytes = password.encode("utf-8")
    if not 3 <= len(username) <= 64:
        return RedirectResponse("/invite/" + token + "?error=invalid_username", status_code=303)
    if not 12 <= len(password_bytes) <= 72:
        return RedirectResponse("/invite/" + token + "?error=invalid_password", status_code=303)
    if record.intended_username and username.casefold() != record.intended_username.casefold():
        return RedirectResponse(generic, status_code=303)
    from api.models import User
    if db.query(User).filter(User.username == username).first():
        return RedirectResponse(generic, status_code=303)
    if not _claim_token(db, record):
        db.rollback()
        return RedirectResponse(generic, status_code=303)

    user = User(
        username=username,
        password_hash=bcrypt.hashpw(password_bytes, bcrypt.gensalt()).decode(),
        event_id=record.event_id,
        is_admin=record.intended_is_admin,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    response = RedirectResponse("/admin" if user.is_admin else "/", status_code=303)
    set_session_cookie(response, user)
    return response


@router.get("/password-resets/{token}")
async def validate_password_reset(token: str, db: Session = Depends(get_db)):
    valid = _valid_token(db, token, "password_reset") is not None
    return {"valid": valid, "message": None if valid else "This link is invalid or has expired."}


@router.post("/password-resets/{token}")
async def redeem_password_reset(
    token: str,
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    from api.models import User, utcnow
    record = _valid_token(db, token, "password_reset")
    generic = "/reset/" + token + "?error=invalid_or_expired"
    if not record:
        return RedirectResponse(generic, status_code=303)
    password_bytes = password.encode("utf-8")
    if not 12 <= len(password_bytes) <= 72:
        return RedirectResponse("/reset/" + token + "?error=invalid_password", status_code=303)
    user = db.query(User).filter(User.id == record.target_user_id).first()
    if not user:
        return RedirectResponse(generic, status_code=303)
    if not _claim_token(db, record):
        db.rollback()
        return RedirectResponse(generic, status_code=303)
    user.password_hash = bcrypt.hashpw(password_bytes, bcrypt.gensalt()).decode()
    user.password_changed_at = utcnow()
    user.updated_at = utcnow()
    user.session_version += 1
    db.commit()
    response = RedirectResponse("/?reset=success", status_code=303)
    response.delete_cookie("session")
    return response


@router.post("/logout")
async def logout():
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie("session")
    return response
