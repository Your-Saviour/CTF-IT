"""Automated FreeBSD-to-OPNsense golden-image lifecycle.

Every cloud identifier is committed before the next mutation.  Re-running
``run_image_build`` therefore resumes the persisted builder, conversion,
snapshot, or disposable-clone stage without starting a second conversion.
"""

from __future__ import annotations

import base64
import bcrypt
import hashlib
import io
import json
import os
import re
import secrets
import shlex
import socket
import time
from datetime import timezone
from ipaddress import ip_network
from urllib.parse import urlparse

import httpx
import paramiko
from sqlalchemy.orm import Session

from api.models import AdminAudit, OpnsenseImage, PlatformSettings, utcnow
from api.services.ssh_keys import get_or_create_platform_keypair

SUPPORTED_RELEASES = {"26.7": "FreeBSD 15 x64"}
BOOTSTRAP_SOURCE_URL = (
    "https://raw.githubusercontent.com/opnsense/update/master/"
    "src/bootstrap/opnsense-bootstrap.sh.in"
)
BUILD_METHOD = "freebsd-bootstrap"
BUILDER_PLAN = "vc2-2c-4gb"
RUNNING_STATES = {"creating_builder", "bootstrapping", "validating", "snapshotting"}
RESUMABLE_STATES = RUNNING_STATES | {"interrupted", "failed"}
TERMINAL_STATES = {"ready", "active", "retired"}
ACTIVE_SETTING = "active_opnsense_image_id"
POLL_SECONDS = int(os.environ.get("OPNSENSE_IMAGE_POLL_SECONDS", "10"))
POLL_TIMEOUT = int(os.environ.get("OPNSENSE_IMAGE_TIMEOUT_SECONDS", "1800"))


class ImageWorkflowError(RuntimeError):
    pass


def validate_release(version: str) -> str:
    value = (version or "").strip()
    if value not in SUPPORTED_RELEASES:
        raise ValueError("supported OPNsense release is 26.7")
    return value


def release_matches(actual: str, requested: str) -> bool:
    """Accept the requested OPNsense train and its security/patch releases."""
    return actual == requested or actual.startswith((requested + ".", requested + "_"))


def validate_control_plane_cidr(value: str | None = None) -> str:
    raw = value if value is not None else os.environ.get("CTF_CONTROL_PLANE_CIDR", "")
    if not raw or "/" not in raw:
        raise ImageWorkflowError("CTF_CONTROL_PLANE_CIDR must be a valid IPv4 CIDR")
    try:
        network = ip_network(raw, strict=False)
    except ValueError as exc:
        raise ImageWorkflowError("CTF_CONTROL_PLANE_CIDR must be a valid IPv4 CIDR") from exc
    if network.version != 4:
        raise ImageWorkflowError("CTF_CONTROL_PLANE_CIDR must be a valid IPv4 CIDR")
    return str(network)


def validate_bootstrap_url(url: str) -> str:
    parsed = urlparse(url)
    expected = urlparse(BOOTSTRAP_SOURCE_URL)
    if (parsed.scheme, parsed.netloc, parsed.path) != (expected.scheme, expected.netloc, expected.path):
        raise ImageWorkflowError("bootstrap source must be the official OPNsense update repository")
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ImageWorkflowError("bootstrap source must be the official OPNsense update repository")
    return url


def download_bootstrap(url: str = BOOTSTRAP_SOURCE_URL, *, client: httpx.Client | None = None) -> tuple[bytes, str]:
    validate_bootstrap_url(url)
    owned = client is None
    http = client or httpx.Client(timeout=60)
    try:
        response = http.get(url, follow_redirects=False)
        if response.is_redirect:
            raise ImageWorkflowError("bootstrap download redirect rejected")
        response.raise_for_status()
        content = response.content
        if not content or len(content) > 1024 * 1024:
            raise ImageWorkflowError("bootstrap source is empty or unexpectedly large")
        return content, hashlib.sha256(content).hexdigest()
    finally:
        if owned:
            http.close()


def active_image(db: Session) -> OpnsenseImage | None:
    setting = db.query(PlatformSettings).filter_by(key=ACTIVE_SETTING).first()
    if not setting or not setting.value.isdigit():
        return None
    image = db.get(OpnsenseImage, int(setting.value))
    return image if image and image.status == "active" and image.ami_id and image.validated_at else None


def interrupt_running_jobs(db: Session) -> int:
    rows = db.query(OpnsenseImage).filter(OpnsenseImage.status.in_(RUNNING_STATES)).all()
    for row in rows:
        row.status = "interrupted"
        row.error_detail = "The API restarted during this phase. Resume continues from persisted cloud resources."
    if rows:
        db.commit()
    return len(rows)


def image_payload(image: OpnsenseImage) -> dict:
    result = {column.name: (value.isoformat() if hasattr(value, "isoformat") else value)
              for column in image.__table__.columns for value in [getattr(image, column.name)]}
    result["validation_results"] = json.loads(image.validation_results) if image.validation_results else None
    return result


def elapsed_seconds(image: OpnsenseImage) -> int:
    created_at = image.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return max(0, int((utcnow() - created_at).total_seconds()))


