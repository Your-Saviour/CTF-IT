import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import bcrypt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, StaticPool
from sqlalchemy.orm import sessionmaker

from api.database import Base, get_db
from api.main import app
from api.models import Event, HintReveal, PlatformSettings, Team, TeamTrainingCredential, User, VerificationAttempt, VM, VMModule
from api.routes import auth
from api.services.secrets import decrypt_secret, encrypt_secret
from api.services.verification import InvalidSpecification, VerificationResult, capture_baseline, validate_spec, verify_assignment, verify_spec
from api.services.training_credentials import generate_credential, rotate_team_credential
from api.services.training_provisioning import finalize_training_vm
from api.services.verifier_account import provision_verifier
from builder.catalogue_validation import validate_catalogue
from builder.module_loader import load_all_modules
from builder.preset_loader import load_presets, validate_presets
from builder.selector import select_modules


@pytest.fixture
def training_app(monkeypatch):
    monkeypatch.setenv("LEARNER_TRAINING_ENABLED", "true")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)

    def override():
        db = sessions()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    db = sessions()
    event = Event(name="Training Event", quota="{}", status="open")
    db.add(event); db.flush()
    alpha = Team(name="Alpha", event_id=event.id)
    bravo = Team(name="Bravo", event_id=event.id)
    db.add_all([alpha, bravo]); db.flush()
    participant = User(username="learner", password_hash=bcrypt.hashpw(b"secure-password", bcrypt.gensalt()).decode(),
                       event_id=event.id, team_id=alpha.id)
    outsider = User(username="other", password_hash=participant.password_hash, event_id=event.id, team_id=bravo.id)
    admin = User(username="admin", password_hash=participant.password_hash, event_id=event.id, is_admin=True)
    db.add_all([participant, outsider, admin]); db.flush()
    alpha_vm = VM(hostname="alpha-target", ip_address="192.0.2.10", status="active", team_id=alpha.id,
                  event_id=event.id, ssh_port=22)
    bravo_vm = VM(hostname="bravo-target", ip_address="192.0.2.11", status="active", team_id=bravo.id,
                  event_id=event.id, ssh_port=22)
    db.add_all([alpha_vm, bravo_vm]); db.flush()
    alpha_module = VMModule(vm_id=alpha_vm.id, module_id="disable_ssh_root_login", module_type="hardening",
                            difficulty="medium", points=200, stage="preapplied")
    bravo_module = VMModule(vm_id=bravo_vm.id, module_id="disable_ssh_root_login", module_type="hardening",
                            difficulty="medium", points=200, stage="preapplied")
    hidden = VMModule(vm_id=alpha_vm.id, module_id="weak_ssh_credentials", module_type="vulnerability",
                      difficulty="easy", points=999, stage="caldera")
    infrastructure = VMModule(vm_id=alpha_vm.id, module_id="inventory_dashboard", module_type="application_external",
                              difficulty="medium", points=999, stage=None)
    db.add_all([alpha_module, bravo_module, hidden, infrastructure])
    db.add(TeamTrainingCredential(team_id=alpha.id, username="ctf-trainee",
        private_key_encrypted=encrypt_secret("PRIVATE"), public_key="ssh-ed25519 PUBLIC",
        sudo_password_encrypted=encrypt_secret("SUDO-PASSWORD"), status="active"))
    db.commit()
    client = TestClient(app)
    client.cookies.set("session", auth.serializer.dumps({"user_id": participant.id, "session_version": participant.session_version}))
    yield client, sessions, {"event": event, "alpha": alpha, "bravo": bravo, "participant": participant,
                             "admin": admin, "alpha_vm": alpha_vm, "bravo_vm": bravo_vm,
                             "alpha_module": alpha_module, "bravo_module": bravo_module}
    db.close()
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_training_payload_and_scoreboard_are_team_and_event_safe(training_app):
    client, _, data = training_app
    response = client.get("/api/me/training")
    assert response.status_code == 200
    payload = response.json()
    assert payload["team"] == {"id": data["alpha"].id, "name": "Alpha"}
    assert [vm["id"] for vm in payload["vms"]] == [data["alpha_vm"].id]
    assert [module["id"] for module in payload["vms"][0]["modules"]] == ["disable_ssh_root_login"]
    assert payload["score"]["assigned"] == 1
    assert "verification" not in json.dumps(payload)
    assert "bravo-target" not in json.dumps(payload)

    board = client.get("/api/scoreboard").json()
    assert board["current_team_id"] == data["alpha"].id
    assert all("ip" not in json.dumps(row).lower() and "password" not in json.dumps(row).lower()
               for row in board["teams"])


