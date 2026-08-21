"""Ordered, retry-safe GameNet provisioning state machine.

Provider-specific operations deliberately sit behind individual functions so a
failed run can resume at the first incomplete resource and acceptance tests can
exercise the exact security ordering.
"""

from __future__ import annotations

import json
import asyncio
import base64
import hashlib
import logging
import os
import shlex
import socket
from ipaddress import ip_network
from sqlalchemy.orm import object_session

from api.database import SessionLocal
from api.models import (
    Event, PrivateBootCertification, Site, Team, VM, VMGoal, VMModule,
    VPNCredential, Zone, utcnow,
)
from api.services.gamenet_provider import (
    AwsGameNetProvider, GameNetProviderError, configure_snapshot_opnsense,
    configure_gateway, configure_site_wireguard, install_local_wireguard,
    ssh_command, ssh_host_command, tcp_closed, ubuntu_cloud_init,
    upload_text, validate_site_tunnel,
    verify_endpoint_network,
)
from api.services.secrets import decrypt_secret
from api.services.ssh_keys import get_or_create_platform_keypair
from builder.infrastructure_validation import gamenet_hostname

log = logging.getLogger(__name__)

PROVISIONING_STEPS = (
    "allocate_keys_and_addresses", "create_gateways", "create_site_firewalls",
    "establish_site_tunnels", "connecting_control_plane", "certifying_private_boot",
    "create_private_endpoints", "apply_blue_modules", "run_connectivity_checks",
    "lock_down_public_ingress", "run_exposure_checks",
)


def provision_event_gamenets(event_id: int) -> None:
    db = SessionLocal()
    try:
        event = db.query(Event).filter_by(id=event_id).first()
        if not event or event.status not in {"provisioning", "provision_failed"}:
            return
        infrastructure = json.loads(event.infrastructure)
        event.status, event.open = "provisioning", False
        for vm in db.query(VM).filter(VM.event_id == event.id, VM.status == "failed"):
            vm.status = "creating"
            vm.provision_error = None
        db.commit()
        for step in PROVISIONING_STEPS:
            for vm in db.query(VM).filter(
                VM.event_id == event.id,
                VM.status.notin_(("active", "stopped")),
            ):
                vm.provision_step = step
                vm.provision_error = None
            db.commit()
            globals()[step](db, event, infrastructure)
            db.commit()
        event.status, event.open = "open", True
        event.started_at = event.started_at or utcnow()
        event.ends_at = (event.started_at + __import__("datetime").timedelta(minutes=event.time_limit_minutes)
                         if event.time_limit_minutes else None)
        db.commit()
        from api.services.expo_ust import configured, synchronize
        if configured():
            asyncio.run(synchronize(event.id))
    except Exception as exc:
        db.rollback()
        event = db.query(Event).filter_by(id=event_id).first()
        if event:
            event.status, event.open = "provision_failed", False
            message = f"{type(exc).__name__}: {exc}"[:4000]
            for vm in db.query(VM).filter(
                VM.event_id == event.id,
                VM.status.notin_(("active", "stopped")),
            ):
                vm.status = "failed"
                vm.provision_error = message
            db.commit()
        log.exception("GameNet provisioning failed for event %s: %s", event_id, exc)
    finally:
        db.close()


def cleanup_event_gamenets(event_id: int, *, session_factory=SessionLocal,
                           provider_factory=None):
    """Delete owned event resources in dependency order without forgetting failures."""
    from api.services.aws import CleanupResult
    db = session_factory()
    provider = None
    removed, remaining = [], []
    try:
        event = db.get(Event, event_id)
        if not event:
            return CleanupResult()
        if not any(vm.cloud_instance_id or vm.eip_allocation_id for vm in event.vms) and not any(
                site.vpc_id for site in event.sites):
            return CleanupResult()
        provider = (provider_factory or _provider)()
        role_order = {"blue_endpoint": 0, "red_endpoint": 0, "site_firewall": 1,
                      "vpn_gateway": 2}
        vms = sorted(event.vms, key=lambda vm: role_order.get(vm.role, 0))
        for vm in vms:
            if not vm.cloud_instance_id and not vm.eip_allocation_id:
                continue
            try:
                provider.cleanup_vm(vm, db.get(Site, vm.site_id) if vm.site_id else None)
                removed.append(f"instance/{vm.cloud_instance_id}")
                vm.cloud_instance_id = vm.primary_eni_id = vm.wan_eni_id = vm.lan_eni_id = None
                vm.eip_allocation_id = vm.public_ip = None
                vm.status = "stopped"
                db.commit()
            except Exception as exc:
                db.rollback()
                remaining.append(f"vm/{vm.id}: {exc}")
        for site in event.sites:
            if not site.vpc_id:
                continue
            try:
                provider.cleanup_site(site)
                removed.append(f"vpc/{site.vpc_id}")
                site.vpc_id = site.public_subnet_id = site.infrastructure_subnet_id = None
                site.internet_gateway_id = site.route_table_ids_json = None
                site.wan_security_group_id = site.lan_security_group_id = None
                for zone in site.zones:
                    zone.subnet_id = zone.security_group_id = None
                db.commit()
            except Exception as exc:
                db.rollback()
                remaining.append(f"site/{site.id}: {exc}")
        return CleanupResult(tuple(removed), tuple(remaining))
    finally:
        if provider:
            provider.close()
        db.close()