def redact_error(exc: Exception) -> str:
    detail = str(exc)
    key = os.environ.get("VULTR_API_KEY")
    if key:
        detail = detail.replace(key, "[redacted]")
    return detail[:1000]


def _audit(db: Session, action: str, image: OpnsenseImage, **metadata) -> None:
    db.add(AdminAudit(action=action, metadata_json=json.dumps(
        {"image_id": image.id, "version": image.version, **metadata}, sort_keys=True,
    )))


def _set_phase(db: Session, image: OpnsenseImage, phase: str) -> None:
    image.phase = image.status = phase
    image.error_detail = None
    db.commit()


class VultrImageClient:
    """Minimal Vultr v2 adapter with independently mockable lifecycle calls."""

    def __init__(self):
        key = os.environ.get("VULTR_API_KEY")
        if not key:
            raise ImageWorkflowError("VULTR_API_KEY is required")
        self.client = httpx.Client(
            base_url="https://api.vultr.com/v2", timeout=30,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )

    def close(self):
        self.client.close()

    def request(self, method: str, path: str, **kwargs) -> dict:
        response = None
        for attempt in range(5):
            response = self.client.request(method, path, **kwargs)
            if method.upper() != "GET" or response.status_code < 500:
                break
            if attempt < 4:
                time.sleep(2 ** attempt)
        assert response is not None
        if response.status_code not in {200, 201, 202, 204}:
            raise ImageWorkflowError(f"Vultr {method} {path} failed ({response.status_code}): {response.text[:300]}")
        return response.json() if response.content else {}

    def preflight(self, base_os: str) -> int:
        region = os.environ.get("VULTR_DEFAULT_REGION", "syd")
        plans = self.request("GET", "/plans", params={"type": "vc2", "per_page": 500}).get("plans", [])
        plan = next((row for row in plans if row.get("id") == BUILDER_PLAN), None)
        if not plan or region not in plan.get("locations", []):
            raise ImageWorkflowError(f"Vultr plan {BUILDER_PLAN} is unavailable in {region}")
        rows = self.request("GET", "/os", params={"per_page": 500}).get("os", [])
        match = next((row for row in rows if row.get("name", "").casefold() == base_os.casefold()), None)
        if not match:
            raise ImageWorkflowError(f"Vultr OS is unavailable: {base_os}")
        # This authenticated read fails early for suspended or inaccessible accounts.
        self.request("GET", "/account")
        return int(match["id"])

    def ensure_ssh_key(self, public_key: str) -> str:
        material = " ".join(public_key.strip().split()[:2])
        rows = self.request("GET", "/ssh-keys", params={"per_page": 500}).get("ssh_keys", [])
        found = next((row for row in rows if " ".join(row.get("ssh_key", "").split()[:2]) == material), None)
        if found:
            return found["id"]
        return self.request("POST", "/ssh-keys", json={"name": "ctf-platform", "ssh_key": public_key})["ssh_key"]["id"]

    def create_firewall(self, version: str, cidr: str) -> str:
        network = ip_network(cidr)
        group = self.request("POST", "/firewalls", json={
            "description": f"ctf-opnsense-builder-{version}",
        })["firewall_group"]
        self.request("POST", f"/firewalls/{group['id']}/rules", json={
            "ip_type": "v4", "protocol": "tcp", "subnet": str(network.network_address),
            "subnet_size": network.prefixlen, "port": "22",
        })
        return group["id"]

    def create_builder(self, image: OpnsenseImage, *, os_id: int, ssh_key_id: str) -> str:
        body = {
            "region": os.environ.get("VULTR_DEFAULT_REGION", "syd"), "plan": BUILDER_PLAN,
            "os_id": os_id, "sshkey_id": [ssh_key_id],
            "label": f"ctf-opnsense-builder-{image.version}-{image.id}",
            "hostname": "opnsense-golden", "firewall_group_id": image.builder_firewall_group_id,
            "enable_ipv6": False, "backups": "disabled",
        }
        return self.request("POST", "/instances", json=body)["instance"]["id"]

    def instance(self, identifier: str) -> dict:
        return self.request("GET", f"/instances/{identifier}")["instance"]

    def wait_instance(self, identifier: str) -> dict:
        return self._wait(lambda: self.instance(identifier), {"active"}, label="instance")

    def start(self, identifier: str):
        self.request("POST", f"/instances/{identifier}/start")

    def halt(self, identifier: str):
        self.request("POST", f"/instances/{identifier}/halt")

    def wait_stopped(self, identifier: str) -> dict:
        deadline = time.monotonic() + POLL_TIMEOUT
        while time.monotonic() < deadline:
            row = self.instance(identifier)
            if row.get("power_status") == "stopped":
                return row
            time.sleep(POLL_SECONDS)
        raise ImageWorkflowError("timed out waiting for instance to stop")

    def create_snapshot(self, image: OpnsenseImage) -> str:
        return self.request("POST", "/snapshots", json={
            "instance_id": image.builder_instance_id,
            "description": f"CTF OPNsense {image.version} FreeBSD bootstrap",
        })["snapshot"]["id"]

    def wait_snapshot(self, identifier: str):
        return self._wait(
            lambda: self.request("GET", f"/snapshots/{identifier}")["snapshot"],
            {"complete"}, label="snapshot",
        )

    def create_clone(self, image: OpnsenseImage, number: int) -> str:
        return self.request("POST", "/instances", json={
            "region": os.environ.get("VULTR_DEFAULT_REGION", "syd"), "plan": BUILDER_PLAN,
            "snapshot_id": image.snapshot_id, "label": f"ctf-opnsense-validation-{image.id}-{number}",
            "hostname": f"opnsense-validation-{number}", "enable_ipv6": False,
            "backups": "disabled", "firewall_group_id": image.builder_firewall_group_id,
        })["instance"]["id"]

    def create_validation_vpc(self, image: OpnsenseImage) -> str:
        return self.request("POST", "/vpcs", json={
            "region": os.environ.get("VULTR_DEFAULT_REGION", "syd"),
            "description": f"ctf-opnsense-validation-{image.id}",
            "v4_subnet": "172.31.254.0", "v4_subnet_mask": 28,
        })["vpc"]["id"]

    def attach_vpc(self, instance_id: str, vpc_id: str) -> dict:
        path = f"/instances/{instance_id}/vpcs"
        rows = self.request("GET", path, params={"per_page": 100}).get("vpcs", [])
        match = next((row for row in rows if row.get("id") == vpc_id), None)
        if not match:
            self.request("POST", f"{path}/attach", json={"vpc_id": vpc_id})
        deadline = time.monotonic() + POLL_TIMEOUT
        while time.monotonic() < deadline:
            rows = self.request("GET", path, params={"per_page": 100}).get("vpcs", [])
            match = next((row for row in rows if row.get("id") == vpc_id), None)
            if match and match.get("mac_address") and match.get("ip_address"):
                return match
            time.sleep(POLL_SECONDS)
        raise ImageWorkflowError("validation VPC attachment did not become ready")

    def delete(self, kind: str, identifier: str | None):
        if identifier:
            self.request("DELETE", f"/{kind}/{identifier}")

    def _wait(self, getter, success: set[str], *, label: str):
        deadline = time.monotonic() + POLL_TIMEOUT
        while time.monotonic() < deadline:
            row = getter()
            status = row.get("status", "")
            server = row.get("server_status", "")
            if status in success and (label != "instance" or server in {"ok", "none"}):
                return row
            if status in {"failed", "error"}:
                raise ImageWorkflowError(f"Vultr {label} entered {status} state")
            time.sleep(POLL_SECONDS)
        raise ImageWorkflowError(f"timed out waiting for Vultr {label}")