def test_tampered_gt_removes_existing_vm_points_from_scoreboard(training_app):
    client, sessions, data = training_app
    db = sessions()
    assignment = db.get(VMModule, data["alpha_module"].id)
    assignment.status = "completed"
    assignment.completed = True
    db.add(PlatformSettings(key=f"verifier_vm_{assignment.vm_id}", value="tampered"))
    db.commit()

    payload = client.get("/api/me/training").json()
    board = client.get("/api/scoreboard").json()
    alpha = next(row for row in board["teams"] if row["team_id"] == data["alpha"].id)
    assert payload["score"]["total"] == 0
    assert payload["score"]["completed"] == 0
    assert alpha["total_score"] == 0
    assignment.status = "pending"
    assignment.completed = False
    db.query(PlatformSettings).filter_by(key=f"verifier_vm_{assignment.vm_id}").delete()
    db.commit()
    db.close()


def test_identifier_tampering_cannot_cross_team(training_app):
    client, _, data = training_app
    vm_id = data["bravo_vm"].id
    module = data["bravo_module"].module_id
    assert client.post(f"/api/vms/{vm_id}/modules/{module}/verify").status_code == 404
    assert client.post(f"/api/vms/{vm_id}/modules/{module}/hints/0/reveal").status_code == 404


def test_team_access_is_no_store_decrypted_and_audited(training_app):
    client, sessions, data = training_app
    response = client.get("/api/me/team-access")
    assert response.status_code == 200
    assert "no-store" in response.headers["cache-control"]
    assert response.json()["private_key"] == "PRIVATE"
    assert response.json()["sudo_password"] == "SUDO-PASSWORD"
    db = sessions()
    from api.models import AdminAudit
    assert db.query(AdminAudit).filter_by(actor_id=data["participant"].id, action="team_credential_revealed").count() == 1
    db.close()


def test_hints_are_progressive_idempotent_and_free(training_app):
    client, sessions, data = training_app
    base = f"/api/vms/{data['alpha_vm'].id}/modules/{data['alpha_module'].module_id}/hints"
    assert client.post(f"{base}/1/reveal").status_code == 409
    first = client.post(f"{base}/0/reveal")
    assert first.status_code == 200 and first.json()["points_penalty"] == 0
    assert client.post(f"{base}/0/reveal").status_code == 200
    assert client.post(f"{base}/1/reveal").status_code == 200
    db = sessions()
    assert db.query(HintReveal).filter_by(user_id=data["participant"].id).count() == 2
    db.close()


def test_stopped_event_is_read_only_but_viewable(training_app):
    client, sessions, data = training_app
    db = sessions(); event = db.get(Event, data["event"].id); event.status = "stopped"; db.commit(); db.close()
    assert client.get("/api/me/training").json()["read_only"] is True
    url = f"/api/vms/{data['alpha_vm'].id}/modules/{data['alpha_module'].module_id}"
    assert client.post(url + "/verify").status_code == 409
    assert client.post(url + "/hints/0/reveal").status_code == 409


def test_participant_invitation_requires_matching_team_when_enabled(training_app):
    client, _, data = training_app
    client.cookies.set("session", auth.serializer.dumps({"user_id": data["admin"].id,
                                                          "session_version": data["admin"].session_version}))
    assert client.post("/admin/api/invitations", json={"event_id": data["event"].id,
                                                        "role": "participant"}).status_code == 422
    created = client.post("/admin/api/invitations", json={"event_id": data["event"].id,
        "team_id": data["alpha"].id, "role": "participant"})
    assert created.status_code == 200


def test_compose_does_not_override_root_learner_training_setting():
    root = Path(__file__).resolve().parents[1]
    for compose in (root / "docker-compose.yml", root / "deploy" / "docker-compose.yml"):
        source = compose.read_text()
        assert "env_file:" in source
        assert "LEARNER_TRAINING_ENABLED=${LEARNER_TRAINING_ENABLED:-false}" not in source