def ensure_vm_placeholders(db, event, infrastructure) -> list[VM]:
    """Materialise the complete intended topology before provider mutations.

    This makes progress totals truthful even when the first cloud operation
    fails, and gives retries stable records to resume from.
    """
    created: list[VM] = []
    definitions = {site["key"]: site for site in infrastructure["sites"]}
    gateway_spec = infrastructure["vpn_gateway"]
    teams = db.query(Team).filter_by(event_id=event.id).order_by(Team.id).all()
    for team in teams:
        gateway = team.vpn_gateway
        if gateway and gateway.vm_id:
            gateway_vm = db.get(VM, gateway.vm_id)
        else:
            gateway_vm = db.query(VM).filter_by(
                event_id=event.id, hostname=gamenet_hostname(event.id, team.id, "gateway"),
            ).first()
        if not gateway_vm:
            gateway_vm = VM(
                hostname=gamenet_hostname(event.id, team.id, "gateway"), team_id=team.id,
                event_id=event.id, status="creating", role="vpn_gateway", vm_type="vpn_gateway",
                base_type=gateway_spec["base_type"], instance_type=gateway_spec["default_plan"],
                cloud_region=gateway_spec["region"],
                ust_prompt=gateway_spec.get("ust_prompt"),
            )
            db.add(gateway_vm)
            db.flush()
        if gateway:
            gateway.vm_id = gateway_vm.id
        created.append(gateway_vm)
        gateway_vm.ust_prompt = gateway_spec.get("ust_prompt")

        for site in db.query(Site).filter_by(team_id=team.id).order_by(Site.order):
            site_spec = definitions[site.key]
            firewall = db.get(VM, site.firewall_vm_id) if site.firewall_vm_id else None
            if not firewall:
                firewall = VM(
                    hostname=gamenet_hostname(event.id, team.id, site.key, "fw"),
                    team_id=team.id, event_id=event.id, site_id=site.id,
                    status="creating", role="site_firewall", vm_type=f"{site.key}_firewall",
                    base_type=site_spec["firewall"]["base_type"],
                    instance_type=site_spec["firewall"]["default_plan"], cloud_region=site.region,
                    ust_prompt=site_spec["firewall"].get("ust_prompt"),
                )
                db.add(firewall)
                db.flush()
                site.firewall_vm_id = firewall.id
            created.append(firewall)
            firewall.ust_prompt = site_spec["firewall"].get("ust_prompt")

            zones = {zone.key: zone for zone in site.zones}
            for zone_spec in site_spec["zones"]:
                zone = zones[zone_spec["key"]]
                next_host = 10
                for endpoint in zone_spec["endpoints"]:
                    for index in range(endpoint["count"]):
                        hostname = gamenet_hostname(
                            event.id, team.id, site.key, zone.key, endpoint["key"], index + 1,
                        )
                        vm = db.query(VM).filter_by(event_id=event.id, hostname=hostname).first()
                        if not vm:
                            private_ip = str(ip_network(zone.subnet).network_address + next_host)
                            vm = VM(
                                hostname=hostname, team_id=team.id, event_id=event.id,
                                site_id=site.id, zone_id=zone.id, status="creating",
                                role=f"{zone.team_role}_endpoint", vm_type=endpoint["key"],
                                base_type=endpoint["base_type"],
                                instance_type=endpoint["default_plan"], cloud_region=site.region,
                                private_ip=private_ip, ip_address=private_ip,
                                ust_prompt=endpoint.get("ust_prompt"),
                            )
                            db.add(vm)
                            db.flush()
                        created.append(vm)
                        vm.ust_prompt = endpoint.get("ust_prompt")
                        next_host += 1
    return created