def _new_image_vultr(db: Session, version: str, *, vultr_factory=VultrImageClient) -> OpnsenseImage:
    """Validate every prerequisite before persisting a build or creating cloud resources."""
    release = validate_release(version)
    validate_control_plane_cidr()
    if db.query(OpnsenseImage).filter(OpnsenseImage.status.in_(RUNNING_STATES | {"interrupted"})).first():
        raise ImageWorkflowError("another OPNsense image job is already running")
    client = vultr_factory()
    try:
        client.preflight(SUPPORTED_RELEASES[release])
    finally:
        client.close()
    row = OpnsenseImage(
        version=release, build_method=BUILD_METHOD, base_os=SUPPORTED_RELEASES[release],
        bootstrap_source_url=BOOTSTRAP_SOURCE_URL, status="creating_builder", phase="creating_builder",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _ssh(db: Session, host: str, command: str, *, timeout: int = 180,
         retry: bool = True) -> tuple[int, str, str]:
    private_key, _ = get_or_create_platform_keypair(db)
    key = paramiko.Ed25519Key.from_private_key(io.StringIO(private_key))
    deadline = time.monotonic() + (POLL_TIMEOUT if retry else 1)
    last_error = None
    while time.monotonic() < deadline:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(host, username="root", pkey=key, allow_agent=False, look_for_keys=False,
                           timeout=15, banner_timeout=15, auth_timeout=15)
            # OPNsense assigns root a csh login shell.  Always select POSIX sh
            # explicitly because lifecycle and validation commands use sh
            # conditionals, substitutions, and `set -eu`.
            _stdin, stdout, stderr = client.exec_command(_posix_command(command), timeout=timeout)
            return (stdout.channel.recv_exit_status(), stdout.read().decode(errors="replace"),
                    stderr.read().decode(errors="replace"))
        except Exception as exc:
            last_error = exc
            if not retry:
                break
            time.sleep(POLL_SECONDS)
        finally:
            client.close()
    raise ImageWorkflowError(f"SSH did not become ready: {last_error}")


def _posix_command(command: str) -> str:
    return "/bin/sh -c " + shlex.quote(command)


def _upload_atomic(db: Session, host: str, path: str, content: bytes, mode: int = 0o600) -> None:
    encoded = base64.b64encode(content).decode()
    temporary = path + ".part"
    directory = path.rsplit("/", 1)[0] or "/"
    command = (
        f"umask 077; install -d -m 700 {shlex.quote(directory)}; "
        f"printf %s {shlex.quote(encoded)} | base64 -d > {shlex.quote(temporary)} && "
        f"chmod {mode:o} {shlex.quote(temporary)} && mv -f {shlex.quote(temporary)} {shlex.quote(path)}"
    )
    code, _, error = _ssh(db, host, command)
    if code:
        raise ImageWorkflowError(f"atomic upload of {path} failed: {error[:300]}")


def render_golden_config(db: Session, image: OpnsenseImage, cidr: str) -> str:
    _, public_key = get_or_create_platform_keypair(db)
    password_hash = bcrypt.hashpw(secrets.token_bytes(32), bcrypt.gensalt(rounds=12)).decode()
    provenance = hashlib.sha256(f"ctf-opnsense:{image.id}:{image.version}".encode()).hexdigest()
    key = base64.b64encode(public_key.strip().encode()).decode()
    return f'''<?xml version="1.0"?>
<opnsense><theme>opnsense</theme><system><hostname>opnsense-golden</hostname><domain>localdomain</domain>
<group><name>admins</name><description>System Administrators</description><scope>system</scope><gid>1999</gid><member>0</member><priv>page-all</priv></group>
<user><name>root</name><descr>System Administrator</descr><scope>system</scope><groupname>admins</groupname><password>{password_hash}</password><uid>0</uid><authorizedkeys>{key}</authorizedkeys></user>
<nextuid>2000</nextuid><nextgid>2000</nextgid><timezone>UTC</timezone><language>en_US</language>
<ssh><enabled>1</enabled><port>22</port><permitrootlogin>1</permitrootlogin><interfaces>wan</interfaces><group>admins</group></ssh>
<ctf_builder_provenance>{provenance}</ctf_builder_provenance></system>
<interfaces><wan><if>vtnet0</if><descr>WAN</descr><enable>1</enable><ipaddr>dhcp</ipaddr><ipaddrv6>none</ipaddrv6><blockpriv>1</blockpriv><blockbogons>1</blockbogons></wan></interfaces>
<gateways/><staticroutes/><filter/><OPNsense><Firewall><Filter><general><snat_mode>automatic</snat_mode></general><rules>
<rule><enabled>1</enabled><statetype>keep</statetype><sequence>1</sequence><action>pass</action><quick>1</quick><interface>wan</interface><direction>in</direction><ipprotocol>inet</ipprotocol><protocol>tcp</protocol><source_net>{cidr}</source_net><destination_net>wanip</destination_net><destination_port>22</destination_port><description>CTF builder SSH</description></rule>
</rules><snatrules/><npt/><onetoone/></Filter></Firewall></OPNsense><nat><outbound><mode>automatic</mode></outbound></nat><dhcpd/></opnsense>'''


def _guest_state(db: Session, host: str) -> str:
    command = ("if command -v configctl >/dev/null 2>&1; then echo opnsense; "
               "elif pgrep -f '[o]pnsense-bootstrap' >/dev/null; then echo converting; "
               "else echo freebsd; fi")
    code, output, error = _ssh(db, host, command)
    if code:
        raise ImageWorkflowError(f"could not inspect builder state: {error[:300]}")
    return output.strip().splitlines()[-1]


def _verify_freebsd_base(db: Session, host: str) -> None:
    code, output, error = _ssh(db, host, "uname -m; freebsd-version -u")
    lines = output.strip().splitlines()
    if code or len(lines) < 2 or lines[0] != "amd64" or not re.match(r"^15\.1(?:-|$)", lines[1]):
        raise ImageWorkflowError(f"builder must be amd64 FreeBSD 15.1-compatible: {(error or output)[:300]}")


def _wait_for_opnsense(db: Session, host: str, version: str) -> None:
    deadline = time.monotonic() + POLL_TIMEOUT
    last = "OPNsense has not answered"
    while time.monotonic() < deadline:
        try:
            code, output, error = _ssh(
                db, host, f"test -x /usr/local/sbin/configctl && opnsense-version -v", retry=False,
            )
            if code == 0 and release_matches(output.strip(), version):
                return
            last = (error or output or f"exit {code}")[:300]
        except Exception as exc:
            last = redact_error(exc)
        time.sleep(POLL_SECONDS)
    raise ImageWorkflowError(f"bootstrap reboot did not return the expected OPNsense release: {last}")


def _provenance(image: OpnsenseImage) -> str:
    return hashlib.sha256(f"ctf-opnsense:{image.id}:{image.version}".encode()).hexdigest()


def builder_validation_command(*, public_ip: str, version: str, cidr: str,
                               provenance: str, nic_count: int = 1) -> str:
    php = ('require_once("config.inc"); $wan=$config["interfaces"]["wan"]["if"]??""; '
           '$lan=isset($config["interfaces"]["lan"])?"yes":"no"; '
           '$p=$config["system"]["ctf_builder_provenance"]??""; echo "$wan $lan $p";')
    source = str(ip_network(cidr).network_address)
    return (
        f"set -eu; actual_version=$(opnsense-version -v); case \"$actual_version\" in "
        f"{shlex.quote(version)}|{shlex.quote(version)}.*|{shlex.quote(version)}_*) ;; *) exit 1;; esac; "
        "test -x /usr/local/sbin/configctl; test -w /conf/config.xml; "
        f"set -- $(/usr/local/bin/php -r {shlex.quote(php)}); wan_if=$1; test \"$2\" = no; "
        f"test \"$3\" = {shlex.quote(provenance)}; "
        "set -- $(ifconfig -l | tr ' ' '\\n' | grep -E '^vtnet[0-9]+$'); "
        f"test \"$#\" -eq {nic_count}; test \"$wan_if\" = vtnet0; "
        f"ifconfig \"$wan_if\" | grep -F {shlex.quote('inet ' + public_ip)} >/dev/null; "
        "route -n get default | grep -F \"interface: $wan_if\" >/dev/null; "
        "test -s /root/.ssh/authorized_keys; "
        "/usr/local/sbin/sshd -T | grep -qi '^permitrootlogin yes$'; "
        "/usr/local/sbin/sshd -T | grep -qi '^pubkeyauthentication yes$'; "
        "/usr/local/sbin/sshd -T | grep -qi '^passwordauthentication no$'; "
        "/usr/local/sbin/sshd -T | grep -qi '^kbdinteractiveauthentication no$'; "
        f"pfctl -sr | grep -F {shlex.quote('from ' + source + ' to')} | grep -E 'port = (ssh|22)' >/dev/null"
    )


def _validate_builder(db: Session, image: OpnsenseImage, host: str, cidr: str, label: str) -> None:
    command = builder_validation_command(
        public_ip=host, version=image.version, cidr=cidr, provenance=_provenance(image),
    )
    code, output, error = _ssh(db, host, command)
    if code:
        raise ImageWorkflowError(f"{label} failed: {(error or output)[:300]}")


def _boot_id(db: Session, host: str) -> str:
    code, output, error = _ssh(db, host, "sysctl -n kern.boottime")
    if code or not output.strip():
        raise ImageWorkflowError(f"could not read boot identity: {error[:300]}")
    return output.strip()


def _fingerprint(db: Session, host: str) -> str:
    command = ("key=$(/usr/local/sbin/sshd -T | awk 'tolower($1)==\"hostkey\" && tolower($2) ~ /ed25519/ {print $2; exit}'); "
               "test -n \"$key\"; ssh-keygen -lf \"$key.pub\" | awk '{print $2}'")
    code, output, error = _ssh(db, host, command)
    if code or not output.strip():
        raise ImageWorkflowError(f"could not read SSH host key: {error[:300]}")
    return output.strip()


def _halt(db: Session, host: str) -> None:
    delayed = "/bin/sh -c " + shlex.quote("sleep 1; /sbin/shutdown -p now")
    command = f"nohup {delayed} >/dev/null 2>&1 </dev/null &"
    code, _, error = _ssh(db, host, command, timeout=30)
    if code:
        raise ImageWorkflowError(f"clean power-off request failed: {error[:300]}")


def _wait_for_guest_shutdown(host: str) -> None:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, 22), timeout=2):
                time.sleep(2)
        except OSError:
            return
    raise ImageWorkflowError("guest did not close SSH during clean shutdown")


