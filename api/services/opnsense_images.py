"""Secure, resumable OPNsense ISO-to-Vultr-snapshot workflow."""

from __future__ import annotations

import base64
import bz2
import hashlib
import os
import re
import secrets
import shutil
import subprocess
import time
from ipaddress import ip_network
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
import io
import json
import paramiko
from sqlalchemy.orm import Session

from api.models import AdminAudit, OpnsenseImage, PlatformSettings, utcnow
from api.services.ssh_keys import get_or_create_platform_keypair

RELEASE_RE = re.compile(r"^(?:2[4-9]|[3-9][0-9])\.(?:1|7)$")
RUNNING_STATES = {"downloading", "verifying", "decompressing", "importing", "validating", "snapshotting"}
RESUMABLE_STATES = RUNNING_STATES | {"interrupted"}
TERMINAL_STATES = {"ready", "active", "failed", "retired"}
MIRROR_BASE = os.environ.get("OPNSENSE_MIRROR_BASE", "https://pkg.opnsense.org/releases/mirror/")
ISO_DIR = Path(os.environ.get("OPNSENSE_ISO_DIR", "/var/lib/ctf-opnsense"))
TRUSTED_KEY = Path(os.environ.get("OPNSENSE_TRUSTED_KEY", Path(__file__).with_name("opnsense-release.pub")))
MIN_FREE_BYTES = int(os.environ.get("OPNSENSE_MIN_FREE_BYTES", str(3 * 1024**3)))
MAX_DOWNLOAD_BYTES = int(os.environ.get("OPNSENSE_MAX_DOWNLOAD_BYTES", str(2 * 1024**3)))
POLL_SECONDS = int(os.environ.get("OPNSENSE_IMAGE_POLL_SECONDS", "10"))
POLL_TIMEOUT = int(os.environ.get("OPNSENSE_IMAGE_TIMEOUT_SECONDS", "1800"))
ACTIVE_SETTING = "active_opnsense_image_id"


class ImageWorkflowError(RuntimeError):
    pass


def validate_release(version: str) -> str:
    value = (version or "").strip()
    if not RELEASE_RE.fullmatch(value):
        raise ValueError("version must be an OPNsense major release such as 26.7")
    return value


def artifact_urls(version: str, mirror_base: str = MIRROR_BASE) -> dict[str, str]:
    version = validate_release(version)
    base = mirror_base.rstrip("/") + "/"
    parsed = urlparse(base)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("OPNsense mirror base must be an HTTPS origin")
    filename = f"OPNsense-{version}-dvd-amd64.iso.bz2"
    return {
        "filename": filename,
        "artifact": urljoin(base, filename),
        "checksum": urljoin(base, f"OPNsense-{version}-checksums-amd64.sha256"),
        "signature": urljoin(base, filename.removesuffix(".bz2") + ".sig"),
    }


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_published_checksum(text: str, filename: str) -> str:
    if Path(filename).name != filename:
        raise ImageWorkflowError("invalid artifact filename")
    pattern = re.compile(rf"(?:SHA256 \({re.escape(filename)}\) = |^)([0-9a-fA-F]{{64}})(?:\s+[* ]?{re.escape(filename)})?$", re.M)
    match = pattern.search(text)
    if not match:
        raise ImageWorkflowError("published checksum does not contain the expected artifact")
    return match.group(1).lower()


def ensure_disk_space(directory: Path = ISO_DIR, required: int = MIN_FREE_BYTES) -> None:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if shutil.disk_usage(directory).free < required:
        raise ImageWorkflowError("insufficient temporary storage; at least 3 GB free is required")


