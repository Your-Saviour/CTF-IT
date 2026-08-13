"""Ordered, retry-safe GameNet provisioning state machine.

Provider-specific operations deliberately sit behind individual functions so a
failed run can resume at the first incomplete resource and acceptance tests can
exercise the exact security ordering.
"""

from __future__ import annotations

import json
import logging
import os
import socket
from ipaddress import ip_network
from sqlalchemy.orm import object_session

from api.database import SessionLocal
from api.models import Event, Site, Team, VM, VMGoal, VMModule, VPNCredential, Zone, utcnow
from api.services.gamenet_provider import (
    GameNetProviderError, VultrGameNetProvider, bootstrap_opnsense, configure_endpoint_network,
    configure_gateway, configure_site_wireguard, endpoint_cloud_init, install_local_wireguard, render_opnsense_config,
    ssh_command, tcp_closed, ubuntu_cloud_init, update_vm_addresses, upload_text,
)
from api.services.secrets import decrypt_secret
from builder.infrastructure_validation import gamenet_hostname

log = logging.getLogger(__name__)

PROVISIONING_STEPS = (
    "allocate_keys_and_addresses", "create_gateways", "create_site_firewalls",
    "establish_site_tunnels", "create_private_endpoints", "apply_blue_modules",
    "connect_control_plane", "lock_down_public_ingress", "run_acceptance_checks",
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
                base_type=gateway_spec["base_type"], vultr_plan=gateway_spec["default_plan"],
                vultr_region=gateway_spec["region"],
            )
            db.add(gateway_vm)
            db.flush()
        if gateway:
            gateway.vm_id = gateway_vm.id
        created.append(gateway_vm)

        for site in db.query(Site).filter_by(team_id=team.id).order_by(Site.order):
            site_spec = definitions[site.key]
            firewall = db.get(VM, site.firewall_vm_id) if site.firewall_vm_id else None
            if not firewall:
                firewall = VM(
                    hostname=gamenet_hostname(event.id, team.id, site.key, "fw"),
                    team_id=team.id, event_id=event.id, site_id=site.id,
                    status="creating", role="site_firewall", vm_type=f"{site.key}_firewall",
                    base_type=site_spec["firewall"]["base_type"],
                    vultr_plan=site_spec["firewall"]["default_plan"], vultr_region=site.region,
                )
                db.add(firewall)
                db.flush()
                site.firewall_vm_id = firewall.id
            created.append(firewall)

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
                                vultr_plan=endpoint["default_plan"], vultr_region=site.region,
                                private_ip=private_ip, ip_address=private_ip,
                            )
                            db.add(vm)
                            db.flush()
                        created.append(vm)
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
                    vultr_plan=gateway["default_plan"], vultr_region=gateway["region"])
            db.add(vm); db.flush(); team.vpn_gateway.vm_id = vm.id
        if not vm.vultr_id or not vm.public_ip:
            _create_provider_instance(vm, public=True)
        if not vm.public_ip:
            raise RuntimeError("VPN gateway did not receive a public address")
        vm.ip_address, vm.status, team.vpn_gateway.status = vm.public_ip, "active", "active"
        sites = db.query(Site).filter_by(team_id=team.id).order_by(Site.order).all()
        participants = db.query(VPNCredential).filter_by(team_id=team.id, status="active").all()
        configure_gateway(team.vpn_gateway, vm, sites, participants)


def create_site_firewalls(db, event, infrastructure):
    _require_provider()
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
                    base_type=spec["base_type"], vultr_plan=spec["default_plan"], vultr_region=site.region)
            db.add(vm); db.flush(); site.firewall_vm_id = vm.id
        if not vm.vultr_id or not vm.public_ip:
            _create_provider_instance(vm, public=True, vpc_ids=[site.vpc_id])
            vm.vpc_ip = vm.private_ip
            vm.private_ip = str(ip_network(site.allocated_cidr).network_address + 1)
        if not vm.public_ip or not vm.private_ip:
            raise RuntimeError("site firewall requires both public WAN and private VPC addresses")
        vm.ip_address = vm.private_ip
        if vm.status != "active":
            bootstrap_opnsense(site, vm)
            vm.status = "active"


