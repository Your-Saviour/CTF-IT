"""Provision and connect the restricted ctf-verifier control account."""

from __future__ import annotations

import io
import os
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
    client.connect(hostname=vm.ip_address, port=vm.ssh_port or 22, username="ctf-verifier",
                   pkey=key, timeout=10, banner_timeout=10, auth_timeout=10,
                   allow_agent=False, look_for_keys=False)
    return client


def provision_verifier(vm: VM, db: Session) -> bool:
    from api.services.ssh_connection import connect_vm
    client = None
    try:
        _, public = get_or_create_verifier_keypair(db)
        client = connect_vm(vm, db)
        gateway = Path(__file__).resolve().parent.parent.parent / "templates" / "audit_gateway.py"
        sftp = client.open_sftp()
        sftp.put(str(gateway), "/tmp/ctf-audit-gateway")
        sftp.close()
        restriction = (
            'command="/usr/bin/sudo -n /usr/local/sbin/ctf-audit-gateway",no-agent-forwarding,'
            'no-port-forwarding,no-X11-forwarding,no-pty '
        ) + public + " ctf-verifier"
        command = (
            "id -u ctf-verifier >/dev/null 2>&1 || useradd -r -m -s /bin/bash ctf-verifier; "
            "install -o root -g root -m 0755 /tmp/ctf-audit-gateway /usr/local/sbin/ctf-audit-gateway; "
            "install -d -o ctf-verifier -g ctf-verifier -m 0700 /home/ctf-verifier/.ssh; "
            f"printf '%s\\n' {shlex.quote(restriction)} > /home/ctf-verifier/.ssh/authorized_keys; "
            "chown ctf-verifier:ctf-verifier /home/ctf-verifier/.ssh/authorized_keys; "
            "chmod 0600 /home/ctf-verifier/.ssh/authorized_keys; "
            "printf '%s\\n' 'Defaults:ctf-verifier env_keep += \"SSH_ORIGINAL_COMMAND\"' "
            "'ctf-verifier ALL=(root) NOPASSWD: /usr/local/sbin/ctf-audit-gateway' "
            "> /etc/sudoers.d/ctf-verifier; chmod 0440 /etc/sudoers.d/ctf-verifier; "
            "visudo -cf /etc/sudoers.d/ctf-verifier"
        )
        _, stdout, _ = client.exec_command(command, timeout=20)
        ok = stdout.channel.recv_exit_status() == 0
        if ok:
            marker = db.query(PlatformSettings).filter_by(key=f"verifier_vm_{vm.id}").first()
            if marker:
                marker.value = "provisioned"
            else:
                db.add(PlatformSettings(key=f"verifier_vm_{vm.id}", value="provisioned"))
            db.commit()
        return ok
    except Exception:
        return False
    finally:
        if client:
            client.close()
