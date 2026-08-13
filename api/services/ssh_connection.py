"""SSH connection helper with trust-on-first-use host-key pinning per VM."""

import io
import socket

import paramiko
from sqlalchemy.orm import Session

from api.models import VM
from api.services.ssh_keys import get_or_create_platform_keypair


def _key_text(key: paramiko.PKey) -> str:
    return f"{key.get_name()} {key.get_base64()}"


def read_remote_host_key(vm: VM, db: Session | None = None) -> str:
    jump_client = None
    try:
        sock = socket.create_connection((vm.ip_address, vm.ssh_port or 22), timeout=10)
    except OSError:
        if not db or not isinstance(vm.site_id, int):
            raise
        from api.models import Site
        site = db.query(Site).filter_by(id=vm.site_id).first()
        jump = db.query(VM).filter_by(id=site.firewall_vm_id).first() if site else None
        if not jump or not jump.public_ip:
            raise
        private_key_pem, _ = get_or_create_platform_keypair(db)
        pkey = paramiko.Ed25519Key.from_private_key(io.StringIO(private_key_pem))
        jump_client = paramiko.SSHClient()
        jump_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        jump_client.connect(jump.public_ip, username=jump.ssh_user or "root", pkey=pkey, timeout=10)
        sock = jump_client.get_transport().open_channel(
            "direct-tcpip", (vm.ip_address, vm.ssh_port or 22), ("127.0.0.1", 0)
        )
    transport = paramiko.Transport(sock)
    try:
        transport.start_client(timeout=10)
        key = transport.get_remote_server_key()
        return _key_text(key)
    finally:
        transport.close()
        if jump_client:
            jump_client.close()


class _PinnedHostKeyPolicy(paramiko.MissingHostKeyPolicy):
    def __init__(self, expected: str):
        self.expected = expected

    def missing_host_key(self, client, hostname, key):
        if _key_text(key) != self.expected:
            raise paramiko.SSHException(f"SSH host key mismatch for {hostname}")


def connect_vm(vm: VM, db: Session) -> paramiko.SSHClient:
    """Connect after pinning the first observed key and rejecting later changes."""
    observed = read_remote_host_key(vm, db)
    if vm.ssh_host_key and vm.ssh_host_key != observed:
        raise paramiko.SSHException(f"SSH host key changed for {vm.hostname or vm.ip_address}")
    if not vm.ssh_host_key:
        vm.ssh_host_key = observed
        db.commit()

    private_key_pem, _ = get_or_create_platform_keypair(db)
    pkey = paramiko.Ed25519Key.from_private_key(io.StringIO(private_key_pem))
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(_PinnedHostKeyPolicy(vm.ssh_host_key))
    proxy_sock = None
    jump_client = None
    try:
        socket.create_connection((vm.ip_address, vm.ssh_port or 22), timeout=3).close()
    except OSError:
        if isinstance(vm.site_id, int):
            from api.models import Site
            site = db.query(Site).filter_by(id=vm.site_id).first()
            jump = db.query(VM).filter_by(id=site.firewall_vm_id).first() if site else None
            if jump and jump.public_ip:
                jump_client = paramiko.SSHClient()
                jump_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                jump_client.connect(jump.public_ip, username=jump.ssh_user or "root", pkey=pkey, timeout=10)
                proxy_sock = jump_client.get_transport().open_channel(
                    "direct-tcpip", (vm.ip_address, vm.ssh_port or 22), ("127.0.0.1", 0)
                )
    client.connect(
        hostname=vm.ip_address,
        port=vm.ssh_port or 22,
        username=vm.ssh_user or "root",
        pkey=pkey,
        timeout=10,
        banner_timeout=10,
        auth_timeout=10,
        sock=proxy_sock,
    )
    client._gamenet_jump_client = jump_client
    return client
