"""Disposable-VM acceptance suite; exercised by the scheduled workflow."""

import asyncio
import io
import json
import os
from pathlib import Path

import paramiko
import pytest

from api.database import SessionLocal, init_db
from api.models import Event, Team, User, VM, VMModule
from api.services.secrets import decrypt_secret
from api.services.ssh_connection import connect_vm
from api.services.training_credentials import generate_credential, provision_team_credential, rotate_team_credential
from api.services.verification import capture_baseline, verify_assignment, verify_spec
from api.services.verifier_account import connect_verifier, provision_verifier

pytestmark = pytest.mark.skipif(not os.environ.get("NIGHTLY_VM_HOST"), reason="requires a disposable VM")


def test_disposable_vm_training_lifecycle():
    init_db()
    db = SessionLocal()
    root = None
    trainee = None
    verifier = None
    try:
        event = Event(name="Nightly disposable VM", quota="{}", status="open")
        db.add(event); db.flush()
        team = Team(name="Nightly", event_id=event.id); db.add(team); db.flush()
        user = User(username="nightly-learner", password_hash="not-used", event_id=event.id, team_id=team.id)
        db.add(user); db.flush()
        vm = VM(hostname="nightly-target", ip_address=os.environ["NIGHTLY_VM_HOST"],
                ssh_port=int(os.environ.get("NIGHTLY_VM_PORT", "22")),
                ssh_user="root", status="active", provision_step="completed", team_id=team.id, event_id=event.id)
        db.add(vm); db.flush()
        assignment = VMModule(vm_id=vm.id, module_id="malicious_cron_beacon", module_type="payload",
                              difficulty="medium", points=200, stage="preapplied")
        db.add(assignment)
        credential = generate_credential(team); db.add(credential); db.commit()

        root = connect_vm(vm, db)
        sftp = root.open_sftp()
        module_script = Path(__file__).resolve().parent.parent / "modules/vulns/malicious_cron_beacon/malicious_cron_beacon.sh"
        sftp.put(str(module_script), "/tmp/malicious_cron_beacon.sh")
        sftp.close()
        root.exec_command("chmod 0700 /tmp/malicious_cron_beacon.sh && /tmp/malicious_cron_beacon.sh")[1].channel.recv_exit_status()
        assert provision_team_credential(db, credential)["failed_vm_ids"] == []
        assert provision_verifier(vm, db)

        # The VM-local gt account rejects arbitrary commands and accepts only a
        # structured allow-listed audit request.
        verifier = connect_verifier(vm, db)
        _, stdout, _ = verifier.exec_command(json.dumps({"type": "shell", "command": "id"}))
        assert json.loads(stdout.read().decode())["status"] == 2

        # Changing the gt boundary deliberately disables scoring for the VM.
        root.exec_command("chmod 0644 /home/gt/.ssh/authorized_keys")[1].channel.recv_exit_status()
        _, stdout, _ = verifier.exec_command(json.dumps({"type": "file_exists", "path": "/etc/passwd"}))
        assert json.loads(stdout.read().decode())["status"] == 3
        assert provision_verifier(vm, db)

        spec = {"type": "cron_not_present", "pattern": "beacon.sh"}
        assert asyncio.run(verify_spec(spec, vm)).result == "fail"
        remediation = "crontab -l 2>/dev/null | grep -Fv beacon.sh | crontab -; rm -rf /opt/.hidden"
        root.exec_command(remediation)[1].channel.recv_exit_status()
        assert asyncio.run(verify_assignment(db, assignment, spec, "learner", user)).passed
        assert assignment.status == "completed"
        root.exec_command("/tmp/malicious_cron_beacon.sh")[1].channel.recv_exit_status()
        assert asyncio.run(verify_assignment(db, assignment, spec, "periodic")).result == "fail"
        assert assignment.status == "regressed"
        root.exec_command(remediation)[1].channel.recv_exit_status()
        assert asyncio.run(verify_assignment(db, assignment, spec, "periodic")).passed

        root.exec_command("install -d /var/lib/ctf-nightly; printf baseline > /var/lib/ctf-nightly/baseline")[1].channel.recv_exit_status()
        baseline_spec = {"type": "file_hash_changed", "path": "/var/lib/ctf-nightly/baseline"}
        baseline = asyncio.run(capture_baseline(vm, baseline_spec))
        root.exec_command("printf changed > /var/lib/ctf-nightly/baseline")[1].channel.recv_exit_status()
        assert asyncio.run(verify_spec(baseline_spec, vm, baseline)).passed

        private = decrypt_secret(credential.private_key_encrypted)
        key = paramiko.Ed25519Key.from_private_key(io.StringIO(private))
        trainee = paramiko.SSHClient(); trainee.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        trainee.connect(vm.ip_address, port=vm.ssh_port or 22, username="ctf-trainee", pkey=key,
                        allow_agent=False, look_for_keys=False)
        stdin, stdout, _ = trainee.exec_command("sudo -S -p '' id -u")
        stdin.write(decrypt_secret(credential.sudo_password_encrypted) + "\n"); stdin.flush()
        assert stdout.read().decode().strip() == "0"

        previous = credential.private_key_encrypted
        rotated, report = rotate_team_credential(db, team)
        assert report["rotated"] and rotated.private_key_encrypted != previous
    finally:
        for client in (verifier, trainee, root):
            if client:
                client.close()
        db.close()
