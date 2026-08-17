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
import logging
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
RUNNING_STATES = {"creating_builder", "bootstrapping", "validating", "snapshotting"}
RESUMABLE_STATES = RUNNING_STATES | {"interrupted", "failed"}
TERMINAL_STATES = {"ready", "active", "retired"}
ACTIVE_SETTING = "active_opnsense_image_id"
POLL_SECONDS = int(os.environ.get("OPNSENSE_IMAGE_POLL_SECONDS", "10"))
POLL_TIMEOUT = int(os.environ.get("OPNSENSE_IMAGE_TIMEOUT_SECONDS", "3600"))
BOOTSTRAP_STALL_TIMEOUT = int(os.environ.get("OPNSENSE_BOOTSTRAP_STALL_SECONDS", "600"))

logger = logging.getLogger(__name__)


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


def make_pkgbase_compatible_bootstrap(content: bytes) -> bytes:
    """Keep pkgbase OS files available until OPNsense replaces base and kernel."""
    upstream = b"""\tif pkg -N; then
\t\tpkg unlock -ya
\t\tpkg delete -fa
\tfi
\trm -rf /var/db/pkg/*"""
    replacement = b"""\tif pkg -N; then
\t\tpkg unlock -ya
\t\tif pkg query '%n' | grep -q '^FreeBSD-'; then
\t\t\tPACKAGES=\"$(pkg query '%n' | grep -v '^FreeBSD-' || true)\"
\t\t\tif [ -n \"${PACKAGES}\" ]; then
\t\t\t\tpkg delete -fy ${PACKAGES}
\t\t\tfi
\t\telse
\t\t\tpkg delete -fa
\t\tfi
\tfi
\trm -rf /var/db/pkg/*"""
    if content.count(upstream) != 1:
        raise ImageWorkflowError("upstream bootstrap package block changed")
    content = content.replace(upstream, replacement, 1)
    upstream_finish = b"\topnsense-update ${DO_VERBOSE} -bkf\n\treboot"
    replacement_finish = b"""\topnsense-update ${DO_VERBOSE} -bkf
\tif [ -f /root/ctf-golden-config.xml ]; then
\t\tinstall -d -m 700 /conf
\t\tinstall -m 600 /root/ctf-golden-config.xml /conf/config.xml
\t\tsync
\tfi
\treboot"""
    if content.count(upstream_finish) != 1:
        raise ImageWorkflowError("upstream bootstrap final block changed")
    return content.replace(upstream_finish, replacement_finish, 1)


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
    return str(exc)[:1000]


def _audit(db: Session, action: str, image: OpnsenseImage, **metadata) -> None:
    db.add(AdminAudit(action=action, metadata_json=json.dumps(
        {"image_id": image.id, "version": image.version, **metadata}, sort_keys=True,
    )))


def _set_phase(db: Session, image: OpnsenseImage, phase: str) -> None:
    image.phase = image.status = phase
    image.error_detail = None
    db.commit()


def _ssh(db: Session, host: str, command: str, *, timeout: int = 180,
         retry: bool = True) -> tuple[int, str, str]:
    private_key, _ = get_or_create_platform_keypair(db)
    key = paramiko.Ed25519Key.from_private_key(io.StringIO(private_key))
    deadline = time.monotonic() + (POLL_TIMEOUT if retry else 1)
    last_error = None
    while time.monotonic() < deadline:
        for username in ("root", "freebsd", "ec2-user"):
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            try:
                client.connect(host, username=username, pkey=key, allow_agent=False,
                               look_for_keys=False, timeout=15, banner_timeout=15,
                               auth_timeout=15)
                # Official FreeBSD images disable direct root login. Depending
                # on the image release, their cloud user receives passwordless
                # doas or sudo. Converted OPNsense images instead expose the
                # managed root key. Select POSIX sh in both cases because the
                # account's login shell may be csh.
                shell = _posix_command(command)
                if username != "root":
                    privileged = (
                        "if test -x /usr/local/bin/doas; then "
                        f"exec /usr/local/bin/doas /bin/sh -c {shlex.quote(command)}; "
                        "elif test -x /usr/local/bin/sudo; then "
                        f"exec /usr/local/bin/sudo -n /bin/sh -c {shlex.quote(command)}; "
                        "elif command -v sudo >/dev/null 2>&1; then "
                        f"exec sudo -n /bin/sh -c {shlex.quote(command)}; "
                        "else echo 'no supported privilege escalation tool' >&2; exit 127; fi"
                    )
                    shell = _posix_command(privileged)
                _stdin, stdout, stderr = client.exec_command(shell, timeout=timeout)
                return (stdout.channel.recv_exit_status(), stdout.read().decode(errors="replace"),
                        stderr.read().decode(errors="replace"))
            except paramiko.AuthenticationException as exc:
                last_error = exc
                continue
            except Exception as exc:
                last_error = exc
                break
            finally:
                client.close()
        if not retry:
            break
        time.sleep(POLL_SECONDS)
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
<interfaces><wan><if>ena0</if><descr>WAN</descr><enable>1</enable><ipaddr>dhcp</ipaddr><ipaddrv6>none</ipaddrv6><blockpriv>1</blockpriv><blockbogons>1</blockbogons></wan></interfaces>
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


