from copy import deepcopy
from datetime import datetime, timezone

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from api.database import Base
from api.integrations.expo_it import ExpoITAdapter, ExpoContractError, build_owned_snapshot, merge_remote
from api.integrations.expo_it_contract import ExpoData
from api.models import Event, Team, VM, VMGoal, VMModule


REMOTE = {
    "phases": [], "inbox": [], "scoring": [], "spot_reports": [], "ust": [],
    "collaboration_points": [],
    "infrastructure": {
        "systems": [
            {"expo_id": "expo-owned", "zones": [], "networks": [], "system_aliases": ["keep"]},
            {"expo_id": "ctf-event-7-vm-1", "zones": [], "networks": [], "system_aliases": ["old"]},
        ],
        "credentials": [],
    },
}


def test_contract_requires_complete_top_level_document():
    ExpoData.model_validate(REMOTE)
    missing = deepcopy(REMOTE); missing.pop("ust")
    with pytest.raises(ValidationError):
        ExpoData.model_validate(missing)
    extra = deepcopy(REMOTE); extra["unexpected"] = []
    with pytest.raises(ValidationError):
        ExpoData.model_validate(extra)


def test_merge_replaces_owned_collections_and_namespaced_systems():
    owned = {
        "phases": [{"number": 0, "time_range": "09:00Z–10:00Z", "current": True}],
        "scoring": [],
        "systems": [{"expo_id": "ctf-event-7-vm-2", "zones": [], "networks": [], "system_aliases": ["new"]}],
    }
    merged = merge_remote(deepcopy(REMOTE), owned, event_id=7)
    assert merged["phases"] == owned["phases"]
    assert [item["expo_id"] for item in merged["infrastructure"]["systems"]] == [
        "expo-owned", "ctf-event-7-vm-2"
    ]
    assert merged["inbox"] == []


def test_build_snapshot_maps_timeline_scores_and_safe_vm_fields():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        event = Event(
            name="Exercise", quota="{}", status="open",
            started_at=datetime(2026, 8, 21, 9, tzinfo=timezone.utc),
            timeline='{"version":1,"phases":[{"id":"p0","name":"Start","start_offset_minutes":0,"end_offset_minutes":60,"color":"#112233","description":""}],"injects":[]}',
        )
        db.add(event); db.flush()
        team = Team(name="BT01", event_id=event.id); db.add(team); db.flush()
        vm = VM(hostname="web-1", ip_address="192.0.2.10", private_ip="10.0.0.10",
                os="Ubuntu 24.04", role="server", team_id=team.id, event_id=event.id)
        db.add(vm); db.flush()
        db.add(VMModule(vm_id=vm.id, module_id="m", module_type="hardening", difficulty="easy",
                        points=100, stage="preapplied", status="completed"))
        db.add(VMGoal(vm_id=vm.id, module_id="g", defend_points=25, defend_count=2))
        db.commit()
        snapshot = build_owned_snapshot(db, event.id, datetime(2026, 8, 21, 9, 30, tzinfo=timezone.utc))
    assert snapshot["phases"] == [{"number": 0, "time_range": "09:00Z–10:00Z", "current": True}]
    assert snapshot["scoring"][0]["defense"] == 150
    assert snapshot["scoring"][0]["reverts"] == 50
    system = snapshot["systems"][0]
    assert system["expo_id"].startswith(f"ctf-event-{event.id}-vm-")
    assert system["system_aliases"] == ["web-1"]
    assert {row.get("ipv4") for row in system["networks"]} == {"192.0.2.10", "10.0.0.10"}
    assert "credential_ids" not in system


def test_merge_refuses_to_remove_system_referenced_by_ticket():
    remote = deepcopy(REMOTE)
    remote["ust"] = [{
        "ticket_id":"T-1","priority":"High","status":"Open","category":"Access",
        "subject":"Help","reporter":"User","system_expo_id":"ctf-event-7-vm-1",
        "team":"BT01","opened":"now","age":"1m","summary":"Cannot sign in","replies":[],
    }]
    with pytest.raises(ExpoContractError, match="referenced"):
        merge_remote(remote, {"phases": [], "scoring": [], "systems": []}, 7)


@pytest.mark.asyncio
async def test_adapter_gets_then_puts_complete_document(monkeypatch):
    requests = []
    async def handler(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=REMOTE)
        assert set(__import__("json").loads(request.content)) == {
            "phases", "inbox", "scoring", "spot_reports", "ust", "collaboration_points", "infrastructure"
        }
        return httpx.Response(200, json=REMOTE)
    adapter = ExpoITAdapter(transport=httpx.MockTransport(handler))
    monkeypatch.setattr("api.integrations.expo_it.snapshot_for_event", lambda event_id: {"phases": [], "scoring": [], "systems": []})
    binding = type("Binding", (), {"event_id": 7})()
    destination = type("Destination", (), {"base_url": "https://expo.example"})()
    result = await adapter.synchronize(binding, destination, "api-key")
    assert result.ok is True
    assert [request.method for request in requests] == ["GET", "PUT"]
    assert all(request.headers["X-API-Key"] == "api-key" for request in requests)