def allocate_keys_and_addresses(db, event, infrastructure):
    # Allocation is performed transactionally by the start endpoint. This
    # assertion prevents a retry from continuing with a partial address plan.
    expected = db.query(Team).filter_by(event_id=event.id).count() * len(infrastructure["sites"])
    if db.query(Site).filter_by(event_id=event.id).count() != expected:
        raise RuntimeError("GameNet address allocation is incomplete")


def create_gateways(db, event, infrastructure):
    _require_provider()
    gateway = infrastructure["vpn_gateway"]
    for team in db.query(Team).filter_by(event_id=event.id):
        vm = db.query(VM).filter_by(id=team.vpn_gateway.vm_id).first() if team.vpn_gateway.vm_id else None
        if not vm:
            vm = VM(hostname=gamenet_hostname(event.id, team.id, "gateway"), team_id=team.id, event_id=event.id,
                    status="creating", role="vpn_gateway", base_type=gateway["base_type"],
                    instance_type=gateway["default_plan"], cloud_region=gateway["region"])
            db.add(vm); db.flush(); team.vpn_gateway.vm_id = vm.id
        if not vm.cloud_instance_id or not vm.public_ip:
            _, public_key = get_or_create_platform_keypair(db)
            provider = _provider()
            try:
                result = provider.create_gateway(
                    event, team, vm, key_name=provider.config.key_pair_name, public_key=public_key,
                    ingress=_gateway_ingress(team, temporary=True), user_data=ubuntu_cloud_init(),
                )
                _persist_instance_result(vm, result)
            finally:
                provider.close()
            db.commit()
        if not vm.public_ip:
            raise RuntimeError("VPN gateway did not receive a public address")
        _apply_temporary_gateway_firewall(event, team, vm)
        vm.ip_address, vm.status, team.vpn_gateway.status = vm.public_ip, "active", "active"
        sites = db.query(Site).filter_by(team_id=team.id).order_by(Site.order).all()
        participants = db.query(VPNCredential).filter_by(team_id=team.id, status="active").all()
        configure_gateway(team.vpn_gateway, vm, sites, participants)


def create_site_firewalls(db, event, infrastructure):
    _require_provider()
    from api.services.opnsense_images import active_image
    image = active_image(db)
    definitions = {site["key"]: site for site in infrastructure["sites"]}
    for site in db.query(Site).filter_by(event_id=event.id):
        if not site.vpc_id:
            site.vpc_id = _create_provider_vpc(site)
            db.commit()
        vm = db.query(VM).filter_by(id=site.firewall_vm_id).first() if site.firewall_vm_id else None
        if not vm:
            spec = definitions[site.key]["firewall"]
            vm = VM(hostname=gamenet_hostname(event.id, site.team_id, site.key, "fw"), team_id=site.team_id,
                    event_id=event.id, site_id=site.id, status="creating", role="site_firewall",
                    base_type=spec["base_type"], instance_type=spec["default_plan"], cloud_region=site.region)
            db.add(vm); db.flush(); site.firewall_vm_id = vm.id
        if not vm.cloud_instance_id or not vm.public_ip:
            if not image:
                raise RuntimeError("No active validated OPNsense image. Open /admin/settings to build and activate one.")
            # AWS reserves the first four addresses in every subnet; use the
            # first assignable address as the OPNsense LAN gateway.
            vm.private_ip = str(ip_network(site.infrastructure_subnet).network_address + 4)
            vm.opnsense_image_id, vm.opnsense_release = image.id, image.version
            provider = _provider()
            try:
                result = provider.create_firewall(site, vm, ami_id=image.ami_id)
                _persist_instance_result(vm, result)
                provider.configure_private_routes(site, result.lan_eni_id)
            finally:
                provider.close()
            db.commit()
        if not vm.public_ip or not vm.private_ip:
            raise RuntimeError("site firewall requires both public WAN and private VPC addresses")
        vm.ip_address = vm.private_ip
        if vm.status != "active":
            configure_snapshot_opnsense(site, vm, vm.opnsense_release, lan_mac=vm.vpc_mac)
            duplicate = db.query(VM).filter(
                VM.id != vm.id, VM.ssh_host_key == vm.ssh_host_key,
                VM.role == "site_firewall", VM.ssh_host_key.is_not(None),
            ).first()
            if duplicate:
                raise GameNetProviderError("AMI clones presented the same SSH host key")
            vm.status = "active"


