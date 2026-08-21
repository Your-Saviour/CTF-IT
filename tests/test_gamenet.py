import json
import asyncio
from copy import deepcopy
from ipaddress import ip_network
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.database import Base
from api.models import (
    Event, OpnsenseImage, PlatformSettings, PrivateBootCertification,
    Site, Team, User, VM, VPNCredential, utcnow,
)
from api.routes.admin import PlanPreviewRequest, get_event, overview, plan_preview, update_event
from api.routes.vm import _gamenet_gateway_proxy
from api.services.gamenet import (
    allocate_event_networks, ensure_user_vpn_credential, render_user_config,
    site_dns_zone, vm_dns_name,
)
from api.services.gamenet_provider import (
    GameNetProviderError, VultrGameNetProvider, add_deterministic_endpoint_address,
    endpoint_cloud_init, opnsense_config_fingerprint, render_opnsense_config,
    snapshot_site_validation_command, ubuntu_cloud_init, validate_site_tunnel,
    validate_vpc_only_instance,
)
from api.services.gamenet_provisioning import (
    PROVISIONING_STEPS, certify_private_boot, create_private_endpoints,
    ensure_vm_placeholders,
)
from api.services.secrets import decrypt_secret
from builder.infrastructure_validation import gamenet_hostname, infrastructure_summary, validate_infrastructure


INFRASTRUCTURE = {
    "vpn_gateway": {"base_type": "ubuntu_24_server", "default_plan": "vc2-1c-1gb", "region": "ewr", "listen_port": 51820},
    "sites": [{"key": "head_office", "name": "Head Office", "region": "ewr",
        "firewall": {"base_type": "opnsense", "default_plan": "vc2-2c-4gb"},
        "zones": [
            {"key": "corporate", "name": "Corporate", "team": "blue", "endpoints": [
                {"key": "workstation", "base_type": "ubuntu_24_server", "count": 2, "default_plan": "vc2-1c-1gb"}]},
            {"key": "red_team", "name": "Red Team", "team": "red", "endpoints": []},
        ]}],
}
BASES = {"ubuntu_24_server", "opnsense"}


def test_snapshot_site_validation_checks_effective_pf_policy():
    command = snapshot_site_validation_command(
        token="generation-token",
        expected_version="26.7", public_ip="198.51.100.12", private_ip="10.128.0.1",
        wan_interface="vtnet0", lan_interface="vtnet1", lan_mac="00:11:22:33:44:55",
        management_cidr="192.0.2.8/32",
    )
    assert "pass in quick on vtnet1 inet from (vtnet1:network) to any" in command
    assert "nat on" in command and "from 192.0.2.8 to" in command
    assert "Allow management SSH" not in command
    assert "printf '%s\\n' generation-token" in command
    assert "/conf/ctf-site-ready" in command


def test_opnsense_config_fingerprint_is_stable_and_tracks_semantic_inputs():
    site = MagicMock(id=7, allocated_cidr="10.128.0.0/20")
    vm = MagicMock(id=9, vultr_id="instance-1", public_ip="198.51.100.12",
                   private_ip="10.128.0.1", hostname="firewall")
    gateway_vm = MagicMock(public_ip="198.51.100.20")
    values = dict(
        site=site, vm=vm, expected_version="26.7", lan_mac="00:11:22:33:44:55",
        wan_interface="vtnet0", lan_interface="vtnet1", gateway_vm=gateway_vm,
        gateway_listen_port=51820, management_cidr="192.0.2.8/32",
        platform_public_key="ssh-ed25519 TEST",
    )
    first = opnsense_config_fingerprint(**values)
    assert first == opnsense_config_fingerprint(**values)
    assert len(first) == 64
    assert first != opnsense_config_fingerprint(**{**values, "lan_mac": "00:11:22:33:44:66"})


def test_vm_persists_opnsense_configuration_generation(db_session):
    event = Event(name="GameNet", quota="{}")
    db_session.add(event); db_session.flush()
    team = Team(name="One", event_id=event.id)
    db_session.add(team); db_session.flush()
    vm = VM(
        hostname="firewall", team_id=team.id, event_id=event.id,
        opnsense_config_token="a" * 64,
        opnsense_config_fingerprint="b" * 64,
        opnsense_config_status="applying",
        opnsense_config_started_at=utcnow(),
    )
    db_session.add(vm); db_session.commit(); db_session.expire_all()
    saved = db_session.get(VM, vm.id)
    assert saved.opnsense_config_token == "a" * 64
    assert saved.opnsense_config_fingerprint == "b" * 64
    assert saved.opnsense_config_status == "applying"


def test_general_integration_migration_replaces_obsolete_event_sync_columns():
    migration = Path("migrations/versions/0017_general_integrations.py").read_text()
    for table in (
        "integration_destinations", "event_integrations",
        "integration_sync_jobs", "integration_sync_attempts",
    ):
        assert table in migration
    for column in (
        "expo_sync_status", "expo_sync_last_error",
        "expo_sync_attempts", "expo_sync_completed_at",
    ):
        assert f'drop_column("{column}")' in migration

    startup = Path("api/main.py").read_text()
    assert "expo_sync_status" not in startup
    assert "expo_sync_last_error" not in startup


def test_snapshot_configuration_resume_does_not_relaunch_live_generation(monkeypatch, db_session):
    from api.services.gamenet_provider import configure_snapshot_opnsense

    event = Event(name="GameNet", quota="{}", infrastructure=json.dumps(INFRASTRUCTURE))
    db_session.add(event); db_session.flush()
    team = Team(name="One", event_id=event.id); db_session.add(team); db_session.flush()
    allocate_event_networks(db_session, event, [team], INFRASTRUCTURE)
    site = db_session.query(Site).one()
    gateway_vm = VM(hostname="gateway", team_id=team.id, event_id=event.id,
                    role="vpn_gateway", public_ip="198.51.100.20")
    db_session.add(gateway_vm); db_session.flush()
    team.vpn_gateway.vm_id = gateway_vm.id
    vm = VM(
        hostname="firewall", team_id=team.id, event_id=event.id, site_id=site.id,
        public_ip="198.51.100.12", private_ip="10.128.0.1", vultr_id="firewall-1",
        opnsense_config_token="a" * 64, opnsense_config_fingerprint="fingerprint",
        opnsense_config_status="applying",
    )
    db_session.add(vm); db_session.commit()
    monkeypatch.setattr("api.services.gamenet_provider.get_or_create_platform_keypair",
                        lambda _db: ("private", "ssh-ed25519 TEST"))
    monkeypatch.setattr("api.services.gamenet_provider._snapshot_interface_mapping",
                        lambda *_args: ("vtnet0", "vtnet1"))
    monkeypatch.setattr("api.services.gamenet_provider.opnsense_config_fingerprint",
                        lambda **_kwargs: "fingerprint")
    monkeypatch.setattr("api.services.gamenet_provider._capture_ssh_host_key", lambda _vm: None)
    uploads = []
    monkeypatch.setattr("api.services.gamenet_provider.upload_text",
                        lambda *_args, **_kwargs: uploads.append(_args[1]))
    responses = iter([
        (1, "", "not ready"),  # semantic validation before resume
        (0, "", ""),           # exact applying process is alive
        (0, "OPNsense 26.7", ""),
    ])
    monkeypatch.setattr("api.services.gamenet_provider.ssh_command",
                        lambda *_args, **_kwargs: next(responses))
    configure_snapshot_opnsense(site, vm, "26.7", lan_mac="00:11:22:33:44:55")
    assert uploads == []
    assert vm.opnsense_config_status == "applied"


def test_snapshot_site_apply_uses_posix_shell_for_opnsense_root():
    source = Path("api/services/gamenet_provider.py").read_text()
    assert 'ssh_command(vm, "/bin/sh -c " + shlex.quote(launch)' in source
    assert "nohup lockf -t 0 /conf/ctf-site-apply.lock /bin/sh" in source


