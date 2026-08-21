from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, StaticPool
from sqlalchemy.orm import sessionmaker

from api.database import Base, get_db
from api.integrations.base import ConnectionTestResult
from api.main import app
from api.models import Event, EventIntegration, IntegrationDestination, IntegrationSyncJob, ServiceCredential, User, utcnow
from api.services.secrets import encrypt_secret


engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Sessions = sessionmaker(bind=engine)


@pytest.fixture(autouse=True)
def database(monkeypatch):
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "integration-api-test-key")
    Base.metadata.create_all(engine)
    with Sessions() as db:
        admin = User(username="admin", password_hash="x", is_admin=True)
        event = Event(name="Exercise", quota="{}", status="open", open=True)
        credential = ServiceCredential(
            service_name="Expo token", credential_type="token", password=encrypt_secret("secret")
        )
        db.add_all([admin, event, credential]); db.commit()
        ids = admin.id, event.id, credential.id
    yield ids
    Base.metadata.drop_all(engine)


@pytest.fixture
def client(database):
    admin_id, _, _ = database

    def override_db():
        with Sessions() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    with patch("api.routes.integrations.get_current_user", side_effect=lambda request, db: db.get(User, admin_id)):
        with TestClient(app) as test_client:
            yield test_client
    app.dependency_overrides.clear()


def destination_payload(credential_id, **overrides):
    value = {
        "name": "Expo staging", "adapter_key": "expo_it", "base_url": "https://expo.example/",
        "credential_id": credential_id, "enabled": True, "allow_insecure_http": False, "config": {},
    }
    value.update(overrides)
    return value


def test_admin_creates_browser_safe_destination(client, database):
    _, _, credential_id = database
    response = client.post("/admin/api/integrations/destinations", json=destination_payload(credential_id))
    assert response.status_code == 201
    assert response.json()["base_url"] == "https://expo.example"
    assert "secret" not in response.text and "password" not in response.text


def test_destination_rejects_insecure_http_without_override(client, database):
    _, _, credential_id = database
    response = client.post("/admin/api/integrations/destinations", json=destination_payload(
        credential_id, base_url="http://expo.internal"
    ))
    assert response.status_code == 422


def test_event_binding_and_manual_sync_are_queued(client, database):
    _, event_id, credential_id = database
    destination = client.post("/admin/api/integrations/destinations", json=destination_payload(credential_id)).json()
    response = client.put(f"/admin/api/events/{event_id}/integrations", json={
        "destination_id": destination["id"], "enabled": True,
    })
    assert response.status_code == 200
    with patch("api.routes.integrations.enqueue_event_sync", return_value=True) as enqueue:
        sync = client.post(f"/admin/api/events/{event_id}/integrations/{response.json()['id']}/sync")
    assert sync.status_code == 202
    enqueue.assert_called_once_with(event_id, "manual", priority=100)


def test_connection_test_is_non_mutating_and_sanitized(client, database):
    _, _, credential_id = database
    destination = client.post("/admin/api/integrations/destinations", json=destination_payload(credential_id)).json()
    with patch("api.routes.integrations.get_adapter") as adapter:
        adapter.return_value.test_connection = AsyncMock(
            return_value=ConnectionTestResult(True, "ok", "Connected")
        )
        response = client.post(f"/admin/api/integrations/destinations/{destination['id']}/test")
    assert response.status_code == 200
    assert response.json()["last_test_status"] == "successful"
    assert response.headers["cache-control"] == "no-store"


def test_disabling_binding_cancels_queued_delivery(client, database):
    _, event_id, credential_id = database
    destination = client.post(
        "/admin/api/integrations/destinations", json=destination_payload(credential_id)
    ).json()
    binding = client.put(f"/admin/api/events/{event_id}/integrations", json={
        "destination_id": destination["id"], "enabled": True,
    }).json()
    with Sessions() as db:
        db.add(IntegrationSyncJob(
            binding_id=binding["id"], status="pending", trigger_reason="vm_updated",
            next_attempt_at=utcnow(),
        ))
        db.commit()

    response = client.put(f"/admin/api/events/{event_id}/integrations", json={
        "destination_id": destination["id"], "enabled": False,
    })
    assert response.status_code == 200
    with Sessions() as db:
        assert db.query(IntegrationSyncJob).one().status == "cancelled"
