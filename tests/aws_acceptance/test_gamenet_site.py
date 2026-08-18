import json
import subprocess

from api.models import Event, Team, VM
from api.services.gamenet import allocate_event_networks
from api.services.gamenet_provider import ssh_command
from api.services.gamenet_provisioning import (
    cleanup_event_gamenets, ensure_vm_placeholders, provision_event_gamenets,
)


def _infrastructure(region):
    return {
        "vpn_gateway": {
            "base_type": "ubuntu_24_server", "default_plan": "t3.small",
            "region": region, "listen_port": 51820,
        },
        "sites": [{
            "key": "acceptance", "name": "Acceptance Site", "region": region,
            "firewall": {"base_type": "opnsense", "default_plan": "t3.medium"},
            "zones": [{
                "key": "workloads", "name": "Private Workloads", "team": "red",
                "endpoints": [{
                    "key": "host", "base_type": "ubuntu_24_server",
                    "count": 2, "default_plan": "t3.small",
                }],
            }],
        }],
    }


def test_gamenet_site_is_private_and_routes_through_opnsense(
        aws_context, aws_opnsense_image, monkeypatch):
    from api.services import gamenet_provisioning

    db = aws_opnsense_image.db
    infrastructure = _infrastructure(aws_context.region)
    event = Event(
        name=f"AWS acceptance {aws_context.run_id}", quota="{}",
        infrastructure=json.dumps(infrastructure), status="provisioning", open=False,
    )
    db.add(event)
    db.flush()
    team = Team(name="Acceptance", event_id=event.id)
    db.add(team)
    db.flush()
    team_id = team.id
    allocate_event_networks(db, event, [team], infrastructure)
    ensure_vm_placeholders(db, event, infrastructure)
    db.commit()
    event_id = event.id
    monkeypatch.setattr(gamenet_provisioning, "SessionLocal", lambda: db)

    try:
        provision_event_gamenets(event_id)
        db.expire_all()
        event = db.get(Event, event_id)
        assert event.status == "open" and event.open is True
        gateway = db.query(VM).filter_by(event_id=event_id, role="vpn_gateway").one()
        firewall = db.query(VM).filter_by(event_id=event_id, role="site_firewall").one()
        endpoints = db.query(VM).filter(VM.event_id == event_id, VM.role.like("%_endpoint")).all()
        assert len(endpoints) == 2
        assert all(vm.public_ip is None and vm.private_ip for vm in endpoints)
        code, output, error = ssh_command(
            endpoints[0],
            "ping -c 1 -W 5 " + endpoints[1].private_ip
            + " >/dev/null && curl -fsS --max-time 20 https://checkip.amazonaws.com",
            jump=gateway, timeout=60,
        )
        assert code == 0, error
        assert output.strip() == firewall.public_ip
    finally:
        result = cleanup_event_gamenets(
            event_id, session_factory=lambda: db,
        )
        assert not result.remaining
        for name in (f"ctf-e{event_id}-t{team_id}", "ctf-gamenet"):
            subprocess.run(
                ["ip", "link", "delete", name], check=False,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