def stream_download(client: httpx.Client, url: str, destination: Path, *, max_bytes: int = MAX_DOWNLOAD_BYTES) -> str:
    """Download atomically, rejecting cross-origin redirects and oversized bodies."""
    temporary = destination.with_name(destination.name + ".part")
    temporary.unlink(missing_ok=True)
    origin = urlparse(url)
    total = 0
    digest = hashlib.sha256()
    try:
        with client.stream("GET", url, follow_redirects=False) as response:
            if response.is_redirect:
                target = urlparse(urljoin(url, response.headers.get("location", "")))
                if (target.scheme, target.netloc) != (origin.scheme, origin.netloc):
                    raise ImageWorkflowError("cross-origin download redirect rejected")
                raise ImageWorkflowError("download redirect rejected")
            response.raise_for_status()
            length = int(response.headers.get("content-length", "0") or 0)
            if length > max_bytes:
                raise ImageWorkflowError("artifact exceeds maximum download size")
            with temporary.open("xb") as output:
                for chunk in response.iter_bytes(1024 * 1024):
                    total += len(chunk)
                    if total > max_bytes:
                        raise ImageWorkflowError("artifact exceeds maximum download size")
                    output.write(chunk)
                    digest.update(chunk)
                output.flush()
                os.fsync(output.fileno())
        if total == 0:
            raise ImageWorkflowError("downloaded artifact is empty")
        os.replace(temporary, destination)
        return digest.hexdigest()
    finally:
        temporary.unlink(missing_ok=True)


def decompress_bz2(source: Path, destination: Path, *, max_bytes: int = MAX_DOWNLOAD_BYTES * 4) -> str:
    temporary = destination.with_name(destination.name + ".part")
    temporary.unlink(missing_ok=True)
    total = 0
    digest = hashlib.sha256()
    try:
        with bz2.open(source, "rb") as compressed, temporary.open("xb") as output:
            for chunk in iter(lambda: compressed.read(1024 * 1024), b""):
                total += len(chunk)
                if total > max_bytes:
                    raise ImageWorkflowError("decompressed ISO exceeds maximum size")
                output.write(chunk); digest.update(chunk)
            output.flush(); os.fsync(output.fileno())
        if total == 0:
            raise ImageWorkflowError("decompressed ISO is empty")
        os.replace(temporary, destination)
        return digest.hexdigest()
    except (OSError, EOFError) as exc:
        raise ImageWorkflowError("invalid or truncated bzip2 artifact") from exc
    finally:
        temporary.unlink(missing_ok=True)


def verify_iso_signature(iso: Path, signature: Path, trusted_key: Path = TRUSTED_KEY) -> None:
    if not trusted_key.is_file():
        raise ImageWorkflowError("trusted OPNsense release public key is not installed")
    decoded = signature.with_suffix(signature.suffix + ".decoded")
    try:
        try:
            decoded.write_bytes(base64.b64decode(b"".join(signature.read_bytes().split()), validate=True))
        except ValueError as exc:
            raise ImageWorkflowError("invalid base64 release signature") from exc
        result = subprocess.run(
            ["openssl", "dgst", "-sha256", "-verify", str(trusted_key), "-signature", str(decoded), str(iso)],
            capture_output=True, text=True, timeout=30, check=False,
        )
        if result.returncode or "Verified OK" not in result.stdout:
            raise ImageWorkflowError("OPNsense ISO signature verification failed")
    finally:
        decoded.unlink(missing_ok=True)


def active_image(db: Session) -> OpnsenseImage | None:
    setting = db.query(PlatformSettings).filter_by(key=ACTIVE_SETTING).first()
    if not setting or not setting.value.isdigit():
        return None
    image = db.get(OpnsenseImage, int(setting.value))
    return image if image and image.status == "active" and image.snapshot_id and image.validated_at else None


def interrupt_running_jobs(db: Session) -> int:
    rows = db.query(OpnsenseImage).filter(OpnsenseImage.status.in_(RUNNING_STATES)).all()
    for row in rows:
        row.status = "interrupted"
        row.error_detail = "The API restarted while this phase was running. Resume to continue safely."
    if rows:
        db.commit()
    return len(rows)


def cleanup_local(image: OpnsenseImage, *, keep_config: bool = False) -> None:
    directory = ISO_DIR / str(image.id)
    for name in (artifact_urls(image.version)["filename"], artifact_urls(image.version)["filename"].removesuffix(".bz2"), "release.sig"):
        (directory / name).unlink(missing_ok=True)
    if not keep_config:
        (directory / "config.xml").unlink(missing_ok=True)
        try:
            directory.rmdir()
        except OSError:
            pass
    public_root = ISO_DIR / "public"
    if image.route_token:
        route = public_root / image.route_token
        for item in route.glob("*") if route.exists() else ():
            item.unlink(missing_ok=True)
        try:
            route.rmdir()
        except OSError:
            pass