def test_gateway_resets_unbound_start_limit_after_wireguard_is_ready():
    source = Path("api/services/gamenet_provider.py").read_text()
    command = "systemctl reset-failed unbound && systemctl enable unbound && systemctl restart unbound"
    assert command in source
    assert source.index("systemctl start wg-quick@gamenet") < source.index(command)


def test_gateway_waits_for_cloud_init_and_dpkg_locks():
    source = Path("api/services/gamenet_provider.py").read_text()
    assert '"cloud-init status --wait && "' in source
    assert source.count("DPkg::Lock::Timeout=300") >= 2


def test_gateway_skips_apt_when_required_packages_are_already_installed(monkeypatch):
    from api.services import gamenet_provider

    gateway = MagicMock(
        private_key_encrypted="encrypted", vpn_address="10.64.0.1",
        listen_port=51820, platform_public_key=None, platform_address=None,
    )
    vm = MagicMock()
    commands = []
    monkeypatch.setattr(gamenet_provider, "decrypt_secret", lambda _value: "private-key")
    monkeypatch.setattr(gamenet_provider, "upload_text", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        gamenet_provider,
        "ssh_command",
        lambda _vm, command, **_kwargs: commands.append(command) or (0, "", ""),
    )

    gamenet_provider.configure_gateway(gateway, vm, [], [])

    assert len(commands) == 1
    assert (
        "if ! command -v wg >/dev/null 2>&1 || "
        "! command -v iptables >/dev/null 2>&1 || "
        "! command -v unbound >/dev/null 2>&1; then "
    ) in commands[0]
    assert "install -y wireguard iptables unbound; fi &&" in commands[0]


def test_gateway_guest_firewall_allows_wireguard_listener():
    source = Path("api/services/gamenet_provider.py").read_text()
    assert 'ufw allow {gateway.listen_port}/udp' in source


@pytest.fixture
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)


def test_infrastructure_validation_accepts_empty_zone_and_summarises():
    assert validate_infrastructure(INFRASTRUCTURE, BASES) == []
    assert infrastructure_summary(INFRASTRUCTURE, 3) == {
        "teams": 3, "sites": 3, "gateways": 3, "firewalls": 3,
        "endpoints": 6, "vms": 12, "vpcs_by_region": {"ewr": 3},
    }


def test_infrastructure_validation_accepts_individual_endpoint_with_name():
    value = deepcopy(INFRASTRUCTURE)
    endpoint = value["sites"][0]["zones"][0]["endpoints"][0]
    endpoint.pop("count")
    endpoint["name"] = "Workstation 1"

    assert validate_infrastructure(value, BASES) == []
    assert infrastructure_summary(value)["endpoints"] == 1


def test_legacy_endpoint_groups_expand_without_mutating_input():
    from builder.infrastructure_planner import normalize_infrastructure

    legacy = deepcopy(INFRASTRUCTURE)
    expanded = normalize_infrastructure(legacy)
    endpoints = expanded["sites"][0]["zones"][0]["endpoints"]

    assert [(row["key"], row["name"]) for row in endpoints] == [
        ("workstation_1", "Workstation 1"),
        ("workstation_2", "Workstation 2"),
    ]
    assert legacy["sites"][0]["zones"][0]["endpoints"][0]["count"] == 2
    assert all("count" not in row for row in endpoints)


def test_machine_icon_override_rejects_unknown_library_keyword():
    from builder.infrastructure_validation import validate_infrastructure

    value = deepcopy(INFRASTRUCTURE)
    value["sites"][0]["zones"][0]["endpoints"][0]["icon"] = "not-in-library"

    errors = validate_infrastructure(value, BASES)

    assert "sites[0].zones[0].endpoints[0].icon must reference a supported planner icon" in errors


def test_machine_primary_icon_override_rejects_unknown_library_keyword():
    from builder.infrastructure_validation import validate_infrastructure

    value = deepcopy(INFRASTRUCTURE)
    value["sites"][0]["zones"][0]["endpoints"][0]["primary_icon"] = "not-in-library"

    errors = validate_infrastructure(value, BASES)

    assert "sites[0].zones[0].endpoints[0].primary_icon must reference a supported planner icon" in errors


def test_planner_icon_allowlist_covers_cyber_training_catalogue():
    from builder.infrastructure_validation import PLANNER_ICONS

    assert PLANNER_ICONS == {
        "server", "desktop", "laptop", "mobile", "appliance",
        "gateway", "router", "switch", "firewall", "vpn", "proxy", "load_balancer",
        "web", "database", "dns", "mail", "directory", "file_share", "storage",
        "certificate_authority", "identity", "attacker", "target", "siem", "ids",
        "monitoring", "logging", "honeypot", "malware", "bastion", "vulnerable",
        "cloud", "container", "kubernetes", "backup", "git", "cicd", "linux",
        "ubuntu", "debian", "kali", "redhat", "windows", "macos", "freebsd",
        "opnsense", "pfsense", "aws", "azure", "gcp",
    }


def test_legacy_expansion_avoids_existing_endpoint_key_collisions():
    from builder.infrastructure_planner import normalize_infrastructure

    value = deepcopy(INFRASTRUCTURE)
    endpoints = value["sites"][0]["zones"][0]["endpoints"]
    endpoints.append({
        "key": "workstation_1", "name": "Existing workstation",
        "base_type": "ubuntu_24_server", "default_plan": "vc2-1c-1gb",
    })

    keys = [row["key"] for row in normalize_infrastructure(value)["sites"][0]["zones"][0]["endpoints"]]
    assert keys == ["workstation_1_2", "workstation_2", "workstation_1"]


def test_layout_accepts_known_stable_node_ids():
    from builder.infrastructure_planner import normalize_infrastructure, validate_infrastructure_layout

    infrastructure = normalize_infrastructure(INFRASTRUCTURE)
    layout = {"version": 1, "nodes": {
        "gateway": {"x": 10, "y": 20},
        "site:head_office": {"x": 100.5, "y": 80},
        "vm:head_office/corporate/workstation_1": {"x": 220, "y": 300},
    }}

    assert validate_infrastructure_layout(layout, infrastructure) == []


def test_layout_ids_model_firewall_as_vm_inside_automatic_zone():
    from builder.infrastructure_planner import infrastructure_node_ids, normalize_infrastructure

    ids = infrastructure_node_ids(normalize_infrastructure(INFRASTRUCTURE))

    assert "firewall-zone:head_office" in ids
    assert "firewall:head_office/primary" in ids
    assert "firewall:head_office" not in ids


def test_layout_rejects_unknown_ids_and_non_finite_coordinates():
    from builder.infrastructure_planner import normalize_infrastructure, validate_infrastructure_layout

    errors = validate_infrastructure_layout(
        {"version": 1, "nodes": {"vm:unknown": {"x": float("inf"), "y": 0}}},
        normalize_infrastructure(INFRASTRUCTURE),
    )

    assert "infrastructure_layout.nodes.vm:unknown references an unknown node id" in errors
    assert "infrastructure_layout.nodes.vm:unknown.x must be a finite number" in errors


def test_layout_rejects_unsupported_version_and_oversize_payload():
    from builder.infrastructure_planner import normalize_infrastructure, validate_infrastructure_layout

    infrastructure = normalize_infrastructure(INFRASTRUCTURE)
    errors = validate_infrastructure_layout({"version": 2, "nodes": {}}, infrastructure)
    assert errors == ["infrastructure_layout.version must be 1"]

    huge = {"version": 1, "nodes": {"gateway": {"x": 1, "y": 2, "padding": "x" * 300_000}}}
    assert "infrastructure_layout exceeds 262144 bytes" in validate_infrastructure_layout(huge, infrastructure)


def test_event_persists_network_planner_layout_and_revision(db_session):
    layout = {"version": 1, "nodes": {"gateway": {"x": 10, "y": 20}}}
    event = Event(name="Planner", quota="{}", infrastructure_layout=json.dumps(layout))
    db_session.add(event)
    db_session.commit()
    db_session.expire_all()

    saved = db_session.get(Event, event.id)
    assert json.loads(saved.infrastructure_layout) == layout
    assert saved.updated_at is not None


