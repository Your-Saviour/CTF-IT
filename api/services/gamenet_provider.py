"""Concrete Vultr, WireGuard and remote-host operations for GameNet.

All provider mutations are idempotent: persisted Vultr IDs are preferred and
resources are also recovered by their deterministic labels after an interrupted
database commit.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shlex
import socket
import subprocess
import time
import secrets
import string
from ipaddress import ip_network

import httpx
import paramiko
import bcrypt
from jinja2 import Environment, FileSystemLoader
from sqlalchemy.orm import object_session

from api.models import Site, TeamVPNGateway, VM, VPNCredential
from api.services.secrets import decrypt_secret
from api.services.ssh_keys import get_or_create_platform_keypair
from builder.base_loader import load_base_type

API_ROOT = "https://api.vultr.com/v2"
POLL_SECONDS = int(os.environ.get("GAMENET_PROVIDER_POLL_SECONDS", "10"))
CREATE_TIMEOUT = int(os.environ.get("GAMENET_INSTANCE_TIMEOUT_SECONDS", "900"))
WG_INTERFACE = os.environ.get("GAMENET_WG_INTERFACE", "ctf-gamenet")
OPNSENSE_RELEASE = os.environ.get("GAMENET_OPNSENSE_RELEASE", "26.7")


class GameNetProviderError(RuntimeError):
    pass


class VultrGameNetProvider:
    def __init__(self):
        key = os.environ.get("VULTR_API_KEY")
        if not key:
            raise GameNetProviderError("VULTR_API_KEY is required")
        self.client = httpx.Client(
            base_url=API_ROOT, timeout=30.0,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            transport=httpx.HTTPTransport(retries=3),
        )

    def close(self):
        self.client.close()

    def _request(self, method: str, path: str, **kwargs) -> dict:
        response = self.client.request(method, path, **kwargs)
        if response.status_code not in {200, 201, 202, 204}:
            raise GameNetProviderError(f"Vultr {method} {path} failed ({response.status_code}): {response.text[:300]}")
        return response.json() if response.content else {}

    def create_vpc(self, site: Site) -> str:
        label = self.vpc_label(site)
        for vpc in self._request("GET", "/vpcs", params={"per_page": 500}).get("vpcs", []):
            if vpc.get("description") == label:
                self._verify_vpc(vpc, site)
                return vpc["id"]
        network = ip_network(site.allocated_cidr)
        created = self._request("POST", "/vpcs", json={
            "region": site.region, "description": label,
            "v4_subnet": str(network.network_address), "v4_subnet_mask": network.prefixlen,
        })["vpc"]
        self._verify_vpc(created, site)
        return created["id"]

    @staticmethod
    def vpc_label(site: Site) -> str:
        return f"gamenet-event-{site.event_id}-team-{site.team_id}-site-{site.key}"

    @staticmethod
    def _verify_vpc(vpc: dict, site: Site) -> None:
        actual = f"{vpc.get('v4_subnet')}/{vpc.get('v4_subnet_mask')}"
        if vpc.get("region") != site.region or actual != site.allocated_cidr:
            raise GameNetProviderError(f"existing VPC {vpc.get('id')} does not match {site.allocated_cidr} in {site.region}")

    def create_instance(self, vm: VM, *, public: bool, vpc_ids: list[str] | None = None,
                        user_data: str | None = None, image_source: dict | None = None) -> dict:
        if vm.vultr_id:
            instance = self._request("GET", f"/instances/{vm.vultr_id}").get("instance")
            if instance:
                return self._instance_with_vpc(self._wait_instance(instance["id"]), vpc_ids)
        for instance in self._request("GET", "/instances", params={"per_page": 500}).get("instances", []):
            if instance.get("label") == vm.hostname:
                vm.vultr_id = instance["id"]
                object_session(vm).commit()
                return self._instance_with_vpc(self._wait_instance(instance["id"]), vpc_ids)

        _, public_key = get_or_create_platform_keypair(object_session(vm))
        ssh_key_id = self._ensure_ssh_key("ctf-platform", public_key)
        body = {
            "region": vm.vultr_region, "plan": vm.vultr_plan,
            "label": vm.hostname, "hostname": vm.hostname,
            "sshkey_id": [ssh_key_id], "enable_ipv6": False, "backups": "disabled",
            "ddos_protection": False,
        }
        if image_source and image_source.get("snapshot_id"):
            body["snapshot_id"] = image_source["snapshot_id"]
            # Snapshot contains the platform key; Vultr's OS key injection is
            # intentionally not relied upon for custom snapshots.
            body.pop("sshkey_id", None)
        else:
            base = load_base_type(vm.base_type)
            body["os_id"] = self._resolve_os_id(base.os)
        if vpc_ids:
            body["attach_vpc"] = vpc_ids
            body["enable_vpc"] = True
        if not public:
            # Vultr's VPC-only compute flag removes both public NICs. It is not
            # equivalent to disable_public_ipv4, which can still expose IPv6.
            body["vpc_only"] = True
        if user_data:
            body["user_data"] = base64.b64encode(user_data.encode()).decode()
        instance = self._request("POST", "/instances", json=body)["instance"]
        vm.vultr_id = instance["id"]
        object_session(vm).commit()
        result = self._wait_instance(instance["id"])
        return self._instance_with_vpc(result, vpc_ids)

    def _instance_with_vpc(self, result: dict, vpc_ids: list[str] | None) -> dict:
        if vpc_ids:
            attached = self._request("GET", f"/instances/{result['id']}/vpcs", params={"per_page": 100}).get("vpcs", [])
            selected = next((row for row in attached if row.get("id") in vpc_ids), None)
            if not selected or not selected.get("ip_address"):
                raise GameNetProviderError(f"instance {result['id']} did not attach to the requested VPC")
            result["internal_ip"] = selected["ip_address"]
        return result

    def _resolve_os_id(self, name: str) -> int:
        for os_row in self._request("GET", "/os", params={"per_page": 500}).get("os", []):
            if os_row.get("name", "").casefold() == name.casefold():
                return int(os_row["id"])
        raise GameNetProviderError(f"Vultr OS is unavailable: {name}")

    def _ensure_ssh_key(self, name: str, key: str) -> str:
        def material(value: str) -> str:
            # Comments are not key material and Vultr may preserve or omit them.
            parts = value.strip().split()
            return " ".join(parts[:2])

        expected = material(key)
        rows = self._request("GET", "/ssh-keys", params={"per_page": 500}).get("ssh_keys", [])
        matching = next((row for row in rows if material(row.get("ssh_key", "")) == expected), None)
        if matching:
            return matching["id"]

        requested_name = name
        if any(row.get("name") == requested_name for row in rows):
            fingerprint = hashlib.sha256(expected.encode()).hexdigest()[:12]
            requested_name = f"{name}-{fingerprint}"
            existing = next((row for row in rows if row.get("name") == requested_name), None)
            if existing:
                raise GameNetProviderError(
                    f"Vultr SSH key name '{requested_name}' exists with different material"
                )
        return self._request("POST", "/ssh-keys", json={
            "name": requested_name, "ssh_key": key,
        })["ssh_key"]["id"]

    def _wait_instance(self, instance_id: str) -> dict:
        deadline = time.monotonic() + CREATE_TIMEOUT
        while time.monotonic() < deadline:
            instance = self._request("GET", f"/instances/{instance_id}")["instance"]
            if instance.get("status") == "active" and instance.get("server_status") in {"ok", "none"}:
                return instance
            if instance.get("status") in {"resizing", "reinstalling"} or instance.get("server_status") in {"installing", "locked"}:
                time.sleep(POLL_SECONDS); continue
            if instance.get("status") in {"pending", "active"}:
                time.sleep(POLL_SECONDS); continue
            raise GameNetProviderError(f"Vultr instance {instance_id} entered state {instance.get('status')}/{instance.get('server_status')}")
        raise GameNetProviderError(f"timed out waiting for Vultr instance {instance_id}")

    def create_firewall_group(self, label: str, rules: list[dict]) -> str:
        groups = self._request("GET", "/firewalls", params={"per_page": 500}).get("firewall_groups", [])
        group = next((item for item in groups if item.get("description") == label), None)
        if not group:
            group = self._request("POST", "/firewalls", json={"description": label})["firewall_group"]
        group_id = group["id"]
        existing = self._request("GET", f"/firewalls/{group_id}/rules", params={"per_page": 500}).get("firewall_rules", [])
        canonical_existing = {_canonical_rule(row) for row in existing}
        canonical_required = {_canonical_rule(row) for row in rules}
        for row in existing:
            if _canonical_rule(row) not in canonical_required:
                self._request("DELETE", f"/firewalls/{group_id}/rules/{row['id']}")
        for rule in rules:
            if _canonical_rule(rule) not in canonical_existing:
                self._request("POST", f"/firewalls/{group_id}/rules", json=rule)
        return group_id

    def attach_firewall_group(self, vm: VM, group_id: str) -> None:
        self._request("PATCH", f"/instances/{vm.vultr_id}", json={"firewall_group_id": group_id})

    def get_instance(self, vm: VM) -> dict:
        return self._request("GET", f"/instances/{vm.vultr_id}")["instance"]

    def firewall_rules(self, group_id: str) -> list[dict]:
        return self._request("GET", f"/firewalls/{group_id}/rules", params={"per_page": 500}).get("firewall_rules", [])


def _canonical_rule(rule: dict) -> tuple:
    return tuple(str(rule.get(key, "")) for key in ("ip_type", "protocol", "subnet", "subnet_size", "port", "source"))


def update_vm_addresses(vm: VM, instance: dict, *, public: bool) -> None:
    main_ip = instance.get("main_ip")
    internal_ip = instance.get("internal_ip") or instance.get("vpc_ip")
    vm.public_ip = main_ip if public and main_ip not in {None, "", "0.0.0.0"} else None
    if internal_ip:
        vm.private_ip = internal_ip


def ubuntu_cloud_init() -> str:
    return """#cloud-config