def image_payload(image: OpnsenseImage) -> dict:
    return {column.name: (value.isoformat() if hasattr(value, "isoformat") else value)
            for column in image.__table__.columns for value in [getattr(image, column.name)]}


def redact_error(exc: Exception) -> str:
    detail = str(exc).replace(os.environ.get("VULTR_API_KEY", "__never__"), "[redacted]")
    return detail[:1000]


def _system_audit(db: Session, action: str, image: OpnsenseImage, **metadata) -> None:
    db.add(AdminAudit(action=action, metadata_json=json.dumps(
        {"image_id": image.id, "version": image.version, **metadata}, sort_keys=True,
    )))


def new_image(db: Session, version: str) -> OpnsenseImage:
    if db.query(OpnsenseImage).filter(OpnsenseImage.status.in_(RUNNING_STATES | {"awaiting_install", "interrupted"})).first():
        raise ImageWorkflowError("another OPNsense image job is already running")
    urls = artifact_urls(version)
    row = OpnsenseImage(version=version, artifact_url=urls["artifact"], checksum_url=urls["checksum"],
                        signature_url=urls["signature"], route_token=secrets.token_urlsafe(32),
                        builder_config_token=secrets.token_urlsafe(32))
    db.add(row); db.commit(); db.refresh(row)
    return row


def elapsed_seconds(image: OpnsenseImage) -> int:
    return max(0, int((utcnow() - image.created_at).total_seconds()))