def _guest_ssh_online(host: str) -> bool:
    try:
        with socket.create_connection((host, 22), timeout=2):
            return True
    except OSError:
        return False


def _power_off_builder(db: Session, client: VultrImageClient,
                       image: OpnsenseImage, host: str) -> None:
    _halt(db, host)
    _wait_for_guest_shutdown(host)
    # Vultr can leave a cleanly halted custom guest marked running.  Once the
    # guest is offline, synchronize the hypervisor state before the hard gate.
    client.halt(image.builder_instance_id)
    client.wait_stopped(image.builder_instance_id)


def _sanitize_and_halt(db: Session, client: VultrImageClient, image: OpnsenseImage, host: str) -> None:
    sanitize = (
        "rm -f /conf/sshd/ssh_host_* /etc/ssh/ssh_host_* /usr/local/etc/ssh/ssh_host_* "
        "/var/db/dhclient.leases.* /var/db/dhclient.leases /root/.*history /root/.sh_history "
        "/root/opnsense-bootstrap.sh /var/log/opnsense-bootstrap.log; "
        "rm -f /conf/ctf-site-ready /conf/ctf-site-applying /conf/ctf-site-failed "
        "/conf/ctf-site-apply.lock /var/log/ctf-site-apply.log "
        "/usr/local/etc/inc/plugins.inc.d/gamenet.inc; "
        "find /var/log -type f -exec sh -c ': > \"$1\"' _ {} \\;; "
        "rm -rf /tmp/* /var/tmp/* /root/.cache; touch /firstboot; sync"
    )
    code, _, error = _ssh(db, host, sanitize)
    if code:
        raise ImageWorkflowError(f"builder sanitization failed: {error[:300]}")
    _power_off_builder(db, client, image, host)


