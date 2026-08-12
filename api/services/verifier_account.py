"""Provision and connect the VM-local checker through the protected ``gt`` account."""

from __future__ import annotations

import io
import shlex
from pathlib import Path

import paramiko
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
from sqlalchemy.orm import Session

from api.models import PlatformSettings, VM
from api.services.secrets import decrypt_secret, encrypt_secret
from api.services.ssh_connection import _PinnedHostKeyPolicy, read_remote_host_key

PRIVATE_SETTING = "verifier_private_key"
PUBLIC_SETTING = "verifier_public_key"
VERIFIER_USERNAME = "gt"


def _set_provisioning_marker(db: Session, vm_id: int, value: str) -> None:
    marker = db.query(PlatformSettings).filter_by(key=f"verifier_vm_{vm_id}").first()
    if marker:
        marker.value = value
    else:
        db.add(PlatformSettings(key=f"verifier_vm_{vm_id}", value=value))
    db.commit()


def mark_verifier_tampered(db: Session, vm_id: int) -> None:
    _set_provisioning_marker(db, vm_id, "tampered")


def scoring_enabled_vm_ids(db: Session, vm_ids: list[int] | set[int]) -> set[int]:
    """Only explicitly provisioned, non-tampered VMs contribute score."""
    ids = set(vm_ids)
    if not ids:
        return set()
    rows = db.query(PlatformSettings).filter(
        PlatformSettings.key.in_([f"verifier_vm_{vm_id}" for vm_id in ids])
    ).all()
    disabled = {int(row.key.removeprefix("verifier_vm_")) for row in rows
                if row.value in {"tampered", "failed"}}
    return ids - disabled


def get_or_create_verifier_keypair(db: Session) -> tuple[str, str]:
    private = db.query(PlatformSettings).filter_by(key=PRIVATE_SETTING).first()
    public = db.query(PlatformSettings).filter_by(key=PUBLIC_SETTING).first()
    if private and public:
        return decrypt_secret(private.value), public.value
    key = Ed25519PrivateKey.generate()
    private_value = key.private_bytes(Encoding.PEM, PrivateFormat.OpenSSH, NoEncryption()).decode()
    public_value = key.public_key().public_bytes(Encoding.OpenSSH, PublicFormat.OpenSSH).decode().strip()
    db.add(PlatformSettings(key=PRIVATE_SETTING, value=encrypt_secret(private_value)))
    db.add(PlatformSettings(key=PUBLIC_SETTING, value=public_value))
    db.commit()
    return private_value, public_value


def connect_verifier(vm: VM, db: Session) -> paramiko.SSHClient:
    observed = read_remote_host_key(vm)
    if vm.ssh_host_key and vm.ssh_host_key != observed:
        raise paramiko.SSHException("SSH host key mismatch")
    if not vm.ssh_host_key:
        vm.ssh_host_key = observed
        db.commit()
    private, _ = get_or_create_verifier_keypair(db)
    key = paramiko.Ed25519Key.from_private_key(io.StringIO(private))
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(_PinnedHostKeyPolicy(vm.ssh_host_key))
    client.connect(hostname=vm.ip_address, port=vm.ssh_port or 22, username=VERIFIER_USERNAME,
                   pkey=key, timeout=10, banner_timeout=10, auth_timeout=10,
                   allow_agent=False, look_for_keys=False)
    return client


def provision_verifier(vm: VM, db: Session) -> bool:
    """Install the checker in the VM and seal the gt account's expected state."""
    from api.services.ssh_connection import connect_vm
    client = None
    try:
        _, public = get_or_create_verifier_keypair(db)
        client = connect_vm(vm, db)
        gateway = Path(__file__).resolve().parent.parent.parent / "templates" / "audit_gateway.py"
        sftp = client.open_sftp()
        sftp.put(str(gateway), "/tmp/ctf-audit-gateway")
        sealer = Path(__file__).resolve().parent.parent.parent / "templates" / "gt_seal.py"
        sftp.put(str(sealer), "/tmp/ctf-gt-seal")
        sftp.close()
        restriction = (
            'command="/usr/bin/sudo -n /usr/local/sbin/ctf-audit-gateway",no-agent-forwarding,'
            'no-port-forwarding,no-X11-forwarding,no-pty '
        ) + public + " gt-checker"
        quoted_restriction = shlex.quote(restriction)
        command = (
            "set -eu; "
            f"getent group {VERIFIER_USERNAME} >/dev/null 2>&1 || groupadd -r {VERIFIER_USERNAME}; "
            f"id -u {VERIFIER_USERNAME} >/dev/null 2>&1 || "
            f"useradd -r -m -g {VERIFIER_USERNAME} -s /bin/bash {VERIFIER_USERNAME}; "
            f"test \"$(id -u {VERIFIER_USERNAME})\" -ne 0; "
            f"usermod -g {VERIFIER_USERNAME} -G '' -d /home/{VERIFIER_USERNAME} -s /bin/bash "
            f"-c 'Green Team scoring account' {VERIFIER_USERNAME}; "
            f"gpasswd -M '' {VERIFIER_USERNAME} >/dev/null; "
            f"passwd -l {VERIFIER_USERNAME} >/dev/null 2>&1 || true; "
            "install -o root -g root -m 0755 /tmp/ctf-audit-gateway /usr/local/sbin/ctf-audit-gateway; "
            f"install -d -o {VERIFIER_USERNAME} -g {VERIFIER_USERNAME} -m 0750 /home/{VERIFIER_USERNAME}; "
            f"install -d -o {VERIFIER_USERNAME} -g {VERIFIER_USERNAME} -m 0700 /home/{VERIFIER_USERNAME}/.ssh; "
            f"printf '%s\\n' {quoted_restriction} > /home/{VERIFIER_USERNAME}/.ssh/authorized_keys; "
            f"chown {VERIFIER_USERNAME}:{VERIFIER_USERNAME} /home/{VERIFIER_USERNAME}/.ssh/authorized_keys; "
            f"chmod 0600 /home/{VERIFIER_USERNAME}/.ssh/authorized_keys; "
            f"printf '%s\\n' 'Defaults:{VERIFIER_USERNAME} env_keep += \"SSH_ORIGINAL_COMMAND\"' "
            f"'{VERIFIER_USERNAME} ALL=(root) NOPASSWD: /usr/local/sbin/ctf-audit-gateway' "
            f"> /etc/sudoers.d/{VERIFIER_USERNAME}; chmod 0440 /etc/sudoers.d/{VERIFIER_USERNAME}; "
            f"visudo -cf /etc/sudoers.d/{VERIFIER_USERNAME}; "
            "install -d -o root -g root -m 0700 /etc/ctf; "
            f"GT_AUTHORIZED_KEY={quoted_restriction} /usr/bin/python3 /tmp/ctf-gt-seal; "
            "rm -f /etc/sudoers.d/ctf-verifier; "
            "if id -u ctf-verifier >/dev/null 2>&1; then "
            "userdel -r ctf-verifier >/dev/null 2>&1 || true; fi; "
            "rm -f /tmp/ctf-audit-gateway /tmp/ctf-gt-seal"
        )
        _, stdout, _ = client.exec_command(command, timeout=20)
        ok = stdout.channel.recv_exit_status() == 0
        _set_provisioning_marker(db, vm.id, "provisioned" if ok else "failed")
        return ok
    except Exception:
        try:
            _set_provisioning_marker(db, vm.id, "failed")
        except Exception:
            db.rollback()
        return False
    finally:
        if client:
            client.close()
