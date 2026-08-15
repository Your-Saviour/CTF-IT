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

from dataclasses import replace

from api.services.aws import InstanceSpec, NetworkInterfaceSpec, ownership_tags


class AwsGameNetProvider:
    """Map the existing appliance-based GameNet to EC2 and VPC primitives."""

    def __init__(self, compute, network, config):
        self.compute = compute
        self.network = network
        self.config = config

    def _tags(self, site, vm):
        return ownership_tags(
            self.config.environment,
            event_id=site.event_id,
            team_id=site.team_id,
            site_id=site.id,
            vm_id=vm.id,
        )

    def create_firewall(self, site, vm, *, ami_id: str):
        tags = self._tags(site, vm)
        wan = self.network.create_eni(
            site.public_subnet_id, None, [site.wan_security_group_id], tags,
        )
        lan = self.network.create_eni(
            site.infrastructure_subnet_id, vm.private_ip, [site.lan_security_group_id], tags,
        )
        result = self.compute.launch_instance(InstanceSpec(
            ami_id=ami_id,
            instance_type=vm.instance_type or "t3.medium",
            client_token=f"ctf-it-vm-{vm.id}",
            network_interfaces=(
                NetworkInterfaceSpec(0, eni_id=wan.eni_id, delete_on_termination=False),
                NetworkInterfaceSpec(1, eni_id=lan.eni_id, delete_on_termination=False),
            ),
            tags=tags,
        ))
        self.compute.set_source_dest_check(result.instance_id, enabled=False)
        allocation = self.compute.allocate_eip(tags)
        self.compute.associate_eip(allocation.allocation_id, wan.eni_id)
        return replace(
            result,
            public_ip=allocation.public_ip,
            wan_eni_id=wan.eni_id,
            lan_eni_id=lan.eni_id,
        )

    def create_endpoint(self, site, zone, vm, *, ami_id: str):
        return self.compute.launch_instance(InstanceSpec(
            ami_id=ami_id,
            instance_type=vm.instance_type or "t3.small",
            client_token=f"ctf-it-vm-{vm.id}",
            network_interfaces=(NetworkInterfaceSpec(
                0,
                subnet_id=zone.subnet_id,
                security_group_ids=(zone.security_group_id,),
                associate_public_ip=False,
                private_ip=vm.private_ip,
            ),),
            tags=self._tags(site, vm),
        ))
import paramiko
import bcrypt
from jinja2 import Environment, FileSystemLoader
from sqlalchemy.orm import object_session

from api.models import Site, TeamVPNGateway, VM, VPNCredential, utcnow
from api.services.secrets import decrypt_secret
from api.services.ssh_keys import get_or_create_platform_keypair
from builder.base_loader import load_base_type