def _record_validation(image: OpnsenseImage, name: str, **data) -> None:
    results = json.loads(image.validation_results) if image.validation_results else {}
    results[name] = {"passed": True, "at": utcnow().isoformat(), **data}
    image.validation_results = json.dumps(results, sort_keys=True)


def _validate_clone_one(db: Session, image: OpnsenseImage, host: str, cidr: str) -> str:
    _validate_builder(db, image, host, cidr, "WAN-only clone validation")
    fingerprint = _fingerprint(db, host)
    _record_validation(image, "clone_wan", public_ip=host, ssh_host_key=fingerprint)
    db.commit()
    return fingerprint


def _validate_clone_two(db: Session, image: OpnsenseImage, host: str, attachment: dict,
                        cidr: str, peer_host: str, peer_ip: str) -> str:
    """Use the production snapshot-site configurator, then validate LAN/NAT policy."""
    from api.services.gamenet_provider import configure_snapshot_validation_site
    configure_snapshot_validation_site(
        db, host=host, private_ip="172.31.254.1", lan_mac=attachment["mac_address"],
        expected_version=image.version, control_plane_cidr=cidr,
    )
    command = (
        "set -eu; wan_if=$(route -n get default | awk '/interface:/{print $2}'); "
        "lan_if=$(ifconfig -l | tr ' ' '\\n' | while read i; do "
        f"if ifconfig \"$i\" | grep -qiF {shlex.quote('ether ' + attachment['mac_address'].lower())}; then echo \"$i\"; fi; done); "
        "test -n \"$wan_if\" || { echo 'default WAN interface missing' >&2; exit 1; }; "
        "test -n \"$lan_if\" || { echo 'VPC MAC did not map to a LAN interface' >&2; exit 1; }; "
        f"ifconfig \"$wan_if\" | grep -F {shlex.quote('inet ' + host)} >/dev/null || "
        "{ echo 'public WAN address missing' >&2; exit 1; }; "
        "ifconfig \"$lan_if\" | grep -F 'inet 172.31.254.1' >/dev/null || "
        "{ echo 'private LAN address missing' >&2; exit 1; }; "
        "pfctl -sr | grep -F \"pass in quick on $lan_if inet from ($lan_if:network) to any\" >/dev/null || "
        "{ echo 'effective LAN pass rule missing' >&2; exit 1; }; "
        "pfctl -sn | grep -E 'nat on' >/dev/null || { echo 'effective outbound NAT missing' >&2; exit 1; }"
    )
    code, output, error = _ssh(db, host, command)
    if code:
        raise ImageWorkflowError(f"VPC clone validation failed: {(error or output)[:300]}")
    # The WAN-only clone intentionally has no inbound VPC pass rule.  Initiate
    # the connectivity probe from that peer toward clone two's configured LAN,
    # where the production site policy permits private traffic.
    peer_command = (
        f"ifconfig -a | grep -F {shlex.quote('inet ' + peer_ip)} >/dev/null; "
        "ping -c 1 -t 3 172.31.254.1"
    )
    code, output, error = _ssh(db, peer_host, peer_command)
    if code:
        raise ImageWorkflowError(f"VPC private connectivity failed: {(error or output)[:300]}")
    fingerprint = _fingerprint(db, host)
    _record_validation(image, "clone_vpc", public_ip=host, private_ip="172.31.254.1",
                       ssh_host_key=fingerprint)
    db.commit()
    return fingerprint