def test_event_detail_exposes_planner_layout_and_revision(monkeypatch, db_session):
    layout = {"version": 1, "nodes": {"gateway": {"x": 10, "y": 20}}}
    event = Event(name="Planner", quota="{}", infrastructure_layout=json.dumps(layout))
    db_session.add(event)
    db_session.commit()
    monkeypatch.setattr("api.routes.admin.require_admin", lambda *_args, **_kwargs: User(is_admin=True))

    payload = asyncio.run(get_event(event.id, MagicMock(), db_session))

    assert payload["infrastructure_layout"] == layout
    assert payload["updated_at"] == event.updated_at.isoformat()


def test_planner_save_updates_infrastructure_and_layout_atomically(monkeypatch, db_session):
    from builder.infrastructure_planner import normalize_infrastructure

    event = Event(name="Planner", quota="{}", infrastructure=json.dumps(INFRASTRUCTURE))
    db_session.add(event); db_session.commit()
    layout = {"version": 1, "nodes": {"gateway": {"x": 10, "y": 20}}}
    request = MagicMock()
    request.json = AsyncMock(return_value={
        "infrastructure": normalize_infrastructure(INFRASTRUCTURE),
        "infrastructure_layout": layout,
        "expected_updated_at": event.updated_at.isoformat(),
    })
    monkeypatch.setattr("api.routes.admin.require_admin", lambda *_args, **_kwargs: User(is_admin=True))

    response = asyncio.run(update_event(event.id, request, db_session))

    assert response["status"] == "updated"
    assert response["updated_at"]
    assert json.loads(event.infrastructure_layout) == layout


def test_planner_save_rejects_stale_revision_without_partial_update(monkeypatch, db_session):
    event = Event(name="Planner", quota="{}", infrastructure=json.dumps(INFRASTRUCTURE))
    db_session.add(event); db_session.commit()
    original = event.infrastructure
    request = MagicMock()
    request.json = AsyncMock(return_value={
        "infrastructure": {"broken": True},
        "infrastructure_layout": {"version": 1, "nodes": {}},
        "expected_updated_at": "2000-01-01T00:00:00+00:00",
    })
    monkeypatch.setattr("api.routes.admin.require_admin", lambda *_args, **_kwargs: User(is_admin=True))

    response = asyncio.run(update_event(event.id, request, db_session))

    assert response.status_code == 409
    assert event.infrastructure == original
    assert event.infrastructure_layout is None


def test_planner_save_requires_revision_token(monkeypatch, db_session):
    event = Event(name="Planner", quota="{}", infrastructure=json.dumps(INFRASTRUCTURE))
    db_session.add(event); db_session.commit()
    request = MagicMock()
    request.json = AsyncMock(return_value={"infrastructure_layout": {"version": 1, "nodes": {}}})
    monkeypatch.setattr("api.routes.admin.require_admin", lambda *_args, **_kwargs: User(is_admin=True))

    response = asyncio.run(update_event(event.id, request, db_session))

    assert response.status_code == 409
    assert json.loads(response.body)["error"] == "expected_updated_at is required for planner updates"


def test_planner_save_accepts_equivalent_timezone_revision(monkeypatch, db_session):
    event = Event(name="Planner", quota="{}", infrastructure=json.dumps(INFRASTRUCTURE))
    db_session.add(event); db_session.commit()
    expected = event.updated_at.astimezone(__import__("datetime").timezone(
        __import__("datetime").timedelta(hours=9, minutes=30)
    )).isoformat()
    request = MagicMock()
    request.json = AsyncMock(return_value={
        "infrastructure_layout": {"version": 1, "nodes": {}},
        "expected_updated_at": expected,
    })
    monkeypatch.setattr("api.routes.admin.require_admin", lambda *_args, **_kwargs: User(is_admin=True))

    response = asyncio.run(update_event(event.id, request, db_session))

    assert response["status"] == "updated"


def test_gamenet_hostname_is_provider_safe_stable_and_bounded():
    hostname = gamenet_hostname(123, 456, "training_site", "blue_zone", "endpoint_" + "x" * 80, 1)
    assert len(hostname) <= 63
    assert hostname == gamenet_hostname(123, 456, "training_site", "blue_zone", "endpoint_" + "x" * 80, 1)
    assert hostname.replace("-", "").isalnum()
    assert "_" not in hostname


def test_opnsense_config_encodes_authorized_key(monkeypatch, db_session):
    monkeypatch.setenv("CTF_CONTROL_PLANE_CIDR", "127.0.0.1/32")
    event = Event(name="GameNet", quota="{}", infrastructure=json.dumps(INFRASTRUCTURE))
    db_session.add(event); db_session.flush()
    team = Team(name="One", event_id=event.id); db_session.add(team); db_session.flush()
    allocate_event_networks(db_session, event, [team], INFRASTRUCTURE)
    site = db_session.query(Site).one()
    vm = VM(hostname="firewall", team_id=team.id, event_id=event.id, site_id=site.id)
    db_session.add(vm); db_session.flush()
    rendered = render_opnsense_config(
        site, vm, "ssh-ed25519 TEST", "password", temporary_management=True,
        wan_interface="vtnet7", lan_interface="vtnet3",
    )
    assert "<authorizedkeys>c3NoLWVkMjU1MTkgVEVTVA==</authorizedkeys>" in rendered
    assert "<active_interface>lan</active_interface>" in rendered
    assert "<OPNsense>" in rendered
    assert "<source_net>127.0.0.1/32</source_net>" in rendered
    assert "<description>Allow management SSH</description>" in rendered
    assert "<filter/>" in rendered
    assert "<filter>" not in rendered
    assert "<if>vtnet7</if>" in rendered
    assert "<if>vtnet3</if>" in rendered
    assert "<passwordauth>" not in rendered
    assert "<ssl-certref>self-signed</ssl-certref>" not in rendered


def test_opnsense_config_uses_replyto_free_wan_outbound_without_ad_hoc_wireguard_rule(db_session):
    event = Event(name="GameNet", quota="{}", infrastructure=json.dumps(INFRASTRUCTURE))
    db_session.add(event); db_session.flush()
    team = Team(name="One", event_id=event.id); db_session.add(team); db_session.flush()
    allocate_event_networks(db_session, event, [team], INFRASTRUCTURE)
    site = db_session.query(Site).one()
    vm = VM(hostname="firewall", team_id=team.id, event_id=event.id, site_id=site.id)
    db_session.add(vm); db_session.flush()
    rendered = render_opnsense_config(
        site, vm, "ssh-ed25519 TEST", "password", temporary_management=True,
    )
    assert "Allow site WireGuard replies" not in rendered
    outbound_rule = rendered.split("Allow WAN outbound")[0].rsplit("<rule>", 1)[-1]
    assert "<disablereplyto>1</disablereplyto>" in outbound_rule


def test_validate_site_tunnel_requires_both_handshakes_and_private_ssh(monkeypatch):
    site = MagicMock(tunnel_public_key="SITE", tunnel_address="10.64.0.3")
    firewall = MagicMock(private_ip="10.128.0.1")
    gateway = MagicMock(public_key="GATEWAY")
    gateway_vm = MagicMock()
    responses = iter([
        (0, "0", ""), (0, "0", ""),
        (0, "1700000000", ""), (0, "1700000000", ""), (0, "", ""),
    ])
    monkeypatch.setattr("api.services.gamenet_provider.ssh_command",
                        lambda *_args, **_kwargs: next(responses))
    monkeypatch.setattr("api.services.gamenet_provider.time.time", lambda: 1700000010)
    monkeypatch.setattr("api.services.gamenet_provider.time.sleep", lambda *_args: None)
    validate_site_tunnel(site, firewall, gateway, gateway_vm, timeout=1)


