from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.database import Base, get_db
from api.models import Event, User
from api.routes.scenarios import router


@pytest.fixture
def api_client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    db = sessions()
    event = Event(name="Timeline", quota="{}", status="draft", time_limit_minutes=120)
    db.add(event); db.commit(); db.refresh(event)
    event_id = event.id

    app = FastAPI()
    app.include_router(router)

    def override_db():
        session = sessions()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    with patch("api.routes.scenarios.require_admin", return_value=User(is_admin=True)):
        with TestClient(app) as client:
            yield client, sessions, event_id


TIMELINE = {"version": 1, "phases": [], "injects": [
    {"id": "i1", "name": "Beat", "offset_minutes": 30, "kind": "milestone", "payload": {}}
]}


def test_save_and_reload_timeline(api_client):
    client, _, event_id = api_client
    loaded = client.get(f"/admin/api/events/{event_id}/timeline").json()
    saved = client.put(f"/admin/api/events/{event_id}/timeline",
                       json={"timeline": TIMELINE, "expected_updated_at": loaded["updated_at"]})
    assert saved.status_code == 200
    assert client.get(f"/admin/api/events/{event_id}/timeline").json()["timeline"]["injects"][0]["name"] == "Beat"


def test_stale_timeline_save_is_rejected(api_client):
    client, _, event_id = api_client
    loaded = client.get(f"/admin/api/events/{event_id}/timeline").json()
    assert client.put(f"/admin/api/events/{event_id}/timeline",
                      json={"timeline": TIMELINE, "expected_updated_at": loaded["updated_at"]}).status_code == 200
    assert client.put(f"/admin/api/events/{event_id}/timeline",
                      json={"timeline": TIMELINE, "expected_updated_at": loaded["updated_at"]}).status_code == 409


def test_plan_health_returns_keys(api_client):
    client, _, event_id = api_client
    response = client.get(f"/admin/api/events/{event_id}/plan-health")
    assert response.status_code == 200
    body = response.json()
    assert {"module_issues", "timeline_issues", "operation_issues"} <= set(body.keys())