class VultrImageClient:
    """Small Vultr v2 adapter whose methods are straightforward to fake in tests."""

    def __init__(self):
        key = os.environ.get("VULTR_API_KEY")
        if not key:
            raise ImageWorkflowError("VULTR_API_KEY is required")
        self.client = httpx.Client(base_url="https://api.vultr.com/v2", timeout=30,
                                   headers={"Authorization": f"Bearer {key}"})

    def close(self):
        self.client.close()

    def request(self, method: str, path: str, **kwargs) -> dict:
        response = self.client.request(method, path, **kwargs)
        if response.status_code not in {200, 201, 202, 204}:
            raise ImageWorkflowError(f"Vultr {method} {path} failed ({response.status_code}): {response.text[:300]}")
        return response.json() if response.content else {}

    def import_iso(self, url: str) -> str:
        return self.request("POST", "/iso", json={"url": url})["iso"]["id"]

    def wait_iso(self, iso_id: str):
        self._wait(lambda: self.request("GET", f"/iso/{iso_id}")["iso"], {"complete"})

    def create_vpc(self, version: str) -> str:
        region = os.environ.get("VULTR_DEFAULT_REGION", "syd")
        return self.request("POST", "/vpcs", json={"region": region, "description": f"ctf-opnsense-builder-{version}",
                                                    "v4_subnet": "172.31.254.0", "v4_subnet_mask": 28})["vpc"]["id"]

    def create_firewall(self, version: str) -> str:
        cidr = os.environ.get("CTF_CONTROL_PLANE_CIDR", "")
        if not cidr:
            raise ImageWorkflowError("CTF_CONTROL_PLANE_CIDR is required for the builder")
        if "/" not in cidr:
            raise ImageWorkflowError("CTF_CONTROL_PLANE_CIDR must be a valid IPv4 CIDR")
        try:
            control_plane = ip_network(cidr, strict=False)
        except ValueError as exc:
            raise ImageWorkflowError("CTF_CONTROL_PLANE_CIDR must be a valid IPv4 CIDR") from exc
        if control_plane.version != 4:
            raise ImageWorkflowError("CTF_CONTROL_PLANE_CIDR must be a valid IPv4 CIDR")
        group = self.request("POST", "/firewalls", json={"description": f"ctf-opnsense-builder-{version}"})["firewall_group"]
        self.request("POST", f"/firewalls/{group['id']}/rules", json={
            "ip_type": "v4", "protocol": "tcp", "subnet": str(control_plane.network_address),
            "subnet_size": control_plane.prefixlen, "port": "22",
        })
        return group["id"]

    def create_builder(self, image: OpnsenseImage) -> str:
        body = {"region": os.environ.get("VULTR_DEFAULT_REGION", "syd"),
                "plan": os.environ.get("GAMENET_FIREWALL_PLAN", "vc2-2c-4gb"), "iso_id": image.vultr_iso_id,
                "label": f"ctf-opnsense-builder-{image.version}-{image.id}", "hostname": "opnsense-builder",
                "attach_vpc": [image.builder_vpc_id], "enable_vpc": True, "firewall_group_id": image.builder_firewall_group_id,
                "enable_ipv6": False, "backups": "disabled"}
        return self.request("POST", "/instances", json=body)["instance"]["id"]

    def instance(self, instance_id: str) -> dict:
        return self.request("GET", f"/instances/{instance_id}")["instance"]

    def detach_iso_and_reboot(self, image: OpnsenseImage):
        self.request("PATCH", f"/instances/{image.builder_instance_id}", json={"iso_id": None})
        self.request("POST", f"/instances/{image.builder_instance_id}/reboot")

    def create_snapshot(self, image: OpnsenseImage) -> str:
        return self.request("POST", "/snapshots", json={"instance_id": image.builder_instance_id,
                                                        "description": f"CTF OPNsense {image.version}"})["snapshot"]["id"]

    def halt(self, instance_id: str):
        self.request("POST", f"/instances/{instance_id}/halt")

    def create_test_instance(self, image: OpnsenseImage) -> str:
        body = {"region": os.environ.get("VULTR_DEFAULT_REGION", "syd"),
                "plan": os.environ.get("GAMENET_FIREWALL_PLAN", "vc2-2c-4gb"), "snapshot_id": image.snapshot_id,
                "label": f"ctf-opnsense-validation-{image.id}", "hostname": "opnsense-validation",
                "enable_ipv6": False, "backups": "disabled", "firewall_group_id": image.builder_firewall_group_id}
        return self.request("POST", "/instances", json=body)["instance"]["id"]

    def wait_instance(self, instance_id: str) -> dict:
        deadline = time.monotonic() + POLL_TIMEOUT
        while time.monotonic() < deadline:
            row = self.instance(instance_id)
            if row.get("status") == "active" and row.get("server_status") in {"ok", "none"}:
                return row
            if row.get("status") in {"failed", "error"}:
                raise ImageWorkflowError("Vultr validation instance failed")
            time.sleep(POLL_SECONDS)
        raise ImageWorkflowError("timed out waiting for validation instance")

    def wait_snapshot(self, snapshot_id: str):
        self._wait(lambda: self.request("GET", f"/snapshots/{snapshot_id}")["snapshot"], {"complete"})

    def delete(self, kind: str, identifier: str | None):
        if identifier:
            self.request("DELETE", f"/{kind}/{identifier}")

    def _wait(self, getter, successful: set[str]):
        deadline = time.monotonic() + POLL_TIMEOUT
        while time.monotonic() < deadline:
            row = getter(); status = row.get("status", "")
            if status in successful:
                return row
            if status in {"failed", "error"}:
                raise ImageWorkflowError(f"Vultr artifact entered {status} state")
            time.sleep(POLL_SECONDS)
        raise ImageWorkflowError("timed out waiting for Vultr artifact")


def _set_phase(db: Session, image: OpnsenseImage, phase: str) -> None:
    image.phase = image.status = phase
    image.error_detail = None
    db.commit()


def generic_builder_config(db: Session) -> str:
    """Generic config: public key only, DHCP WAN and inert documentation LAN."""
    _, public_key = get_or_create_platform_keypair(db)
    # Deliberately contains no password hash or reusable private credential.
    return f"""<?xml version=\"1.0\"?>
<opnsense><system><hostname>opnsense-template</hostname><domain>invalid</domain>
<ssh><enabled>enabled</enabled><permitrootlogin>1</permitrootlogin><passwordauth>0</passwordauth></ssh>
<user><name>root</name><authorizedkeys>{base64.b64encode(public_key.strip().encode()).decode()}</authorizedkeys></user>
</system><interfaces><wan><if>vtnet0</if><ipaddr>dhcp</ipaddr></wan>
<lan><if>vtnet1</if><ipaddr>192.0.2.1</ipaddr><subnet>30</subnet></lan></interfaces></opnsense>"""