package_update: true
packages: [wireguard, iproute2, iptables, curl]
runcmd:
  - [systemctl, enable, --now, ssh]
"""


def endpoint_cloud_init(private_ip: str, cidr: str) -> str:
    """Configure a VPC-only guest before any operation that needs internet."""
    network = ip_network(cidr)
    gateway = str(network.network_address + 1)
    prefix = network.prefixlen
    return f"""#cloud-config
write_files:
  - path: /usr/local/sbin/gamenet-network.sh
    permissions: '0755'
    content: |
      #!/bin/sh
      iface=$(ip -o link show | awk -F': ' '$2 != "lo" {{print $2; exit}}')
      test -n "$iface" || exit 1
      cat > /etc/netplan/90-gamenet.yaml <<EOF
      network:
        version: 2
        ethernets:
          $iface:
            addresses: [{private_ip}/{prefix}]
            routes: [{{to: default, via: {gateway}}}]
            nameservers: {{addresses: [{gateway}]}}
      EOF
      netplan apply
bootcmd:
  - [sh, /usr/local/sbin/gamenet-network.sh]
runcmd:
  - [systemctl, enable, --now, ssh]
"""


def ssh_command(vm: VM, command: str, *, host: str | None = None, jump: VM | None = None,
                timeout: int = 60, connect_timeout: int | None = None) -> tuple[int, str, str]:
    db = object_session(vm)
    private_key, _ = get_or_create_platform_keypair(db)
    key = paramiko.Ed25519Key.from_private_key(io.StringIO(private_key))
    target_host = host or vm.public_ip or vm.private_ip or vm.ip_address
    if not target_host:
        raise GameNetProviderError(f"VM {vm.id} has no reachable address")
    jump_client = None
    sock = None
    password = decrypt_secret(vm.admin_password) if vm.admin_password else None
    if jump:
        jump_client = _connect_ssh(
            jump.public_ip or jump.ip_address, jump.ssh_user or "root", key,
            connect_timeout=connect_timeout,
        )
        channel_deadline = time.monotonic() + min(connect_timeout or CREATE_TIMEOUT, CREATE_TIMEOUT)
        last_channel_error = None
        while time.monotonic() < channel_deadline:
            try:
                sock = jump_client.get_transport().open_channel(
                    "direct-tcpip", (target_host, vm.ssh_port or 22), ("127.0.0.1", 0), timeout=15,
                )
                break
            except Exception as exc:
                last_channel_error = exc
                time.sleep(POLL_SECONDS)
        if sock is None:
            jump_client.close()
            raise GameNetProviderError(
                f"SSH jump channel did not become ready for {target_host}: {last_channel_error}"
            )
    client = _connect_ssh(
        target_host, vm.ssh_user or "root", key, password=password,
        port=vm.ssh_port or 22, sock=sock, connect_timeout=connect_timeout,
    )
    try:
        _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        code = stdout.channel.recv_exit_status()
        return code, stdout.read().decode(errors="replace"), stderr.read().decode(errors="replace")
    finally:
        client.close()
        if jump_client:
            jump_client.close()


def upload_text(vm: VM, path: str, content: str, *, host: str | None = None,
                jump: VM | None = None, mode: int = 0o600) -> None:
    encoded = base64.b64encode(content.encode()).decode()
    directory = os.path.dirname(path) or "."
    command = (
        f"mkdir -p {shlex.quote(directory)} && "
        f"echo {shlex.quote(encoded)} | base64 -d > {shlex.quote(path)} && "
        f"chmod {mode:o} {shlex.quote(path)}"
    )
    code, _, error = ssh_command(vm, command, host=host, jump=jump)
    if code:
        raise GameNetProviderError(f"failed to upload {path} to {vm.hostname}: {error[:300]}")


def _connect_ssh(host: str, user: str, key, *, password: str | None = None, port: int = 22,
                 sock=None, connect_timeout: int | None = None):
    deadline = time.monotonic() + min(connect_timeout or CREATE_TIMEOUT, CREATE_TIMEOUT)
    last_error = None
    while time.monotonic() < deadline:
        client = paramiko.SSHClient(); client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=host, port=port, username=user, pkey=key, password=password,
                allow_agent=False, look_for_keys=False, sock=sock,
                timeout=15, banner_timeout=15, auth_timeout=15,
            )
            return client
        except Exception as exc:
            client.close(); last_error = exc; time.sleep(POLL_SECONDS)
    raise GameNetProviderError(f"SSH did not become ready on {host}: {last_error}")


def install_local_wireguard(config: str, interface: str | None = None) -> None:
    """Install/update the API control-plane interface without a public listener."""
    interface = interface or WG_INTERFACE
    config_path = f"/etc/wireguard/{interface}.conf"
    os.makedirs("/etc/wireguard", exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as handle:
        handle.write(config)
    os.chmod(config_path, 0o600)
    subprocess.run(["wg-quick", "down", interface], check=False, capture_output=True)
    result = subprocess.run(["wg-quick", "up", interface], capture_output=True, text=True)
    if result.returncode:
        raise GameNetProviderError(f"control-plane WireGuard failed: {result.stderr[:300]}")


def tcp_closed(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return False
    except OSError:
        return True


def bootstrap_opnsense(site: Site, vm: VM) -> None:
    """Convert the site's FreeBSD image and install its initial private config."""
    db = object_session(vm)
    _, public_key = get_or_create_platform_keypair(db)
    if not vm.admin_password:
        password = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(24))
        from api.services.secrets import encrypt_secret
        vm.admin_password = encrypt_secret(password)
        db.flush()
    else:
        password = decrypt_secret(vm.admin_password)
    config = render_opnsense_config(site, vm, public_key, password, temporary_management=True)
    upload_text(vm, "/tmp/gamenet-config.xml", config)
    command = (
        "mkdir -p /conf /root/.ssh && cp /tmp/gamenet-config.xml /conf/config.xml && "
        f"echo {shlex.quote(public_key)} > /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys && "
        "if [ ! -x /usr/local/sbin/configctl ]; then "
        "fetch -o /tmp/opnsense-bootstrap.sh https://raw.githubusercontent.com/opnsense/update/master/src/bootstrap/opnsense-bootstrap.sh.in && "
        f"chmod 700 /tmp/opnsense-bootstrap.sh && nohup /tmp/opnsense-bootstrap.sh -r {shlex.quote(OPNSENSE_RELEASE)} -y "
        ">/var/log/opnsense-bootstrap.log 2>&1 & fi"
    )
    code, _, error = ssh_command(vm, command, timeout=90)
    if code:
        raise GameNetProviderError(f"failed to start OPNsense bootstrap: {error[:300]}")
    # The bootstrap starts asynchronously and may remain reachable briefly
    # before it reboots. Poll the actual OPNsense readiness command instead of
    # treating that pre-reboot window as a terminal failure.
    deadline = time.monotonic() + 1200
    last_detail = "bootstrap is still running"
    while time.monotonic() < deadline:
        try:
            code, output, error = ssh_command(
                vm, "test -x /usr/local/sbin/configctl && configctl service reload all", timeout=120,
                connect_timeout=120,
            )
            if code == 0:
                return
            last_detail = (error or output or f"readiness command exited {code}").strip()
        except Exception as exc:
            last_detail = str(exc)
        time.sleep(POLL_SECONDS)
    raise GameNetProviderError(f"OPNsense bootstrap did not complete: {last_detail[:300]}")