def bootstrap_launch_command(version: str) -> str:
    release = validate_release(version)
    bootstrap = (
        f"/bin/sh /root/opnsense-bootstrap.sh -r {shlex.quote(release)} -y 2>&1 | "
        "/usr/bin/tee -a /var/log/opnsense-bootstrap.log /dev/console >/dev/null"
    )
    return (
        "printf 'nameserver 169.254.169.253\\n' > /etc/resolv.conf && "
        ": > /var/run/ctf-opnsense-bootstrap-launched && "
        f"/usr/sbin/daemon -f /bin/sh -c {shlex.quote(bootstrap)}"
    )


def _launch_bootstrap_daemon(db: Session, host: str, version: str) -> None:
    deadline = time.monotonic() + 300
    while True:
        try:
            code, output, error = _ssh(
                db, host, bootstrap_launch_command(version),
                timeout=POLL_TIMEOUT, retry=False,
            )
        except ImageWorkflowError as exc:
            detail = str(exc).lower()
            expected_disconnects = (
                "socket is closed", "eof", "ssh session not active",
                "error reading ssh protocol banner",
            )
            if not any(marker in detail for marker in expected_disconnects):
                raise
            try:
                marker_code, _, _ = _ssh(
                    db, host, "test -f /var/run/ctf-opnsense-bootstrap-launched",
                    retry=False,
                )
            except ImageWorkflowError:
                marker_code = 1
            if marker_code == 0:
                logger.info("OPNsense bootstrap disconnected after launch: %s", exc)
                return
            if time.monotonic() >= deadline:
                raise ImageWorkflowError(
                    f"could not launch OPNsense bootstrap before SSH deadline: {exc}"
                ) from exc
            logger.info("OPNsense bootstrap SSH unavailable before launch; retrying")
            time.sleep(POLL_SECONDS)
            continue
        if code not in {0, -1}:
            detail = (error or output or f"exit {code}")[-1000:]
            raise ImageWorkflowError(f"OPNsense bootstrap failed: {detail}")
        return


def _wait_for_opnsense(db: Session, host: str, version: str, *, diagnostics=None) -> None:
    def failure(message: str) -> ImageWorkflowError:
        if diagnostics is not None:
            try:
                detail = diagnostics()
            except Exception as exc:
                detail = f"diagnostics unavailable: {redact_error(exc)}"
            if detail:
                message = f"{message}\n{detail}"
        return ImageWorkflowError(message)

    deadline = time.monotonic() + POLL_TIMEOUT
    last = "OPNsense has not answered"
    last_progress = None
    stalled_polls = 0
    while time.monotonic() < deadline:
        try:
            command = (
                "if pgrep -f '[o]pnsense-bootstrap' >/dev/null; then "
                "tail -n 20 /var/log/opnsense-bootstrap.log >&2 2>/dev/null; exit 1; "
                "elif test -x /usr/local/sbin/configctl; then opnsense-version -v; "
                "else tail -n 20 /var/log/opnsense-bootstrap.log >&2 2>/dev/null; exit 2; fi"
            )
            code, output, error = _ssh(
                db, host, command, retry=False,
            )
            if code == 0 and release_matches(output.strip(), version):
                return
            last = (error or output or f"exit {code}")[-1000:]
        except Exception as exc:
            last = redact_error(exc)
        else:
            if code == 2:
                raise failure(
                    f"OPNsense bootstrap exited before conversion completed: {last}"
                )
            if code:
                if last != last_progress:
                    logger.info("OPNsense bootstrap progress: %s", last)
                    last_progress = last
                    stalled_polls = 0
                else:
                    stalled_polls += 1
                    if stalled_polls * POLL_SECONDS >= BOOTSTRAP_STALL_TIMEOUT:
                        raise failure(f"OPNsense bootstrap stalled: {last}")
        time.sleep(POLL_SECONDS)
    raise failure(f"bootstrap reboot did not return the expected OPNsense release: {last}")


def _provenance(image: OpnsenseImage) -> str:
    return hashlib.sha256(f"ctf-opnsense:{image.id}:{image.version}".encode()).hexdigest()