API_ROOT = "https://api.vultr.com/v2"
POLL_SECONDS = int(os.environ.get("GAMENET_PROVIDER_POLL_SECONDS", "10"))
CREATE_TIMEOUT = int(os.environ.get("GAMENET_INSTANCE_TIMEOUT_SECONDS", "900"))
WG_INTERFACE = os.environ.get("GAMENET_WG_INTERFACE", "ctf-gamenet")
OPNSENSE_RELEASE = os.environ.get("GAMENET_OPNSENSE_RELEASE", "26.7")
OPNSENSE_SITE_CONFIG_SCHEMA = 2


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

        body = {
            "region": vm.vultr_region, "plan": vm.vultr_plan,
            "label": vm.hostname, "hostname": vm.hostname,
            "enable_ipv6": False, "backups": "disabled",
            "ddos_protection": False,
        }
        if image_source and image_source.get("snapshot_id"):
            body["snapshot_id"] = image_source["snapshot_id"]
            # Snapshot contains the platform key; Vultr's OS key injection is
            # intentionally not relied upon for custom snapshots.
        else:
            _, public_key = get_or_create_platform_keypair(object_session(vm))
            ssh_key_id = self._ensure_ssh_key("ctf-platform", public_key)
            body["sshkey_id"] = [ssh_key_id]
            base = load_base_type(vm.base_type)
            body["os_id"] = self._resolve_os_id(base.os)
        if vpc_ids:
            body["attach_vpc"] = vpc_ids
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

    def create_private_boot_canary(self, certification, *, hostname: str, db) -> dict:
        """Create or recover the disposable stock-image VPC-only canary.

        The instance ID is committed immediately after the provider accepts the
        request, before any readiness polling, so an interrupted worker resumes
        the same instance instead of creating a duplicate.
        """
        if certification.instance_id:
            result = self._wait_instance(certification.instance_id)
            return self._instance_with_vpc(result, [certification.vpc_id])
        for instance in self._request("GET", "/instances", params={"per_page": 500}).get("instances", []):
            if instance.get("label") == hostname:
                certification.instance_id = instance["id"]
                certification.phase = "polling_instance"
                certification.updated_at = utcnow()
                db.commit()
                return self._instance_with_vpc(self._wait_instance(instance["id"]), [certification.vpc_id])

        _, public_key = get_or_create_platform_keypair(db)
        ssh_key_id = self._ensure_ssh_key("ctf-platform", public_key)
        body = {
            "region": certification.region,
            "plan": certification.plan,
            "os_id": certification.os_id,
            "label": hostname,
            "hostname": hostname,
            "enable_ipv6": False,
            "backups": "disabled",
            "ddos_protection": False,
            "attach_vpc": [certification.vpc_id],
            "vpc_only": True,
            "sshkey_id": [ssh_key_id],
        }
        instance = self._request("POST", "/instances", json=body)["instance"]
        certification.instance_id = instance["id"]
        certification.phase = "polling_instance"
        certification.updated_at = utcnow()
        db.commit()
        return self._instance_with_vpc(self._wait_instance(instance["id"]), [certification.vpc_id])

    def delete_instance(self, instance_id: str) -> None:
        self._request("DELETE", f"/instances/{instance_id}")

    def attach_vpc(self, vm: VM, vpc_id: str) -> dict:
        """Attach a VPC after WAN-only snapshot boot and return its observed NIC metadata."""
        path = f"/instances/{vm.vultr_id}/vpcs"
        attached = self._request("GET", path, params={"per_page": 100}).get("vpcs", [])
        selected = next((row for row in attached if row.get("id") == vpc_id), None)
        if not selected:
            self._request("POST", f"{path}/attach", json={"vpc_id": vpc_id})
            self._wait_instance(vm.vultr_id)
        deadline = time.monotonic() + CREATE_TIMEOUT
        while time.monotonic() < deadline:
            attached = self._request("GET", path, params={"per_page": 100}).get("vpcs", [])
            selected = next((row for row in attached if row.get("id") == vpc_id), None)
            if selected and selected.get("mac_address") and selected.get("ip_address"):
                return selected
            time.sleep(POLL_SECONDS)
        raise GameNetProviderError(f"VPC {vpc_id} attachment metadata did not become ready")

    def _instance_with_vpc(self, result: dict, vpc_ids: list[str] | None) -> dict:
        if vpc_ids:
            attached = self._request("GET", f"/instances/{result['id']}/vpcs", params={"per_page": 100}).get("vpcs", [])
            selected = next((row for row in attached if row.get("id") in vpc_ids), None)
            if not selected or not selected.get("ip_address") or not selected.get("mac_address"):
                raise GameNetProviderError(
                    f"missing VPC attachment metadata for instance {result['id']}"
                )
            result["internal_ip"] = selected["ip_address"]
            result["vpc_mac"] = selected["mac_address"]
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


def validate_vpc_only_instance(instance: dict, *, label: str = "instance") -> None:
    if instance.get("vpc_only") is not True:
        raise GameNetProviderError(f"{label} was not created with vpc_only=true")
    internal_ip = str(instance.get("internal_ip") or "")
    public_values = []
    for key in ("main_ip", "v6_main_ip"):
        value = instance.get(key)
        if value and value not in {"0.0.0.0", "::", internal_ip}:
            public_values.append(str(value))
    public_values.extend(
        str(value) for value in (instance.get("ipv4") or [])
        if value and str(value) not in {"0.0.0.0", internal_ip}
    )
    if public_values:
        raise GameNetProviderError(
            f"{label} unexpectedly received a public address: {', '.join(public_values)}"
        )


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
    """Legacy renderer retained for historical records; new endpoints never use it."""
    network = ip_network(cidr)
    gateway = str(network.network_address + 1)
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
            addresses: [{private_ip}/{network.prefixlen}]
            routes: [{{to: default, via: {gateway}}}]
            nameservers: {{addresses: [{gateway}]}}
      EOF
      netplan apply
runcmd:
  - [sh, /usr/local/sbin/gamenet-network.sh]
  - [systemctl, enable, --now, ssh]