def configure_snapshot_opnsense(site: Site, vm: VM, expected_version: str) -> None:
    """Apply unique site state to an already validated OPNsense snapshot."""
    db = object_session(vm)
    _, public_key = get_or_create_platform_keypair(db)
    if not vm.admin_password:
        password = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(24))
        from api.services.secrets import encrypt_secret
        vm.admin_password = encrypt_secret(password); db.flush()
    else:
        password = decrypt_secret(vm.admin_password)
    config = render_opnsense_config(site, vm, public_key, password, temporary_management=True)
    upload_text(vm, "/conf/config.xml", config)
    command = ("test -x /usr/local/sbin/configctl && opnsense-version -v && "
               "ifconfig vtnet0 >/dev/null && ifconfig vtnet1 >/dev/null && "
               "configctl service reload all")
    code, output, error = ssh_command(vm, command, timeout=180, connect_timeout=300)
    if code or expected_version not in output:
        raise GameNetProviderError(f"snapshot OPNsense validation failed: {(error or output)[:300]}")
    host = vm.public_ip or vm.ip_address
    if host:
        transport = paramiko.Transport((host, vm.ssh_port or 22))
        try:
            transport.start_client(timeout=15)
            key = transport.get_remote_server_key()
            vm.ssh_host_key = f"{key.get_name()} {key.get_base64()}"
        finally:
            transport.close()