def establish_site_tunnels(db, event, infrastructure):
    for site in db.query(Site).filter_by(event_id=event.id):
        if site.tunnel_status != "active":
            _configure_site_tunnel(site)
            site.tunnel_status = "active"


def create_private_endpoints(db, event, infrastructure):
    definitions = {site["key"]: site for site in infrastructure["sites"]}
    for site in db.query(Site).filter_by(event_id=event.id):
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
                                        base_type=endpoint["base_type"], vultr_plan=endpoint["default_plan"],
                                        vultr_region=site.region, private_ip=private_ip, ip_address=private_ip)
                    if not existing:
                        db.add(vm); db.flush()
                    if not vm.vultr_id:
                        _create_provider_instance(
                            vm, public=False, vpc_ids=[site.vpc_id],
                            user_data=endpoint_cloud_init(vm.private_ip, site.allocated_cidr),
                        )
                    if vm.public_ip:
                        raise RuntimeError("private endpoint unexpectedly received a public address")
                    firewall = db.query(VM).filter_by(id=site.firewall_vm_id).one()
                    if vm.status == "creating":
                        configure_endpoint_network(vm, site, firewall)
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


def lock_down_public_ingress(db, event, infrastructure):
    _apply_final_firewall_groups(event, infrastructure)
    # The provider firewall must be in place before temporary management rules
    # are removed. Any failure here keeps the event closed.
    _remove_temporary_management_access(event)


def run_acceptance_checks(db, event, infrastructure):
    result = _run_connectivity_and_exposure_checks(event, infrastructure)
    required = {"vpn_routes", "same_site", "site_isolation", "team_isolation", "private_management", "public_exposure"}
    if set(result) != required or not all(result.values()):
        raise RuntimeError("GameNet acceptance checks did not all pass")


def _require_provider():
    if not os.environ.get("VULTR_API_KEY"):
        raise RuntimeError("VULTR_API_KEY is required for GameNet provisioning")


def _provider():
    return VultrGameNetProvider()


def _create_provider_instance(vm, *, public, vpc_ids=None, user_data=None):
    provider = _provider()
    try:
        instance = provider.create_instance(
            vm, public=public, vpc_ids=vpc_ids,
            user_data=user_data if user_data is not None else ubuntu_cloud_init(),
        )
        if public:
            update_vm_addresses(vm, instance, public=True)
        else:
            vm.public_ip = None
            vm.vpc_ip = instance.get("internal_ip") or instance.get("vpc_ip") or vm.private_ip
            if instance.get("vpc_only") is not True:
                raise GameNetProviderError("Vultr did not create the endpoint as VPC-only")
    finally:
        provider.close()


def _create_provider_vpc(site):
    provider = _provider()
    try:
        return provider.create_vpc(site)
    finally:
        provider.close()


def _configure_site_tunnel(site):
    db = object_session(site)
    firewall = db.query(VM).filter_by(id=site.firewall_vm_id).one()
    gateway = site.team.vpn_gateway
    gateway_vm = db.query(VM).filter_by(id=gateway.vm_id).one()
    team_sites = db.query(Site).filter_by(team_id=site.team_id).all()
    configure_site_wireguard(site, firewall, gateway, gateway_vm, team_sites)


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
    # Semaphore reaches private endpoints through the site's temporary public
    # firewall SSH path. _run_provision raises via persisted VM failure state,
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
            group = provider.create_firewall_group(
                f"gamenet-e{event.id}-t{team.id}-gateway-final",
                [{"ip_type": "v4", "protocol": "udp", "subnet": "0.0.0.0", "subnet_size": 0,
                  "port": str(gateway.listen_port), "source": ""}],
            )
            provider.attach_firewall_group(gateway_vm, group)
        for site in db.query(Site).filter_by(event_id=event.id):
            firewall = db.query(VM).filter_by(id=site.firewall_vm_id).one()
            group = provider.create_firewall_group(f"gamenet-e{event.id}-site-{site.id}-deny-inbound", [])
            provider.attach_firewall_group(firewall, group)
    finally:
        provider.close()