def establish_site_tunnels(db, event, infrastructure):
    for team in db.query(Team).filter_by(event_id=event.id):
        gateway_vm = db.get(VM, team.vpn_gateway.vm_id)
        _apply_temporary_gateway_firewall(event, team, gateway_vm)
    for site in db.query(Site).filter_by(event_id=event.id):
        if site.tunnel_status != "active":
            _configure_site_tunnel(site)
            site.tunnel_status = "active"


def create_private_endpoints(db, event, infrastructure):
    _require_endpoint_prerequisites(db, event, infrastructure)
    _, public_key = get_or_create_platform_keypair(db)
    definitions = {site["key"]: site for site in infrastructure["sites"]}
    for site in db.query(Site).filter_by(event_id=event.id):
        gateway_vm = db.get(VM, site.team.vpn_gateway.vm_id)
        zone_defs = {zone["key"]: zone for zone in definitions[site.key]["zones"]}
        for zone in site.zones:
            next_host = 10
            for endpoint in zone_defs[zone.key]["endpoints"]:
                for index in range(endpoint["count"]):
                    hostname = gamenet_hostname(
                        event.id, site.team_id, site.key, zone.key, endpoint["key"], index + 1,
                    )
                    existing = db.query(VM).filter_by(event_id=event.id, hostname=hostname).first()
                    private_ip = str(__import__("ipaddress").ip_network(zone.subnet).network_address + next_host)
                    vm = existing or VM(hostname=hostname, team_id=site.team_id, event_id=event.id, site_id=site.id,
                                        zone_id=zone.id, status="creating", role=f"{zone.team_role}_endpoint",
                                        base_type=endpoint["base_type"], instance_type=endpoint["default_plan"],
                                        cloud_region=site.region, private_ip=private_ip, ip_address=private_ip)
                    if not existing:
                        db.add(vm); db.flush()
                    if not vm.cloud_instance_id or not vm.private_ip:
                        from api.services.aws import AwsConfig
                        provider = _provider()
                        try:
                            result = provider.create_endpoint(
                                site, zone, vm, ami_id=AwsConfig.from_env().ubuntu_ami(site.region),
                                key_name=provider.config.key_pair_name, public_key=public_key,
                            )
                            _persist_instance_result(vm, result)
                        finally:
                            provider.close()
                        db.commit()
                    if vm.network_phase != "network_converted":
                        verify_endpoint_network(vm, site, gateway_vm)
                        vm.network_phase = "network_converted"
                        db.commit()
                    if vm.public_ip or not vm.private_ip:
                        raise GameNetProviderError("missing private ENI metadata or unexpected public endpoint address")
                    if vm.network_phase == "network_converted":
                        vm.status = "registered"
                    if zone.team_role == "blue":
                        _assign_blue_modules(db, vm, event)
                    else:
                        vm.status = "active"
                    next_host += 1


def apply_blue_modules(db, event, infrastructure):
    for vm in db.query(VM).filter(VM.event_id == event.id, VM.role == "blue_endpoint", VM.status != "active"):
        _apply_modules_through_jump_access(vm)


def connect_control_plane(db, event, infrastructure):
    _configure_control_plane_peers(event)
    for site in db.query(Site).filter_by(event_id=event.id):
        firewall = db.get(VM, site.firewall_vm_id)
        code, _, error = ssh_command(
            firewall, "true", host=firewall.private_ip, connect_timeout=120,
        )
        if code:
            raise GameNetProviderError(
                f"control-plane VPN could not reach firewall LAN {firewall.private_ip}: {error[:300]}"
            )
        site.control_plane_status = "active"
        db.commit()


def connecting_control_plane(db, event, infrastructure):
    connect_control_plane(db, event, infrastructure)


