import json

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from api.database import Base
from api.models import Event, GreenDeploymentFact, GreenDeploymentState, VM
from api.services.secrets import encrypt_secret


PRIVATE_KEY = "-----BEGIN OPENSSH PRIVATE KEY-----\nsecret-material\n-----END OPENSSH PRIVATE KEY-----"
EXPO_DATA = json.dumps({
    "phases": [], "inbox": [], "scoring": [], "spot_reports": [], "ust": [],
    "collaboration_points": [], "infrastructure": {"systems": [], "credentials": []},
})


def test_executor_transports_secret_by_file_and_persists_safe_outputs(monkeypatch):
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "green-executor-test")
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    commands, uploads = [], []

    def upload(vm, path, content, **kwargs):
        uploads.append((path, content))

    def command(vm, value, **kwargs):
        commands.append(value)
        if value.startswith("curl -kfsS"):
            return 0, EXPO_DATA, ""
        if value.startswith("bash "):
            return 0, json.dumps({
                "expo_it.resolved_commit": "abc123",
                "expo_it.private_url": "https://10.64.0.20",
            }), ""
        if "expo_it.api_key" in value and value.startswith("cat "):
            return 0, "generated-api-token", ""
        return 0, "", ""

    monkeypatch.setattr("api.services.green_deployment.upload_text", upload)
    monkeypatch.setattr("api.services.green_deployment.ssh_command", command)
    with Session(engine) as db:
        event = Event(name="Exercise", quota="{}", status="provisioning")
        db.add(event); db.flush()
        vm = VM(event_id=event.id, green_key="expo_it", team_id=None, role="green_service",
                ip_address="198.51.100.10", private_ip="10.64.0.20")
        db.add(vm); db.flush()
        db.add(GreenDeploymentFact(
            event_id=event.id, vm_key="expo_it", trait="git.ssh_private_key",
            encrypted_value=encrypt_secret(PRIVATE_KEY), secret=True,
        )); db.commit()

        from api.services.green_deployment import execute_green_modules
        outputs = execute_green_modules(db, event, vm, ["expo_it"])

        assert outputs["expo_it.api_key"] == "generated-api-token"
        assert any(content == PRIVATE_KEY for _, content in uploads)
        assert PRIVATE_KEY not in " ".join(commands)
        state = db.query(GreenDeploymentState).one()
        assert state.status == "healthy"
        assert state.resolved_commit == "abc123"
        assert state.service_url == "https://10.64.0.20"


def test_healthy_state_is_rebuilt_when_health_check_fails(monkeypatch):
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "green-executor-retry-test")
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    commands, health_checks = [], 0

    monkeypatch.setattr("api.services.green_deployment.upload_text", lambda *args, **kwargs: None)

    def command(vm, value, **kwargs):
        nonlocal health_checks
        commands.append(value)
        if value.startswith("curl -kfsS"):
            health_checks += 1
            return (1, "", "connection refused") if health_checks == 1 else (0, EXPO_DATA, "")
        if value.startswith("bash -c"):
            return 0, json.dumps({
                "expo_it.resolved_commit": "new-commit",
                "expo_it.private_url": "https://10.64.0.20",
            }), ""
        if value.startswith("cat "):
            return 0, "new-api-token", ""
        return 0, "", ""

    monkeypatch.setattr("api.services.green_deployment.ssh_command", command)
    with Session(engine) as db:
        event = Event(name="Exercise", quota="{}", status="provisioning")
        db.add(event); db.flush()
        vm = VM(event_id=event.id, green_key="expo_it", team_id=None, role="green_service",
                ip_address="198.51.100.10", private_ip="10.64.0.20")
        db.add(vm); db.flush()
        db.add_all([
            GreenDeploymentFact(
                event_id=event.id, vm_key="expo_it", trait="git.ssh_private_key",
                encrypted_value=encrypt_secret(PRIVATE_KEY), secret=True,
            ),
            GreenDeploymentFact(
                event_id=event.id, vm_key="expo_it", trait="expo_it.api_key",
                encrypted_value=encrypt_secret("old-api-token"), secret=True,
            ),
            GreenDeploymentState(
                vm_id=vm.id, module_id="expo_it", status="healthy", health_status="healthy",
                service_url="https://10.64.0.20", resolved_commit="old-commit",
            ),
        ])
        db.commit()

        from api.services.green_deployment import execute_green_modules
        outputs = execute_green_modules(db, event, vm, ["expo_it"])

        assert outputs["expo_it.resolved_commit"] == "new-commit"
        assert any(value.startswith("bash -c") for value in commands)
        assert db.query(GreenDeploymentState).one().resolved_commit == "new-commit"


def test_executor_redacts_input_secret_from_persisted_failure(monkeypatch):
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "green-executor-redaction-test")
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    monkeypatch.setattr("api.services.green_deployment.upload_text", lambda *args, **kwargs: None)

    def command(vm, value, **kwargs):
        if value.startswith("bash -c"):
            return 1, "", f"git rejected key {PRIVATE_KEY}"
        return 0, "", ""

    monkeypatch.setattr("api.services.green_deployment.ssh_command", command)
    with Session(engine) as db:
        event = Event(name="Exercise", quota="{}", status="provisioning")
        db.add(event); db.flush()
        vm = VM(event_id=event.id, green_key="expo_it", team_id=None, role="green_service",
                ip_address="198.51.100.10", private_ip="10.64.0.20")
        db.add(vm); db.flush()
        db.add(GreenDeploymentFact(
            event_id=event.id, vm_key="expo_it", trait="git.ssh_private_key",
            encrypted_value=encrypt_secret(PRIVATE_KEY), secret=True,
        )); db.commit()

        from api.services.green_deployment import GreenDeploymentError, execute_green_modules
        import pytest
        with pytest.raises(GreenDeploymentError):
            execute_green_modules(db, event, vm, ["expo_it"])

        error = db.query(GreenDeploymentState).one().error_message
        assert PRIVATE_KEY not in error
        assert "[REDACTED]" in error