def test_validate_site_tunnel_rejects_missing_handshake(monkeypatch):
    site = MagicMock(tunnel_public_key="SITE", tunnel_address="10.64.0.3")
    firewall = MagicMock(private_ip="10.128.0.1")
    gateway = MagicMock(public_key="GATEWAY")
    gateway_vm = MagicMock()
    monkeypatch.setattr("api.services.gamenet_provider.ssh_command",
                        lambda *_args, **_kwargs: (0, "0", ""))
    ticks = iter([0.0, 2.0])
    monkeypatch.setattr("api.services.gamenet_provider.time.monotonic", lambda: next(ticks))
    monkeypatch.setattr("api.services.gamenet_provider.time.sleep", lambda *_args: None)
    with pytest.raises(GameNetProviderError, match="WireGuard handshake"):
        validate_site_tunnel(site, firewall, gateway, gateway_vm, timeout=1)


def test_managed_dns_names_normalize_keys_and_preserve_endpoint_ordinals(db_session):
    event = Event(name="GameNet", quota="{}", infrastructure=json.dumps(INFRASTRUCTURE))
    db_session.add(event); db_session.flush()
    team = Team(name="One", event_id=event.id); db_session.add(team); db_session.flush()
    allocate_event_networks(db_session, event, [team], INFRASTRUCTURE)
    endpoints = [vm for vm in ensure_vm_placeholders(db_session, event, INFRASTRUCTURE)
                 if vm.role == "blue_endpoint"]
    assert site_dns_zone(db_session.query(Site).one()) == "head-office.gamenet.test"
    assert [vm_dns_name(vm) for vm in endpoints] == [
        "workstation-1.corporate.head-office.gamenet.test",
        "workstation-2.corporate.head-office.gamenet.test",
    ]


def test_user_profile_routes_and_uses_team_resolver(monkeypatch, db_session):
    event = Event(name="GameNet", quota="{}", infrastructure=json.dumps(INFRASTRUCTURE))
    db_session.add(event); db_session.flush()
    team = Team(name="One", event_id=event.id); db_session.add(team); db_session.flush()
    allocate_event_networks(db_session, event, [team], INFRASTRUCTURE)
    ensure_vm_placeholders(db_session, event, INFRASTRUCTURE)
    gateway = team.vpn_gateway
    gateway.status = "active"
    gateway_vm = db_session.get(VM, gateway.vm_id)
    gateway_vm.public_ip = "192.0.2.10"
    user = User(username="learner", password_hash="x", event_id=event.id, team_id=team.id)
    db_session.add(user); db_session.flush()
    monkeypatch.setattr("api.services.gamenet._sync_team_gateway_if_active", lambda *_: None)
    config = render_user_config(db_session, user)
    assert f"DNS = {gateway.vpn_address}" in config
    assert f"AllowedIPs = {gateway.vpn_address}/32, {team.sites[0].allocated_cidr}" in config


def test_opnsense_bootstrap_waits_through_pre_reboot_window(monkeypatch, db_session):
    from api.services.gamenet_provider import bootstrap_opnsense

    event = Event(name="GameNet", quota="{}", infrastructure=json.dumps(INFRASTRUCTURE))
    db_session.add(event); db_session.flush()
    team = Team(name="One", event_id=event.id); db_session.add(team); db_session.flush()
    allocate_event_networks(db_session, event, [team], INFRASTRUCTURE)
    site = db_session.query(Site).one()
    vm = VM(hostname="firewall", team_id=team.id, event_id=event.id, site_id=site.id,
            public_ip="192.0.2.1")
    db_session.add(vm); db_session.flush()
    responses = iter([(0, "", ""), (1, "", ""), (0, "ready", "")])
    monkeypatch.setattr("api.services.gamenet_provider.upload_text", lambda *_args, **_kwargs: None)
    commands = []
    monkeypatch.setattr("api.services.gamenet_provider.ssh_command",
                        lambda _vm, command, **_kwargs: commands.append(command) or next(responses))
    monkeypatch.setattr("api.services.gamenet_provider.time.sleep", lambda *_args: None)
    bootstrap_opnsense(site, vm)
    assert "-r 26.7" in commands[0]


def test_firewall_ssh_uses_encrypted_admin_password_as_key_fallback(monkeypatch, db_session):
    from api.services.gamenet_provider import ssh_command
    from api.services.secrets import encrypt_secret

    event = Event(name="GameNet", quota="{}")
    db_session.add(event); db_session.flush()
    team = Team(name="One", event_id=event.id); db_session.add(team); db_session.flush()
    vm = VM(hostname="firewall", team_id=team.id, event_id=event.id, public_ip="192.0.2.1",
            admin_password=encrypt_secret("opnsense-password"))
    db_session.add(vm); db_session.flush()
    captured = {}

    class Channel:
        def recv_exit_status(self): return 0

    class Stream:
        channel = Channel()
        def read(self): return b""

    class Client:
        def exec_command(self, *_args, **_kwargs): return None, Stream(), Stream()
        def close(self): pass

    monkeypatch.setattr("api.services.gamenet_provider._connect_ssh",
                        lambda *_args, **kwargs: captured.update(kwargs) or Client())
    ssh_command(vm, "true")
    assert captured["password"] == "opnsense-password"
    assert captured["connect_timeout"] is None


def test_upload_text_creates_parent_directory(monkeypatch):
    from api.services.gamenet_provider import upload_text
    captured = {}
    monkeypatch.setattr("api.services.gamenet_provider.ssh_command",
                        lambda _vm, command, **_kwargs: captured.update(command=command) or (0, "", ""))
    upload_text(MagicMock(), "/usr/local/etc/wireguard/wg0.conf", "contents")
    assert captured["command"].startswith("mkdir -p /usr/local/etc/wireguard && ")


def test_endpoint_cloud_init_configures_network_before_package_work():
    rendered = endpoint_cloud_init("10.128.1.10", "10.128.0.0/20")
    assert "bootcmd:" not in rendered
    assert rendered.index("write_files:") < rendered.index("runcmd:")
    assert "  - [sh, /usr/local/sbin/gamenet-network.sh]" in rendered
    assert rendered.index("gamenet-network.sh]") < rendered.index("systemctl, enable, --now, ssh")
    assert "10.128.1.10/20" in rendered
    assert "via: 10.128.0.1" in rendered
    assert "package_update" not in rendered


def test_ssh_jump_channel_retries_until_private_endpoint_is_ready(monkeypatch, db_session):
    from api.services.gamenet_provider import ssh_command

    event = Event(name="GameNet", quota="{}")
    db_session.add(event); db_session.flush()
    team = Team(name="One", event_id=event.id); db_session.add(team); db_session.flush()
    endpoint = VM(hostname="endpoint", team_id=team.id, event_id=event.id, private_ip="10.128.1.10")
    jump = VM(hostname="firewall", team_id=team.id, event_id=event.id, public_ip="192.0.2.1")
    db_session.add_all([endpoint, jump]); db_session.flush()

    class Channel:
        def recv_exit_status(self): return 0

    class Stream:
        channel = Channel()
        def read(self): return b""

    class TargetClient:
        def exec_command(self, *_args, **_kwargs): return None, Stream(), Stream()
        def close(self): pass

    attempts = {"count": 0}
    class Transport:
        def open_channel(self, *_args, **_kwargs):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise RuntimeError("not ready")
            return object()

    class JumpClient:
        def get_transport(self): return Transport()
        def close(self): pass

    clients = iter([JumpClient(), TargetClient()])
    monkeypatch.setattr("api.services.gamenet_provider._connect_ssh", lambda *_args, **_kwargs: next(clients))
    monkeypatch.setattr("api.services.gamenet_provider.time.sleep", lambda *_args: None)
    code, _, _ = ssh_command(endpoint, "true", jump=jump, connect_timeout=30)
    assert code == 0
    assert attempts["count"] == 2