def _configure_validation_peer(db: Session, host: str, attachment: dict) -> str:
    """Give clone one a temporary VPC address after its WAN-only gate passes."""
    peer_ip = "172.31.254.2"
    mac = attachment["mac_address"].lower()
    command = (
        "set -eu; iface=''; for candidate in $(ifconfig -l); do "
        "value=$(ifconfig \"$candidate\" 2>/dev/null | awk '/ether/{print tolower($2); exit}'); "
        f"if [ \"$value\" = {shlex.quote(mac)} ]; then iface=$candidate; fi; done; "
        f"test -n \"$iface\"; ifconfig \"$iface\" inet {peer_ip}/28 up"
    )
    code, output, error = _ssh(db, host, command)
    if code:
        raise ImageWorkflowError(f"could not prepare private validation peer: {(error or output)[:300]}")
    return peer_ip


def _run_image_build_vultr(db: Session, image_id: int, *, vultr_factory=VultrImageClient,
                    bootstrap_downloader=download_bootstrap) -> None:
    image = db.get(OpnsenseImage, image_id)
    if not image or image.status in TERMINAL_STATES:
        return
    client = None
    try:
        cidr = validate_control_plane_cidr()
        validate_release(image.version)
        validate_bootstrap_url(image.bootstrap_source_url or "")
        client = vultr_factory()
        os_id = client.preflight(image.base_os)
        _, public_key = get_or_create_platform_keypair(db)
        ssh_key_id = client.ensure_ssh_key(public_key)
        validations = json.loads(image.validation_results or "{}")
        snapshot_resume = (
            not image.snapshot_id and image.phase == "snapshotting"
            and validations.get("builder_boot_2", {}).get("passed")
        )
        builder_key = validations.get("builder_boot_2", {}).get("ssh_host_key")

        if snapshot_resume:
            builder = client.instance(image.builder_instance_id)
            if builder.get("power_status") != "stopped":
                host = builder.get("main_ip")
                if not host:
                    raise ImageWorkflowError("snapshot resume cannot resolve the builder address")
                if _guest_ssh_online(host):
                    _sanitize_and_halt(db, client, image, host)
                else:
                    # A prior run can be interrupted after the clean guest halt
                    # but before Vultr records the corresponding power state.
                    client.halt(image.builder_instance_id)
                    client.wait_stopped(image.builder_instance_id)

        if not image.snapshot_id and not snapshot_resume:
            if not image.builder_firewall_group_id:
                _set_phase(db, image, "creating_builder")
                image.builder_firewall_group_id = client.create_firewall(image.version, cidr)
                db.commit()
            if not image.builder_instance_id:
                image.builder_instance_id = client.create_builder(image, os_id=os_id, ssh_key_id=ssh_key_id)
                db.commit()
            builder = client.wait_instance(image.builder_instance_id)
            host = builder.get("main_ip")
            if not host:
                raise ImageWorkflowError("builder did not receive a public IPv4 address")

            state = _guest_state(db, host)
            if state == "freebsd":
                _set_phase(db, image, "bootstrapping")
                _verify_freebsd_base(db, host)
                script, digest = bootstrap_downloader(image.bootstrap_source_url)
                image.bootstrap_sha256 = digest
                db.commit()
                config = render_golden_config(db, image, cidr).encode()
                _upload_atomic(db, host, "/conf/config.xml", config)
                _upload_atomic(db, host, "/root/opnsense-bootstrap.sh", script, 0o700)
                command = (f"nohup sh /root/opnsense-bootstrap.sh -r {shlex.quote(image.version)} -y "
                           ">/var/log/opnsense-bootstrap.log 2>&1 </dev/null &")
                code, _, error = _ssh(db, host, command, timeout=30)
                if code:
                    raise ImageWorkflowError(f"could not launch OPNsense bootstrap: {error[:300]}")
            elif state not in {"converting", "opnsense"}:
                raise ImageWorkflowError(f"unrecognized builder state: {state}")

            _wait_for_opnsense(db, host, image.version)
            _set_phase(db, image, "validating")
            _validate_builder(db, image, host, cidr, "first disk-boot validation")
            builder_key = _fingerprint(db, host)
            first_boot = _boot_id(db, host)
            _record_validation(image, "builder_boot_1", public_ip=host, boot_id=first_boot,
                               ssh_host_key=builder_key)
            db.commit()

            _power_off_builder(db, client, image, host)
            client.start(image.builder_instance_id)
            client.wait_instance(image.builder_instance_id)
            _validate_builder(db, image, host, cidr, "second disk-boot validation")
            second_boot = _boot_id(db, host)
            if second_boot == first_boot:
                raise ImageWorkflowError("second validation did not prove a changed boot identity")
            _record_validation(image, "builder_boot_2", public_ip=host, boot_id=second_boot,
                               ssh_host_key=builder_key)
            db.commit()
            _set_phase(db, image, "snapshotting")
            _sanitize_and_halt(db, client, image, host)

        if not image.snapshot_id:
            image.snapshot_id = client.create_snapshot(image)
            db.commit()
        _set_phase(db, image, "snapshotting")
        client.wait_snapshot(image.snapshot_id)
        if not builder_key:
            validations = json.loads(image.validation_results or "{}")
            builder_key = validations.get("builder_boot_2", {}).get("ssh_host_key")
        if not builder_key:
            raise ImageWorkflowError("snapshot resume is missing the builder SSH host-key result")

        if not image.test_instance_id:
            image.test_instance_id = client.create_clone(image, 1)
            db.commit()
        clone_one = client.wait_instance(image.test_instance_id)
        validations = json.loads(image.validation_results or "{}")
        clone_one_key = validations.get("clone_wan", {}).get("ssh_host_key")
        if not clone_one_key:
            clone_one_key = _validate_clone_one(db, image, clone_one["main_ip"], cidr)
        if clone_one_key == builder_key:
            raise ImageWorkflowError("clone one reused the builder SSH host key")

        if not image.validation_vpc_id:
            image.validation_vpc_id = client.create_validation_vpc(image)
            db.commit()
        peer_attachment = client.attach_vpc(image.test_instance_id, image.validation_vpc_id)
        peer_ip = _configure_validation_peer(db, clone_one["main_ip"], peer_attachment)
        if not image.second_test_instance_id:
            image.second_test_instance_id = client.create_clone(image, 2)
            db.commit()
        clone_two = client.wait_instance(image.second_test_instance_id)
        attachment = client.attach_vpc(image.second_test_instance_id, image.validation_vpc_id)
        clone_two_key = _validate_clone_two(
            db, image, clone_two["main_ip"], attachment, cidr, clone_one["main_ip"], peer_ip,
        )
        if len({builder_key, clone_one_key, clone_two_key}) != 3:
            raise ImageWorkflowError("builder and clone SSH host keys are not unique")

        image.validated_at = image.completed_at = utcnow()
        image.build_duration_seconds = elapsed_seconds(image)
        image.status = image.phase = "ready"
        image.error_detail = None
        _audit(db, "opnsense_image_validation", image, snapshot_id=image.snapshot_id)
        db.commit()
        cleanup_validated_image(db, image, client)
    except Exception as exc:
        db.rollback()
        image = db.get(OpnsenseImage, image_id)
        if image:
            image.status = "failed"
            image.error_detail = redact_error(exc)
            _audit(db, "opnsense_image_failure", image, phase=image.phase, error=image.error_detail)
            db.commit()
    finally:
        if client:
            client.close()


