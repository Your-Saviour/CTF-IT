"""Encrypted team training credentials and all-or-nothing rotation."""

from __future__ import annotations

import json
import secrets
import shlex
import string

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
from sqlalchemy.orm import Session

from api.models import Team, TeamTrainingCredential, VM, utcnow
from api.services.secrets import decrypt_secret, encrypt_secret

TRAINING_USERNAME = "ctf-trainee"
VERIFIER_USERNAME = "ctf-verifier"


def _keypair() -> tuple[str, str]:
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(Encoding.PEM, PrivateFormat.OpenSSH, NoEncryption()).decode()
    public = key.public_key().public_bytes(Encoding.OpenSSH, PublicFormat.OpenSSH).decode().strip()
    return private, public


def _password() -> str:
    alphabet = string.ascii_letters + string.digits + "-_.!@%+"
    return "".join(secrets.choice(alphabet) for _ in range(28))


def generate_credential(team: Team) -> TeamTrainingCredential:
    private, public = _keypair()
    return TeamTrainingCredential(
        team_id=team.id,
        username=TRAINING_USERNAME,
        private_key_encrypted=encrypt_secret(private),
        public_key=public,
        sudo_password_encrypted=encrypt_secret(_password()),
        status="pending",
        provisioned_vm_ids_json="[]",
    )


def _deploy(vm: VM, db: Session, credential: TeamTrainingCredential) -> bool:
    from api.services.ssh_connection import connect_vm
    client = None
    try:
        client = connect_vm(vm, db)
        public = shlex.quote(credential.public_key + " ctf-team-training")
        command = (
            f"id -u {TRAINING_USERNAME} >/dev/null 2>&1 || useradd -m -s /bin/bash {TRAINING_USERNAME}; "
            f"install -d -m 700 -o {TRAINING_USERNAME} -g {TRAINING_USERNAME} /home/{TRAINING_USERNAME}/.ssh; "
            f"printf '%s\\n' {public} > /home/{TRAINING_USERNAME}/.ssh/authorized_keys; "
            f"chown {TRAINING_USERNAME}:{TRAINING_USERNAME} /home/{TRAINING_USERNAME}/.ssh/authorized_keys; "
            f"chmod 600 /home/{TRAINING_USERNAME}/.ssh/authorized_keys; "
            f"printf '%s ALL=(ALL:ALL) ALL\\n' {TRAINING_USERNAME} > /etc/sudoers.d/{TRAINING_USERNAME}; "
            f"chmod 440 /etc/sudoers.d/{TRAINING_USERNAME}; chpasswd"
        )
        stdin, stdout, _ = client.exec_command(command, timeout=20)
        stdin.write(f"{TRAINING_USERNAME}:{decrypt_secret(credential.sudo_password_encrypted)}\n")
        stdin.channel.shutdown_write()
        return stdout.channel.recv_exit_status() == 0
    except Exception:
        return False
    finally:
        if client:
            client.close()


def provision_team_credential(db: Session, credential: TeamTrainingCredential) -> dict:
    vms = db.query(VM).filter(VM.team_id == credential.team_id, VM.status == "active").all()
    succeeded, failed = [], []
    for vm in vms:
        (succeeded if _deploy(vm, db, credential) else failed).append(vm.id)
    credential.provisioned_vm_ids_json = json.dumps(succeeded)
    credential.status = "active" if not failed else "partial"
    credential.last_error_code = None if not failed else "vm_provision_failed"
    db.commit()
    return {"succeeded_vm_ids": succeeded, "failed_vm_ids": failed}


def rotate_team_credential(db: Session, team: Team) -> tuple[TeamTrainingCredential, dict]:
    """Deploy candidate everywhere before atomically replacing the stored secret."""
    candidate = generate_credential(team)
    active_vms = db.query(VM).filter(VM.team_id == team.id, VM.status == "active").all()
    succeeded, failed = [], []
    for vm in active_vms:
        (succeeded if _deploy(vm, db, candidate) else failed).append(vm.id)
    if failed:
        rollback_failed = []
        if team.training_credential:
            for vm in active_vms:
                if vm.id in succeeded and not _deploy(vm, db, team.training_credential):
                    rollback_failed.append(vm.id)
        return team.training_credential, {"rotated": False, "succeeded_vm_ids": succeeded,
                                          "failed_vm_ids": failed, "rollback_failed_vm_ids": rollback_failed}
    current = team.training_credential
    if current is None:
        current = candidate
        db.add(current)
    else:
        current.private_key_encrypted = candidate.private_key_encrypted
        current.public_key = candidate.public_key
        current.sudo_password_encrypted = candidate.sudo_password_encrypted
        current.rotated_at = utcnow()
    current.status = "active"
    current.provisioned_vm_ids_json = json.dumps(succeeded)
    current.last_error_code = None
    db.commit()
    return current, {"rotated": True, "succeeded_vm_ids": succeeded, "failed_vm_ids": [],
                     "rollback_failed_vm_ids": []}