def builder_validation_command(*, public_ip: str, version: str, cidr: str,
                               provenance: str, nic_count: int = 1) -> str:
    php = ('require_once("config.inc"); $wan=$config["interfaces"]["wan"]["if"]??""; '
           '$lan=isset($config["interfaces"]["lan"])?"yes":"no"; '
           '$p=$config["system"]["ctf_builder_provenance"]??""; echo "$wan $lan $p";')
    source = str(ip_network(cidr).network_address)
    def failed(message: str) -> str:
        return f"{{ echo {shlex.quote(message)} >&2; exit 1; }}"

    return (
        f"set -eu; actual_version=$(opnsense-version -v); case \"$actual_version\" in "
        f"{shlex.quote(version)}|{shlex.quote(version)}.*|{shlex.quote(version)}_*) ;; "
        f"*) {failed('unexpected OPNsense version')};; esac; "
        f"test -x /usr/local/sbin/configctl || {failed('configctl missing')}; "
        f"test -w /conf/config.xml || {failed('golden config not writable')}; "
        f"set -- $(/usr/local/bin/php -r {shlex.quote(php)}); "
        f"test \"$#\" -eq 3 || {failed('golden config not loaded')}; wan_if=$1; "
        f"test \"$2\" = no || {failed('golden config unexpectedly contains LAN')}; "
        f"test \"$3\" = {shlex.quote(provenance)} || {failed('golden config not loaded')}; "
        "set -- $(ifconfig -l | tr ' ' '\\n' | grep -E '^ena[0-9]+$'); "
        f"test \"$#\" -eq {nic_count} || {failed('unexpected ENA NIC count')}; "
        f"test \"$wan_if\" = ena0 || {failed('WAN interface mismatch')}; "
        f"ifconfig \"$wan_if\" | grep -F {shlex.quote('inet ' + public_ip)} >/dev/null || {failed('WAN address missing')}; "
        f"route -n get default | grep -F \"interface: $wan_if\" >/dev/null || {failed('default route mismatch')}; "
        f"test -s /root/.ssh/authorized_keys || {failed('managed root key missing')}; "
        f"/usr/local/sbin/sshd -T | grep -qi '^permitrootlogin yes$' || {failed('root SSH login disabled')}; "
        f"/usr/local/sbin/sshd -T | grep -qi '^pubkeyauthentication yes$' || {failed('SSH public key authentication disabled')}; "
        f"/usr/local/sbin/sshd -T | grep -qi '^passwordauthentication no$' || {failed('SSH password authentication enabled')}; "
        f"/usr/local/sbin/sshd -T | grep -qi '^kbdinteractiveauthentication no$' || {failed('SSH keyboard authentication enabled')}; "
        f"pfctl -sr | grep -F {shlex.quote('from ' + source + ' to')} | grep -E 'port = (ssh|22)' >/dev/null || {failed('builder firewall rule missing')}"
    )


def _validate_builder(db: Session, image: OpnsenseImage, host: str, cidr: str, label: str) -> None:
    command = builder_validation_command(
        public_ip=host, version=image.version, cidr=cidr, provenance=_provenance(image),
    )
    code, output, error = _ssh(db, host, command)
    if code:
        raise ImageWorkflowError(f"{label} failed: {(error or output)[:300]}")


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


def _power_off_builder(db: Session, client,
                       image: OpnsenseImage, host: str) -> None:
    _halt(db, host)
    _wait_for_guest_shutdown(host)
    # Once the guest is offline, synchronize the EC2 state before imaging.
    client.halt(image.builder_instance_id)
    client.wait_stopped(image.builder_instance_id)


def _sanitize_and_halt(db: Session, client, image: OpnsenseImage, host: str) -> None:
    sanitize = (
        "rm -f /conf/sshd/ssh_host_* /etc/ssh/ssh_host_* /usr/local/etc/ssh/ssh_host_* "
        "/var/db/dhclient.leases.* /var/db/dhclient.leases /root/.*history /root/.sh_history "
        "/root/opnsense-bootstrap.sh /root/ctf-golden-config.xml "
        "/var/log/opnsense-bootstrap.log; "
        "rm -f /var/run/ctf-opnsense-bootstrap-launched; "
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


def _default_aws_workflow_factory():
    from api.services.aws.opnsense_workflow import AwsOpnsenseWorkflow
    return AwsOpnsenseWorkflow.from_env()


def new_image(db: Session, version: str, *, provider_factory=None) -> OpnsenseImage:
    """Create a resumable AWS AMI job."""
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
                    bootstrap_downloader=download_bootstrap) -> None:
    image = db.get(OpnsenseImage, image_id)
    if not image or image.status in TERMINAL_STATES:
        return
    try:
        provider = (provider_factory or _default_aws_workflow_factory)()
        result = provider.build(db, image, bootstrap_downloader)
        evidence = result["validation_results"]
        if not evidence.get("public_clone", {}).get("passed") or not evidence.get("private_clone", {}).get("passed"):
            raise ImageWorkflowError("public and private AMI validation must both pass")
        provider.cleanup_temporary(image, result)
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