def cleanup_remote(image: OpnsenseImage, client: VultrImageClient, *, preserve_snapshot: bool) -> None:
    errors = []
    resources = [
        ("instances", "test_instance_id"), ("instances", "second_test_instance_id"),
        ("instances", "builder_instance_id"), ("vpcs", "validation_vpc_id"),
        ("firewalls", "builder_firewall_group_id"),
    ]
    # Legacy IDs are cleanup-only and are never created by this workflow.
    resources.extend([("iso", "vultr_iso_id"), ("vpcs", "builder_vpc_id")])
    if not preserve_snapshot:
        resources.append(("snapshots", "snapshot_id"))
    for kind, attribute in resources:
        identifier = getattr(image, attribute)
        if not identifier:
            continue
        try:
            client.delete(kind, identifier)
            setattr(image, attribute, None)
        except Exception as exc:
            errors.append(f"{kind}/{identifier}: {redact_error(exc)}")
    if errors:
        raise ImageWorkflowError("; ".join(errors))


def cleanup_validated_image(db: Session, image: OpnsenseImage, client: VultrImageClient) -> None:
    try:
        cleanup_remote(image, client, preserve_snapshot=True)
        image.error_detail = None
    except Exception as exc:
        image.error_detail = "Snapshot validated; cleanup incomplete: " + redact_error(exc)
        _audit(db, "opnsense_image_cleanup_failure", image, error=image.error_detail)
    db.commit()


