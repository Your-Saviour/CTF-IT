import json
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.database import Base, get_db
from api.models import Event, EventOperation, Scenario, User
from api.routes.scenarios import router
from builder.operation_plan import empty_operation_plan


@pytest.fixture
def api_client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    db = sessions()
    event = Event(name="Source", quota="{}", status="draft")
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


def _capture(client, event_id):
    return client.post("/admin/api/scenarios/from-event", json={"event_id": event_id, "name": "Locked"})


def test_save_from_event_and_instantiate(api_client):
    client, sessions, event_id = api_client
    created = _capture(client, event_id)
    assert created.status_code == 201
    scenario_id = created.json()["id"]

    inst = client.post(f"/admin/api/scenarios/{scenario_id}/instantiate", json={"name": "Clone"})
    assert inst.status_code == 200
    body = inst.json()
    assert body["report"] == []
    new_event_id = body["event_id"]

    db = sessions()
    clone = db.get(Event, new_event_id)
    assert clone.name == "Clone"
    assert clone.scenario_id == scenario_id
    assert clone.scenario_version == 1


def test_resave_bumps_version(api_client):
    client, sessions, event_id = api_client
    first = _capture(client, event_id).json()
    second = _capture(client, event_id).json()
    assert first["id"] == second["id"]
    assert first["version"] == 1
    assert second["version"] == 2


def test_instantiate_copies_operations(api_client):
    client, sessions, event_id = api_client
    db = sessions()
    op = EventOperation(event_id=event_id, name="Recon", position=0,
                        operation_plan=json.dumps(empty_operation_plan()))
    db.add(op); db.commit()

    created = _capture(client, event_id)
    inst = client.post(f"/admin/api/scenarios/{created.json()['id']}/instantiate")
    rows = db.query(EventOperation).filter(EventOperation.event_id == inst.json()["event_id"]).all()
    assert [r.name for r in rows] == ["Recon"]


def test_delete_referenced_scenario_is_blocked(api_client):
    client, sessions, event_id = api_client
    created = _capture(client, event_id)
    scenario_id = created.json()["id"]
    client.post(f"/admin/api/scenarios/{scenario_id}/instantiate")
    assert client.delete(f"/admin/api/scenarios/{scenario_id}").status_code == 409


def test_delete_unreferenced_scenario_succeeds(api_client):
    client, sessions, event_id = api_client
    created = _capture(client, event_id)
    assert client.delete(f"/admin/api/scenarios/{created.json()['id']}").status_code == 204
