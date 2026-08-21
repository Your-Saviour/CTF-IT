import importlib
import json
import os
import sqlite3
import sys
from copy import deepcopy
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from api.integrations.expo_it import ExpoITAdapter


pytestmark = [pytest.mark.expo_it_live, pytest.mark.asyncio]


def _load_sibling_app(tmp_path: Path):
    repository = Path(os.environ.get("EXPO_IT_REPO", Path(__file__).resolve().parents[2] / "expo-it"))
    if not (repository / "app" / "main.py").is_file():
        pytest.skip("../../expo-it is not available")
    os.environ["EXPO_SESSION_SECRET"] = "live-contract-session-secret"
    os.environ["EXPO_API_KEY"] = "live-contract-api-key"
    os.environ["EXPO_TEAMS"] = "BT01,BT02,BT03"
    os.environ["EXPO_DATABASE_PATH"] = str(tmp_path / "expo-it.db")
    sys.path.insert(0, str(repository))
    try:
        return importlib.import_module("app.main").app
    finally:
        sys.path.remove(str(repository))


@pytest.mark.asyncio
async def test_real_management_api_round_trip_preserves_expo_owned_data(tmp_path, monkeypatch):
    expo_app = _load_sibling_app(tmp_path)
    headers = {"X-API-Key": "live-contract-api-key"}
    transport = httpx.ASGITransport(app=expo_app)

    with TestClient(expo_app):
        async with httpx.AsyncClient(transport=transport, base_url="http://expo.test") as client:
            initial = (await client.get("/api/v1/data", headers=headers)).json()
            credential_id = initial["infrastructure"]["credentials"][0]["id"]
            expo_system = initial["infrastructure"]["systems"][0]
            expo_system["system_aliases"] = ["expo-owned-alias"]
            old_owned = deepcopy(expo_system)
            old_owned.update({
                "expo_id": "ctf-event-41-vm-9",
                "system_aliases": ["old-owned-alias"],
                "credential_ids": [credential_id],
            })
            initial["infrastructure"]["systems"].append(old_owned)
            seeded = await client.put("/api/v1/data", headers=headers, json=initial)
            assert seeded.status_code == 200, seeded.text
            before = (await client.get("/api/v1/data", headers=headers)).json()

        with sqlite3.connect(expo_app.state.database_path) as connection:
            raw = connection.execute(
                "SELECT data FROM managed_datasets WHERE resource='infrastructure'"
            ).fetchone()[0]
        password_before = json.loads(raw)["credentials"][0]["password"]

        owned = {
            "phases": [{"number": 0, "time_range": "09:00Z–10:00Z", "current": True}],
            "scoring": [{
                "team": "BT01", "defense": 20, "usability": 0, "availability": 0,
                "reverts": 5, "ctirep": 0, "sitrep": 0, "forensics": 0,
                "legal": 0, "stratcom": 0, "stratex": 0, "xpoints": 0,
                "collaboration": 0,
            }],
            "systems": [{
                "expo_id": "ctf-event-41-vm-9", "zones": ["blue"],
                "networks": [{"name": "address-1", "ipv4": "192.0.2.41"}],
                "team": "BT01", "team_name": "BT01", "role": "server",
                "system_aliases": ["new-owned-alias"],
            }],
        }
        monkeypatch.setattr("api.integrations.expo_it.snapshot_for_event", lambda event_id: owned)
        adapter = ExpoITAdapter(transport=transport)
        binding = type("Binding", (), {"event_id": 41})()
        destination = type("Destination", (), {"base_url": "http://expo.test"})()
        result = await adapter.synchronize(binding, destination, "live-contract-api-key")
        assert result.ok is True

        async with httpx.AsyncClient(transport=transport, base_url="http://expo.test") as client:
            after = (await client.get("/api/v1/data", headers=headers)).json()

        assert after["phases"] == owned["phases"]
        assert after["scoring"] == owned["scoring"]
        for key in ("inbox", "spot_reports", "ust", "collaboration_points"):
            assert after[key] == before[key]
        assert after["infrastructure"]["credentials"] == before["infrastructure"]["credentials"]
        assert all("password" not in item and "original_password" not in item
                   for item in after["infrastructure"]["credentials"])
        systems = {item["expo_id"]: item for item in after["infrastructure"]["systems"]}
        assert systems[expo_system["expo_id"]]["system_aliases"] == ["expo-owned-alias"]
        assert systems["ctf-event-41-vm-9"]["system_aliases"] == ["new-owned-alias"]
        assert systems["ctf-event-41-vm-9"]["availability"] == old_owned["availability"]
        assert systems["ctf-event-41-vm-9"]["credential_ids"] == [credential_id]
        with sqlite3.connect(expo_app.state.database_path) as connection:
            raw = connection.execute(
                "SELECT data FROM managed_datasets WHERE resource='infrastructure'"
            ).fetchone()[0]
        assert json.loads(raw)["credentials"][0]["password"] == password_before