def render_opnsense_config(site: Site, vm: VM, public_key: str, password: str,
                           *, temporary_management: bool) -> str:
    templates = FileSystemLoader(os.path.join(os.path.dirname(__file__), "..", "..", "templates"))
    environment = Environment(loader=templates, autoescape=False)
    environment.filters["b64encode"] = lambda value: base64.b64encode(str(value).encode()).decode()
    template = environment.get_template("opnsense_config.xml.j2")
    network = ip_network(site.allocated_cidr)
    return template.render(
        opnsense_hostname=vm.hostname, opnsense_lan_ip=str(network.network_address + 1),
        opnsense_lan_subnet=network.prefixlen,
        opnsense_admin_password_hash=bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=10)).decode(),
        opnsense_ssh_pubkey=public_key, temporary_management=temporary_management,
    )


def _site_unbound_config(site: Site) -> str:
    """Build a resolver bound only to this site's private interfaces."""
    from api.services.gamenet import site_dns_zone, vm_dns_name
    db = object_session(site)
    lan_address = str(ip_network(site.allocated_cidr).network_address + 1)
    records = []
    for endpoint in db.query(VM).filter(VM.site_id == site.id, VM.role.like("%_endpoint")).all():
        name = vm_dns_name(endpoint)
        if name and endpoint.private_ip:
            records.append(f'  local-data: "{name}. 60 IN A {endpoint.private_ip}"')
    return "\n".join([
        "server:", f"  interface: {lan_address}", f"  interface: {site.tunnel_address}",
        "  interface-automatic: no", f"  access-control: {site.allocated_cidr} allow",
        "  access-control: 10.64.0.0/10 allow", "  access-control: 0.0.0.0/0 refuse",
        f'  local-zone: "{site_dns_zone(site)}." static', *records,
        "forward-zone:", '  name: "."', "  forward-addr: 1.1.1.1", "  forward-addr: 8.8.8.8", "",
    ])


