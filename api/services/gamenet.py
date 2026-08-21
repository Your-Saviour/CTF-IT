"""GameNet address allocation and WireGuard credential lifecycle."""

from __future__ import annotations

import base64
import json
from ipaddress import ip_address, ip_network

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from sqlalchemy.orm import Session

from api.models import Site, Team, TeamVPNGateway, User, VPNCredential, Zone, utcnow
from api.services.secrets import decrypt_secret, encrypt_secret
from builder.infrastructure_validation import site_subnets

SITE_POOL = ip_network("10.128.0.0/9")
VPN_POOL = ip_network("10.64.0.0/10")


def dns_label(value: str) -> str:
    """Convert an infrastructure key to its managed DNS label."""
    return value.replace("_", "-").lower()


def site_dns_zone(site: Site) -> str:
    return f"{dns_label(site.key)}.gamenet.test"


def vm_dns_name(vm) -> str | None:
    """Return the stable site-local name for an endpoint VM."""
    if not vm.zone or not vm.vm_type:
        return None
    peers = sorted(
        (candidate for candidate in vm.zone.vms if candidate.vm_type == vm.vm_type),
        key=lambda candidate: candidate.id,
    )
    try:
        ordinal = peers.index(vm) + 1
    except ValueError:
        return None
    host_label = dns_label(vm.vm_type) if len(peers) == 1 else f"{dns_label(vm.vm_type)}-{ordinal}"
    return ".".join((
        host_label, dns_label(vm.zone.key),
        site_dns_zone(vm.zone.site),
    ))


def generate_wireguard_keypair() -> tuple[str, str]:
    private = X25519PrivateKey.generate()
    private_raw = private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                        serialization.NoEncryption())
    public_raw = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(private_raw).decode(), base64.b64encode(public_raw).decode()


def allocate_event_networks(db: Session, event, teams: list[Team], infrastructure: dict) -> None:
    """Create runtime sites/zones and gateway state, safely retrying existing rows."""
    used = {ip_network(row[0]) for row in db.query(Site.allocated_cidr).all()}
    candidates = (block for block in SITE_POOL.subnets(new_prefix=20) if block not in used)
    used_vpn = {row[0] for row in db.query(TeamVPNGateway.vpn_address).all() if row[0]}
    used_vpn.update(row[0] for row in db.query(TeamVPNGateway.platform_address).all() if row[0])
    used_vpn.update(row[0] for row in db.query(Site.tunnel_address).all() if row[0])
    vpn_hosts = (str(host) for host in VPN_POOL.hosts() if str(host) not in used_vpn)
    gateway_spec = infrastructure["vpn_gateway"]
    for team in teams:
        gateway = db.query(TeamVPNGateway).filter_by(team_id=team.id).first()
        if not gateway:
            private, public = generate_wireguard_keypair()
            platform_private, platform_public = generate_wireguard_keypair()
            gateway = TeamVPNGateway(
                team_id=team.id, listen_port=gateway_spec["listen_port"], vpn_address=next(vpn_hosts),
                public_key=public, private_key_encrypted=encrypt_secret(private),
                platform_public_key=platform_public,
                platform_private_key_encrypted=encrypt_secret(platform_private),
                platform_address=next(vpn_hosts),
            )
            db.add(gateway)
        for order, definition in enumerate(infrastructure["sites"]):
            if db.query(Site).filter_by(team_id=team.id, key=definition["key"]).first():
                continue
            cidr = str(next(candidates))
            tunnel_private, tunnel_public = generate_wireguard_keypair()
            infra_subnet, zone_blocks = site_subnets(cidr, len(definition["zones"]))
            site = Site(event_id=event.id, team_id=team.id, key=definition["key"], name=definition["name"],
                        region=definition["region"], allocated_cidr=cidr,
                        infrastructure_subnet=infra_subnet, order=order)
            site.tunnel_public_key = tunnel_public
            site.tunnel_private_key_encrypted = encrypt_secret(tunnel_private)
            site.tunnel_address = next(vpn_hosts)
            db.add(site)
            db.flush()
            for zone_order, (zone_def, (subnet, gateway_address)) in enumerate(zip(definition["zones"], zone_blocks)):
                db.add(Zone(site_id=site.id, key=zone_def["key"], name=zone_def["name"],
                            team_role=zone_def["team"], subnet=subnet,
                            gateway_address=gateway_address, order=zone_order))
    db.flush()


def ensure_user_vpn_credential(db: Session, user: User) -> VPNCredential | None:
    if user.is_admin or not user.team_id:
        return None
    current = db.query(VPNCredential).filter_by(user_id=user.id).first()
    if current and current.team_id == user.team_id and current.status == "active":
        return current
    used = {ip_address(row[0]) for row in db.query(VPNCredential.address).all()}
    # Participant addresses occupy the upper half of the VPN pool; gateways use the lower half.
    address = next(str(host) for host in ip_network("10.96.0.0/11").hosts() if host not in used)
    private, public = generate_wireguard_keypair()
    if current:
        current.team_id, current.address = user.team_id, address
        current.public_key, current.private_key_encrypted = public, encrypt_secret(private)
        current.status, current.revoked_at, current.created_at = "active", None, utcnow()
        credential = current
    else:
        credential = VPNCredential(user_id=user.id, team_id=user.team_id, address=address,
                                   public_key=public, private_key_encrypted=encrypt_secret(private))
    db.add(credential)
    db.flush()
    _sync_team_gateway_if_active(db, user.team_id)
    return credential


def revoke_user_vpn(db: Session, user_id: int) -> None:
    credential = db.query(VPNCredential).filter_by(user_id=user_id, status="active").first()
    if credential:
        credential.status = "revoked"
        credential.revoked_at = utcnow()
        db.flush()
        _sync_team_gateway_if_active(db, credential.team_id)


def _sync_team_gateway_if_active(db: Session, team_id: int) -> None:
    gateway = db.query(TeamVPNGateway).filter_by(team_id=team_id, status="active").first()
    if not gateway or not gateway.vm_id:
        return
    from api.models import VM
    from api.services.gamenet_provider import configure_gateway
    vm = db.query(VM).filter_by(id=gateway.vm_id).one()
    sites = db.query(Site).filter_by(team_id=team_id).order_by(Site.order).all()
    participants = db.query(VPNCredential).filter_by(team_id=team_id, status="active").all()
    configure_gateway(gateway, vm, sites, participants, management_host=gateway.vpn_address)


def render_user_config(db: Session, user: User) -> str:
    credential = ensure_user_vpn_credential(db, user)
    gateway = db.query(TeamVPNGateway).filter_by(team_id=user.team_id).first()
    if not credential or not gateway or not gateway.vm_id:
        raise RuntimeError("GameNet VPN is not ready")
    from api.models import VM
    gateway_vm = db.query(VM).filter_by(id=gateway.vm_id).first()
    endpoint = gateway_vm.public_ip if gateway_vm else None
    if not endpoint:
        raise RuntimeError("GameNet VPN is not ready")
    routes = [gateway.vpn_address + "/32"]
    routes += [site.allocated_cidr for site in db.query(Site).filter_by(team_id=user.team_id).order_by(Site.order)]
    return "\n".join([
        "[Interface]", f"PrivateKey = {decrypt_secret(credential.private_key_encrypted)}",
        f"Address = {credential.address}/32", f"DNS = {gateway.vpn_address}", "", "[Peer]", f"PublicKey = {gateway.public_key}",
        f"Endpoint = {endpoint}:{gateway.listen_port}", f"AllowedIPs = {', '.join(routes)}",
        "PersistentKeepalive = 25", "",
    ])
