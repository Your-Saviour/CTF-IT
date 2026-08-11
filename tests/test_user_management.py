from datetime import timedelta
import hashlib
from unittest.mock import patch

import bcrypt
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, StaticPool, text
from sqlalchemy.orm import sessionmaker

from api.database import Base, get_db
from api import database
from api.main import app
from api.models import AccountToken, AdminAudit, Event, User, utcnow
from api.routes import auth


def managed_client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_db():
        db = sessions()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app), sessions, engine


def make_user(db, username, *, admin=False, active=True, event=None):
    user = User(
        username=username,
        password_hash=bcrypt.hashpw(b"correct-horse-battery", bcrypt.gensalt()).decode(),
        is_admin=admin,
        active=active,
        event_id=event.id if event else None,
    )
    db.add(user)
    db.commit()
    return user


def login_cookie(user):
    return auth.serializer.dumps({"user_id": user.id, "session_version": user.session_version})


def close_client(engine):
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)


def test_invitation_is_hashed_single_use_and_event_bound():
    client, sessions, engine = managed_client()
    db = sessions()
    event = Event(name="Invited Event", quota="{}", status="draft")
    db.add(event)
    db.commit()
    admin = make_user(db, "owner", admin=True, event=event)
    try:
        client.cookies.set("session", login_cookie(admin))
        created = client.post("/admin/api/invitations", json={"event_id": event.id, "role": "participant"})
        assert created.status_code == 200
        raw = created.json()["link"].rsplit("/", 1)[-1]
        stored = db.query(AccountToken).one()
        assert raw not in stored.token_hash
        assert stored.token_hash == hashlib.sha256(raw.encode()).hexdigest()
        assert stored.expires_at - stored.created_at >= timedelta(days=6, hours=23)

        redeemed = client.post(
            f"/auth/invitations/{raw}",
            data={"username": "participant", "password": "a-strong-password"},
            follow_redirects=False,
        )
        assert redeemed.status_code == 303
        participant = db.query(User).filter_by(username="participant").one()
        assert participant.event_id == event.id
        replay = client.post(
            f"/auth/invitations/{raw}",
            data={"username": "someone-else", "password": "a-strong-password"},
            follow_redirects=False,
        )
        assert "invalid_or_expired" in replay.headers["location"]
    finally:
        db.close()
        close_client(engine)


def test_expired_and_unknown_tokens_have_same_public_response():
    client, sessions, engine = managed_client()
    db = sessions()
    event = Event(name="Event", quota="{}")
    db.add(event)
    db.commit()
    db.add(AccountToken(
        token_hash=hashlib.sha256(b"expired").hexdigest(), purpose="invitation",
        event_id=event.id, expires_at=utcnow() - timedelta(seconds=1),
    ))
    db.commit()
    try:
        expired = client.get("/auth/invitations/expired").json()
        unknown = client.get("/auth/invitations/unknown").json()
        assert expired == unknown == {"valid": False, "message": "This link is invalid or has expired."}
    finally:
        db.close()
        close_client(engine)


def test_role_change_revokes_session_and_self_and_last_admin_are_protected():
    client, sessions, engine = managed_client()
    db = sessions()
    event = Event(name="Event", quota="{}")
    db.add(event)
    db.commit()
    owner = make_user(db, "owner", admin=True, event=event)
    participant = make_user(db, "participant", event=event)
    old_participant_cookie = login_cookie(participant)
    try:
        client.cookies.set("session", login_cookie(owner))
        self_demote = client.patch(f"/admin/api/users/{owner.id}", json={"role": "participant"})
        assert self_demote.status_code == 409
        self_deactivate = client.patch(f"/admin/api/users/{owner.id}/activation", json={"active": False})
        assert self_deactivate.status_code == 409

        changed = client.patch(f"/admin/api/users/{participant.id}", json={"role": "administrator"})
        assert changed.status_code == 200
        client.cookies.set("session", old_participant_cookie)
        assert client.get("/admin/api/users").status_code == 403

        db.refresh(participant)
        assert participant.event_id == event.id  # partial role update preserves event
        assert db.query(AdminAudit).filter_by(target_user_id=participant.id).count() == 1
    finally:
        db.close()
        close_client(engine)


