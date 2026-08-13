import json
import asyncio
from ipaddress import ip_network
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.database import Base
from api.models import Event, Site, Team, User, VM, VPNCredential
from api.routes.admin import PlanPreviewRequest, overview, plan_preview
from api.services.gamenet import allocate_event_networks, ensure_user_vpn_credential
from api.services.gamenet_provider import VultrGameNetProvider, render_opnsense_config, ubuntu_cloud_init
from api.services.gamenet_provisioning import ensure_vm_placeholders
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


def test_opnsense_config_encodes_authorized_key(db_session):
    event = Event(name="GameNet", quota="{}", infrastructure=json.dumps(INFRASTRUCTURE))
    db_session.add(event); db_session.flush()
    team = Team(name="One", event_id=event.id); db_session.add(team); db_session.flush()
    allocate_event_networks(db_session, event, [team], INFRASTRUCTURE)
    site = db_session.query(Site).one()
    vm = VM(hostname="firewall", team_id=team.id, event_id=event.id, site_id=site.id)
    db_session.add(vm); db_session.flush()
    rendered = render_opnsense_config(site, vm, "ssh-ed25519 TEST", "password", temporary_management=True)
    assert "<authorizedkeys>c3NoLWVkMjU1MTkgVEVTVA==</authorizedkeys>" in rendered


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
    from api.services.gamenet_provider import endpoint_cloud_init
    rendered = endpoint_cloud_init("10.128.1.10", "10.128.0.0/20")
    assert "bootcmd:" in rendered
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
        {"vpcs": [{"id": "vpc-id", "ip_address": "10.128.16.10"}]},
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
    assert body["enable_ipv6"] is False
    assert result["main_ip"] == "0.0.0.0"
    provider.close()


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
