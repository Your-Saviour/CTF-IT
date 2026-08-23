from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from api.database import Base
from api.models import Event, EventIntegration, IntegrationDestination, IntegrationSyncJob, ServiceCredential, VM
from api.services.secrets import decrypt_secret, encrypt_secret


def test_expo_outputs_create_one_owned_enabled_binding(monkeypatch):
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "green-integration-test")
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        event = Event(name="Exercise", quota="{}", status="provisioning")
        db.add(event); db.flush()
        vm = VM(event_id=event.id, team_id=None, green_key="expo_it", role="green_service")
        db.add(vm); db.flush()
        from api.services.green_integrations import ensure_expo_it_integration
        outputs = {"expo_it.private_url": "https://10.64.0.20", "expo_it.api_key": "token-value"}
        first = ensure_expo_it_integration(db, event, vm, outputs)
        second = ensure_expo_it_integration(db, event, vm, outputs)

        assert first.id == second.id
        assert db.query(EventIntegration).count() == 1
        destination = db.query(IntegrationDestination).one()
        credential = db.query(ServiceCredential).one()
        assert destination.owner_green_vm_id == vm.id
        assert credential.owner_green_vm_id == vm.id
        assert decrypt_secret(credential.password) == "token-value"
        job = db.query(IntegrationSyncJob).one()
        assert job.trigger_reason == "green_deployment_completed"
        assert job.priority == 100


def test_admin_owned_expo_binding_is_preserved_and_blocks_generation(monkeypatch):
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "green-integration-conflict-test")
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        event = Event(name="Exercise", quota="{}", status="provisioning")
        db.add(event); db.flush()
        vm = VM(event_id=event.id, team_id=None, green_key="expo_it", role="green_service")
        credential = ServiceCredential(service_name="Admin Expo", credential_type="token",
                                       password=encrypt_secret("admin-token"))
        db.add_all([vm, credential]); db.flush()
        destination = IntegrationDestination(
            name="Admin Expo", adapter_key="expo_it", base_url="https://admin.invalid",
            credential_id=credential.id, enabled=True,
        )
        db.add(destination); db.flush()
        db.add(EventIntegration(event_id=event.id, destination_id=destination.id, enabled=True))
        db.commit()

        from api.services.green_integrations import ensure_expo_it_integration
        import pytest
        with pytest.raises(ValueError, match="administrator-managed"):
            ensure_expo_it_integration(db, event, vm, {
                "expo_it.private_url": "https://10.64.0.20", "expo_it.api_key": "new-token",
            })

        assert db.query(IntegrationDestination).count() == 1
