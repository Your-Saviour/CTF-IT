"""Ordered, retry-safe GameNet provisioning state machine.

Provider-specific operations deliberately sit behind individual functions so a
failed run can resume at the first incomplete resource and acceptance tests can
exercise the exact security ordering.
"""

from __future__ import annotations

import json
import asyncio
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
    GameNetProviderError, VultrGameNetProvider, add_deterministic_endpoint_address,
    bootstrap_opnsense, configure_snapshot_opnsense, configure_gateway,
    configure_site_wireguard, finalize_endpoint_network, install_local_wireguard,
    ssh_command, ssh_host_command, tcp_closed, ubuntu_cloud_init,
    update_vm_addresses, upload_text, validate_site_tunnel, validate_snapshot_wan,
    validate_vpc_only_instance,
)
from api.services.secrets import decrypt_secret
from builder.infrastructure_validation import gamenet_hostname
from builder.infrastructure_planner import endpoint_instances

log = logging.getLogger(__name__)

PROVISIONING_STEPS = (
    "allocate_keys_and_addresses", "create_gateways", "create_site_firewalls",
    "establish_site_tunnels", "connecting_control_plane", "certifying_private_boot",
    "create_private_endpoints", "apply_blue_modules", "lock_down_public_ingress",
    "run_acceptance_checks",
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
                    vultr_plan=site_spec["firewall"]["default_plan"], vultr_region=site.region,
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
                for endpoint_group in zone_spec["endpoints"]:
                    for endpoint in endpoint_instances(endpoint_group):
                        hostname = gamenet_hostname(
                            event.id, team.id, site.key, zone.key, endpoint["key"],
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
                    vultr_plan=gateway["default_plan"], vultr_region=gateway["region"])
            db.add(vm); db.flush(); team.vpn_gateway.vm_id = vm.id
        if not vm.vultr_id or not vm.public_ip:
            _create_provider_instance(vm, public=True)
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
                    base_type=spec["base_type"], vultr_plan=spec["default_plan"], vultr_region=site.region)
            db.add(vm); db.flush(); site.firewall_vm_id = vm.id
        if not vm.vultr_id or not vm.public_ip:
            if not image:
                raise RuntimeError("No active validated OPNsense image. Open /admin/settings to build and activate one.")
            vm.opnsense_image_id, vm.opnsense_release, vm.opnsense_snapshot_id = image.id, image.version, image.snapshot_id
            _create_provider_instance(vm, public=True,
                                      image_source={"snapshot_id": image.snapshot_id})
        if vm.opnsense_snapshot_id and vm.provision_step != "snapshot_wan_validated":
            validate_snapshot_wan(vm, vm.opnsense_release)
            vm.provision_step = "snapshot_wan_validated"
            db.commit()
        if vm.opnsense_snapshot_id and not vm.vpc_ip:
            attachment = _attach_provider_vpc(vm, site.vpc_id)
            vm.vpc_ip = attachment["ip_address"]
            vm.private_ip = str(ip_network(site.allocated_cidr).network_address + 1)
            db.commit()
        if not vm.public_ip or not vm.private_ip:
            raise RuntimeError("site firewall requires both public WAN and private VPC addresses")
        vm.ip_address = vm.private_ip
        if vm.status != "active":
            # Legacy records that already have an instance but no provenance
            # finish their original conversion path; all new instances use the snapshot.
            if vm.opnsense_snapshot_id:
                attachment = _attach_provider_vpc(vm, site.vpc_id)
                configure_snapshot_opnsense(
                    site, vm, vm.opnsense_release, lan_mac=attachment["mac_address"]
                )
                duplicate = db.query(VM).filter(
                    VM.id != vm.id, VM.ssh_host_key == vm.ssh_host_key,
                    VM.role == "site_firewall", VM.ssh_host_key.is_not(None),
                ).first()
                if duplicate:
                    raise GameNetProviderError("snapshot clones presented the same SSH host key")
            else:
                bootstrap_opnsense(site, vm)
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
    definitions = {site["key"]: site for site in infrastructure["sites"]}
    for site in db.query(Site).filter_by(event_id=event.id):
        zone_defs = {zone["key"]: zone for zone in definitions[site.key]["zones"]}
        for zone in site.zones:
            next_host = 10
            for endpoint_group in zone_defs[zone.key]["endpoints"]:
                for endpoint in endpoint_instances(endpoint_group):
                    hostname = gamenet_hostname(
                        event.id, site.team_id, site.key, zone.key, endpoint["key"],
                    )
                    existing = db.query(VM).filter_by(event_id=event.id, hostname=hostname).first()
                    private_ip = str(__import__("ipaddress").ip_network(zone.subnet).network_address + next_host)
                    vm = existing or VM(hostname=hostname, team_id=site.team_id, event_id=event.id, site_id=site.id,
                                        zone_id=zone.id, status="creating", role=f"{zone.team_role}_endpoint",
                                        base_type=endpoint["base_type"], vultr_plan=endpoint["default_plan"],
                                        vultr_region=site.region, private_ip=private_ip, ip_address=private_ip)
                    if not existing:
                        db.add(vm); db.flush()
                    if not vm.vultr_id or not vm.vpc_ip or not vm.vpc_mac:
                        _create_provider_instance(
                            vm, public=False, vpc_ids=[site.vpc_id],
                            stock_image=True,
                        )
                        vm.network_phase = "provider_created"
                        db.commit()
                    if vm.public_ip or not vm.vpc_ip or not vm.vpc_mac:
                        raise GameNetProviderError("missing VPC attachment metadata or unexpected public endpoint address")
                    gateway_vm = db.get(VM, site.team.vpn_gateway.vm_id)
                    if vm.network_phase in {None, "provider_created"}:
                        add_deterministic_endpoint_address(vm, site, gateway_vm)
                        vm.network_phase = "address_added"
                        db.commit()
                    if vm.network_phase == "address_added":
                        finalize_endpoint_network(vm, site, gateway_vm)
                        vm.network_phase = "network_converted"
                        vm.ip_address = vm.private_ip
                        db.commit()
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
            for endpoint_group in zone["endpoints"]
            for endpoint in endpoint_instances(endpoint_group)
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

    base = load_base_type(base_type_id)
    firewall = db.get(VM, site.firewall_vm_id)
    if not firewall or not firewall.vultr_id:
        raise GameNetProviderError("site firewall instance is missing before private-boot certification")
    if site.tunnel_status != "active" or site.control_plane_status != "active":
        raise GameNetProviderError("site tunnel and control-plane VPN must be active before private-boot certification")
    provider = _provider()
    try:
        os_id = provider._resolve_os_id(base.os)
    finally:
        provider.close()
    cert = db.query(PrivateBootCertification).filter_by(
        site_id=site.id, os_id=os_id,
        region=site.region, vpc_id=site.vpc_id, firewall_instance_id=firewall.vultr_id,
    ).first()
    if cert and cert.status == "passed" and cert.cleanup_completed_at:
        return
    if not cert:
        cert = PrivateBootCertification(
            site_id=site.id, base_type=base_type_id, os_id=os_id,
            region=site.region, vpc_id=site.vpc_id, firewall_instance_id=firewall.vultr_id,
            plan=base.default_plan,
            status="creating", phase="creating_instance", started_at=utcnow(),
        )
        db.add(cert)
        db.commit()

    if cert.status in {"creating", "testing"} and cert.instance_id:
        cert.phase = "cleanup_after_failure"
        cert.diagnostic_detail = "interrupted private-boot certification; recreating canary"
        db.commit()
        try:
            _cleanup_canary(db, cert)
        except Exception as exc:
            cert.status = "cleanup_failed"
            cert.diagnostic_detail = f"canary cleanup failure after interrupted certification: {exc}"[:4000]
            db.commit()
            raise GameNetProviderError(cert.diagnostic_detail) from exc
        cert.instance_id = None
        cert.provider_ip = None
        cert.mac_address = None
        db.commit()

    # A worker may have stopped while deleting. Finish that exact cleanup
    # before deciding whether a failed certification needs a new instance.
    if cert.status == "cleanup_failed" and cert.instance_id:
        cleanup_after_pass = cert.phase == "cleanup_after_pass"
        _cleanup_canary(db, cert)
        if cleanup_after_pass:
            cert.status, cert.phase = "passed", "passed"
            cert.completed_at = cert.completed_at or utcnow()
            cert.diagnostic_detail = None
            db.commit()
            return
        cert.instance_id = None
        cert.provider_ip = None
        cert.mac_address = None
        db.commit()

    if cert.status == "failed" and cert.cleanup_completed_at:
        cert.instance_id = None
        cert.provider_ip = None
        cert.mac_address = None
        db.commit()

    cert.status, cert.phase = "creating", "creating_instance"
    cert.started_at, cert.completed_at = utcnow(), None
    cert.cleanup_completed_at = None
    cert.diagnostic_detail = None
    db.commit()
    success = False
    failure = None
    try:
        provider = _provider()
        try:
            instance = provider.create_private_boot_canary(
                cert,
                hostname=gamenet_hostname(
                    event.id, site.team_id, site.key, base_type_id, "canary",
                ),
                db=db,
            )
        except Exception as exc:
            if "missing VPC attachment metadata" in str(exc):
                raise GameNetProviderError(str(exc)) from exc
            raise GameNetProviderError(f"provider instance creation failure: {exc}") from exc
        finally:
            provider.close()
        validate_vpc_only_instance(instance, label="private-boot canary")
        cert.provider_ip = instance.get("internal_ip")
        cert.mac_address = instance.get("vpc_mac")
        if not cert.provider_ip or not cert.mac_address:
            raise GameNetProviderError("missing VPC attachment metadata for private-boot canary")
        cert.status, cert.phase, cert.updated_at = "testing", "testing_reachability", utcnow()
        db.commit()
        _validate_private_boot_canary(db, cert, site, base.os)
        success = True
        cert.phase = "cleanup_after_pass"
        cert.completed_at = utcnow()
        db.commit()
    except Exception as exc:
        failure = exc
        cert.status = "failed"
        cert.phase = "cleanup_after_failure"
        cert.diagnostic_detail = str(exc)[:4000]
        cert.completed_at = utcnow()
        db.commit()
    try:
        _cleanup_canary(db, cert)
    except Exception as cleanup_exc:
        cert.status = "cleanup_failed"
        cert.diagnostic_detail = (
            f"{cert.diagnostic_detail + '; ' if cert.diagnostic_detail else ''}"
            f"canary cleanup failure: {cleanup_exc}"
        )[:4000]
        db.commit()
        raise GameNetProviderError(cert.diagnostic_detail) from cleanup_exc
    if success:
        cert.status, cert.phase = "passed", "passed"
        cert.diagnostic_detail = None
        db.commit()
        return
    cert.status, cert.phase = "failed", "failed"
    db.commit()
    raise GameNetProviderError(
        f"Vultr region/OS private boot certification failed for {site.region}/{base.os}: {failure}"
    ) from failure