def sync_to_awaiting_install(db: Session, image_id: int, *, vultr_factory=VultrImageClient) -> None:
    """Run/re-run persisted phases through the administrator installation gate."""
    image = db.get(OpnsenseImage, image_id)
    if not image:
        return
    directory = ISO_DIR / str(image.id)
    urls = artifact_urls(image.version)
    compressed = directory / urls["filename"]
    iso = directory / urls["filename"].removesuffix(".bz2")
    signature = directory / "release.sig"
    client = None
    try:
        ensure_disk_space(directory)
        http = httpx.Client(timeout=120)
        try:
            if not compressed.exists():
                _set_phase(db, image, "downloading")
                image.compressed_sha256 = stream_download(http, image.artifact_url, compressed); db.commit()
            _set_phase(db, image, "verifying")
            checksum_response = http.get(image.checksum_url, follow_redirects=False); checksum_response.raise_for_status()
            expected = parse_published_checksum(checksum_response.text, compressed.name)
            actual = sha256_file(compressed)
            if actual != expected:
                raise ImageWorkflowError("compressed ISO checksum verification failed")
            image.compressed_sha256 = actual; db.commit()
            if not iso.exists():
                _set_phase(db, image, "decompressing")
                image.iso_sha256 = decompress_bz2(compressed, iso); db.commit()
            if not signature.exists():
                stream_download(http, image.signature_url, signature, max_bytes=1024 * 1024)
            verify_iso_signature(iso, signature)
        finally:
            http.close()
        client = vultr_factory()
        if not image.vultr_iso_id:
            _set_phase(db, image, "importing")
            domain = os.environ.get("DOMAIN")
            if not domain:
                raise ImageWorkflowError("DOMAIN is required for Vultr ISO import")
            route_dir = ISO_DIR / "public" / image.route_token
            route_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            # The Nginx sidecar runs as an unprivileged user and mounts this
            # volume read-only. The random token is the access boundary; the
            # directory must remain traversable for GET/HEAD to reach the file.
            route_dir.chmod(0o755)
            public_iso = route_dir / iso.name
            public_iso.unlink(missing_ok=True)
            os.link(iso, public_iso)
            public_url = f"https://ctf.{domain}/vultr-iso/{image.route_token}/{iso.name}"
            image.vultr_iso_id = client.import_iso(public_url); db.commit()
        client.wait_iso(image.vultr_iso_id)
        # Vultr no longer needs the public route after its private copy is complete.
        old_token = image.route_token
        cleanup_local(image, keep_config=True)
        image.route_token = None; db.commit()
        if not image.builder_vpc_id:
            image.builder_vpc_id = client.create_vpc(image.version); db.commit()
        if not image.builder_firewall_group_id:
            image.builder_firewall_group_id = client.create_firewall(image.version); db.commit()
        if not image.builder_instance_id:
            image.builder_instance_id = client.create_builder(image); db.commit()
        image.status = image.phase = "awaiting_install"; image.error_detail = None
        _system_audit(db, "opnsense_image_sync_complete", image); db.commit()
    except Exception as exc:
        db.rollback(); image = db.get(OpnsenseImage, image_id)
        image.status = "failed"; image.error_detail = redact_error(exc)
        _system_audit(db, "opnsense_image_failure", image, phase=image.phase, error=image.error_detail); db.commit()
    finally:
        if client:
            client.close()


def _builder_ssh(db: Session, host: str, command: str, timeout: int = 120) -> tuple[int, str, str]:
    private_key, _ = get_or_create_platform_keypair(db)
    key = paramiko.Ed25519Key.from_private_key(io.StringIO(private_key))
    deadline = time.monotonic() + POLL_TIMEOUT
    last_error = None
    while time.monotonic() < deadline:
        client = paramiko.SSHClient(); client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(host, username="root", pkey=key, timeout=15, auth_timeout=15)
            _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
            return stdout.channel.recv_exit_status(), stdout.read().decode(errors="replace"), stderr.read().decode(errors="replace")
        except Exception as exc:
            last_error = exc; time.sleep(POLL_SECONDS)
        finally:
            client.close()
    raise ImageWorkflowError(f"builder SSH did not become ready: {last_error}")