def _default_aws_workflow_factory():
    from api.services.aws.opnsense_workflow import AwsOpnsenseWorkflow
    return AwsOpnsenseWorkflow.from_env()


def new_image(db: Session, version: str, *, provider_factory=None,
              vultr_factory=None) -> OpnsenseImage:
    """Create a resumable AWS AMI job; explicit legacy factories are test-only."""
    if vultr_factory is not None:
        return _new_image_vultr(db, version, vultr_factory=vultr_factory)
    release = validate_release(version)
    validate_control_plane_cidr()
    if db.query(OpnsenseImage).filter(
            OpnsenseImage.status.in_(RUNNING_STATES | {"interrupted"})).first():
        raise ImageWorkflowError("another OPNsense image job is already running")
    provider = (provider_factory or _default_aws_workflow_factory)()
    placement = provider.preflight(SUPPORTED_RELEASES[release])
    row = OpnsenseImage(
        version=release, build_method=BUILD_METHOD, base_os=SUPPORTED_RELEASES[release],
        bootstrap_source_url=BOOTSTRAP_SOURCE_URL, status="creating_builder",
        phase="creating_builder", region=placement["region"],
        availability_zone=placement["availability_zone"],
    )
    db.add(row); db.commit(); db.refresh(row)
    return row


def run_image_build(db: Session, image_id: int, *, provider_factory=None,
                    vultr_factory=None, bootstrap_downloader=download_bootstrap) -> None:
    if vultr_factory is not None:
        return _run_image_build_vultr(
            db, image_id, vultr_factory=vultr_factory,
            bootstrap_downloader=bootstrap_downloader,
        )
    image = db.get(OpnsenseImage, image_id)
    if not image or image.status in TERMINAL_STATES:
        return
    try:
        provider = (provider_factory or _default_aws_workflow_factory)()
        result = provider.build(db, image, bootstrap_downloader)
        evidence = result["validation_results"]
        if not evidence.get("public_clone", {}).get("passed") or not evidence.get("private_clone", {}).get("passed"):
            raise ImageWorkflowError("public and private AMI validation must both pass")
        for field in (
            "builder_instance_id", "builder_vpc_id", "builder_subnet_id",
            "validation_subnet_id", "ami_id",
        ):
            setattr(image, field, result.get(field))
        image.backing_snapshot_ids_json = json.dumps(result["snapshot_ids"], sort_keys=True)
        image.validation_results = json.dumps(evidence, sort_keys=True)
        image.validated_at = image.completed_at = utcnow()
        image.build_duration_seconds = elapsed_seconds(image)
        image.status = image.phase = "ready"
        image.error_detail = None
        _audit(db, "opnsense_ami_validation", image, ami_id=image.ami_id)
        db.commit()
    except Exception as exc:
        db.rollback()
        image = db.get(OpnsenseImage, image_id)
        if image:
            image.status = "failed"
            image.error_detail = redact_error(exc)
            _audit(db, "opnsense_ami_failure", image, phase=image.phase, error=image.error_detail)
            db.commit()


def cleanup_local(_image: OpnsenseImage, **_kwargs) -> None:
    """Compatibility hook for retiring historical ISO records; no local artifacts remain."""