def test_password_reset_and_deactivation_revoke_sessions():
    client, sessions, engine = managed_client()
    db = sessions()
    admin = make_user(db, "admin", admin=True)
    user = make_user(db, "user")
    user_cookie = login_cookie(user)
    try:
        client.cookies.set("session", login_cookie(admin))
        reset = client.post(f"/admin/api/users/{user.id}/reset-link")
        raw = reset.json()["link"].rsplit("/", 1)[-1]
        result = client.post(
            f"/auth/password-resets/{raw}", data={"password": "new-secure-password"},
            follow_redirects=False,
        )
        assert result.status_code == 303
        client.cookies.set("session", user_cookie)
        assert client.get("/").context["user"] is None

        db.refresh(user)
        fresh_cookie = login_cookie(user)
        client.cookies.set("session", login_cookie(admin))
        assert client.patch(f"/admin/api/users/{user.id}/activation", json={"active": False}).status_code == 200
        client.cookies.set("session", fresh_cookie)
        assert client.get("/").context["user"] is None
    finally:
        db.close()
        close_client(engine)


def test_startup_migrates_existing_users_without_losing_role_or_event(tmp_path):
    path = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE account_tokens"))
        connection.execute(text("DROP TABLE admin_audit"))
        connection.execute(text("ALTER TABLE users RENAME TO users_current"))
        connection.execute(text("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                username VARCHAR(64) UNIQUE NOT NULL,
                password_hash VARCHAR(128) NOT NULL,
                created_at DATETIME,
                is_admin BOOLEAN,
                event_id INTEGER REFERENCES events(id)
            )
        """))
        connection.execute(text("""
            INSERT INTO users (id, username, password_hash, created_at, is_admin, event_id)
            SELECT id, username, password_hash, created_at, is_admin, event_id FROM users_current
        """))
        connection.execute(text("DROP TABLE users_current"))
        connection.execute(text("INSERT INTO events (id, name, quota, open, status, created_at) VALUES (42, 'Legacy', '{}', 1, 'open', CURRENT_TIMESTAMP)"))
        connection.execute(text("INSERT INTO teams (id, name, event_id, created_at) VALUES (99, 'Only Team', 42, CURRENT_TIMESTAMP)"))
        connection.execute(text("""
            INSERT INTO users (id, username, password_hash, is_admin, event_id)
            VALUES (7, 'legacy-admin', 'unused', 1, 42)
        """))
        connection.execute(text("""
            INSERT INTO users (id, username, password_hash, is_admin, event_id)
            VALUES (8, 'legacy-participant', 'unused', 0, 42)
        """))

    sessions = sessionmaker(bind=engine)
    try:
        with patch.object(database, "engine", engine), patch.object(database, "SessionLocal", sessions):
            with TestClient(app):
                pass
        columns = {column["name"] for column in inspect(engine).get_columns("users")}
        assert {"active", "session_version", "updated_at", "deactivated_at", "password_changed_at", "team_id"} <= columns
        assert inspect(engine).has_table("account_tokens")
        assert inspect(engine).has_table("admin_audit")
        assert inspect(engine).has_table("team_training_credentials")
        assert inspect(engine).has_table("verification_attempts")
        assert inspect(engine).has_table("hint_reveals")
        vm_module_columns = {column["name"] for column in inspect(engine).get_columns("vm_modules")}
        assert {"status", "last_verified_at", "first_completed_at", "completed_by_id",
                "verification_error_code", "verification_baseline_json"} <= vm_module_columns
        db = sessions()
        legacy = db.query(User).filter_by(username="legacy-admin").one()
        assert legacy.active is True
        assert legacy.session_version == 1
        assert legacy.is_admin is True
        assert legacy.event_id == 42
        # Administrators stay event-scoped and do not require a team.
        assert legacy.team_id is None
        participant = db.query(User).filter_by(username="legacy-participant").one()
        assert participant.team_id == 99
        db.close()
    finally:
        engine.dispose()