def _gateway_unbound_config(gateway: TeamVPNGateway, sites: list[Site]) -> str:
    from api.services.gamenet import site_dns_zone
    lines = [
        "server:", f"  interface: {gateway.vpn_address}", "  interface-automatic: no",
        "  access-control: 10.64.0.0/10 allow", "  access-control: 0.0.0.0/0 refuse",
    ]
    for site in sites:
        lines += ["forward-zone:", f'  name: "{site_dns_zone(site)}."',
                  f"  forward-addr: {site.tunnel_address}"]
    lines += ["forward-zone:", '  name: "."', "  forward-addr: 1.1.1.1", "  forward-addr: 8.8.8.8", ""]
    return "\n".join(lines)


def configure_gateway(gateway: TeamVPNGateway, vm: VM, sites: list[Site], participants: list[VPNCredential],
                      *, management_host: str | None = None) -> None:
    private_key = decrypt_secret(gateway.private_key_encrypted)
    lines = ["[Interface]", f"Address = {gateway.vpn_address}/32", f"ListenPort = {gateway.listen_port}",
             f"PrivateKey = {private_key}", "PostUp = sysctl -w net.ipv4.ip_forward=1",
             "PostUp = iptables -A FORWARD -i %i -o %i -j ACCEPT",
             "PostDown = iptables -D FORWARD -i %i -o %i -j ACCEPT", ""]
    for source in sites:
        for destination in sites:
            if source.id == destination.id:
                continue
            lines.insert(7, f"PostDown = iptables -D FORWARD -s {source.allocated_cidr} -d {destination.allocated_cidr} -j DROP")
            lines.insert(6, f"PostUp = iptables -I FORWARD 1 -s {source.allocated_cidr} -d {destination.allocated_cidr} -j DROP")
    for site in sites:
        lines += ["[Peer]", f"PublicKey = {site.tunnel_public_key}",
                  f"AllowedIPs = {site.allocated_cidr},{site.tunnel_address}/32", "PersistentKeepalive = 25", ""]
    if gateway.platform_public_key and gateway.platform_address:
        lines += ["[Peer]", f"PublicKey = {gateway.platform_public_key}",
                  f"AllowedIPs = {gateway.platform_address}/32", ""]
    for credential in participants:
        lines += ["[Peer]", f"PublicKey = {credential.public_key}",
                  f"AllowedIPs = {credential.address}/32", ""]
    upload_text(vm, "/etc/wireguard/gamenet.conf", "\n".join(lines), host=management_host)
    upload_text(vm, "/etc/unbound/unbound.conf.d/gamenet.conf", _gateway_unbound_config(gateway, sites),
                host=management_host)
    command = (
        "apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y wireguard iptables unbound && "
        "systemctl enable wg-quick@gamenet && "
        "if wg show gamenet >/dev/null 2>&1; then wg-quick strip gamenet >/tmp/gamenet.stripped && wg syncconf gamenet /tmp/gamenet.stripped; "
        "else systemctl start wg-quick@gamenet; fi && systemctl enable --now unbound && systemctl restart unbound"
    )
    code, _, error = ssh_command(vm, command, host=management_host, timeout=300)
    if code:
        raise GameNetProviderError(f"gateway WireGuard configuration failed: {error[:300]}")