def certify_private_boot(db, event, infrastructure):
    """Certify every distinct stock endpoint image before creating workloads."""
    from builder.base_loader import load_base_type

    definitions = {row["key"]: row for row in infrastructure["sites"]}
    for site in db.query(Site).filter_by(event_id=event.id).order_by(Site.id):
        base_types = {
            endpoint["base_type"]
            for zone in definitions[site.key]["zones"]
            for endpoint in zone["endpoints"]
            if endpoint.get("count", 0) > 0
        }
        unique_images = {}
        for base_type_id in sorted(base_types):
            base = load_base_type(base_type_id)
            unique_images.setdefault(base.os.casefold(), base_type_id)
        for base_type_id in unique_images.values():
            _certify_site_image(db, event, site, base_type_id)


def certifying_private_boot(db, event, infrastructure):
    certify_private_boot(db, event, infrastructure)


def _certify_site_image(db, event, site, base_type_id):
    from builder.base_loader import load_base_type
    from api.services.opnsense_images import active_image

    base = load_base_type(base_type_id)
    firewall = db.get(VM, site.firewall_vm_id)
    if not firewall or not firewall.cloud_instance_id:
        raise GameNetProviderError("site firewall instance is missing before private-boot certification")
    if site.tunnel_status != "active" or site.control_plane_status != "active":
        raise GameNetProviderError("site tunnel and control-plane VPN must be active before private-boot certification")
    image = active_image(db)
    if not image or image.region != site.region:
        raise GameNetProviderError(
            f"no active privately validated OPNsense AMI for {site.region}"
        )
    # AWS private-only boot is certified during the AMI workflow using an
    # isolated validation subnet. Record that immutable evidence against this
    # event/site so retries retain the same gate without launching a per-site
    # per-site canary.
    os_id = int(hashlib.sha256(image.ami_id.encode()).hexdigest()[:7], 16)
    cert = db.query(PrivateBootCertification).filter_by(
        site_id=site.id, os_id=os_id, region=site.region, vpc_id=site.vpc_id,
        firewall_instance_id=firewall.cloud_instance_id,
    ).first()
    if not cert:
        cert = PrivateBootCertification(
            site_id=site.id, base_type=base_type_id, os_id=os_id,
            region=site.region, vpc_id=site.vpc_id,
            firewall_instance_id=firewall.cloud_instance_id,
            plan=base.default_plan, status="passed", phase="passed",
            started_at=utcnow(), completed_at=utcnow(), cleanup_completed_at=utcnow(),
            diagnostic_detail=f"validated by AMI {image.ami_id}",
        )
        db.add(cert)
        db.commit()
    return
def lock_down_public_ingress(db, event, infrastructure):
    _apply_final_firewall_groups(event, infrastructure)
    # The provider firewall must be in place before temporary management rules
    # are removed. Any failure here keeps the event closed.
    _remove_temporary_management_access(event)


def run_connectivity_checks(db, event, infrastructure):
    result = _run_connectivity_and_exposure_checks(event, infrastructure, exposure=False)
    required = {"vpn_routes", "same_site", "site_isolation", "team_isolation", "private_management", "nat_egress"}
    if set(result) != required or not all(result.values()):
        missing = ",".join(sorted(required - set(result))) or "none"
        failed = ",".join(sorted(name for name in required & set(result) if not result[name])) or "none"
        unexpected = ",".join(sorted(set(result) - required)) or "none"
        raise RuntimeError(
            "GameNet connectivity checks did not all pass: "
            f"missing={missing}; failed={failed}; unexpected={unexpected}"
        )


def run_exposure_checks(db, event, infrastructure):
    result = _run_connectivity_and_exposure_checks(event, infrastructure, connectivity=False)
    required = {"public_exposure"}
    if set(result) != required or not all(result.values()):
        raise RuntimeError("GameNet public exposure checks did not all pass")


def _require_provider():
    from api.services.aws import AwsConfig
    AwsConfig.from_env()


def _provider():
    from api.services.aws import AwsComputeProvider, AwsConfig, AwsNetworkProvider, AwsSessionFactory
    config = AwsConfig.from_env()
    sessions = AwsSessionFactory(config)
    ec2 = sessions.client("ec2")
    return AwsGameNetProvider(AwsComputeProvider(ec2), AwsNetworkProvider(ec2), config)


def _configured_availability_zone(region: str) -> str:
    from api.services.aws import AwsConfig
    return AwsConfig.from_env().availability_zone(region)


