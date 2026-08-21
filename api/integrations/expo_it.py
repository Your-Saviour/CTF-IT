from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from ipaddress import ip_address

import httpx
from pydantic import ValidationError

from api.database import SessionLocal
from api.integrations.base import ConnectionTestResult, SyncResult
from api.integrations.expo_it_contract import ExpoData
from api.models import Event, Team, VM, VMGoal, VMModule, utcnow
from api.services.verifier_account import scoring_enabled_vm_ids
from builder.timeline import normalize_timeline


class ExpoContractError(ValueError):
    pass


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _phase_rows(event: Event, now: datetime) -> list[dict]:
    timeline = normalize_timeline(json.loads(event.timeline) if event.timeline else None)
    if not event.started_at:
        return []
    start = _aware(event.started_at)
    current = _aware(now)
    rows = []
    for number, phase in enumerate(sorted(timeline["phases"], key=lambda item: item["start_offset_minutes"])):
        phase_start = start + timedelta(minutes=phase["start_offset_minutes"])
        phase_end = start + timedelta(minutes=phase["end_offset_minutes"])
        rows.append({
            "number": number,
            "time_range": (
                phase_start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
                + "/"
                + phase_end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
            ),
            "current": event.status == "open" and phase_start <= current < phase_end,
        })
    return rows


def _scoring_rows(db, event_id: int) -> list[dict]:
    rows = []
    for team in db.query(Team).filter_by(event_id=event_id).order_by(Team.id):
        modules = db.query(VMModule).join(VM).filter(
            VM.team_id == team.id, VM.event_id == event_id, VMModule.stage == "preapplied"
        ).all()
        goals = db.query(VMGoal).join(VM).filter(VM.team_id == team.id, VM.event_id == event_id).all()
        enabled = scoring_enabled_vm_ids(db, {item.vm_id for item in modules} | {item.vm_id for item in goals})
        defensive = sum(item.points for item in modules if item.vm_id in enabled and item.status == "completed")
        reactive = sum(item.defend_points * item.defend_count for item in goals if item.vm_id in enabled)
        rows.append({
            "team": team.name,
            "defense": defensive + reactive,
            "usability": 0,
            "availability": 0,
            "reverts": reactive,
            "ctirep": 0,
            "sitrep": 0,
            "forensics": 0,
            "legal": 0,
            "stratcom": 0,
            "stratex": 0,
            "xpoints": 0,
            "collaboration": 0,
        })
    return rows


def _system_row(vm: VM, event_id: int) -> dict:
    addresses = []
    seen = set()
    for value in (vm.ip_address, vm.public_ip, vm.private_ip, vm.vpc_ip):
        if not value or value in seen:
            continue
        try:
            parsed = ip_address(value)
        except ValueError:
            continue
        seen.add(value)
        item = {"name": f"address-{len(addresses) + 1}"}
        item["ipv4" if parsed.version == 4 else "ipv6"] = value
        addresses.append(item)
    result = {
        "expo_id": f"ctf-event-{event_id}-vm-{vm.id}",
        "zones": [vm.zone.name] if vm.zone else [],
        "networks": addresses,
        "team": vm.team.name,
        "team_name": vm.team.name,
        "system_aliases": [vm.hostname] if vm.hostname else [],
    }
    if vm.os:
        result["os"] = vm.os
    if vm.role or vm.vm_type:
        result["role"] = vm.role or vm.vm_type
    return result


def build_owned_snapshot(db, event_id: int, now: datetime | None = None) -> dict:
    event = db.get(Event, event_id)
    if not event:
        raise ExpoContractError("event not found")
    systems = [
        _system_row(vm, event_id)
        for vm in db.query(VM).filter_by(event_id=event_id).order_by(VM.id)
        if vm.team
    ]
    return {
        "phases": _phase_rows(event, now or utcnow()),
        "scoring": _scoring_rows(db, event_id),
        "systems": systems,
    }


def snapshot_for_event(event_id: int) -> dict:
    with SessionLocal() as db:
        return build_owned_snapshot(db, event_id)