def test_site_wireguard_runs_redirects_under_posix_shell(monkeypatch):
    from api.services.gamenet_provider import configure_site_wireguard
    gateway = MagicMock(public_key="PUBLIC", listen_port=51820)
    gateway_vm = MagicMock(public_ip="192.0.2.1")
    site = MagicMock(tunnel_address="10.64.0.3", tunnel_private_key_encrypted="PRIVATE",
                     allocated_cidr="10.128.0.0/20", id=1)
    firewall = MagicMock()
    commands = []
    uploads = {}
    monkeypatch.setattr("api.services.gamenet_provider.decrypt_secret", lambda value: value)
    monkeypatch.setattr("api.services.gamenet_provider._site_unbound_config", lambda _site: "resolver-config")
    monkeypatch.setattr(
        "api.services.gamenet_provider.upload_text",
        lambda _vm, path, content, **_kwargs: uploads.update({path: content}),
    )
    monkeypatch.setattr("api.services.gamenet_provider.ssh_command",
                        lambda _vm, command, **_kwargs: commands.append(command) or (0, "", ""))
    configure_site_wireguard(site, firewall, gateway, gateway_vm, [site])
    assert commands[-1].startswith("sh -c ")
    assert "test -x /usr/bin/wg" in commands[-1]
    assert "Address =" not in uploads["/usr/local/etc/wireguard/wg0.conf"]
    startup = uploads["/usr/local/etc/rc.d/gamenet"]
    assert "/usr/bin/wg setconf wg0" in startup
    assert "/sbin/ifconfig wg0 inet 10.64.0.3/32 alias" in startup
    assert "wg-quick" not in startup
    assert "/usr/local/etc/gamenet.pf" not in uploads
    assert uploads["/usr/local/etc/unbound.opnsense.d/gamenet.conf"] == "resolver-config"
    plugin = uploads["/usr/local/etc/inc/plugins.inc.d/gamenet.inc"]
    assert "function gamenet_firewall" in plugin
    assert "'interface' => 'gamenet'" in plugin
    assert "'from' => '10.64.0.0/10'" in plugin
    assert "'to' => \"10.128.0.0/20\"" in plugin
    assert "'to_port' => 53" in plugin
    assert "configctl filter reload" in commands[-1]
    assert "Configure GameNet tunnel interface" in commands[-1]
    assert 'require_once("util.inc")' in commands[-1]
    assert "pfctl -a gamenet" not in startup


def test_site_resolver_is_private_and_contains_managed_records(db_session):
    from api.services.gamenet_provider import _site_unbound_config
    event = Event(name="GameNet", quota="{}", infrastructure=json.dumps(INFRASTRUCTURE))
    db_session.add(event); db_session.flush()
    team = Team(name="One", event_id=event.id); db_session.add(team); db_session.flush()
    allocate_event_networks(db_session, event, [team], INFRASTRUCTURE)
    ensure_vm_placeholders(db_session, event, INFRASTRUCTURE)
    site = db_session.query(Site).one()
    rendered = _site_unbound_config(site)
    assert f"interface: {site.tunnel_address}" in rendered
    assert "interface-automatic: no" in rendered
    assert "access-control: 0.0.0.0/0 refuse" in rendered
    assert 'local-zone: "head-office.gamenet.test." static' in rendered
    assert 'workstation-1.corporate.head-office.gamenet.test. 60 IN A' in rendered


def test_infrastructure_rejects_duplicates_exhaustion_and_vpc_capacity():
    value = json.loads(json.dumps(INFRASTRUCTURE))
    value["sites"][0]["zones"].append(json.loads(json.dumps(value["sites"][0]["zones"][0])))
    value["sites"][0]["zones"][0]["endpoints"][0]["count"] = 246
    errors = validate_infrastructure(value, BASES, team_count=3, live_vpcs_by_region={"ewr": 3})
    assert any("duplicates key" in error for error in errors)
    assert any("exhaust" in error for error in errors)
    assert any("limit is 5" in error for error in errors)


def test_global_site_allocation_and_encrypted_user_key(db_session):
    event = Event(name="GameNet", quota="{}", infrastructure=json.dumps(INFRASTRUCTURE))
    db_session.add(event); db_session.flush()
    teams = [Team(name=name, event_id=event.id) for name in ("One", "Two")]
    db_session.add_all(teams); db_session.flush()
    allocate_event_networks(db_session, event, teams, INFRASTRUCTURE)
    sites = db_session.query(Site).order_by(Site.id).all()
    assert len(sites) == 2
    assert ip_network(sites[0].allocated_cidr).overlaps(ip_network(sites[1].allocated_cidr)) is False
    assert [zone.team_role for zone in sites[0].zones] == ["blue", "red"]
    user = User(username="learner", password_hash="x", event_id=event.id, team_id=teams[0].id)
    db_session.add(user); db_session.flush()
    credential = ensure_user_vpn_credential(db_session, user)
    assert credential.private_key_encrypted.startswith("enc:v1:")
    assert decrypt_secret(credential.private_key_encrypted) != credential.private_key_encrypted
    assert db_session.query(VPNCredential).count() == 1
    assert sites[0].tunnel_address and teams[0].vpn_gateway.platform_address


def test_vultr_private_instance_request_is_vpc_only(monkeypatch, db_session):
    event = Event(name="GameNet", quota="{}", infrastructure=json.dumps(INFRASTRUCTURE))
    db_session.add(event); db_session.flush()
    team = Team(name="One", event_id=event.id); db_session.add(team); db_session.flush()
    from api.models import VM
    vm = VM(hostname="private-endpoint", team_id=team.id, event_id=event.id,
            base_type="ubuntu_24_server", vultr_plan="vc2-1c-2gb", vultr_region="ewr")
    db_session.add(vm); db_session.flush()
    monkeypatch.setenv("VULTR_API_KEY", "test")
    provider = VultrGameNetProvider()
    calls = []
    monkeypatch.setattr(provider, "_resolve_os_id", lambda _: 2284)
    monkeypatch.setattr(provider, "_ensure_ssh_key", lambda *_: "key-id")
    responses = iter([
        {"instances": []},
        {"instance": {"id": "instance-id"}},
        {"vpcs": [{"id": "vpc-id", "ip_address": "10.128.16.10", "mac_address": "5a:00:00:00:00:10"}]},
    ])
    monkeypatch.setattr(provider, "_request", lambda method, path, **kwargs: calls.append((method, path, kwargs)) or next(responses))
    monkeypatch.setattr(provider, "_wait_instance", lambda instance_id: {
        "id": instance_id, "status": "active", "server_status": "ok", "vpc_only": True,
        "internal_ip": "10.128.16.10", "main_ip": "0.0.0.0",
    })
    result = provider.create_instance(vm, public=False, vpc_ids=["vpc-id"], user_data=ubuntu_cloud_init())
    body = next(call[2]["json"] for call in calls if call[0] == "POST" and call[1] == "/instances")
    assert body["vpc_only"] is True
    assert body["attach_vpc"] == ["vpc-id"]
    assert "enable_vpc" not in body
    assert body["enable_ipv6"] is False
    assert result["main_ip"] == "0.0.0.0"
    provider.close()


def test_firewall_and_certification_phases_precede_endpoint_creation():
    assert PROVISIONING_STEPS.index("create_site_firewalls") < PROVISIONING_STEPS.index("establish_site_tunnels")
    assert PROVISIONING_STEPS.index("establish_site_tunnels") < PROVISIONING_STEPS.index("connecting_control_plane")
    assert PROVISIONING_STEPS.index("connecting_control_plane") < PROVISIONING_STEPS.index("certifying_private_boot")
    assert PROVISIONING_STEPS.index("certifying_private_boot") < PROVISIONING_STEPS.index("create_private_endpoints")


def test_vpc_only_validation_rejects_public_or_ambiguous_responses():
    with pytest.raises(GameNetProviderError, match="vpc_only=true"):
        validate_vpc_only_instance({"vpc_only": 1, "main_ip": "0.0.0.0"})
    with pytest.raises(GameNetProviderError, match="public address"):
        validate_vpc_only_instance({
            "vpc_only": True,
            "main_ip": "8.8.8.8",
            "internal_ip": "10.128.0.8",
        })