def configure_site_wireguard(site: Site, firewall: VM, gateway: TeamVPNGateway, gateway_vm: VM,
                              all_team_sites: list[Site]) -> None:
    blocked = [other.allocated_cidr for other in all_team_sites if other.id != site.id]
    config = "\n".join([
        "[Interface]", f"PrivateKey = {decrypt_secret(site.tunnel_private_key_encrypted)}", "",
        "[Peer]", f"PublicKey = {gateway.public_key}",
        f"Endpoint = {gateway_vm.public_ip}:{gateway.listen_port}",
        f"AllowedIPs = 10.64.0.0/10", "PersistentKeepalive = 25", "",
    ])
    upload_text(firewall, "/usr/local/etc/wireguard/wg0.conf", config)
    block_rules = "\n".join(f"block quick from {site.allocated_cidr} to {cidr}" for cidr in blocked)
    pf_rules = "\n".join([
        block_rules,
        f"pass quick on wg0 proto {{ udp tcp }} from 10.64.0.0/10 to {site.tunnel_address} port 53 keep state",
        f"pass quick on wg0 from 10.64.0.0/10 to {site.allocated_cidr} keep state",
        f"pass quick from {site.allocated_cidr} to any keep state",
    ])
    upload_text(firewall, "/usr/local/etc/gamenet.pf", pf_rules)
    upload_text(firewall, "/usr/local/etc/unbound.opnsense.d/gamenet.conf", _site_unbound_config(site))
    startup = """#!/bin/sh
# PROVIDE: gamenet
# REQUIRE: NETWORKING
. /etc/rc.subr
name=gamenet
start_cmd=gamenet_start
stop_cmd=gamenet_stop
gamenet_start() {
    /sbin/ifconfig wg0 >/dev/null 2>&1 || /sbin/ifconfig wg create name wg0
    /usr/bin/wg setconf wg0 /usr/local/etc/wireguard/wg0.conf || return 1
    /sbin/ifconfig wg0 inet GAMENET_TUNNEL_ADDRESS/32 alias
    /sbin/ifconfig wg0 up
    /sbin/route add -net 10.64.0.0/10 -interface wg0 >/dev/null 2>&1 || true
    /sbin/pfctl -a gamenet -f /usr/local/etc/gamenet.pf
}
gamenet_stop() {
    /sbin/route delete -net 10.64.0.0/10 >/dev/null 2>&1 || true
    /sbin/ifconfig wg0 destroy >/dev/null 2>&1 || true
}
load_rc_config $name
run_rc_command "$1"
""".replace("GAMENET_TUNNEL_ADDRESS", site.tunnel_address)
    upload_text(firewall, "/usr/local/etc/rc.d/gamenet", startup, mode=0o755)
    # OPNsense root uses csh; run shell redirection and boolean operators under
    # POSIX sh to avoid csh's "Ambiguous output redirect" parsing.
    site_command = (
        "test -x /usr/bin/wg && "
        "(service gamenet stop >/dev/null 2>&1 || true) && service gamenet start && configctl unbound restart"
    )
    command = f"sh -c {shlex.quote(site_command)}"
    code, _, error = ssh_command(firewall, command, timeout=300)
    if code:
        raise GameNetProviderError(f"site WireGuard configuration failed: {error[:300]}")


