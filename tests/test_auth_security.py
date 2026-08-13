from unittest.mock import patch

import bcrypt
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, StaticPool, text
from sqlalchemy.orm import sessionmaker

from api.database import Base, get_db as original_get_db
from api.main import app
from api.models import Event, User
from api.routes import auth


def _client_and_session(tmp_path):
    db_file = tmp_path / "test.db"
    engine = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_type = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_db():
        db = session_type()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[original_get_db] = override_get_db
    return TestClient(app), session_type, engine, db_file


def test_cross_site_state_change_is_rejected(tmp_path):
    client, _, engine, db_file = _client_and_session(tmp_path)
    try:
        response = client.post(
            "/auth/login",
            data={"username": "x", "password": "x"},
            headers={"Origin": "https://evil.example", "Host": "ctf.example"},
            follow_redirects=False,
        )
        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
        engine.dispose()
        db_file.unlink(missing_ok=True)


def test_first_admin_requires_bootstrap_token(tmp_path):
    db_file = tmp_path / "test.db"
    engine = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_type = sessionmaker(bind=engine, expire_on_commit=False)

    # Insert event only (no existing user) so db.query(User).count() == 0
    with engine.begin() as conn:
        event_id = conn.execute(
            text("INSERT INTO events (name, quota, open, status, created_at) VALUES ('Bootstrap', '{}', 1, 'open', CURRENT_TIMESTAMP)")
        ).lastrowid

    # Patch get_db in api.database so the Depends object sees it
    def patched_get_db():
        db = session_type()
        try:
            yield db
        finally:
            db.close()

    import api.database
    api.database.get_db = patched_get_db
    # Override the ORIGINAL get_db (the one the Depends object references)
    app.dependency_overrides[original_get_db] = patched_get_db

    try:
        with patch.object(auth, "ADMIN_BOOTSTRAP_TOKEN", "correct-token"), patch.object(
            auth, "COOKIE_SECURE", True
        ), patch.object(auth, "User", User), patch.object(auth, "Event", Event):
            client = TestClient(app)
            rejected = client.post(
                "/auth/register",
                data={
                    "username": "owner",
                    "password": "long-enough-password",
                    "event_id": event_id,
                    "admin_bootstrap_token": "wrong-token",
                },
                follow_redirects=False,
            )
            assert rejected.status_code == 303
            assert "invalid_bootstrap_token" in rejected.headers["location"]

            accepted = client.post(
                "/auth/register",
                data={
                    "username": "owner",
                    "password": "long-enough-password",
                    "event_id": event_id,
                    "admin_bootstrap_token": "correct-token",
                },
                follow_redirects=False,
            )
            assert accepted.status_code == 303
            assert "Secure" in accepted.headers["set-cookie"]

        # Verify user was created by querying raw SQL
        with engine.connect() as conn:
            count = conn.execute(text("SELECT count(*) FROM users WHERE username='owner'")).scalar()
            assert count == 1, f"Expected 1 owner user, got {count}"
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
        engine.dispose()
        db_file.unlink(missing_ok=True)


def test_initial_admin_can_select_seeded_draft_event(tmp_path):
    client, session_type, engine, db_file = _client_and_session(tmp_path)
    db = session_type()
    try:
        event = Event(name="Seeded Draft", quota="{}", status="draft", open=False)
        db.add(event)
        db.commit()

        response = client.get("/api/events")
        assert response.status_code == 200
        assert response.json() == [{
            "id": event.id,
            "name": "Seeded Draft",
            "description": None,
            "welcome_message": None,
        }]

        db.add(User(username="existing", password_hash="unused", event_id=event.id))
        db.commit()
        assert client.get("/api/events").json() == []
    finally:
        db.close()
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
        engine.dispose()
        db_file.unlink(missing_ok=True)


def test_login_rate_limit_after_five_failures(tmp_path):
    client, _, engine, db_file = _client_and_session(tmp_path)
    auth._login_attempts.clear()
    try:
        for _ in range(5):
            response = client.post(
                "/auth/login",
                data={"username": "missing", "password": "wrong"},
                follow_redirects=False,
            )
            assert "invalid_credentials" in response.headers["location"]
        limited = client.post(
            "/auth/login",
            data={"username": "missing", "password": "wrong"},
            follow_redirects=False,
        )
        assert "rate_limited" in limited.headers["location"]
    finally:
        auth._login_attempts.clear()
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
        engine.dispose()
        db_file.unlink(missing_ok=True)