"""


def ssh_command(vm: VM, command: str, *, host: str | None = None, jump: VM | None = None,
                timeout: int = 60, connect_timeout: int | None = None) -> tuple[int, str, str]:
    db = object_session(vm)
    return ssh_host_command(
        db, host or vm.public_ip or vm.private_ip or vm.ip_address, command,
        jump=jump, ssh_user=vm.ssh_user or "root", ssh_port=vm.ssh_port or 22,
        password=decrypt_secret(vm.admin_password) if vm.admin_password else None,
        timeout=timeout, connect_timeout=connect_timeout,
        label=f"VM {vm.id}",
    )


def ssh_host_command(db, host: str | None, command: str, *, jump: VM | None = None,
                     ssh_user: str = "root", ssh_port: int = 22, password: str | None = None,
                     timeout: int = 60, connect_timeout: int | None = None,
                     label: str = "host") -> tuple[int, str, str]:
    """Run a key-only SSH command, optionally through the public team gateway."""
    private_key, _ = get_or_create_platform_keypair(db)
    key = paramiko.Ed25519Key.from_private_key(io.StringIO(private_key))
    target_host = host
    if not target_host:
        raise GameNetProviderError(f"{label} has no reachable address")
    jump_client = None
    sock = None
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
                    "direct-tcpip", (target_host, ssh_port), ("127.0.0.1", 0), timeout=15,
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
        target_host, ssh_user, key, password=password,
        port=ssh_port, sock=sock, connect_timeout=connect_timeout,
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
    config = render_opnsense_config(
        site, vm, public_key, password, temporary_management=True,
    )
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


def validate_snapshot_wan(vm: VM, expected_version: str) -> None:
    """Prove a fresh snapshot clone is installed, WAN-only and key-only."""
    php = ('require_once("config.inc"); require_once("util.inc"); '
           '$wan=$config["interfaces"]["wan"]["if"]??""; '
           'echo $wan," ",(isset($config["interfaces"]["lan"])?"lan":"nolAN")," ",'
           '(is_install_media()?"live":"disk");')
    command = "/bin/sh -c " + shlex.quote(
        f"set -eu; set -- $(/usr/local/bin/php -r {shlex.quote(php)}); "
        "wan_if=$1; test \"$2\" = nolAN; test \"$3\" = disk; "
        "set -- $(ifconfig -l | tr ' ' '\\n' | grep -E '^vtnet[0-9]+$'); "
        "test \"$#\" -eq 1; test \"$1\" = \"$wan_if\"; "
        + _opnsense_release_test(expected_version) + "; "
        "route -n get default | grep -F \"interface: $wan_if\" >/dev/null; "
        "/usr/local/sbin/sshd -T | grep -qi '^permitrootlogin yes$'; "
        "/usr/local/sbin/sshd -T | grep -qi '^pubkeyauthentication yes$'; "
        "/usr/local/sbin/sshd -T | grep -qi '^passwordauthentication no$'; "
        "pfctl -sr | grep -E 'port = (ssh|22)' >/dev/null"
    )
    code, output, error = ssh_command(vm, command, timeout=120, connect_timeout=CREATE_TIMEOUT)
    if code:
        raise GameNetProviderError(f"WAN-only snapshot validation failed: {(error or output)[:300]}")
    host = vm.public_ip or vm.ip_address
    transport = paramiko.Transport((host, vm.ssh_port or 22))
    try:
        transport.start_client(timeout=15)
        key = transport.get_remote_server_key()
        vm.ssh_host_key = f"{key.get_name()} {key.get_base64()}"
    finally:
        transport.close()


def _snapshot_interface_mapping(vm: VM, lan_mac: str) -> tuple[str, str]:
    expected_mac = lan_mac.lower()
    command = "/bin/sh -c " + shlex.quote(
        "set -eu; wan_if=$(route -n get default | awk '/interface:/{print $2}'); lan_if=''; "
        "for candidate in $(ifconfig -l); do "
        "mac=$(ifconfig \"$candidate\" 2>/dev/null | awk '/ether/{print tolower($2); exit}'); "
        f"if [ \"$mac\" = {shlex.quote(expected_mac)} ]; then lan_if=$candidate; fi; done; "
        "test -n \"$wan_if\"; test -n \"$lan_if\"; test \"$wan_if\" != \"$lan_if\"; "
        "printf '%s %s\\n' \"$wan_if\" \"$lan_if\""
    )
    code, output, error = ssh_command(vm, command, timeout=120, connect_timeout=CREATE_TIMEOUT)
    parts = output.strip().split()
    if code or len(parts) != 2:
        raise GameNetProviderError(f"could not map Vultr VPC MAC inside OPNsense: {(error or output)[:300]}")
    return parts[0], parts[1]


def opnsense_config_fingerprint(*, site: Site, vm: VM, expected_version: str, lan_mac: str,
                                wan_interface: str, lan_interface: str, gateway_vm: VM,
                                gateway_listen_port: int, management_cidr: str,
                                platform_public_key: str) -> str:
    """Hash stable desired state, excluding randomized password hashes and secrets."""
    desired = {
        "schema": OPNSENSE_SITE_CONFIG_SCHEMA,
        "site_id": site.id,
        "site_cidr": site.allocated_cidr,
        "vm_id": vm.id,
        "vultr_id": vm.vultr_id,
        "hostname": vm.hostname,
        "release": expected_version,
        "public_ip": vm.public_ip,
        "private_ip": vm.private_ip,
        "lan_mac": lan_mac.lower(),
        "wan_interface": wan_interface,
        "lan_interface": lan_interface,
        "gateway_public_ip": gateway_vm.public_ip,
        "gateway_listen_port": gateway_listen_port,
        "management_cidr": management_cidr,
        "platform_public_key": platform_public_key,
    }
    encoded = json.dumps(desired, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def snapshot_site_validation_command(*, token: str, expected_version: str, public_ip: str, private_ip: str,
                                     wan_interface: str, lan_interface: str, lan_mac: str,
                                     management_cidr: str) -> str:
    management_source = str(ip_network(management_cidr).network_address)
    return (
        f"printf '%s\\n' {shlex.quote(token)} | cmp -s - /conf/ctf-site-ready && "
        f"{_opnsense_release_test(expected_version)} && "
        f"ifconfig {shlex.quote(wan_interface)} | grep -F 'inet {public_ip}' >/dev/null && "
        f"ifconfig {shlex.quote(lan_interface)} | grep -iF 'ether {lan_mac.lower()}' >/dev/null && "
        f"ifconfig {shlex.quote(lan_interface)} | grep -F 'inet {private_ip}' >/dev/null && "
        f"route -n get default | grep -F 'interface: {wan_interface}' >/dev/null && "
        f"pfctl -sr | grep -F 'pass in quick on {lan_interface} inet from ({lan_interface}:network) to any' >/dev/null && "
        "pfctl -sn | grep -E 'nat on' >/dev/null && "
        f"pfctl -sr | grep -F {shlex.quote('from ' + management_source + ' to')} | "
        "grep -E 'port = (ssh|22)' >/dev/null && opnsense-version -v"
    )


def _opnsense_apply_script(token: str, config_path: str, script_path: str) -> str:
    php = ('require_once("config.inc"); require_once("util.inc"); '
           '$c=\\OPNsense\\Core\\Config::getInstance(); '
           f'if(!$c->restoreBackup("{config_path}")){{exit(1);}} $c->forceReload();')
    quoted_token = shlex.quote(token)
    failure = (
        "status=$?; if [ \"$status\" -ne 0 ]; then "
        f"printf '%s\\n' {quoted_token} > /conf/ctf-site-failed.tmp; "
        "mv /conf/ctf-site-failed.tmp /conf/ctf-site-failed; "
        "fi; exit \"$status\""
    )
    return "\n".join([
        "#!/bin/sh", "set -eu", f"trap {shlex.quote(failure)} EXIT",
        f"/usr/local/bin/php -r {shlex.quote(php)}",
        "/usr/local/sbin/pluginctl -M", "/usr/local/opnsense/scripts/auth/sync_user.php -u root",
        "rm -f /usr/local/etc/rc.syshook.d/start/10-ctf-builder /usr/local/etc/rc.syshook.d/start/99-ctf-builder",
        "/usr/local/sbin/configctl interface reconfigure lan",
        "/usr/local/sbin/configctl interface reconfigure wan",
        "/usr/local/sbin/configctl openssh restart", "/usr/local/sbin/configctl filter reload",
        f"rm -f {shlex.quote(config_path)}",
        f"printf '%s\\n' {quoted_token} > /conf/ctf-site-ready.tmp",
        "mv /conf/ctf-site-ready.tmp /conf/ctf-site-ready",
        "rm -f /conf/ctf-site-applying /conf/ctf-site-failed",
        "trap - EXIT", f"rm -f {shlex.quote(script_path)}", "",
    ])


def _opnsense_apply_launch(token: str, script_path: str) -> str:
    quoted_token = shlex.quote(token)
    return (
        "rm -f /conf/ctf-site-ready /conf/ctf-site-failed; "
        f"printf '%s\\n' {quoted_token} > /conf/ctf-site-applying.tmp; "
        "mv /conf/ctf-site-applying.tmp /conf/ctf-site-applying; "
        f"nohup lockf -t 0 /conf/ctf-site-apply.lock /bin/sh {shlex.quote(script_path)} "
        ">>/var/log/ctf-site-apply.log 2>&1 </dev/null &"
    )


def _capture_ssh_host_key(vm: VM) -> None:
    host = vm.public_ip or vm.ip_address
    if not host:
        return
    transport = paramiko.Transport((host, vm.ssh_port or 22))
    try:
        transport.start_client(timeout=15)
        key = transport.get_remote_server_key()
        vm.ssh_host_key = f"{key.get_name()} {key.get_base64()}"
    finally:
        transport.close()


def configure_snapshot_opnsense(site: Site, vm: VM, expected_version: str, *, lan_mac: str) -> None:
    """Apply unique site state to an already validated OPNsense snapshot."""
    db = object_session(vm)
    _, public_key = get_or_create_platform_keypair(db)
    if not vm.admin_password:
        password = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(24))
        from api.services.secrets import encrypt_secret
        vm.admin_password = encrypt_secret(password); db.flush()
    else:
        password = decrypt_secret(vm.admin_password)
    wan_interface, lan_interface = _snapshot_interface_mapping(vm, lan_mac)
    gateway = site.team.vpn_gateway
    gateway_vm = db.get(VM, gateway.vm_id)
    management_cidr = os.environ.get("CTF_CONTROL_PLANE_CIDR", "127.0.0.1/32")
    fingerprint = opnsense_config_fingerprint(
        site=site, vm=vm, expected_version=expected_version, lan_mac=lan_mac,
        wan_interface=wan_interface, lan_interface=lan_interface, gateway_vm=gateway_vm,
        gateway_listen_port=gateway.listen_port, management_cidr=management_cidr,
        platform_public_key=public_key,
    )
    if (vm.opnsense_config_fingerprint != fingerprint or not vm.opnsense_config_token
            or vm.opnsense_config_status == "failed"):
        vm.opnsense_config_token = secrets.token_hex(32)
        vm.opnsense_config_fingerprint = fingerprint
        vm.opnsense_config_status = "pending"
        vm.opnsense_config_started_at = utcnow()
        vm.opnsense_config_completed_at = None
        vm.provision_error = None
        db.commit()
    token = vm.opnsense_config_token
    validation = snapshot_site_validation_command(
        token=token, expected_version=expected_version, public_ip=vm.public_ip,
        private_ip=vm.private_ip, wan_interface=wan_interface, lan_interface=lan_interface,
        lan_mac=lan_mac, management_cidr=management_cidr,
    )
    if vm.opnsense_config_status in {"applying", "applied"}:
        try:
            code, output, _ = ssh_command(
                vm, "/bin/sh -c " + shlex.quote(validation), timeout=60, connect_timeout=60,
            )
            if code == 0 and expected_version in output:
                vm.opnsense_config_status = "applied"
                vm.opnsense_config_completed_at = vm.opnsense_config_completed_at or utcnow()
                vm.provision_error = None
                db.commit()
                _capture_ssh_host_key(vm)
                return
        except Exception:
            pass
    config_path = f"/tmp/ctf-site-config-{token}.xml"
    script_path = f"/tmp/ctf-apply-{token}.sh"
    launch_needed = True
    if vm.opnsense_config_status == "applying":
        live_generation = (
            f"test \"$(cat /conf/ctf-site-applying 2>/dev/null)\" = {shlex.quote(token)} && "
            f"pgrep -f {shlex.quote('/bin/sh ' + script_path)} >/dev/null"
        )
        try:
            live_code, _, _ = ssh_command(
                vm, "/bin/sh -c " + shlex.quote(live_generation), timeout=30, connect_timeout=30,
            )
            launch_needed = live_code != 0
        except Exception:
            launch_needed = True
    if launch_needed:
        config = render_opnsense_config(
            site, vm, public_key, password, temporary_management=True,
            wan_interface=wan_interface, lan_interface=lan_interface,
        )
        upload_text(vm, config_path, config)
        upload_text(vm, script_path, _opnsense_apply_script(token, config_path, script_path), mode=0o700)
        launch = _opnsense_apply_launch(token, script_path)
        code, _, error = ssh_command(vm, "/bin/sh -c " + shlex.quote(launch), timeout=30)
        if code:
            vm.opnsense_config_status = "failed"
            vm.provision_error = f"failed to launch snapshot configuration: {error[:300]}"
            db.commit()
            raise GameNetProviderError(f"failed to launch snapshot configuration: {error[:300]}")
        vm.opnsense_config_status = "applying"
        db.commit()
    deadline = time.monotonic() + 600
    output = error = ""
    while time.monotonic() < deadline:
        try:
            code, output, error = ssh_command(
                vm, "/bin/sh -c " + shlex.quote(validation),
                timeout=60, connect_timeout=60,
            )
            if code == 0 and expected_version in output:
                break
            failure_check = f"printf '%s\\n' {shlex.quote(token)} | cmp -s - /conf/ctf-site-failed"
            failed, _, _ = ssh_command(vm, "/bin/sh -c " + shlex.quote(failure_check), timeout=30)
            if failed == 0:
                _, log_output, log_error = ssh_command(
                    vm, "tail -n 80 /var/log/ctf-site-apply.log", timeout=30,
                )
                error = (log_error or log_output or "guest apply failed")[-2000:]
                break
        except Exception as exc:
            error = str(exc)
        time.sleep(POLL_SECONDS)
    if code != 0 or expected_version not in output:
        detail = f"snapshot OPNsense validation failed for generation {token}: {(error or output)[:2000]}"
        vm.opnsense_config_status = "failed"
        vm.provision_error = detail
        db.commit()
        raise GameNetProviderError(detail)
    vm.opnsense_config_status = "applied"
    vm.opnsense_config_completed_at = utcnow()
    vm.provision_error = None
    db.commit()
    _capture_ssh_host_key(vm)


def configure_snapshot_validation_site(db, *, host: str, private_ip: str, lan_mac: str,
                                       expected_version: str, control_plane_cidr: str) -> None:
    """Apply the production site configuration shape to the disposable VPC clone.

    This deliberately uses ``render_opnsense_config`` and the same restore,
    interface-reconfigure, SSH, and filter sequence as real GameNet firewalls.
    It has an explicit session/host interface because validation clones are not
    persisted as user-visible VM or Site records.
    """
    from types import SimpleNamespace
    from api.services.opnsense_images import ImageWorkflowError, _ssh, _upload_atomic

    mapping = "/bin/sh -c " + shlex.quote(
        "set -eu; wan=$(route -n get default | awk '/interface:/{print $2}'); lan=''; "
        "for candidate in $(ifconfig -l); do "
        "mac=$(ifconfig \"$candidate\" 2>/dev/null | awk '/ether/{print tolower($2); exit}'); "
        f"if [ \"$mac\" = {shlex.quote(lan_mac.lower())} ]; then lan=$candidate; fi; done; "
        "test -n \"$wan\"; test -n \"$lan\"; test \"$wan\" != \"$lan\"; echo \"$wan $lan\""
    )
    code, output, error = _ssh(db, host, mapping)
    interfaces = output.strip().split()
    if code or len(interfaces) != 2:
        raise ImageWorkflowError(f"could not map validation VPC MAC: {(error or output)[:300]}")
    wan_interface, lan_interface = interfaces
    token = secrets.token_hex(32)
    config_path = f"/tmp/ctf-site-config-{token}.xml"
    script_path = f"/tmp/ctf-apply-{token}.sh"
    _, public_key = get_or_create_platform_keypair(db)
    password = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(32))
    # The /28 network and .1 gateway exactly mirror a minimal GameNet site.
    site = SimpleNamespace(allocated_cidr="172.31.254.0/28")
    vm = SimpleNamespace(hostname="opnsense-validation-site")
    previous_cidr = os.environ.get("CTF_CONTROL_PLANE_CIDR")
    os.environ["CTF_CONTROL_PLANE_CIDR"] = control_plane_cidr
    try:
        config = render_opnsense_config(
            site, vm, public_key, password, temporary_management=True,
            wan_interface=wan_interface, lan_interface=lan_interface,
        )
    finally:
        if previous_cidr is None:
            os.environ.pop("CTF_CONTROL_PLANE_CIDR", None)
        else:
            os.environ["CTF_CONTROL_PLANE_CIDR"] = previous_cidr
    _upload_atomic(db, host, config_path, config.encode())
    _upload_atomic(db, host, script_path, _opnsense_apply_script(token, config_path, script_path).encode(), 0o700)
    launch = "/bin/sh -c " + shlex.quote(_opnsense_apply_launch(token, script_path))
    code, _, error = _ssh(db, host, launch, timeout=30)
    if code:
        raise ImageWorkflowError(f"failed to launch validation site configuration: {error[:300]}")
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        try:
            code, output, error = _ssh(
                db, host,
                f"printf '%s\\n' {shlex.quote(token)} | cmp -s - /conf/ctf-site-ready && "
                f"{_opnsense_release_test(expected_version)} && "
                f"ifconfig {shlex.quote(lan_interface)} | grep -F {shlex.quote('inet ' + private_ip)} >/dev/null",
                retry=False,
            )
            if code == 0:
                return
        except Exception:
            pass
        time.sleep(POLL_SECONDS)
    raise ImageWorkflowError("validation site configuration did not become ready")


def _opnsense_release_test(requested: str) -> str:
    """Shell predicate for a release train such as 26.7 and its patch builds."""
    value = shlex.quote(requested)
    dot = value + ".*"
    underscore = value + "_*"
    return (f"actual_version=$(opnsense-version -v); case \"$actual_version\" in "
            f"{value}|{dot}|{underscore}) true;; *) false;; esac")


def render_opnsense_config(site: Site, vm: VM, public_key: str, password: str,
                           *, temporary_management: bool, wan_interface: str = "vtnet0",
                           lan_interface: str = "vtnet1") -> str:
    templates = FileSystemLoader(os.path.join(os.path.dirname(__file__), "..", "..", "templates"))
    environment = Environment(loader=templates, autoescape=False)
    environment.filters["b64encode"] = lambda value: base64.b64encode(str(value).encode()).decode()
    template = environment.get_template("opnsense_config.xml.j2")
    network = ip_network(site.allocated_cidr)
    return template.render(
        opnsense_hostname=vm.hostname, opnsense_lan_ip=str(network.network_address + 1),
        opnsense_lan_subnet=network.prefixlen,
        opnsense_wan_interface=wan_interface, opnsense_lan_interface=lan_interface,
        opnsense_admin_password_hash=bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=10)).decode(),
        opnsense_ssh_pubkey=public_key, temporary_management=temporary_management,
        opnsense_management_cidr=os.environ.get("CTF_CONTROL_PLANE_CIDR", "127.0.0.1/32"),
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
        "cloud-init status --wait && "
        "if ! command -v wg >/dev/null 2>&1 || "
        "! command -v iptables >/dev/null 2>&1 || "
        "! command -v unbound >/dev/null 2>&1; then "
        "apt-get -o DPkg::Lock::Timeout=300 update -qq && "
        "DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=300 install -y wireguard iptables unbound; fi && "
        f"if command -v ufw >/dev/null 2>&1; then ufw allow {gateway.listen_port}/udp; fi && "
        "systemctl enable wg-quick@gamenet && "
        "if wg show gamenet >/dev/null 2>&1; then wg-quick strip gamenet >/tmp/gamenet.stripped && wg syncconf gamenet /tmp/gamenet.stripped; "
        "else systemctl start wg-quick@gamenet; fi && "
        # Installing Unbound starts it before the WireGuard address exists. On
        # a fresh gateway that can exhaust systemd's start limit, so clear the
        # expected transient failure only after the interface is configured.
        "systemctl reset-failed unbound && systemctl enable unbound && systemctl restart unbound"
    )
    code, _, error = ssh_command(vm, command, host=management_host, timeout=300)
    if code:
        raise GameNetProviderError(f"gateway WireGuard configuration failed: {error[:300]}")


def configure_site_wireguard(site: Site, firewall: VM, gateway: TeamVPNGateway, gateway_vm: VM,
                              all_team_sites: list[Site]) -> None:
    config = "\n".join([
        "[Interface]", f"PrivateKey = {decrypt_secret(site.tunnel_private_key_encrypted)}", "",
        "[Peer]", f"PublicKey = {gateway.public_key}",
        f"Endpoint = {gateway_vm.public_ip}:{gateway.listen_port}",
        f"AllowedIPs = 10.64.0.0/10", "PersistentKeepalive = 25", "",
    ])
    upload_text(firewall, "/usr/local/etc/wireguard/wg0.conf", config)
    firewall_plugin = f"""<?php