def _cleanup_canary(db, cert):
    if cert.instance_id:
        provider = _provider()
        try:
            provider.delete_instance(cert.instance_id)
        finally:
            provider.close()
    cert.cleanup_completed_at = utcnow()
    cert.updated_at = utcnow()
    db.commit()


def _validate_private_boot_canary(db, cert, site, expected_os):
    firewall = db.get(VM, site.firewall_vm_id)
    gateway_vm = db.get(VM, site.team.vpn_gateway.vm_id)
    target = shlex.quote(cert.provider_ip)
    arp_target = shlex.quote("(" + cert.provider_ip + ")")
    probe_body = (
        f"target={target}; attempt=0; ping_status=1; tcp_status=1; arp_status=1; "
        'while [ "$attempt" -lt 24 ]; do '
        'if timeout 2 ping -c 1 "$target" >/dev/null 2>&1; then ping_status=0; else ping_status=$?; fi; '
        f"if arp -an | grep -F {arp_target} | grep -Fv '(incomplete)' >/dev/null 2>&1; "
        "then arp_status=0; else arp_status=$?; fi; "
        'if nc -z -w 2 "$target" 22 >/dev/null 2>&1; then tcp_status=0; else tcp_status=$?; fi; '
        'if [ "$ping_status" -eq 0 ] && [ "$tcp_status" -eq 0 ] && [ "$arp_status" -eq 0 ]; '
        "then exit 0; fi; attempt=$((attempt + 1)); sleep 3; done; "
        "printf 'ping=%s tcp=%s arp=%s attempts=%s\\n' "
        '"$ping_status" "$tcp_status" "$arp_status" "$attempt" >&2; exit 1'
    )
    probe = "sh -c " + shlex.quote(probe_body)
    try:
        code, _, error = ssh_command(
            firewall, probe, host=firewall.private_ip, jump=gateway_vm,
            timeout=140, connect_timeout=120,
        )
    except Exception as exc:
        raise GameNetProviderError(f"no ARP/TCP reachability to canary: {exc}") from exc
    if code:
        raise GameNetProviderError(f"no ARP/TCP reachability to canary: {error[:300]}")
    cert.phase = "testing_ssh"
    db.commit()
    try:
        code, _, error = ssh_host_command(
            db, cert.provider_ip, "true", jump=gateway_vm,
            connect_timeout=120, label="private-boot canary",
        )
    except Exception as exc:
        raise GameNetProviderError(f"SSH-key injection failure: {exc}") from exc
    if code:
        raise GameNetProviderError(f"SSH-key injection failure: {error[:300]}")
    cert.phase = "testing_cloud_init"
    db.commit()
    code, output, error = ssh_host_command(
        db, cert.provider_ip, "cloud-init status --wait --long", jump=gateway_vm,
        timeout=CREATE_TIMEOUT, connect_timeout=120, label="private-boot canary",
    )
    if code or "status: done" not in output.lower():
        raise GameNetProviderError(f"cloud-init failure: {(error or output)[:500]}")
    cert.phase = "testing_guest"
    db.commit()
    command = (
        ". /etc/os-release; printf 'os=%s\\n' \"$ID\"; uname -m; "
        f"iface=''; for p in /sys/class/net/*/address; do if [ \"$(tr A-F a-f < \"$p\")\" = {shlex.quote(cert.mac_address.lower())} ]; "
        "then iface=$(basename \"$(dirname \"$p\")\"); break; fi; done; "
        "test -n \"$iface\"; ip -4 address show dev \"$iface\"; "
        f"ip route get {shlex.quote(str(ip_network(site.allocated_cidr).network_address + 1))}"
    )
    code, output, error = ssh_host_command(
        db, cert.provider_ip, command, jump=gateway_vm,
        timeout=120, connect_timeout=120, label="private-boot canary",
    )
    expected_id = expected_os.split()[0].lower()
    expected_arch = (
        "x86_64" if "x64" in expected_os.lower()
        else "aarch64" if "arm64" in expected_os.lower() else ""
    )
    if code or f"os={expected_id}" not in output.lower() or (expected_arch and expected_arch not in output):
        raise GameNetProviderError(f"stock guest OS, architecture, VPC interface, or route check failed: {(error or output)[:500]}")


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