def _persist_instance_result(vm, result) -> None:
    vm.cloud_instance_id = result.instance_id
    vm.primary_eni_id = result.primary_eni_id
    vm.wan_eni_id = result.wan_eni_id
    vm.lan_eni_id = result.lan_eni_id
    vm.eip_allocation_id = result.eip_allocation_id
    vm.availability_zone = result.availability_zone
    vm.public_ip = result.public_ip
    vm.private_ip = result.private_ip
    vm.vpc_ip = result.private_ip
    vm.vpc_mac = result.lan_mac or result.primary_mac
    if result.public_ip:
        vm.ip_address = result.public_ip


def _create_provider_vpc(site):
    if not site.availability_zone:
        site.availability_zone = _configured_availability_zone(site.region)
    provider = _provider()
    try:
        result = provider.create_vpc(site)
        site.vpc_id = result.vpc_id
        site.availability_zone = result.availability_zone
        site.public_subnet_id = result.subnet_ids["wan"]
        site.infrastructure_subnet_id = result.subnet_ids["infra"]
        site.internet_gateway_id = result.internet_gateway_id
        site.route_table_ids_json = json.dumps(dict(result.route_table_ids), sort_keys=True)
        for zone in site.zones:
            zone.subnet_id = result.subnet_ids[zone.key]
        groups = provider.ensure_site_security_groups(site, temporary_management=True)
        site.wan_security_group_id = groups["wan"]
        site.lan_security_group_id = groups["lan"]
        for zone in site.zones:
            zone.security_group_id = groups[zone.key]
        object_session(site).commit()
        return result.vpc_id
    finally:
        provider.close()


def _require_endpoint_prerequisites(db, event, infrastructure):
    from builder.base_loader import load_base_type
    from api.services.opnsense_images import active_image

    definitions = {row["key"]: row for row in infrastructure["sites"]}
    for site in db.query(Site).filter_by(event_id=event.id):
        gateway_vm = db.get(VM, site.team.vpn_gateway.vm_id) if site.team.vpn_gateway else None
        firewall = db.get(VM, site.firewall_vm_id) if site.firewall_vm_id else None
        if (not gateway_vm or gateway_vm.status != "active" or not firewall or
                firewall.status != "active" or site.tunnel_status != "active" or
                site.control_plane_status != "active"):
            raise GameNetProviderError(
                "gateway, firewall, site tunnel, and control-plane VPN must pass before endpoint creation"
            )
        required_os = {
            load_base_type(endpoint["base_type"]).os.casefold()
            for zone in definitions[site.key]["zones"]
            for endpoint in zone["endpoints"]
            if endpoint.get("count", 0) > 0
        }
        image = active_image(db)
        valid = db.query(PrivateBootCertification).filter_by(
            site_id=site.id, region=site.region, vpc_id=site.vpc_id,
            firewall_instance_id=firewall.cloud_instance_id, status="passed",
        ).all()
        certified_os = {load_base_type(cert.base_type).os.casefold() for cert in valid}
        if not image or image.region != site.region or not required_os <= certified_os:
            raise GameNetProviderError(
                "all endpoint stock images must pass private-boot certification before endpoint creation"
            )


def _configure_site_tunnel(site):
    db = object_session(site)
    firewall = db.query(VM).filter_by(id=site.firewall_vm_id).one()
    gateway = site.team.vpn_gateway
    gateway_vm = db.query(VM).filter_by(id=gateway.vm_id).one()
    team_sites = db.query(Site).filter_by(team_id=site.team_id).all()
    configure_site_wireguard(site, firewall, gateway, gateway_vm, team_sites)
    validate_site_tunnel(site, firewall, gateway, gateway_vm)


def _apply_temporary_gateway_firewall(event, team, gateway_vm):
    provider = _provider()
    try:
        group = provider.ensure_gateway_security_group(
            event, team, gateway_vm, _gateway_ingress(team, temporary=True),
        )
        gateway_vm.security_group_ids_json = json.dumps([group])
        object_session(gateway_vm).commit()
    finally:
        provider.close()


def _gateway_ingress(team, *, temporary: bool) -> tuple[dict, ...]:
    value = os.environ.get("CTF_CONTROL_PLANE_CIDR", "").strip()
    try:
        control_plane = ip_network(value)
    except ValueError as exc:
        raise GameNetProviderError("CTF_CONTROL_PLANE_CIDR must be a valid IPv4 CIDR") from exc
    if control_plane.version != 4:
        raise GameNetProviderError("CTF_CONTROL_PLANE_CIDR must be an IPv4 CIDR")
    rules = [{
        "IpProtocol": "udp", "FromPort": team.vpn_gateway.listen_port,
        "ToPort": team.vpn_gateway.listen_port, "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
    }]
    if temporary:
        rules.append({
            "IpProtocol": "tcp", "FromPort": 22, "ToPort": 22,
            "IpRanges": [{"CidrIp": str(control_plane)}],
        })
    return tuple(rules)