def test_vpc_only_validation_accepts_vpc_address_repeated_as_main_ip():
    validate_vpc_only_instance({
        "vpc_only": True,
        "main_ip": "10.128.0.8",
        "internal_ip": "10.128.0.8",
        "v6_main_ip": "",
        "ipv4": None,
    })


def test_private_boot_canary_request_has_no_user_data_and_persists_before_poll(monkeypatch, db_session):
    event = Event(name="GameNet", quota="{}", infrastructure=json.dumps(INFRASTRUCTURE))
    db_session.add(event); db_session.flush()
    team = Team(name="One", event_id=event.id); db_session.add(team); db_session.flush()
    allocate_event_networks(db_session, event, [team], INFRASTRUCTURE)
    site = db_session.query(Site).one(); site.vpc_id = "vpc-id"
    cert = PrivateBootCertification(
        site_id=site.id, base_type="ubuntu_24_server", os_id=2284, region="ewr",
        vpc_id="vpc-id", firewall_instance_id="firewall-id", plan="vc2-1c-2gb",
    )
    db_session.add(cert); db_session.commit()
    monkeypatch.setenv("VULTR_API_KEY", "test")
    provider = VultrGameNetProvider(); calls = []
    monkeypatch.setattr("api.services.gamenet_provider.get_or_create_platform_keypair", lambda _db: ("private", "public"))
    monkeypatch.setattr(provider, "_ensure_ssh_key", lambda *_args: "key-id")
    responses = iter([
        {"instances": []}, {"instance": {"id": "canary-id"}},
        {"vpcs": [{"id": "vpc-id", "ip_address": "10.128.0.9", "mac_address": "5a:00:00:00:00:09"}]},
    ])
    monkeypatch.setattr(provider, "_request", lambda method, path, **kwargs: calls.append((method, path, kwargs)) or next(responses))
    def wait(instance_id):
        assert db_session.get(PrivateBootCertification, cert.id).instance_id == "canary-id"
        return {"id": instance_id, "vpc_only": True, "main_ip": "0.0.0.0"}
    monkeypatch.setattr(provider, "_wait_instance", wait)
    result = provider.create_private_boot_canary(cert, hostname="canary", db=db_session)
    body = next(call[2]["json"] for call in calls if call[0] == "POST")
    assert body["vpc_only"] is True and body["attach_vpc"] == ["vpc-id"]
    assert "user_data" not in body
    assert result["vpc_mac"] == "5a:00:00:00:00:09"
    provider.close()


def test_private_boot_reachability_probe_uses_posix_shell_on_opnsense(monkeypatch):
    from api.services import gamenet_provisioning

    firewall = MagicMock(private_ip="10.128.0.1")
    gateway = MagicMock()
    site = MagicMock(firewall_vm_id=94, allocated_cidr="10.128.0.0/20")
    site.team.vpn_gateway.vm_id = 93
    cert = MagicMock(provider_ip="10.128.0.5", mac_address="5a:00:00:00:00:05")
    db = MagicMock()
    db.get.side_effect = lambda _model, vm_id: firewall if vm_id == 94 else gateway
    commands = []
    monkeypatch.setattr(
        gamenet_provisioning,
        "ssh_command",
        lambda _vm, command, **_kwargs: commands.append(command) or (1, "", "probe failed"),
    )

    with pytest.raises(GameNetProviderError, match="no ARP/TCP reachability"):
        gamenet_provisioning._validate_private_boot_canary(
            db, cert, site, "Ubuntu 24.04 LTS x64",
        )

    assert len(commands) == 1
    assert commands[0].startswith("sh -c ")
    assert 'while [ "$attempt" -lt 24 ]' in commands[0]
    assert 'timeout 2 ping -c 1 "$target"' in commands[0]
    assert "grep -Fv" in commands[0]
    assert "(incomplete)" in commands[0]
    assert "ping=%s tcp=%s arp=%s attempts=%s" in commands[0]


def test_endpoint_creation_uses_stock_image_and_persisted_mac_stages(monkeypatch, db_session):
    from api.services import gamenet_provisioning
    event = Event(name="GameNet", quota="{}", infrastructure=json.dumps(INFRASTRUCTURE))
    db_session.add(event); db_session.flush()
    team = Team(name="One", event_id=event.id); db_session.add(team); db_session.flush()
    allocate_event_networks(db_session, event, [team], INFRASTRUCTURE)
    ensure_vm_placeholders(db_session, event, INFRASTRUCTURE)
    site = db_session.query(Site).one(); site.vpc_id = "vpc-id"
    site.tunnel_status = site.control_plane_status = "active"
    gateway = db_session.get(VM, team.vpn_gateway.vm_id)
    gateway.public_ip, gateway.status = "198.51.100.10", "active"
    firewall = db_session.get(VM, site.firewall_vm_id)
    firewall.vultr_id, firewall.status = "firewall-id", "active"
    db_session.add(PrivateBootCertification(
        site_id=site.id, base_type="ubuntu_24_server", os_id=2284, region="ewr",
        vpc_id="vpc-id", firewall_instance_id="firewall-id", plan="vc2-1c-2gb",
        status="passed", phase="passed", cleanup_completed_at=utcnow(),
    ))
    db_session.commit()
    calls = []
    def create(vm, **kwargs):
        calls.append(kwargs); vm.vultr_id = "instance-" + str(vm.id)
        vm.vpc_ip, vm.vpc_mac = "10.128.0." + str(vm.id + 10), "5a:00:00:00:00:10"
    monkeypatch.setattr(gamenet_provisioning, "_create_provider_instance", create)
    monkeypatch.setattr(gamenet_provisioning, "add_deterministic_endpoint_address", lambda *_args: None)
    monkeypatch.setattr(gamenet_provisioning, "finalize_endpoint_network", lambda *_args: None)
    monkeypatch.setattr(gamenet_provisioning, "_assign_blue_modules", lambda *_args: None)
    create_private_endpoints(db_session, event, INFRASTRUCTURE)
    endpoints = db_session.query(VM).filter_by(role="blue_endpoint").all()
    assert calls and all(call["stock_image"] is True and "user_data" not in call for call in calls)
    assert all(vm.network_phase == "network_converted" for vm in endpoints)


def test_endpoint_stage_one_selects_nic_by_attachment_mac_and_jumps_gateway(monkeypatch, db_session):
    event = Event(name="GameNet", quota="{}"); db_session.add(event); db_session.flush()
    team = Team(name="One", event_id=event.id); db_session.add(team); db_session.flush()
    site = Site(event_id=event.id, team_id=team.id, key="site", name="Site", region="ewr",
                allocated_cidr="10.128.0.0/20", infrastructure_subnet="10.128.0.0/24")
    db_session.add(site); db_session.flush()
    endpoint = VM(hostname="endpoint", team_id=team.id, event_id=event.id, site_id=site.id,
                  role="blue_endpoint", vpc_ip="10.128.0.8", private_ip="10.128.1.10",
                  vpc_mac="5A:00:00:00:00:08")
    gateway = VM(hostname="gateway", team_id=team.id, event_id=event.id, public_ip="198.51.100.10")
    db_session.add_all([endpoint, gateway]); db_session.flush()
    calls = []
    responses = iter([(0, "", ""), (0, "boot-one\n", "")])
    monkeypatch.setattr("api.services.gamenet_provider.ssh_command",
                        lambda vm, command, **kwargs: calls.append((command, kwargs)) or next(responses))
    add_deterministic_endpoint_address(endpoint, site, gateway)
    assert "/sys/class/net/*/address" in calls[0][0]
    assert "5a:00:00:00:00:08" in calls[0][0]
    assert calls[0][1]["host"] == endpoint.vpc_ip and calls[0][1]["jump"] is gateway
    assert calls[1][1]["host"] == endpoint.private_ip
    assert endpoint.network_boot_id == "boot-one"