def _create_provider_instance(vm, *, public, vpc_ids=None, user_data=None,
                              image_source=None, stock_image=False):
    provider = _provider()
    try:
        instance = provider.create_instance(
            vm, public=public, vpc_ids=vpc_ids,
            user_data=(None if stock_image else (user_data if user_data is not None else ubuntu_cloud_init()))
            if not image_source else None,
            image_source=image_source,
        )
        if public:
            update_vm_addresses(vm, instance, public=True)
        else:
            try:
                validate_vpc_only_instance(instance, label=f"endpoint {vm.hostname}")
            except Exception as exc:
                try:
                    provider.delete_instance(vm.vultr_id)
                    vm.vultr_id = None
                    object_session(vm).commit()
                except Exception as cleanup_exc:
                    raise GameNetProviderError(
                        f"{exc}; rejected endpoint cleanup failure: {cleanup_exc}"
                    ) from cleanup_exc
                raise
            vm.public_ip = None
            vm.vpc_ip = instance.get("internal_ip") or instance.get("vpc_ip")
            vm.vpc_mac = instance.get("vpc_mac")
            if not vm.vpc_ip or not vm.vpc_mac:
                raise GameNetProviderError(f"missing VPC attachment metadata for endpoint {vm.hostname}")
            object_session(vm).commit()
    finally:
        provider.close()


