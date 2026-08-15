import json
import asyncio
from ipaddress import ip_network
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.database import Base
from api.models import (
    Event, OpnsenseImage, PlatformSettings, PrivateBootCertification,
    Site, Team, User, VM, VPNCredential, utcnow,
)
from api.routes.admin import PlanPreviewRequest, overview, plan_preview
from api.routes.vm import _gamenet_gateway_proxy
from api.services.gamenet import (
    allocate_event_networks, ensure_user_vpn_credential, render_user_config,
    site_dns_zone, vm_dns_name,
)
from api.services.gamenet_provider import (
    GameNetProviderError, add_deterministic_endpoint_address,
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
    vm = MagicMock(id=9, cloud_instance_id="i-instance-1", public_ip="198.51.100.12",
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


def test_schema_repair_migration_covers_existing_database_feature_columns():
    migration = Path("migrations/versions/0009_existing_feature_columns.py").read_text()
    assert '"ust_prompt"' in migration
    assert '"expo_sync_status"' in migration
    assert '"expo_sync_last_error"' in migration
    assert '"expo_sync_attempts"' in migration
    assert '"expo_sync_completed_at"' in migration


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


def test_endpoint_creation_uses_approved_ami_and_persists_eni_metadata(monkeypatch, db_session):
    from api.services import gamenet_provisioning
    from api.services.aws import AwsConfig, InstanceResult
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
    firewall.cloud_instance_id, firewall.status = "i-firewall", "active"
    db_session.commit()
    calls = []
    class Provider:
        def create_endpoint(self, _site, _zone, vm, *, ami_id):
            calls.append(ami_id)
            return InstanceResult("i-" + str(vm.id), "running", "eni-" + str(vm.id),
                                  None, vm.private_ip, "ap-southeast-2a",
                                  primary_mac="02:00:00:00:00:10")
        def close(self): pass
    monkeypatch.setattr(gamenet_provisioning, "_provider", lambda: Provider())
    monkeypatch.setattr(gamenet_provisioning, "_require_endpoint_prerequisites", lambda *_args: None)
    monkeypatch.setattr(AwsConfig, "from_env", classmethod(lambda cls: type(
        "Config", (), {"ubuntu_ami": lambda self, region: "ami-ubuntu"},
    )()))
    monkeypatch.setattr(gamenet_provisioning, "_assign_blue_modules", lambda *_args: None)
    create_private_endpoints(db_session, event, INFRASTRUCTURE)
    endpoints = db_session.query(VM).filter_by(role="blue_endpoint").all()
    assert calls and set(calls) == {"ami-ubuntu"}
    assert all(vm.cloud_instance_id and vm.primary_eni_id and vm.vpc_mac for vm in endpoints)
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


def test_private_boot_gate_rejects_missing_validated_aws_ami(db_session):
    event = Event(name="GameNet", quota="{}", infrastructure=json.dumps(INFRASTRUCTURE))
    db_session.add(event); db_session.flush()
    team = Team(name="One", event_id=event.id); db_session.add(team); db_session.flush()
    allocate_event_networks(db_session, event, [team], INFRASTRUCTURE)
    ensure_vm_placeholders(db_session, event, INFRASTRUCTURE)
    site = db_session.query(Site).one(); site.vpc_id = "vpc-id"
    site.tunnel_status = site.control_plane_status = "active"
    firewall = db_session.get(VM, site.firewall_vm_id); firewall.cloud_instance_id = "i-firewall"
    with pytest.raises(GameNetProviderError, match="no active privately validated OPNsense AMI"):
        certify_private_boot(db_session, event, INFRASTRUCTURE)
    endpoints = db_session.query(VM).filter(VM.role.like("%_endpoint")).all()
    assert all(vm.cloud_instance_id is None for vm in endpoints)


def test_site_firewall_persists_aws_dual_eni_result(monkeypatch, db_session):
    from api.services import gamenet_provisioning
    from api.services.aws import InstanceResult

    event = Event(name="GameNet", quota="{}", infrastructure=json.dumps(INFRASTRUCTURE))
    db_session.add(event); db_session.flush()
    team = Team(name="One", event_id=event.id); db_session.add(team); db_session.flush()
    allocate_event_networks(db_session, event, [team], INFRASTRUCTURE)
    ensure_vm_placeholders(db_session, event, INFRASTRUCTURE)
    image = OpnsenseImage(
        version="26.7", status="active", phase="active", ami_id="ami-opnsense",
        validated_at=utcnow(), build_method="freebsd-bootstrap", base_os="FreeBSD 15 x64",
    )
    db_session.add(image); db_session.flush()
    db_session.add(PlatformSettings(key="active_opnsense_image_id", value=str(image.id)))
    db_session.commit()
    monkeypatch.setattr(gamenet_provisioning, "_require_provider", lambda: None)
    monkeypatch.setattr(gamenet_provisioning, "_create_provider_vpc", lambda _site: "vpc-id")
    class Provider:
        def create_firewall(self, site, vm, *, ami_id):
            assert ami_id == "ami-opnsense"
            return InstanceResult(
                "i-firewall", "running", "eni-wan", "198.51.100.10", vm.private_ip,
                "ap-southeast-2a", wan_eni_id="eni-wan", lan_eni_id="eni-lan",
                lan_mac="02:00:00:00:00:02", eip_allocation_id="eipalloc-fw",
            )
        def configure_private_routes(self, site, eni): assert eni == "eni-lan"
        def close(self): pass
    monkeypatch.setattr(gamenet_provisioning, "_provider", lambda: Provider())
    monkeypatch.setattr(gamenet_provisioning, "configure_snapshot_opnsense", lambda *_args, **_kwargs: None)

    gamenet_provisioning.create_site_firewalls(db_session, event, INFRASTRUCTURE)
    firewall = db_session.query(VM).filter_by(role="site_firewall").one()
    assert firewall.cloud_instance_id == "i-firewall"
    assert firewall.wan_eni_id == "eni-wan" and firewall.lan_eni_id == "eni-lan"
    assert firewall.vpc_mac == "02:00:00:00:00:02" and firewall.status == "active"


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
    assert len(response["vm_types"]) == 3
    assert len(response["address_plan"]) == 1
    assert response["address_plan"][0]["zones"][0]["subnet"].endswith("/24")
def test_aws_gamenet_firewall_uses_dual_enis_and_disables_source_check():
    from types import SimpleNamespace
    from api.services.aws import ElasticIpResult, InstanceResult, NetworkInterfaceResult
    from api.services.gamenet_provider import AwsGameNetProvider

    class Network:
        def __init__(self): self.enis = []
        def create_eni(self, subnet, ip, groups, tags):
            result = NetworkInterfaceResult(f"eni-{len(self.enis)}", subnet, ip, "02:00:00:00:00:01")
            self.enis.append((subnet, ip, groups, result)); return result
    class Compute:
        def __init__(self): self.spec = None; self.source_checks = []
        def launch_instance(self, spec):
            self.spec = spec
            return InstanceResult("i-fw", "pending", availability_zone="ap-southeast-2a")
        def set_source_dest_check(self, instance_id, enabled): self.source_checks.append((instance_id, enabled))
        def allocate_eip(self, tags): return ElasticIpResult("eipalloc-fw", "198.51.100.8")
        def associate_eip(self, allocation_id, eni_id): return "eipassoc-fw"

    network, compute = Network(), Compute()
    provider = AwsGameNetProvider(compute, network, SimpleNamespace(environment="test"))
    site = SimpleNamespace(
        id=4, event_id=1, team_id=2, availability_zone="ap-southeast-2a",
        public_subnet_id="subnet-wan", infrastructure_subnet_id="subnet-lan",
        wan_security_group_id="sg-wan", lan_security_group_id="sg-lan",
    )
    vm = SimpleNamespace(id=9, private_ip="10.40.1.1", instance_type="t3.medium")

    result = provider.create_firewall(site, vm, ami_id="ami-opnsense")

    assert [eni[0] for eni in network.enis] == ["subnet-wan", "subnet-lan"]
    assert compute.source_checks == [("i-fw", False)]
    assert result.public_ip == "198.51.100.8"
    assert result.wan_eni_id == "eni-0" and result.lan_eni_id == "eni-1"


def test_aws_gamenet_gateway_uses_standard_subnet_owned_sg_and_eip():
    from types import SimpleNamespace
    from api.services.aws import ElasticIpResult, InstanceResult
    from api.services.gamenet_provider import AwsGameNetProvider
    class Network:
        def ensure_security_group(self, spec): self.spec = spec; return "sg-gateway"
    class Compute:
        def ensure_key_pair(self, name, public_key, tags): self.key = (name, public_key); return "key-1"
        def launch_instance(self, spec): self.spec = spec; return InstanceResult("i-gw", "pending", "eni-gw")
        def allocate_eip(self, tags): return ElasticIpResult("eipalloc-gw", "198.51.100.9")
        def associate_eip(self, allocation_id, eni_id): return "eipassoc-gw"
    compute, network = Compute(), Network()
    config = SimpleNamespace(
        environment="test", standard_vpc_id="vpc-standard", standard_subnet_id="subnet-standard",
        ubuntu_ami=lambda region: "ami-ubuntu",
    )
    provider = AwsGameNetProvider(compute, network, config)
    event, team = SimpleNamespace(id=1), SimpleNamespace(id=2)
    vm = SimpleNamespace(id=3, cloud_region="ap-southeast-2", instance_type="t3.small")
    result = provider.create_gateway(
        event, team, vm, key_name="ctf-it", public_key="ssh-ed25519 AAAA",
        ingress=({"IpProtocol": "udp", "FromPort": 51820, "ToPort": 51820,
                  "IpRanges": [{"CidrIp": "0.0.0.0/0"}]},),
        user_data="#cloud-config",
    )
    assert compute.spec.network_interfaces[0].subnet_id == "subnet-standard"
    assert compute.spec.network_interfaces[0].security_group_ids == ("sg-gateway",)
    assert result.public_ip == "198.51.100.9"
    assert result.eip_allocation_id == "eipalloc-gw"


def test_aws_gamenet_private_endpoint_has_no_public_ip():
    from types import SimpleNamespace
    from api.services.aws import InstanceResult
    from api.services.gamenet_provider import AwsGameNetProvider

    class Compute:
        def __init__(self): self.spec = None
        def launch_instance(self, spec):
            self.spec = spec
            return InstanceResult("i-endpoint", "pending", "eni-endpoint", None, "10.40.10.8")
    compute = Compute()
    provider = AwsGameNetProvider(compute, SimpleNamespace(), SimpleNamespace(environment="test"))
    site = SimpleNamespace(id=4, event_id=1, team_id=2)
    zone = SimpleNamespace(subnet_id="subnet-blue", security_group_id="sg-private")
    vm = SimpleNamespace(id=10, private_ip="10.40.10.8", instance_type="t3.small")

    result = provider.create_endpoint(site, zone, vm, ami_id="ami-ubuntu")

    assert result.public_ip is None
    assert compute.spec.network_interfaces[0].associate_public_ip is False
    assert compute.spec.network_interfaces[0].subnet_id == "subnet-blue"


def test_aws_instance_result_is_persisted_to_neutral_vm_fields(db_session):
    from api.services.aws import InstanceResult
    from api.services.gamenet_provisioning import _persist_instance_result
    event = Event(name="AWS result", quota="{}"); db_session.add(event); db_session.flush()
    team = Team(name="One", event_id=event.id); db_session.add(team); db_session.flush()
    vm = VM(hostname="aws-vm", status="creating", base_type="ubuntu_24_server",
            team_id=team.id, event_id=event.id)
    db_session.add(vm); db_session.commit()
    result = InstanceResult(
        "i-123", "running", "eni-primary", "198.51.100.8", "10.40.1.10",
        "ap-southeast-2a", wan_eni_id="eni-wan", lan_eni_id="eni-lan",
        primary_mac="02:00:00:00:00:01", eip_allocation_id="eipalloc-123",
    )
    _persist_instance_result(vm, result)
    assert vm.cloud_instance_id == "i-123"
    assert vm.primary_eni_id == "eni-primary"
    assert vm.wan_eni_id == "eni-wan" and vm.lan_eni_id == "eni-lan"
    assert vm.eip_allocation_id == "eipalloc-123"
    assert vm.public_ip == "198.51.100.8" and vm.private_ip == "10.40.1.10"
    assert vm.vpc_mac == "02:00:00:00:00:01"


def test_aws_private_interface_netplan_matches_recorded_eni_mac():
    from pathlib import Path
    playbook = Path("playbooks/configure-vpc-interface.yml").read_text()
    template = Path("templates/vpc-netplan.yaml.j2").read_text()
    assert "vpc_mac" in playbook and "vpc_mac is match" in playbook
    assert "macaddress: {{ vpc_mac }}" in template
    assert "set-name: ctf-lan" in template
    assert "ens7" not in playbook + template
    assert "1450" not in playbook + template


def test_gamenet_orchestration_requires_aws_configuration_not_vultr(monkeypatch):
    from api.services import gamenet_provisioning
    monkeypatch.delenv("VULTR_API_KEY", raising=False)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-southeast-2")
    monkeypatch.setenv("AWS_ENVIRONMENT", "test")
    monkeypatch.setenv("AWS_STANDARD_VPC_ID", "vpc-standard")
    monkeypatch.setenv("AWS_STANDARD_SUBNET_ID", "subnet-standard")
    monkeypatch.setenv("AWS_UBUNTU_AMIS", '{"ap-southeast-2":"ami-ubuntu"}')
    monkeypatch.setenv("AWS_FREEBSD_AMIS", '{"ap-southeast-2":"ami-freebsd"}')
    monkeypatch.setenv("AWS_AVAILABILITY_ZONES", '{"ap-southeast-2":"ap-southeast-2a"}')
    monkeypatch.setenv("AWS_INSTANCE_TYPES", "t3.small,t3.medium")
    gamenet_provisioning._require_provider()


def test_gamenet_placeholders_store_ec2_type_and_region_not_vultr_fields():
    from pathlib import Path
    source = Path("api/services/gamenet_provisioning.py").read_text()
    placeholder_section = source[source.index("def ensure_vm_placeholders"):source.index("def allocate_keys_and_addresses")]
    assert "instance_type=" in placeholder_section
    assert "cloud_region=" in placeholder_section
    assert "vultr_plan=" not in placeholder_section
    assert "vultr_region=" not in placeholder_section


def test_aws_gamenet_site_network_uses_secondary_wan_cidr():
    from types import SimpleNamespace
    from api.services.aws import SiteNetworkResult
    from api.services.gamenet_provider import AwsGameNetProvider

    class Network:
        def ensure_site_network(self, spec):
            self.spec = spec
            return SiteNetworkResult("vpc-1", spec.availability_zone, {
                "wan": "subnet-wan", "infra": "subnet-infra", "blue": "subnet-blue",
            }, {"wan": "rtb-wan", "infra": "rtb-infra", "blue": "rtb-blue"}, "igw-1")
    network = Network()
    provider = AwsGameNetProvider(SimpleNamespace(), network, SimpleNamespace(environment="test"))
    site = SimpleNamespace(
        id=3, event_id=1, team_id=2, region="ap-southeast-2",
        availability_zone="ap-southeast-2a", allocated_cidr="10.128.0.0/20",
        infrastructure_subnet="10.128.0.0/24",
        zones=[SimpleNamespace(key="blue", subnet="10.128.1.0/24")],
    )
    result = provider.create_vpc(site)
    assert network.spec.vpc_cidr == "10.128.0.0/20"
    assert network.spec.secondary_cidrs == ("172.31.255.0/28",)
    assert network.spec.subnets == {
        "wan": "172.31.255.0/28", "infra": "10.128.0.0/24", "blue": "10.128.1.0/24",
    }
    assert result.vpc_id == "vpc-1"


def test_create_provider_vpc_persists_all_aws_network_ids(monkeypatch, db_session):
    from api.services.aws import SiteNetworkResult
    from api.services import gamenet_provisioning
    event = Event(name="GameNet", quota="{}"); db_session.add(event); db_session.flush()
    team = Team(name="One", event_id=event.id); db_session.add(team); db_session.flush()
    site = Site(
        event_id=event.id, team_id=team.id, key="hq", name="HQ", region="ap-southeast-2",
        allocated_cidr="10.128.0.0/20", infrastructure_subnet="10.128.0.0/24",
        order=0,
    )
    db_session.add(site); db_session.flush()
    zone = __import__("api.models", fromlist=["Zone"]).Zone(
        site_id=site.id, key="blue", name="Blue", team_role="blue",
        subnet="10.128.1.0/24", gateway_address="10.128.1.1", order=0,
    )
    db_session.add(zone); db_session.commit()
    class Provider:
        def create_vpc(self, selected):
            assert selected.availability_zone == "ap-southeast-2a"
            return SiteNetworkResult(
                "vpc-1", "ap-southeast-2a",
                {"wan": "subnet-wan", "infra": "subnet-infra", "blue": "subnet-blue"},
                {"wan": "rtb-wan", "infra": "rtb-infra", "blue": "rtb-blue"}, "igw-1",
            )
        def ensure_site_security_groups(self, selected):
            return {"wan": "sg-wan", "lan": "sg-lan", "blue": "sg-blue"}
        def close(self): pass
    monkeypatch.setattr(gamenet_provisioning, "_provider", lambda: Provider())
    monkeypatch.setattr(
        gamenet_provisioning, "_configured_availability_zone",
        lambda region: "ap-southeast-2a",
        raising=False,
    )
    assert gamenet_provisioning._create_provider_vpc(site) == "vpc-1"
    assert site.public_subnet_id == "subnet-wan"
    assert site.infrastructure_subnet_id == "subnet-infra"
    assert zone.subnet_id == "subnet-blue"
    assert site.wan_security_group_id == "sg-wan"
    assert site.lan_security_group_id == "sg-lan"
    assert zone.security_group_id == "sg-blue"
    assert json.loads(site.route_table_ids_json)["infra"] == "rtb-infra"
