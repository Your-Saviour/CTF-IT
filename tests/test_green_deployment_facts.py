import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, StaticPool
from sqlalchemy.orm import sessionmaker

from api.database import Base, get_db
from api.main import app
from api.models import Event, GreenDeploymentFact, User
from api.services.secrets import decrypt_secret


engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Sessions = sessionmaker(bind=engine)
PRIVATE_KEY = "-----BEGIN OPENSSH PRIVATE KEY-----\nencoded-key\n-----END OPENSSH PRIVATE KEY-----"


@pytest.fixture(autouse=True)
def database(monkeypatch):
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "green-fact-test-key")
    Base.metadata.create_all(engine)
    infrastructure = {"vpn_gateway": {}, "sites": [], "green_infrastructure": {"vms": [{
        "key": "expo_it", "name": "Expo-IT", "base_type": "ubuntu_24_server",
        "default_plan": "small", "region": "syd",
    }]}}
    module_plan = {"version": 1, "assignments": {"green:expo_it": {
        "mode": "manual_only", "pinned_module_ids": ["expo_it"],
        "resolved_module_ids": ["expo_it"],
    }}}
    with Sessions() as db:
        admin = User(username="admin", password_hash="x", is_admin=True)
        event = Event(name="Exercise", quota="{}", status="draft",
                      infrastructure=json.dumps(infrastructure), module_plan=json.dumps(module_plan))
        db.add_all([admin, event]); db.commit()
        ids = admin.id, event.id
    yield ids
    Base.metadata.drop_all(engine)


@pytest.fixture
def client(database):
    admin_id, _ = database
    def override_db():
        with Sessions() as db:
            yield db
    app.dependency_overrides[get_db] = override_db
    with patch("api.routes.admin.get_current_user", side_effect=lambda request, db: db.get(User, admin_id)):
        with TestClient(app) as test_client:
            yield test_client
    app.dependency_overrides.clear()


def test_secret_fact_is_encrypted_and_reads_as_presence_only(client, database):
    _, event_id = database
    url = f"/admin/api/events/{event_id}/green/expo_it/facts/git.ssh_private_key"
    response = client.put(url, json={"value": PRIVATE_KEY})
    assert response.status_code == 200
    assert PRIVATE_KEY not in response.text
    with Sessions() as db:
        row = db.query(GreenDeploymentFact).one()
        assert PRIVATE_KEY not in row.encrypted_value
        assert decrypt_secret(row.encrypted_value) == PRIVATE_KEY

    listing = client.get(f"/admin/api/events/{event_id}/green/expo_it/facts")
    assert listing.status_code == 200
    assert listing.json()[0]["configured"] is True
    assert "value" not in listing.json()[0]
    assert PRIVATE_KEY not in listing.text


def test_fact_rejects_undeclared_traits_and_can_be_cleared(client, database):
    _, event_id = database
    base = f"/admin/api/events/{event_id}/green/expo_it/facts"
    assert client.put(f"{base}/unknown", json={"value": PRIVATE_KEY}).status_code == 422
    assert client.put(f"{base}/git.ssh_private_key", json={"value": PRIVATE_KEY}).status_code == 200
    assert client.delete(f"{base}/git.ssh_private_key").status_code == 204
    assert client.get(base).json()[0]["configured"] is False
