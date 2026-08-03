from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, StaticPool
from sqlalchemy.orm import sessionmaker

from api.database import Base, get_db
from api.main import app
from api.models import Event, User
from api.routes import auth


def _client_and_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_type = sessionmaker(bind=engine)

    def override_get_db():
        db = session_type()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app), session_type, engine


def test_cross_site_state_change_is_rejected():
    client, _, engine = _client_and_session()
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


def test_first_admin_requires_bootstrap_token():
    client, session_type, engine = _client_and_session()
    db = session_type()
    event = Event(name="Bootstrap", quota="{}", status="open")
    db.add(event)
    db.commit()
    event_id = event.id
    db.close()
    try:
        with patch.object(auth, "ADMIN_BOOTSTRAP_TOKEN", "correct-token"), patch.object(
            auth, "COOKIE_SECURE", True
        ):
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

        db = session_type()
        owner = db.query(User).filter_by(username="owner").one()
        assert owner.is_admin is True
        db.close()
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)


def test_login_rate_limit_after_five_failures():
    client, _, engine = _client_and_session()
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
