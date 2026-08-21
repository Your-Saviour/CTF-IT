import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.database import Base
from api.integrations.base import ConnectionTestResult, SyncResult
from api.integrations.registry import adapter_keys, get_adapter, register_adapter
from api.models import Event, EventIntegration, IntegrationDestination, ServiceCredential


class FakeAdapter:
    key = "model-test"


def test_registry_resolves_one_explicit_adapter():
    register_adapter(FakeAdapter())
    assert get_adapter("model-test").key == "model-test"
    assert "model-test" in adapter_keys()
    with pytest.raises(ValueError, match="already registered"):
        register_adapter(FakeAdapter())


def test_destination_and_binding_constraints():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        credential = ServiceCredential(
            service_name="Expo", credential_type="token", password="encrypted"
        )
        event = Event(name="Exercise", quota="{}")
        db.add_all([credential, event])
        db.flush()
        destination = IntegrationDestination(
            name="Expo staging",
            adapter_key="expo_it",
            base_url="https://expo.example",
            credential_id=credential.id,
            enabled=True,
            allow_insecure_http=False,
            config_json="{}",
        )
        db.add(destination)
        db.flush()
        db.add_all([
            EventIntegration(event_id=event.id, destination_id=destination.id, enabled=False),
            EventIntegration(event_id=event.id, destination_id=destination.id, enabled=False),
        ])
        with pytest.raises(IntegrityError):
            db.commit()


def test_result_objects_are_safe_and_typed():
    assert ConnectionTestResult(True, "ok", "Connected").ok is True
    assert SyncResult(False, "timeout", "Timed out", None, True).retryable is True