def _create_provider_vpc(site):
    provider = _provider()
    try:
        return provider.create_vpc(site)
    finally:
        provider.close()


def _require_endpoint_prerequisites(db, event, infrastructure):
    from builder.base_loader import load_base_type

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
            for endpoint_group in zone["endpoints"]
            for endpoint in endpoint_instances(endpoint_group)
        }
        valid = db.query(PrivateBootCertification).filter_by(
            site_id=site.id, region=site.region, vpc_id=site.vpc_id,
            firewall_instance_id=firewall.vultr_id, status="passed",
        ).all()
        certified_os = {
            load_base_type(cert.base_type).os.casefold()
            for cert in valid if cert.cleanup_completed_at is not None
        }
        if not required_os <= certified_os:
            raise GameNetProviderError(
                "all endpoint stock images must pass private-boot certification before endpoint creation"
            )


def _attach_provider_vpc(vm, vpc_id):
    provider = _provider()
    try:
        return provider.attach_vpc(vm, vpc_id)
    finally:
        provider.close()


def _configure_site_tunnel(site):
    db = object_session(site)
    firewall = db.query(VM).filter_by(id=site.firewall_vm_id).one()
    gateway = site.team.vpn_gateway
    gateway_vm = db.query(VM).filter_by(id=gateway.vm_id).one()
    team_sites = db.query(Site).filter_by(team_id=site.team_id).all()
    configure_site_wireguard(site, firewall, gateway, gateway_vm, team_sites)
    validate_site_tunnel(site, firewall, gateway, gateway_vm)