function gamenet_firewall(\\OPNsense\\Firewall\\Plugin $fw)
{{
    $base = [
        'type' => 'pass', 'interface' => 'gamenet', 'direction' => 'in',
        'ipprotocol' => 'inet', 'statetype' => 'keep', 'quick' => true,
        'log' => true, 'disablereplyto' => 1, 'descr' => 'GameNet VPN ingress'
    ];
    $fw->registerFilterRule(
        1,
        ['from' => '10.64.0.0/10', 'to' => {json.dumps(site.allocated_cidr)}],
        $base
    );
    $fw->registerFilterRule(
        1,
        ['protocol' => 'tcp/udp', 'from' => '10.64.0.0/10',
         'to' => {json.dumps(site.tunnel_address)}, 'to_port' => 53],
        $base
    );
}}
"""
    upload_text(firewall, "/usr/local/etc/inc/plugins.inc.d/gamenet.inc", firewall_plugin)
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
    interface_php = (
        'require_once("config.inc"); require_once("util.inc"); global $config; '
        f'$config["interfaces"]["gamenet"]=['
        f'"if"=>"wg0","descr"=>"GameNet","enable"=>"1",'
        f'"ipaddr"=>{json.dumps(site.tunnel_address)},"subnet"=>"32"]; '
        'write_config("Configure GameNet tunnel interface");'
    )
    site_command = (
        f"test -x /usr/bin/wg && /usr/local/bin/php -r {shlex.quote(interface_php)} && "
        "(service gamenet stop >/dev/null 2>&1 || true) && service gamenet start && "
        "configctl filter reload && configctl unbound restart"
    )
    command = f"sh -c {shlex.quote(site_command)}"
    code, _, error = ssh_command(firewall, command, timeout=300)
    if code:
        raise GameNetProviderError(f"site WireGuard configuration failed: {error[:300]}")


def validate_site_tunnel(site: Site, firewall: VM, gateway: TeamVPNGateway, gateway_vm: VM,
                         *, timeout: int = 180) -> None:
    """Require recent handshakes at both peers and gateway-to-LAN SSH reachability."""
    gateway_handshake = (
        "wg show gamenet latest-handshakes | "
        f"awk -v key={shlex.quote(site.tunnel_public_key)} '$1 == key {{print $2}}'"
    )
    firewall_handshake = (
        "wg show wg0 latest-handshakes | "
        f"awk -v key={shlex.quote(gateway.public_key)} '$1 == key {{print $2}}'"
    )
    private_ssh = (
        "python3 -c " + shlex.quote(
            "import socket; s=socket.create_connection((%r, 22), 5); s.close()" % firewall.private_ip
        )
    )
    deadline = time.monotonic() + timeout
    detail = "WireGuard handshake not observed"
    while time.monotonic() < deadline:
        gateway_code, gateway_output, gateway_error = ssh_command(
            gateway_vm, gateway_handshake, timeout=30,
        )
        firewall_code, firewall_output, firewall_error = ssh_command(
            firewall, firewall_handshake, timeout=30,
        )
        try:
            gateway_timestamp = int(gateway_output.strip()) if gateway_code == 0 else 0
            firewall_timestamp = int(firewall_output.strip()) if firewall_code == 0 else 0
        except ValueError:
            gateway_timestamp = firewall_timestamp = 0
        oldest_allowed = int(time.time()) - 180
        if gateway_timestamp >= oldest_allowed and firewall_timestamp >= oldest_allowed:
            code, _, error = ssh_command(gateway_vm, private_ssh, timeout=30)
            if code == 0:
                return
            detail = f"gateway could not reach firewall LAN SSH at {firewall.private_ip}: {error[:300]}"
        else:
            detail = (
                "WireGuard handshake not observed at both peers "
                f"(gateway={gateway_output.strip() or gateway_error[:80]!r}, "
                f"firewall={firewall_output.strip() or firewall_error[:80]!r})"
            )
        time.sleep(POLL_SECONDS)
    raise GameNetProviderError(detail)


def add_deterministic_endpoint_address(vm: VM, site: Site, gateway_vm: VM) -> None:
    """Stage one: add the zone address while retaining Vultr's boot address."""
    if not vm.vpc_ip or not vm.vpc_mac:
        raise GameNetProviderError("missing VPC attachment metadata for endpoint network conversion")
    network = ip_network(site.allocated_cidr)
    router = str(network.network_address + 1)
    mac = vm.vpc_mac.lower()
    command = (
        "set -eu; iface=''; "
        "for path in /sys/class/net/*/address; do "
        f"if [ \"$(tr A-F a-f < \"$path\")\" = {shlex.quote(mac)} ]; then iface=$(basename \"$(dirname \"$path\")\"); break; fi; done; "
        "test -n \"$iface\"; "
        f"ip address replace {vm.private_ip}/{network.prefixlen} dev \"$iface\"; "
        f"ip route replace default via {router} dev \"$iface\"; "
        f"printf 'nameserver {router}\\n' > /etc/resolv.conf; "
        f"ip -4 address show dev \"$iface\" | grep -F {shlex.quote(vm.private_ip + '/')}"
    )
    code, _, error = ssh_command(
        vm, command, host=vm.vpc_ip, jump=gateway_vm, connect_timeout=CREATE_TIMEOUT,
    )
    if code:
        raise GameNetProviderError(f"deterministic-address transition failed while adding address: {error[:300]}")
    code, output, error = ssh_command(
        vm, "cat /proc/sys/kernel/random/boot_id", host=vm.private_ip,
        jump=gateway_vm, connect_timeout=CREATE_TIMEOUT,
    )
    if code:
        raise GameNetProviderError(f"deterministic-address transition failed to reach new address: {error[:300]}")
    vm.network_boot_id = output.strip()