def test_failed_canary_is_cleaned_and_creates_no_workload_instances(monkeypatch, db_session):
    from api.services import gamenet_provisioning
    event = Event(name="GameNet", quota="{}", infrastructure=json.dumps(INFRASTRUCTURE))
    db_session.add(event); db_session.flush()
    team = Team(name="One", event_id=event.id); db_session.add(team); db_session.flush()
    allocate_event_networks(db_session, event, [team], INFRASTRUCTURE)
    ensure_vm_placeholders(db_session, event, INFRASTRUCTURE)
    site = db_session.query(Site).one(); site.vpc_id = "vpc-id"
    site.tunnel_status = site.control_plane_status = "active"
    firewall = db_session.get(VM, site.firewall_vm_id); firewall.vultr_id = "firewall-id"
    gateway = db_session.get(VM, team.vpn_gateway.vm_id); gateway.vultr_id = "gateway-id"
    deleted = []
    requested_hostnames = []
    class Provider:
        def close(self): pass
        def _resolve_os_id(self, _name): return 2284
        def create_private_boot_canary(self, cert, **kwargs):
            requested_hostnames.append(kwargs["hostname"])
            cert.instance_id = "canary-id"; db_session.commit()
            return {"id": "canary-id", "vpc_only": True, "main_ip": "0.0.0.0",
                    "internal_ip": "10.128.0.9", "vpc_mac": "5a:00:00:00:00:09"}
        def delete_instance(self, instance_id): deleted.append(instance_id)
    monkeypatch.setattr(gamenet_provisioning, "_provider", lambda: Provider())
    monkeypatch.setattr(gamenet_provisioning, "_validate_private_boot_canary",
                        lambda *_args: (_ for _ in ()).throw(GameNetProviderError("no ARP/TCP reachability")))
    with pytest.raises(GameNetProviderError, match="private boot certification failed"):
        certify_private_boot(db_session, event, INFRASTRUCTURE)
    cert = db_session.query(PrivateBootCertification).one()
    assert cert.status == "failed" and cert.cleanup_completed_at is not None
    assert cert.instance_id == "canary-id" and deleted == ["canary-id"]
    assert requested_hostnames == [f"gamenet-e{event.id}-t{team.id}-head-office-ubuntu-24-server-canary"]
    endpoints = db_session.query(VM).filter(VM.role.like("%_endpoint")).all()
    assert all(vm.vultr_id is None for vm in endpoints)


def test_snapshot_instance_boots_without_vpc_then_attaches_explicitly(monkeypatch, db_session):
    event = Event(name="GameNet", quota="{}"); db_session.add(event); db_session.flush()
    team = Team(name="One", event_id=event.id); db_session.add(team); db_session.flush()
    vm = VM(hostname="firewall", team_id=team.id, event_id=event.id, base_type="opnsense",
            vultr_plan="vc2-2c-4gb", vultr_region="ewr")
    db_session.add(vm); db_session.flush()
    monkeypatch.setenv("VULTR_API_KEY", "test")
    provider = VultrGameNetProvider(); calls = []
    responses = iter([
        {"instances": []}, {"instance": {"id": "instance-id"}},
        {"vpcs": []}, {},
        {"vpcs": [{"id": "vpc-id", "ip_address": "10.128.0.2", "mac_address": "5a:00:00:00:00:02"}]},
    ])
    monkeypatch.setattr(provider, "_request",
                        lambda method, path, **kwargs: calls.append((method, path, kwargs)) or next(responses))
    monkeypatch.setattr(provider, "_wait_instance", lambda instance_id: {
        "id": instance_id, "status": "active", "server_status": "ok", "main_ip": "198.51.100.10",
    })
    provider.create_instance(vm, public=True, image_source={"snapshot_id": "snapshot-id"})
    create_body = next(call[2]["json"] for call in calls if call[0] == "POST" and call[1] == "/instances")
    assert create_body["snapshot_id"] == "snapshot-id"
    assert "attach_vpc" not in create_body
    attachment = provider.attach_vpc(vm, "vpc-id")
    attach_call = next(call for call in calls if call[1].endswith("/vpcs/attach"))
    assert attach_call[2]["json"] == {"vpc_id": "vpc-id"}
    assert attachment["mac_address"] == "5a:00:00:00:00:02"
    provider.close()


def test_site_firewall_persists_wan_gate_before_vpc_attachment(monkeypatch, db_session):
    from api.services import gamenet_provisioning

    event = Event(name="GameNet", quota="{}", infrastructure=json.dumps(INFRASTRUCTURE))
    db_session.add(event); db_session.flush()
    team = Team(name="One", event_id=event.id); db_session.add(team); db_session.flush()
    allocate_event_networks(db_session, event, [team], INFRASTRUCTURE)
    ensure_vm_placeholders(db_session, event, INFRASTRUCTURE)
    image = OpnsenseImage(
        version="26.7", status="active", phase="active", snapshot_id="snapshot-id",
        validated_at=utcnow(), build_method="freebsd-bootstrap", base_os="FreeBSD 15 x64",
    )
    db_session.add(image); db_session.flush()
    db_session.add(PlatformSettings(key="active_opnsense_image_id", value=str(image.id)))
    db_session.commit()
    calls = []

    monkeypatch.setattr(gamenet_provisioning, "_require_provider", lambda: None)
    monkeypatch.setattr(gamenet_provisioning, "_create_provider_vpc", lambda _site: "vpc-id")
    def create_instance(vm, **_kwargs):
        vm.vultr_id, vm.public_ip = "instance-id", "198.51.100.10"
    monkeypatch.setattr(gamenet_provisioning, "_create_provider_instance", create_instance)
    monkeypatch.setattr(gamenet_provisioning, "validate_snapshot_wan",
                        lambda vm, _release: calls.append(("wan", vm.vpc_ip)))
    def attach(vm, _vpc_id):
        assert vm.provision_step == "snapshot_wan_validated"
        calls.append(("attach", vm.provision_step))
        return {"ip_address": "10.128.0.2", "mac_address": "5a:00:00:00:00:02"}
    monkeypatch.setattr(gamenet_provisioning, "_attach_provider_vpc", attach)
    monkeypatch.setattr(gamenet_provisioning, "configure_snapshot_opnsense", lambda *_args, **_kwargs: None)

    gamenet_provisioning.create_site_firewalls(db_session, event, INFRASTRUCTURE)
    firewall = db_session.query(VM).filter_by(role="site_firewall").one()
    assert calls[0] == ("wan", None)
    assert firewall.provision_step == "snapshot_wan_validated"
    assert firewall.vpc_ip == "10.128.0.2" and firewall.status == "active"


def test_vultr_ssh_key_collision_uses_material_specific_name(monkeypatch):
    monkeypatch.setenv("VULTR_API_KEY", "test")
    provider = VultrGameNetProvider()
    calls = []
    monkeypatch.setattr(provider, "_request", lambda method, path, **kwargs: (
        {"ssh_keys": [{"id": "old", "name": "ctf-platform", "ssh_key": "ssh-ed25519 OLD"}]}
        if method == "GET" else calls.append(kwargs["json"]) or {"ssh_key": {"id": "new"}}
    ))
    assert provider._ensure_ssh_key("ctf-platform", "ssh-ed25519 NEW comment") == "new"
    assert calls[0]["name"].startswith("ctf-platform-")
    assert calls[0]["ssh_key"] == "ssh-ed25519 NEW comment"
    provider.close()


def test_vultr_ssh_key_reuses_matching_material_under_any_name(monkeypatch):
    monkeypatch.setenv("VULTR_API_KEY", "test")
    provider = VultrGameNetProvider()
    monkeypatch.setattr(provider, "_request", lambda method, path, **kwargs: {
        "ssh_keys": [{"id": "existing", "name": "older-deployment", "ssh_key": "ssh-ed25519 SAME old-comment"}]
    })
    assert provider._ensure_ssh_key("ctf-platform", "ssh-ed25519 SAME new-comment") == "existing"
    provider.close()