def merge_remote(remote: dict, owned: dict, event_id: int) -> dict:
    merged = deepcopy(remote)
    prefix = f"ctf-event-{event_id}-vm-"
    current_ids = {system["expo_id"] for system in owned["systems"]}
    stale_ids = {
        system["expo_id"] for system in merged["infrastructure"]["systems"]
        if system["expo_id"].startswith(prefix) and system["expo_id"] not in current_ids
    }
    referenced = {ticket.get("system_expo_id") for ticket in merged.get("ust", [])}
    if stale_ids & referenced:
        raise ExpoContractError("remote_reference_conflict: a stale CTF-IT system is still referenced by an Expo-IT ticket")
    old_owned = {
        system["expo_id"]: system for system in merged["infrastructure"]["systems"]
        if system["expo_id"].startswith(prefix)
    }
    systems = []
    for system in owned["systems"]:
        previous = old_owned.get(system["expo_id"], {})
        if previous.get("availability"):
            system = dict(system, availability=previous["availability"])
        if previous.get("credential_ids"):
            system = dict(system, credential_ids=previous["credential_ids"])
        systems.append(system)
    preserved = [system for system in merged["infrastructure"]["systems"] if not system["expo_id"].startswith(prefix)]
    merged["phases"] = deepcopy(owned["phases"])
    collaboration = {}
    for point in merged.get("collaboration_points", []):
        collaboration[point["team_to"]] = collaboration.get(point["team_to"], 0) + point["points"]
    scoring = deepcopy(owned["scoring"])
    for row in scoring:
        row["collaboration"] = collaboration.get(row["team"], 0)
    merged["scoring"] = scoring
    merged["infrastructure"]["systems"] = preserved + deepcopy(systems)
    try:
        return ExpoData.model_validate(merged).model_dump(exclude_none=True)
    except ValidationError as exc:
        raise ExpoContractError("Expo-IT aggregate contract validation failed") from exc


class ExpoITAdapter:
    key = "expo_it"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self.transport = transport

    def validate_destination(self, destination) -> list[str]:
        return []

    async def _get(self, destination, secret: str) -> tuple[httpx.Response, dict]:
        url = destination.base_url.rstrip("/") + "/api/v1/data"
        async with httpx.AsyncClient(timeout=15, follow_redirects=False, transport=self.transport) as client:
            response = await client.get(url, headers={"X-API-Key": secret, "Accept": "application/json"})
        response.raise_for_status()
        return response, ExpoData.model_validate(response.json()).model_dump(exclude_none=True)

    async def test_connection(self, destination, secret: str) -> ConnectionTestResult:
        try:
            await self._get(destination, secret)
            return ConnectionTestResult(True, "ok", "Connected")
        except httpx.HTTPStatusError as exc:
            return ConnectionTestResult(False, "authentication_failed" if exc.response.status_code in {401, 403} else "http_error", "Expo-IT rejected the connection test")
        except (httpx.HTTPError, ValidationError):
            return ConnectionTestResult(False, "connection_failed", "Could not validate the Expo-IT management API")

    async def synchronize(self, binding, destination, secret: str) -> SyncResult:
        try:
            _, remote = await self._get(destination, secret)
            payload = merge_remote(remote, snapshot_for_event(binding.event_id), binding.event_id)
            url = destination.base_url.rstrip("/") + "/api/v1/data"
            async with httpx.AsyncClient(timeout=15, follow_redirects=False, transport=self.transport) as client:
                response = await client.put(url, headers={"X-API-Key": secret}, json=payload)
            response.raise_for_status()
            return SyncResult(True, "ok", "Synchronized", response.status_code, False)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            return SyncResult(False, "authentication_failed" if status in {401, 403} else "http_error", "Expo-IT rejected synchronization", status, status == 429 or status >= 500)
        except ExpoContractError as exc:
            message = str(exc)
            code = "remote_reference_conflict" if message.startswith("remote_reference_conflict:") else "contract_error"
            return SyncResult(False, code, message.removeprefix("remote_reference_conflict: "), None, False)
        except (httpx.TimeoutException, httpx.NetworkError):
            return SyncResult(False, "connection_failed", "Could not reach Expo-IT", None, True)
        except (httpx.HTTPError, ValidationError, ValueError):
            return SyncResult(False, "contract_error", "Expo-IT returned an incompatible response", None, False)
