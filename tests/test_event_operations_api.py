import json
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.database import Base, get_db
from api.models import Event, EventOperation, User
from api.routes.admin import router
from builder.operation_plan import empty_operation_plan


@pytest.fixture
def api_client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    db = sessions()
    first = Event(name="Exercise One", quota="{}", status="draft")
    second = Event(name="Exercise Two", quota="{}", status="draft")
    db.add_all([first, second])
    db.commit()

    app = FastAPI()
    app.include_router(router)

    def override_db():
        session = sessions()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    with patch("api.routes.admin.require_admin", return_value=User(is_admin=True)):
        with TestClient(app) as client:
            yield client, sessions, first.id, second.id


def test_create_list_and_reject_duplicate_operation_name(api_client):
    client, _, event_id, _ = api_client
    created = client.post(
        f"/admin/api/events/{event_id}/operations",
        json={"name": "  Initial foothold  ", "description": "Public web tier"},
    )
    assert created.status_code == 201
    assert created.json()["name"] == "Initial foothold"

    duplicate = client.post(
        f"/admin/api/events/{event_id}/operations", json={"name": "Initial foothold"}
    )
    assert duplicate.status_code == 409
    assert [row["name"] for row in client.get(
        f"/admin/api/events/{event_id}/operations"
    ).json()["operations"]] == ["Initial foothold"]


def test_blank_operation_name_is_rejected(api_client):
    client, _, event_id, _ = api_client
    response = client.post(f"/admin/api/events/{event_id}/operations", json={"name": "  "})
    assert response.status_code == 422


def test_duplicate_inserts_copy_after_source_with_collision_safe_name(api_client):
    client, _, event_id, _ = api_client
    first = client.post(f"/admin/api/events/{event_id}/operations", json={"name": "Phase"}).json()
    client.post(f"/admin/api/events/{event_id}/operations", json={"name": "Closing"})
    first_copy = client.post(
        f"/admin/api/events/{event_id}/operations/{first['id']}/duplicate"
    )
    second_copy = client.post(
        f"/admin/api/events/{event_id}/operations/{first['id']}/duplicate"
    )

    assert first_copy.status_code == 201
    assert second_copy.status_code == 201
    rows = client.get(f"/admin/api/events/{event_id}/operations").json()["operations"]
    assert [row["name"] for row in rows] == ["Phase", "Phase (copy 2)", "Phase (copy)", "Closing"]


def test_operation_lookup_is_scoped_to_parent_event(api_client):
    client, _, first_event_id, second_event_id = api_client
    operation = client.post(
        f"/admin/api/events/{first_event_id}/operations", json={"name": "Only here"}
    ).json()
    response = client.get(
        f"/admin/api/events/{second_event_id}/operations/{operation['id']}/plan"
    )
    assert response.status_code == 404


def test_graph_saves_use_operation_revision_not_event_revision(api_client):
    client, sessions, event_id, _ = api_client
    operation = client.post(
        f"/admin/api/events/{event_id}/operations", json={"name": "Editable"}
    ).json()
    loaded = client.get(
        f"/admin/api/events/{event_id}/operations/{operation['id']}/plan"
    ).json()

    db = sessions()
    event = db.get(Event, event_id)
    event.description = "An unrelated event edit"
    db.commit()
    db.close()

    saved = client.put(
        f"/admin/api/events/{event_id}/operations/{operation['id']}/plan",
        json={"operation_plan": loaded["operation_plan"], "expected_updated_at": loaded["updated_at"]},
    )
    stale = client.put(
        f"/admin/api/events/{event_id}/operations/{operation['id']}/plan",
        json={"operation_plan": loaded["operation_plan"], "expected_updated_at": loaded["updated_at"]},
    )
    assert saved.status_code == 200
    assert stale.status_code == 409


def test_update_and_delete_operation(api_client):
    client, sessions, event_id, _ = api_client
    operation = client.post(
        f"/admin/api/events/{event_id}/operations", json={"name": "Original"}
    ).json()
    updated = client.patch(
        f"/admin/api/events/{event_id}/operations/{operation['id']}",
        json={"name": "Renamed", "description": "Independent phase"},
    )
    assert updated.status_code == 200
    assert updated.json()["description"] == "Independent phase"
    assert client.delete(
        f"/admin/api/events/{event_id}/operations/{operation['id']}"
    ).status_code == 204
    db = sessions()
    assert db.get(EventOperation, operation["id"]) is None
    db.close()


def test_event_detail_does_not_expose_legacy_single_plan(api_client):
    client, _, event_id, _ = api_client
    response = client.get(f"/admin/api/events/{event_id}")
    assert response.status_code == 200
    assert "operation_plan" not in response.json()