def _remove_temporary_management_access(event):
    db = object_session(event)
    _, public_key = __import__("api.services.ssh_keys", fromlist=["get_or_create_platform_keypair"]).get_or_create_platform_keypair(db)
    for site in db.query(Site).filter_by(event_id=event.id):
        firewall = db.query(VM).filter_by(id=site.firewall_vm_id).one()
        password = decrypt_secret(firewall.admin_password)
        config = render_opnsense_config(site, firewall, public_key, password, temporary_management=False)
        upload_text(firewall, "/conf/config.xml", config, host=firewall.private_ip)
        code, _, error = ssh_command(firewall, "configctl service reload all", host=firewall.private_ip)
        if code:
            raise GameNetProviderError(f"failed to remove temporary OPNsense management rules: {error[:300]}")


def _run_connectivity_and_exposure_checks(event, infrastructure):
    db = object_session(event)
    sites = db.query(Site).filter_by(event_id=event.id).all()
    teams = db.query(Team).filter_by(event_id=event.id).all()
    private_management = True
    same_site = True
    site_isolation = True
    team_isolation = True
    vpn_routes = True
    public_exposure = True
    provider = _provider()
    try:
        for team in teams:
            gateway_vm = db.query(VM).filter_by(id=team.vpn_gateway.vm_id).one()
            instance = provider.get_instance(gateway_vm)
            rules = provider.firewall_rules(instance.get("firewall_group_id", ""))
            public_exposure &= len(rules) == 1
            if rules:
                rule = rules[0]
                public_exposure &= rule.get("protocol") == "udp" and str(rule.get("port")) == str(team.vpn_gateway.listen_port)
        for site in sites:
            firewall = db.query(VM).filter_by(id=site.firewall_vm_id).one()
            instance = provider.get_instance(firewall)
            public_exposure &= provider.firewall_rules(instance.get("firewall_group_id", "")) == []
    finally:
        provider.close()
    for site in sites:
        firewall = db.query(VM).filter_by(id=site.firewall_vm_id).one()
        private_management &= _tcp_probe(firewall.private_ip, 443)
        public_exposure &= _wait_ports_closed(firewall.public_ip, (22, 80, 443))
        endpoints = db.query(VM).filter(VM.site_id == site.id, VM.role.like("%_endpoint")).all()
        if len(endpoints) > 1:
            code, _, _ = ssh_command(endpoints[0], f"ping -c 1 -W 3 {endpoints[1].private_ip}")
            same_site &= code == 0
        other_sites = [other for other in sites if other.team_id == site.team_id and other.id != site.id]
        if endpoints and other_sites:
            target = db.query(VM).filter(VM.site_id == other_sites[0].id, VM.role.like("%_endpoint")).first()
            if target:
                code, _, _ = ssh_command(endpoints[0], f"ping -c 1 -W 3 {target.private_ip}")
                site_isolation &= code != 0
    for team in teams:
        gateway_vm = db.query(VM).filter_by(id=team.vpn_gateway.vm_id).one()
        public_exposure &= _wait_ports_closed(gateway_vm.public_ip, (22, 80, 443, 8080))
        for site in team.sites:
            vpn_routes &= _tcp_probe(str(ip_network(site.allocated_cidr).network_address + 1), 443)
        other = next((candidate for candidate in teams if candidate.id != team.id and candidate.sites), None)
        if other:
            source = db.query(VM).filter(VM.team_id == team.id, VM.role.like("%_endpoint")).first()
            if source:
                destination = str(ip_network(other.sites[0].allocated_cidr).network_address + 1)
                code, _, _ = ssh_command(source, f"ping -c 1 -W 3 {destination}")
                team_isolation &= code != 0
    return {"vpn_routes": vpn_routes, "same_site": same_site, "site_isolation": site_isolation,
            "team_isolation": team_isolation, "private_management": private_management,
            "public_exposure": public_exposure}


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
