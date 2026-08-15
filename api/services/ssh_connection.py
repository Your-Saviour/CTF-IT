"""SSH connection helper with trust-on-first-use host-key pinning per VM."""

import io
import socket

import paramiko
from sqlalchemy.orm import Session

from api.models import TeamVPNGateway, VM
from api.services.ssh_keys import get_or_create_platform_keypair


def _key_text(key: paramiko.PKey) -> str:
    return f"{key.get_name()} {key.get_base64()}"


def _gateway_channel(vm: VM, db: Session):
    """Open the mandatory endpoint path through the team's public VPN gateway."""
    if not isinstance(vm.role, str) or not vm.role.endswith("_endpoint"):
        return None, None
    gateway = db.query(TeamVPNGateway).filter_by(team_id=vm.team_id).first()
    gateway_vm = db.get(VM, gateway.vm_id) if gateway and gateway.vm_id else None
    if not gateway_vm or not gateway_vm.public_ip:
        raise paramiko.SSHException("team VPN gateway is unavailable for endpoint SSH")
    private_key_pem, _ = get_or_create_platform_keypair(db)
    pkey = paramiko.Ed25519Key.from_private_key(io.StringIO(private_key_pem))
    jump_client = paramiko.SSHClient()
    jump_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    jump_client.connect(
        gateway_vm.public_ip, username=gateway_vm.ssh_user or "root", pkey=pkey,
        timeout=10, allow_agent=False, look_for_keys=False,
    )
    channel = jump_client.get_transport().open_channel(
        "direct-tcpip", (vm.ip_address, vm.ssh_port or 22), ("127.0.0.1", 0)
    )
    return channel, jump_client


def read_remote_host_key(vm: VM, db: Session | None = None) -> str:
    jump_client = None
    if db and isinstance(vm.role, str) and vm.role.endswith("_endpoint"):
        sock, jump_client = _gateway_channel(vm, db)
    else:
        sock = socket.create_connection((vm.ip_address, vm.ssh_port or 22), timeout=10)
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
    if isinstance(vm.role, str) and vm.role.endswith("_endpoint"):
        proxy_sock, jump_client = _gateway_channel(vm, db)
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