def complete_install(db: Session, image_id: int, *, vultr_factory=VultrImageClient) -> None:
    image = db.get(OpnsenseImage, image_id)
    if not image:
        return
    client = None
    try:
        if image.status not in {"awaiting_install", "interrupted", "failed", "validating", "snapshotting"}:
            raise ImageWorkflowError("image is not awaiting installer completion")
        client = vultr_factory()
        _set_phase(db, image, "validating")
        client.detach_iso_and_reboot(image)
        builder = client.wait_instance(image.builder_instance_id)
        host = builder.get("main_ip")
        if not host:
            raise ImageWorkflowError("builder has no public address")
        check = ("test \"$(sysctl -n kern.disks | wc -w | tr -d ' ')\" -ge 1 && "
                 "opnsense-version -v && command -v configctl && "
                 "ifconfig vtnet0 >/dev/null && ifconfig vtnet1 >/dev/null && mount | grep ' on / '")
        code, output, error = _builder_ssh(db, host, check)
        if code or image.version not in output:
            raise ImageWorkflowError(f"builder validation failed: {(error or output)[:300]}")
        sanitize = ("rm -f /etc/ssh/ssh_host_* /var/db/dhclient.leases.* /root/.*history /root/.sh_history; "
                    "find /var/log -type f -exec sh -c ': > \"$1\"' _ {} \\;; "
                    "rm -rf /tmp/* /var/tmp/* /root/.cache; "
                    "touch /firstboot; sync")
        code, _, error = _builder_ssh(db, host, sanitize)
        if code:
            raise ImageWorkflowError(f"builder sanitization failed: {error[:300]}")
        client.halt(image.builder_instance_id)
        _set_phase(db, image, "snapshotting")
        if not image.snapshot_id:
            image.snapshot_id = client.create_snapshot(image); db.commit()
        client.wait_snapshot(image.snapshot_id)
        if not image.test_instance_id:
            image.test_instance_id = client.create_test_instance(image); db.commit()
        test = client.wait_instance(image.test_instance_id)
        test_host = test.get("main_ip")
        if not test_host:
            raise ImageWorkflowError("snapshot validation VM has no address")
        code, output, _ = _builder_ssh(db, test_host, "opnsense-version -v; test -x /usr/local/sbin/configctl")
        if code or image.version not in output:
            raise ImageWorkflowError("snapshot validation deployment failed")
        image.validated_at = image.completed_at = utcnow()
        image.build_duration_seconds = elapsed_seconds(image)
        image.status = image.phase = "ready"; image.error_detail = None; image.builder_config_token = None
        _system_audit(db, "opnsense_image_validation", image, snapshot_id=image.snapshot_id); db.commit()
        cleanup_validated_image(db, image, client)
    except Exception as exc:
        db.rollback(); image = db.get(OpnsenseImage, image_id)
        image.status = "failed"; image.error_detail = redact_error(exc)
        _system_audit(db, "opnsense_image_failure", image, phase=image.phase, error=image.error_detail); db.commit()
    finally:
        if client:
            client.close()


def cleanup_remote(image: OpnsenseImage, client: VultrImageClient, *, preserve_snapshot: bool) -> None:
    # Ordering removes compute before its attached network/security resources.
    errors = []
    resources = [
        ("instances", "test_instance_id"), ("instances", "builder_instance_id"),
        ("iso", "vultr_iso_id"), ("vpcs", "builder_vpc_id"),
        ("firewalls", "builder_firewall_group_id"),
    ]
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
    """Best-effort cleanup that can never invalidate a verified snapshot."""
    errors = []
    try:
        cleanup_remote(image, client, preserve_snapshot=True)
    except Exception as exc:
        errors.append(redact_error(exc))
    try:
        cleanup_local(image)
    except Exception as exc:
        errors.append(redact_error(exc))
    if errors:
        image.error_detail = "Snapshot validated; cleanup incomplete: " + "; ".join(errors)
        _system_audit(db, "opnsense_image_cleanup_failure", image, error=image.error_detail)
    else:
        image.error_detail = None
    # The ready state and validation timestamps were committed before cleanup.
    # Persist successful individual deletions and any retryable artifact IDs.
    db.commit()