def test_state_transitions_remove_and_restore_points(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'state.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine); sessions = sessionmaker(bind=engine, expire_on_commit=False)
    db = sessions(); event = Event(name="E", quota="{}", status="open"); db.add(event); db.flush()
    team = Team(name="T", event_id=event.id); db.add(team); db.flush()
    vm = VM(team_id=team.id, event_id=event.id, status="active", ip_address="192.0.2.1"); db.add(vm); db.flush()
    assignment = VMModule(vm_id=vm.id, module_id="x", module_type="hardening", difficulty="easy",
                          points=100, stage="preapplied"); db.add(assignment); db.commit()
    with patch("api.services.verification.verify_spec", new=AsyncMock(return_value=VerificationResult("pass", "ok"))):
        asyncio.run(verify_assignment(db, assignment, {"type":"file_exists","path":"/x"}, "learner"))
    assert assignment.status == "completed" and assignment.completed and assignment.first_completed_at
    first = assignment.first_completed_at
    with patch("api.services.verification.verify_spec", new=AsyncMock(return_value=VerificationResult("fail", "not fixed"))):
        asyncio.run(verify_assignment(db, assignment, {"type":"file_exists","path":"/x"}, "periodic"))
    assert assignment.status == "regressed" and not assignment.completed
    with patch("api.services.verification.verify_spec", new=AsyncMock(return_value=VerificationResult("pass", "ok"))):
        asyncio.run(verify_assignment(db, assignment, {"type":"file_exists","path":"/x"}, "periodic"))
    assert assignment.status == "completed" and assignment.first_completed_at == first
    assert db.query(VerificationAttempt).count() == 3
    db.close(); engine.dispose()


def test_concurrent_manual_and_periodic_checks_are_serialized(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'concurrent.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine); sessions = sessionmaker(bind=engine, expire_on_commit=False)
    setup = sessions(); event = Event(name="E", quota="{}", status="open"); setup.add(event); setup.flush()
    team = Team(name="T", event_id=event.id); setup.add(team); setup.flush()
    vm = VM(team_id=team.id, event_id=event.id, status="active", ip_address="192.0.2.2"); setup.add(vm); setup.flush()
    module = VMModule(vm_id=vm.id, module_id="x", module_type="hardening", difficulty="easy", points=100,
                      stage="preapplied"); setup.add(module); setup.commit(); module_id = module.id; setup.close()

    calls = 0
    async def result_sequence(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return VerificationResult("pass", "ok") if calls == 1 else VerificationResult("fail", "regressed")

    async def run_checks():
        first, second = sessions(), sessions()
        try:
            with patch("api.services.verification.verify_spec", side_effect=result_sequence):
                await asyncio.gather(
                    verify_assignment(first, first.get(VMModule, module_id), {"type":"file_exists","path":"/x"}, "learner"),
                    verify_assignment(second, second.get(VMModule, module_id), {"type":"file_exists","path":"/x"}, "periodic"),
                )
        finally:
            first.close(); second.close()
    asyncio.run(run_checks())
    check = sessions()
    assert check.get(VMModule, module_id).status == "regressed"
    assert check.query(VerificationAttempt).count() == 2
    check.close(); engine.dispose()


def test_credential_rotation_rolls_back_partial_deployment(training_app):
    _, sessions, data = training_app
    db = sessions(); team = db.get(Team, data["alpha"].id)
    original = team.training_credential.private_key_encrypted
    extra = VM(hostname="alpha-two", ip_address="192.0.2.12", status="active",
               team_id=team.id, event_id=team.event_id)
    db.add(extra); db.commit()
    with patch("api.services.training_credentials._deploy", side_effect=[True, False, True, True]) as deploy:
        credential, report = rotate_team_credential(db, team)
    assert report["rotated"] is False
    assert report["rollback_failed_vm_ids"] == []
    assert credential.private_key_encrypted == original
    assert deploy.call_count == 4
    assert {call.args[0].id for call in deploy.call_args_list[2:]} == {
        vm.id for vm in team.vms if vm.status == "active"
    }
    db.close()


def test_initial_credential_failure_revokes_candidate_everywhere(training_app):
    _, sessions, data = training_app
    db = sessions()
    team = Team(name="Unprovisioned", event_id=data["event"].id)
    db.add(team); db.flush()
    vms = [
        VM(hostname=f"new-{index}", ip_address=f"192.0.2.{20 + index}", status="active",
           team_id=team.id, event_id=team.event_id)
        for index in range(2)
    ]
    db.add_all(vms); db.commit()
    with patch("api.services.training_credentials._deploy", side_effect=[True, False]), \
         patch("api.services.training_credentials._revoke", side_effect=[True, True]) as revoke:
        credential, report = rotate_team_credential(db, team)
    assert credential is None
    assert report["rotated"] is False
    assert report["rollback_failed_vm_ids"] == []
    assert {call.args[0].id for call in revoke.call_args_list} == {vm.id for vm in vms}
    db.close()


def test_goal_only_vm_still_receives_restricted_verifier(training_app):
    _, sessions, data = training_app
    db = sessions()
    vm = db.get(VM, data["alpha_vm"].id)
    db.query(VMModule).filter(VMModule.vm_id == vm.id).delete()
    db.commit()
    with patch("api.services.training_provisioning.provision_verifier", return_value=True) as provision:
        report = finalize_training_vm(db, vm)
    provision.assert_called_once_with(vm, db)
    assert report == {"verifier": "provisioned", "credential": "not_applicable", "baselines": 0}
    db.close()


@pytest.mark.parametrize(("exit_status", "expected", "marker_value"), [
    (0, True, "provisioned"),
    (1, False, "failed"),
])
def test_gt_provisioning_is_fail_fast_and_updates_readiness(exit_status, expected, marker_value):
    vm = VM(id=41, ip_address="192.0.2.41", team_id=1, event_id=1)
    marker = MagicMock(value="provisioned")
    db = MagicMock()
    db.query.return_value.filter_by.return_value.first.return_value = marker
    client = MagicMock()
    stdout = MagicMock()
    stdout.channel.recv_exit_status.return_value = exit_status
    client.exec_command.return_value = (MagicMock(), stdout, MagicMock())

    with patch("api.services.verifier_account.get_or_create_verifier_keypair",
               return_value=("private", "ssh-ed25519 AAAAtest")), \
         patch("api.services.ssh_connection.connect_vm", return_value=client):
        assert provision_verifier(vm, db) is expected

    command = client.exec_command.call_args.args[0]
    assert command.startswith("set -eu; ")
    assert "/tmp/ctf-gt-seal" in command
    assert "/usr/local/sbin/ctf-audit-gateway --seal" not in command
    assert client.open_sftp.return_value.put.call_count == 2
    assert marker.value == marker_value


@pytest.mark.parametrize("spec", [
    {"type":"file_contains","path":"/etc/ssh/sshd_config","pattern":"PermitRootLogin no"},
    {"type":"file_absent","path":"/tmp/backdoor"},
    {"type":"file_permissions","path":"/etc/shadow","mode":"640"},
    {"type":"service_running","service":"ssh","expected":"active"},
    {"type":"process_state","process":"sshd","expected":"running"},
    {"type":"listening_port","port":23,"listening":False},
    {"type":"package_installed","package":"fail2ban"},
    {"type":"docker_container_not_privileged","container":"worker"},
    {"type":"ufw_default_deny"},
    {"type":"sysctl_value","key":"net.ipv4.ip_forward","expected":0},
    {"type":"sshd_effective_option","option":"permitrootlogin","expected":"no"},
    {"type":"cron_not_present","pattern":"beacon.sh"},
    {"type":"user_absent","username":"backdoor"},
    {"type":"password_hash_changed","username":"root"},
    {"type":"http_response","port":8080,"path":"/","status_code":200},
    {"type":"all_of","checks":[{"type":"file_exists","path":"/a"},{"type":"file_absent","path":"/b"}]},
    {"type":"any_of","checks":[{"type":"file_exists","path":"/a"}]},
])
def test_supported_verification_contracts_validate(spec):
    validate_spec(spec)


@pytest.mark.parametrize("spec", [
    {"type":"file_exists","path":"../../etc/shadow"},
    {"type":"service_running","service":"sshd; reboot"},
    {"type":"listening_port","port":70000},
    {"type":"http_response","port":80,"path":"/../../metadata"},
    {"type":"shell","command":"id"},
])
def test_unsafe_verification_contracts_are_rejected(spec):
    with pytest.raises(InvalidSpecification):
        validate_spec(spec)


@pytest.mark.parametrize("spec", [
    {"type":"file_contains","path":"/a","pattern":"x"},
    {"type":"file_not_contains","path":"/a","pattern":"x"},
    {"type":"file_exists","path":"/a"},
    {"type":"file_absent","path":"/a"},
    {"type":"file_permissions","path":"/a","mode":"640"},
    {"type":"service_running","service":"ssh","expected":"active"},
    {"type":"process_state","process":"sshd","expected":"running"},
    {"type":"listening_port","port":22,"listening":True},
    {"type":"package_installed","package":"openssh-server"},
    {"type":"docker_container_not_privileged","container":"worker"},
    {"type":"ufw_default_deny"},
    {"type":"sysctl_value","key":"net.ipv4.ip_forward","expected":0},
    {"type":"sshd_effective_option","option":"permitrootlogin","expected":"no"},
    {"type":"cron_not_present","pattern":"beacon"},
    {"type":"user_absent","username":"rogue"},
    {"type":"all_of","checks":[{"type":"file_exists","path":"/a"},{"type":"file_absent","path":"/b"}]},
    {"type":"any_of","checks":[{"type":"file_exists","path":"/a"}]},
])
def test_ssh_and_composite_verifiers_evaluate_pass(spec):
    async def passing(_spec):
        return 0, ""
    vm = VM(id=999, ip_address="192.0.2.20", team_id=1, event_id=1)
    result = asyncio.run(verify_spec(spec, vm, ssh_executor=passing))
    assert result.result == "pass"


def test_baseline_fingerprints_compare_without_storing_raw_values():
    vm = VM(id=999, ip_address="192.0.2.20", team_id=1, event_id=1)
    spec = {"type":"password_hash_changed","username":"root"}
    with patch("api.services.verification._ssh", new=AsyncMock(return_value=(0, "$6$raw-shadow-hash"))):
        baseline = asyncio.run(capture_baseline(vm, spec))
    assert "$6$raw-shadow-hash" not in json.dumps(baseline)
    async def changed(_spec):
        return 0, "$6$different-hash"
    assert asyncio.run(verify_spec(spec, vm, baseline, ssh_executor=changed)).passed


def test_infrastructure_failure_is_not_a_task_failure():
    async def unavailable(_spec):
        return 255, ""
    vm = VM(id=999, ip_address="192.0.2.20", team_id=1, event_id=1)
    result = asyncio.run(verify_spec({"type":"file_exists","path":"/a"}, vm,
                                     ssh_executor=unavailable))
    assert result.result == "unavailable" and result.error_code == "target_unavailable"


def test_gt_integrity_failure_disables_scoring_before_task_check():
    calls = []

    async def tampered(spec):
        calls.append(spec)
        return 3, ""

    vm = VM(id=999, ip_address="192.0.2.20", team_id=1, event_id=1)
    result = asyncio.run(verify_spec({"type":"file_exists","path":"/a"}, vm,
                                     ssh_executor=tampered))
    assert result.result == "unavailable"
    assert result.error_code == "checker_integrity_failed"
    assert calls == [{"type": "integrity"}]


def test_catalogue_and_presets_meet_release_contract():
    modules = load_all_modules()
    assert validate_catalogue(modules) == {}
    assert validate_presets({module.id for module in modules}, modules) == {}
    payload_ids = {module.id for module in modules if module.type == "payload"}
    assert {"unauthorized_ssh_key", "malicious_cron_beacon", "backdoor_bashrc",
            "inventory_unrestricted_upload", "rogue_systemd_persistence",
            "tampered_sshd_configuration"} <= payload_ids
    assert len([module for module in modules if module.type == "application_internal"]) >= 2
    assert len([module for module in modules if module.category == "containers" and module.type != "application_internal"]) >= 4
    assert len(load_presets()) >= 4
    selected = select_modules({"preset": "persistence_incident_response_hunt"}, modules)
    preset = next(item for item in load_presets() if item.id == "persistence_incident_response_hunt")
    assert set(preset.modules) <= {module.id for module in selected}