def _apply_temporary_gateway_firewall(event, team, gateway_vm):
    value = os.environ.get("CTF_CONTROL_PLANE_CIDR", "").strip()
    try:
        control_plane = ip_network(value)
    except ValueError as exc:
        raise GameNetProviderError("CTF_CONTROL_PLANE_CIDR must be a valid IPv4 CIDR") from exc
    if control_plane.version != 4:
        raise GameNetProviderError("CTF_CONTROL_PLANE_CIDR must be an IPv4 CIDR")
    rules = [
        {"ip_type": "v4", "protocol": "udp", "subnet": str(control_plane.network_address),
         "subnet_size": control_plane.prefixlen, "port": str(team.vpn_gateway.listen_port), "source": ""},
        {"ip_type": "v4", "protocol": "tcp", "subnet": str(control_plane.network_address),
         "subnet_size": control_plane.prefixlen, "port": "22", "source": ""},
    ]
    for site in team.sites:
        firewall = object_session(team).get(VM, site.firewall_vm_id) if site.firewall_vm_id else None
        if firewall and firewall.public_ip:
            rules.append({
                "ip_type": "v4", "protocol": "udp", "subnet": firewall.public_ip,
                "subnet_size": 32, "port": str(team.vpn_gateway.listen_port), "source": "",
            })
    provider = _provider()
    try:
        group = provider.create_firewall_group(
            f"gamenet-e{event.id}-t{team.id}-gateway-bootstrap", rules,
        )
        provider.attach_firewall_group(gateway_vm, group)
    finally:
        provider.close()


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
    for site in db.query(Site).filter_by(event_id=event.id):
        firewall = db.query(VM).filter_by(id=site.firewall_vm_id).one()
        php = '''require_once("config.inc"); global $config;
$rules=$config["OPNsense"]["Firewall"]["Filter"]["rules"]["rule"]??[];
$config["OPNsense"]["Firewall"]["Filter"]["rules"]["rule"]=array_values(array_filter(
    $rules, fn($item)=>!in_array($item["description"]??"", ["Allow management SSH","Allow management HTTPS"])
));
$config["system"]["ssh"]["interfaces"]="lan";
write_config("Remove temporary public management access");'''
        command = "/bin/sh -c " + shlex.quote(
            f"/usr/local/bin/php -r {shlex.quote(php)} && "
            "/usr/local/sbin/configctl filter reload && /usr/local/sbin/configctl openssh restart"
        )
        code, _, error = ssh_command(firewall, command, host=firewall.private_ip)
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
        gateway_vm = db.get(VM, site.team.vpn_gateway.vm_id)
        private_management &= _tcp_probe(firewall.private_ip, 443)
        public_exposure &= _wait_ports_closed(firewall.public_ip, (22, 80, 443))
        endpoints = db.query(VM).filter(VM.site_id == site.id, VM.role.like("%_endpoint")).all()
        if len(endpoints) > 1:
            code, _, _ = ssh_command(
                endpoints[0], f"ping -c 1 -W 3 {endpoints[1].private_ip}", jump=gateway_vm,
            )
            same_site &= code == 0
        other_sites = [other for other in sites if other.team_id == site.team_id and other.id != site.id]
        if endpoints and other_sites:
            target = db.query(VM).filter(VM.site_id == other_sites[0].id, VM.role.like("%_endpoint")).first()
            if target:
                code, _, _ = ssh_command(
                    endpoints[0], f"ping -c 1 -W 3 {target.private_ip}", jump=gateway_vm,
                )
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
                code, _, _ = ssh_command(
                    source, f"ping -c 1 -W 3 {destination}", jump=gateway_vm,
                )
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