def _assign_blue_modules(db, vm, event):
    if vm.modules:
        return
    from builder.module_loader import load_all_modules
    from builder.selector import select_modules
    selected = select_modules(json.loads(event.quota), load_all_modules(), base_type_id=vm.base_type)
    for module in selected:
        db.add(VMModule(vm_id=vm.id, module_id=module.id, module_type=module.type,
                        difficulty=module.difficulty, points=module.points, stage=module.stage))
        if module.type == "goal":
            db.add(VMGoal(vm_id=vm.id, module_id=module.id, red_points=module.red_points,
                          defend_points=module.defend_points, status="pending"))
    db.flush()


def _apply_modules_through_jump_access(vm):
    # Semaphore reaches private endpoints through the team's public VPN
    # gateway ProxyJump. _run_provision raises via persisted VM failure state,
    # so verify the final state before the orchestrator advances.
    from api.routes.vm import _run_provision
    _run_provision(vm.id)
    db = object_session(vm)
    db.expire(vm)
    if vm.status != "active":
        raise GameNetProviderError(vm.provision_error or f"module provisioning failed for {vm.hostname}")


def _configure_control_plane_peers(event):
    db = object_session(event)
    # One Linux interface cannot own several unrelated private keys. Create one
    # interface per team, which also keeps routes and peer ownership explicit.
    for team in db.query(Team).filter_by(event_id=event.id):
        gateway = team.vpn_gateway
        gateway_vm = db.query(VM).filter_by(id=gateway.vm_id).one()
        allowed = [gateway.vpn_address + "/32", *[site.allocated_cidr for site in team.sites]]
        config = "\n".join(["[Interface]", f"Address = {gateway.platform_address}/32",
                            f"PrivateKey = {decrypt_secret(gateway.platform_private_key_encrypted)}", "",
                            "[Peer]", f"PublicKey = {gateway.public_key}",
                            f"Endpoint = {gateway_vm.public_ip}:{gateway.listen_port}",
                            f"AllowedIPs = {','.join(allowed)}", "PersistentKeepalive = 25", ""])
        install_local_wireguard(config, f"ctf-e{event.id}-t{team.id}"[:15])


def _apply_final_firewall_groups(event, infrastructure):
    db = object_session(event)
    provider = _provider()
    try:
        for team in db.query(Team).filter_by(event_id=event.id):
            gateway = team.vpn_gateway
            gateway_vm = db.query(VM).filter_by(id=gateway.vm_id).one()
            group = provider.ensure_gateway_security_group(
                event, team, gateway_vm, _gateway_ingress(team, temporary=False),
            )
            gateway_vm.security_group_ids_json = json.dumps([group])
        for site in db.query(Site).filter_by(event_id=event.id):
            groups = provider.ensure_site_security_groups(site)
            site.wan_security_group_id = groups["wan"]
            site.lan_security_group_id = groups["lan"]
            for zone in site.zones:
                zone.security_group_id = groups[zone.key]
        db.commit()
    finally:
        provider.close()


def _remove_temporary_management_access(event):
    db = object_session(event)
    for site in db.query(Site).filter_by(event_id=event.id):
        firewall = db.query(VM).filter_by(id=site.firewall_vm_id).one()
        php = '''require_once("config.inc"); require_once("util.inc"); global $config;
$rules=$config["OPNsense"]["Firewall"]["Filter"]["rules"]["rule"]??[];
$config["OPNsense"]["Firewall"]["Filter"]["rules"]["rule"]=array_values(array_filter(
    $rules, fn($item)=>!in_array($item["description"]??"", ["Allow management SSH","Allow management HTTPS"])
));
$config["system"]["ssh"]["interfaces"]="lan";
write_config("Remove temporary public management access");'''
        script = (
            f"/usr/local/bin/php -r {shlex.quote(php)} && "
            "/usr/local/sbin/configctl filter reload && /usr/local/sbin/configctl openssh restart"
        )
        encoded = base64.b64encode(script.encode()).decode()
        command = f"echo {encoded} | base64 -d | /bin/sh"
        code, output, error = ssh_command(firewall, command, host=firewall.private_ip)
        if code:
            detail = (error or output)[:300]
            raise GameNetProviderError(f"failed to remove temporary OPNsense management rules: {detail}")


