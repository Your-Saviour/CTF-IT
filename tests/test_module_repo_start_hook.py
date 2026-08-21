from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, StaticPool
from sqlalchemy.orm import sessionmaker

from api.database import Base, get_db
from api.models import Event, User
from api.main import app

_engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
_Session = sessionmaker(bind=_engine)


@pytest.fixture(scope="module", autouse=True)
def setup_db():
    Base.metadata.create_all(bind=_engine)
    yield
    Base.metadata.drop_all(bind=_engine)


@pytest.fixture()
def draft_event():
    db = _Session()
    admin = User(username="admin", password_hash="x", is_admin=True)
    db.add(admin); db.flush()
    event = Event(name="Draft", quota="{}", status="draft")
    db.add(event); db.commit(); db.refresh(event)
    yield admin, event
    db.close()


def test_start_event_syncs_repos(draft_event, monkeypatch):
    admin, event = draft_event

    def override_get_db():
        db = _Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with patch("api.routes.admin.require_admin", return_value=admin), \
         patch("api.routes.module_repos.sync_all_repos") as mock_sync, \
         patch("api.services.integration_outbox.enqueue_event_sync", return_value=True):
        with TestClient(app, raise_server_exceptions=True) as c:
            resp = c.post(f"/admin/api/events/{event.id}/start")
    app.dependency_overrides.clear()
    assert resp.status_code == 200
    mock_sync.assert_called_once()


def test_start_event_syncs_repos_off_thread(monkeypatch):
    db = _Session()
    admin = User(username="admin_offthread", password_hash="x", is_admin=True)
    db.add(admin); db.flush()
    event = Event(name="Draft2", quota="{}", status="draft")
    db.add(event); db.commit(); db.refresh(event)
    event_id = event.id
    db.close()

    def override_get_db():
        session = _Session()
        try:
            yield session
        finally:
            session.close()

    to_thread_calls = []

    def fake_to_thread(fn, *args, **kwargs):
        to_thread_calls.append(fn)
        return fn(*args, **kwargs)

    app.dependency_overrides[get_db] = override_get_db
    with patch("api.routes.admin.require_admin", return_value=admin), \
         patch("api.routes.module_repos.sync_all_repos") as mock_sync, \
         patch("api.services.integration_outbox.enqueue_event_sync", return_value=True), \
         patch("asyncio.to_thread", side_effect=fake_to_thread):
        with TestClient(app, raise_server_exceptions=True) as c:
            resp = c.post(f"/admin/api/events/{event_id}/start")
    app.dependency_overrides.clear()
    assert resp.status_code == 200
    mock_sync.assert_called_once()
    assert to_thread_calls