def finalize_endpoint_network(vm: VM, site: Site, gateway_vm: VM) -> None:
    """Stage two: persist MAC-matched netplan, remove the boot address, and reboot."""
    network = ip_network(site.allocated_cidr)
    router = str(network.network_address + 1)
    mac = vm.vpc_mac.lower()
    netplan = f"""network:
  version: 2
  ethernets:
    gamenet:
      match:
        macaddress: {mac}
      set-name: gamenet0
      addresses: [{vm.private_ip}/{network.prefixlen}]
      routes:
        - to: default
          via: {router}
      nameservers:
        addresses: [{router}]
"""
    encoded = base64.b64encode(netplan.encode()).decode()
    command = (
        "set -eu; mkdir -p /etc/cloud/cloud.cfg.d; "
        "printf 'network: {config: disabled}\\n' > /etc/cloud/cloud.cfg.d/99-disable-network-config.cfg; "
        "rm -f /etc/netplan/50-cloud-init.yaml; "
        f"echo {shlex.quote(encoded)} | base64 -d > /etc/netplan/90-gamenet.yaml; "
        "chmod 600 /etc/netplan/90-gamenet.yaml; netplan generate; sync; "
        "nohup sh -c 'sleep 2; reboot' >/dev/null 2>&1 &"
    )
    code, _, error = ssh_command(vm, command, host=vm.private_ip, jump=gateway_vm)
    if code:
        raise GameNetProviderError(f"deterministic-address transition failed to persist network: {error[:300]}")
    verification = (
        "set -eu; "
        f"test \"$(cat /sys/class/net/gamenet0/address | tr A-F a-f)\" = {shlex.quote(mac)}; "
        f"ip -4 address show dev gamenet0 | grep -F {shlex.quote(vm.private_ip + '/')}; "
        f"ip route show default | grep -F {shlex.quote('via ' + router)}; "
        "getent hosts example.com >/dev/null; "
        "curl -4fsS --max-time 20 https://example.com/ >/dev/null; "
        "test -z \"$(ip -o -4 addr show scope global | awk '$4 !~ /^10\\./ {print $4}')\"; "
        "test -z \"$(ip -o -6 addr show scope global)\"; "
        "cat /proc/sys/kernel/random/boot_id"
    )
    code, output, error = ssh_command(
        vm, verification, host=vm.private_ip, jump=gateway_vm,
        timeout=120, connect_timeout=CREATE_TIMEOUT,
    )
    if code:
        raise GameNetProviderError(f"deterministic-address transition failed post-reboot checks: {error[:300]}")
    boot_id = output.strip().splitlines()[-1] if output.strip() else ""
    if not boot_id or boot_id == vm.network_boot_id:
        raise GameNetProviderError("deterministic-address transition failed: endpoint did not reboot")
    vm.network_boot_id = boot_id


def configure_endpoint_network(vm: VM, site: Site, gateway_vm: VM) -> None:
    """Compatibility wrapper for the persisted two-stage conversion."""
    add_deterministic_endpoint_address(vm, site, gateway_vm)
    finalize_endpoint_network(vm, site, gateway_vm)