def _run_connectivity_and_exposure_checks(event, infrastructure, *, connectivity=True, exposure=True):
    db = object_session(event)
    sites = db.query(Site).filter_by(event_id=event.id).all()
    teams = db.query(Team).filter_by(event_id=event.id).all()
    private_management = True
    same_site = True
    site_isolation = True
    team_isolation = True
    vpn_routes = True
    nat_egress = True
    public_exposure = True
    if exposure:
        provider = _provider()
        try:
            for team in teams:
                gateway_vm = db.query(VM).filter_by(id=team.vpn_gateway.vm_id).one()
                group_ids = json.loads(gateway_vm.security_group_ids_json or "[]")
                rules = provider.security_group_rules(group_ids[0]) if len(group_ids) == 1 else ()
                public_exposure &= tuple(rules) == _gateway_ingress(team, temporary=False)
            for site in sites:
                public_exposure &= provider.security_group_rules(site.wan_security_group_id) == ()
        finally:
            provider.close()
    for site in sites:
        firewall = db.query(VM).filter_by(id=site.firewall_vm_id).one()
        gateway_vm = db.get(VM, site.team.vpn_gateway.vm_id)
        if connectivity:
            private_management &= _tcp_probe(firewall.private_ip, 443)
        if exposure:
            public_exposure &= _wait_ports_closed(firewall.public_ip, (22, 80, 443))
        endpoints = db.query(VM).filter(VM.site_id == site.id, VM.role.like("%_endpoint")).all()
        if connectivity and len(endpoints) > 1:
            code, _, _ = ssh_command(
                endpoints[0], f"ping -c 1 -W 3 {endpoints[1].private_ip}", jump=gateway_vm,
            )
            same_site &= code == 0
        if connectivity and endpoints:
            code, output, _ = ssh_command(
                endpoints[0], "curl -fsS --max-time 20 https://checkip.amazonaws.com",
                jump=gateway_vm, timeout=60,
            )
            nat_egress &= code == 0 and output.strip() == firewall.public_ip
        other_sites = [other for other in sites if other.team_id == site.team_id and other.id != site.id]
        if connectivity and endpoints and other_sites:
            target = db.query(VM).filter(VM.site_id == other_sites[0].id, VM.role.like("%_endpoint")).first()
            if target:
                code, _, _ = ssh_command(
                    endpoints[0], f"ping -c 1 -W 3 {target.private_ip}", jump=gateway_vm,
                )
                site_isolation &= code != 0
    for team in teams:
        gateway_vm = db.query(VM).filter_by(id=team.vpn_gateway.vm_id).one()
        if exposure:
            public_exposure &= _wait_ports_closed(gateway_vm.public_ip, (22, 80, 443, 8080))
        if connectivity:
            for site in team.sites:
                firewall = db.get(VM, site.firewall_vm_id)
                vpn_routes &= _tcp_probe(firewall.private_ip, 443)
        other = next((candidate for candidate in teams if candidate.id != team.id and candidate.sites), None)
        if connectivity and other:
            source = db.query(VM).filter(VM.team_id == team.id, VM.role.like("%_endpoint")).first()
            if source:
                destination = db.get(VM, other.sites[0].firewall_vm_id).private_ip
                code, _, _ = ssh_command(
                    source, f"ping -c 1 -W 3 {destination}", jump=gateway_vm,
                )
                team_isolation &= code != 0
    result = {}
    if connectivity:
        result.update({"vpn_routes": vpn_routes, "same_site": same_site, "site_isolation": site_isolation,
                       "team_isolation": team_isolation, "private_management": private_management,
                       "nat_egress": nat_egress})
    if exposure:
        result["public_exposure"] = public_exposure
    return result


def _tcp_probe(host, port, timeout=3):
    if not host:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _wait_ports_closed(host, ports, timeout=60):
    deadline = __import__("time").monotonic() + timeout
    while __import__("time").monotonic() < deadline:
        if all(tcp_closed(host, port) for port in ports):
            return True
        __import__("time").sleep(2)
    return False