def configure_endpoint_network(vm: VM, site: Site, firewall: VM) -> None:
    network = ip_network(site.allocated_cidr)
    command = (
        "iface=$(ip -o -4 addr show | awk '$4 ~ /^10\\./ {print $2; exit}'); "
        "test -n \"$iface\" || iface=$(ip route show default | awk '{print $5; exit}'); "
        f"printf 'network:\n  version: 2\n  ethernets:\n    %s:\n      addresses: [{vm.private_ip}/{network.prefixlen}]\n      routes: [{{to: default, via: {str(network.network_address + 1)}}}]\n      nameservers: {{addresses: [{str(network.network_address + 1)}]}}\n' \"$iface\" > /etc/netplan/90-gamenet.yaml; "
        "nohup sh -c 'sleep 2; netplan apply' >/var/log/gamenet-netplan.log 2>&1 &"
    )
    code, _, error = ssh_command(vm, command, host=vm.private_ip, jump=firewall)
    if code:
        raise GameNetProviderError(f"endpoint private network configuration failed: {error[:300]}")
    code, _, error = ssh_command(vm, "true", host=vm.private_ip, jump=firewall, timeout=CREATE_TIMEOUT)
    if code:
        raise GameNetProviderError(f"endpoint did not return on its assigned private address: {error[:300]}")