def test_gamenet_materialises_complete_vm_plan_before_cloud_calls(db_session):
    event = Event(name="GameNet", quota="{}", infrastructure=json.dumps(INFRASTRUCTURE))
    db_session.add(event); db_session.flush()
    team = Team(name="One", event_id=event.id); db_session.add(team); db_session.flush()
    allocate_event_networks(db_session, event, [team], INFRASTRUCTURE)
    placeholders = ensure_vm_placeholders(db_session, event, INFRASTRUCTURE)
    db_session.flush()
    assert len(placeholders) == infrastructure_summary(INFRASTRUCTURE)["vms"] == 4
    assert {vm.role for vm in placeholders} == {"vpn_gateway", "site_firewall", "blue_endpoint"}
    assert team.vpn_gateway.vm_id is not None
    assert db_session.query(Site).one().firewall_vm_id is not None


def test_gamenet_materialises_individual_endpoint_records(db_session):
    from builder.infrastructure_planner import normalize_infrastructure

    infrastructure = normalize_infrastructure(INFRASTRUCTURE)
    event = Event(name="GameNet", quota="{}", infrastructure=json.dumps(infrastructure))
    db_session.add(event); db_session.flush()
    team = Team(name="One", event_id=event.id); db_session.add(team); db_session.flush()
    allocate_event_networks(db_session, event, [team], infrastructure)

    placeholders = ensure_vm_placeholders(db_session, event, infrastructure)
    endpoints = [vm for vm in placeholders if vm.role == "blue_endpoint"]

    assert [vm.vm_type for vm in endpoints] == ["workstation_1", "workstation_2"]
    assert [vm.private_ip for vm in endpoints] == ["10.128.1.10", "10.128.1.11"]
    assert len({vm.hostname for vm in endpoints}) == 2


def test_gamenet_ignores_display_only_address_annotations_when_allocating_vm_ips(db_session):
    from builder.infrastructure_planner import normalize_infrastructure

    infrastructure = deepcopy(INFRASTRUCTURE)
    site_spec = infrastructure["sites"][0]
    site_spec["firewall_zone_address_range"] = "display-firewall-range/{{team_id}}"
    site_spec["firewall"]["address"] = "not-a-firewall-ip"
    zone = site_spec["zones"][0]
    zone["address_range"] = "display-only/{{team_id}}"
    zone["endpoints"][0]["address"] = "not-an-ip"
    infrastructure = normalize_infrastructure(infrastructure)
    event = Event(name="GameNet", quota="{}", infrastructure=json.dumps(infrastructure))
    db_session.add(event); db_session.flush()
    team = Team(name="One", event_id=event.id); db_session.add(team); db_session.flush()
    allocate_event_networks(db_session, event, [team], infrastructure)

    placeholders = ensure_vm_placeholders(db_session, event, infrastructure)
    endpoints = [vm for vm in placeholders if vm.role == "blue_endpoint"]
    firewall = next(vm for vm in placeholders if vm.role == "site_firewall")
    site = db_session.query(Site).one()

    assert site.allocated_cidr == "10.128.0.0/20"
    assert site.zones[0].subnet == "10.128.1.0/24"
    assert firewall.private_ip is None
    assert firewall.ip_address is None
    assert [vm.private_ip for vm in endpoints] == ["10.128.1.10", "10.128.1.11"]
    assert all(vm.private_ip != "not-an-ip" for vm in endpoints)


def test_gamenet_materialises_mixed_legacy_and_individual_endpoint_keys(db_session):
    infrastructure = deepcopy(INFRASTRUCTURE)
    infrastructure["sites"][0]["zones"][0]["endpoints"] = [
        {
            "key": "workstation", "base_type": "ubuntu_24_server", "count": 1,
            "default_plan": "vc2-1c-1gb",
        },
        {
            "key": "workstation_1", "name": "Existing workstation",
            "base_type": "ubuntu_24_server", "default_plan": "vc2-1c-1gb",
        },
    ]
    event = Event(name="GameNet", quota="{}", infrastructure=json.dumps(infrastructure))
    db_session.add(event); db_session.flush()
    team = Team(name="One", event_id=event.id); db_session.add(team); db_session.flush()
    allocate_event_networks(db_session, event, [team], infrastructure)

    placeholders = ensure_vm_placeholders(db_session, event, infrastructure)
    endpoints = [vm for vm in placeholders if vm.role == "blue_endpoint"]

    assert {vm.vm_type for vm in endpoints} == {"workstation_1", "workstation_1_2"}
    assert len(endpoints) == len({vm.hostname for vm in endpoints}) == 2


def test_semaphore_endpoint_proxy_is_team_gateway_not_firewall(db_session):
    event = Event(name="GameNet", quota="{}", infrastructure=json.dumps(INFRASTRUCTURE))
    db_session.add(event); db_session.flush()
    team = Team(name="One", event_id=event.id); db_session.add(team); db_session.flush()
    allocate_event_networks(db_session, event, [team], INFRASTRUCTURE)
    ensure_vm_placeholders(db_session, event, INFRASTRUCTURE)
    gateway = db_session.get(VM, team.vpn_gateway.vm_id); gateway.public_ip = "198.51.100.10"
    site = db_session.query(Site).one()
    firewall = db_session.get(VM, site.firewall_vm_id); firewall.public_ip = "198.51.100.20"
    endpoint = db_session.query(VM).filter_by(role="blue_endpoint").first()
    assert _gamenet_gateway_proxy(db_session, endpoint) == gateway.public_ip
    assert _gamenet_gateway_proxy(db_session, endpoint) != firewall.public_ip


def test_gamenet_failure_marks_all_unfinished_placeholders_and_overview(monkeypatch, db_session):
    from api.services import gamenet_provisioning

    event = Event(name="Broken GameNet", quota="{}", infrastructure=json.dumps(INFRASTRUCTURE),
                  status="provisioning")
    db_session.add(event); db_session.flush()
    team = Team(name="One", event_id=event.id); db_session.add(team); db_session.flush()
    allocate_event_networks(db_session, event, [team], INFRASTRUCTURE)
    ensure_vm_placeholders(db_session, event, INFRASTRUCTURE)
    db_session.commit()
    event_id = event.id
    monkeypatch.setattr(gamenet_provisioning, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(gamenet_provisioning, "allocate_keys_and_addresses",
                        lambda *_args: (_ for _ in ()).throw(RuntimeError("provider unavailable")))
    gamenet_provisioning.provision_event_gamenets(event_id)

    db_session.expire_all()
    assert db_session.get(Event, event_id).status == "provision_failed"
    vms = db_session.query(VM).filter_by(event_id=event_id).all()
    assert len(vms) == 4
    assert all(vm.status == "failed" for vm in vms)
    assert all("provider unavailable" in vm.provision_error for vm in vms)

    monkeypatch.setattr("api.routes.admin.require_admin", lambda *_args, **_kwargs: User(is_admin=True))
    payload = asyncio.run(overview(MagicMock(), db_session))
    assert any(item["message"] == "Broken GameNet provisioning failed" for item in payload["attention"])


def test_gamenet_plan_preview_returns_counts_cost_shape_and_addresses(monkeypatch, db_session):
    event = Event(name="GameNet", quota="{}", infrastructure=json.dumps(INFRASTRUCTURE))
    db_session.add(event); db_session.flush()
    db_session.add(Team(name="One", event_id=event.id)); db_session.commit()
    monkeypatch.delenv("VULTR_API_KEY", raising=False)
    monkeypatch.setattr("api.routes.admin.require_admin", lambda *_args, **_kwargs: User(is_admin=True))
    response = asyncio.run(plan_preview(event.id, PlanPreviewRequest(), MagicMock(), db_session))
    assert response["summary"]["total_vms"] == 4
    assert response["summary"]["vms"] == 4
    assert response["summary"]["gateways"] == 1
    assert response["summary"]["firewalls"] == 1
    assert response["summary"]["endpoints"] == 2
    assert response["summary"]["estimated_monthly_cost"] == 0
    assert response["total_cost"] == 0
    assert len(response["vm_types"]) == 4
    assert len(response["address_plan"]) == 1
    assert response["address_plan"][0]["zones"][0]["subnet"].endswith("/24")
