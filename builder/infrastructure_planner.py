"""Pure helpers for editing and normalizing event infrastructure plans."""

from __future__ import annotations

from copy import deepcopy
import json
import math


_DEFAULT_INFRASTRUCTURE = {
    "vpn_gateway": {
        "base_type": "ubuntu_24_server",
        "default_plan": "vc2-1c-1gb",
        "region": "ewr",
        "listen_port": 51820,
    },
    "sites": [{
        "key": "head_office",
        "name": "Head Office",
        "region": "ewr",
        "firewall": {"base_type": "opnsense", "default_plan": "vc2-2c-4gb"},
        "zones": [
            {
                "key": "corporate",
                "name": "Corporate",
                "team": "blue",
                "endpoints": [
                    {
                        "key": "workstation_1",
                        "name": "Workstation 1",
                        "base_type": "ubuntu_24_server",
                        "default_plan": "vc2-1c-1gb",
                    },
                    {
                        "key": "workstation_2",
                        "name": "Workstation 2",
                        "base_type": "ubuntu_24_server",
                        "default_plan": "vc2-1c-1gb",
                    },
                ],
            },
            {"key": "red_team", "name": "Red Team", "team": "red", "endpoints": []},
        ],
    }],
}


def default_infrastructure() -> dict:
    """Return a fresh copy of the starter event topology."""
    return deepcopy(_DEFAULT_INFRASTRUCTURE)


def endpoint_instances(endpoint: dict) -> list[dict]:
    """Return individual endpoint records for either supported wire shape."""
    count = endpoint.get("count")
    if count is None:
        return [deepcopy(endpoint)]
    stem = endpoint.get("key", "endpoint")
    common = {key: deepcopy(value) for key, value in endpoint.items() if key != "count"}
    return [
        {**common, "key": f"{stem}_{index}", "name": f"{_humanize(stem)} {index}"}
        for index in range(1, count + 1)
    ]


def normalize_infrastructure(value: dict) -> dict:
    """Deep-copy a plan and expand legacy count-based endpoint groups."""
    result = deepcopy(value)
    for site in result.get("sites", []):
        for zone in site.get("zones", []):
            used: set[str] = set()
            normalized: list[dict] = []
            for endpoint in zone.get("endpoints", []):
                for instance in endpoint_instances(endpoint):
                    instance["key"] = _next_free_key(instance.get("key", "endpoint"), used)
                    instance.setdefault("name", _humanize(instance["key"]))
                    used.add(instance["key"])
                    normalized.append(instance)
            zone["endpoints"] = normalized
    return result


def infrastructure_node_ids(infrastructure: dict) -> set[str]:
    """Return every stable node identifier addressable by the planner."""
    result = {"gateway"}
    for site in infrastructure.get("sites", []):
        site_key = site.get("key")
        if not isinstance(site_key, str):
            continue
        result.update({f"site:{site_key}", f"firewall:{site_key}"})
        for zone in site.get("zones", []):
            zone_key = zone.get("key")
            if not isinstance(zone_key, str):
                continue
            result.add(f"zone:{site_key}/{zone_key}")
            for endpoint in endpoint_instances_for_layout(zone.get("endpoints", [])):
                endpoint_key = endpoint.get("key")
                if isinstance(endpoint_key, str):
                    result.add(f"vm:{site_key}/{zone_key}/{endpoint_key}")
    return result


def endpoint_instances_for_layout(endpoints: list[dict]) -> list[dict]:
    """Expand a zone endpoint list with the same collision rules as normalization."""
    used: set[str] = set()
    result: list[dict] = []
    for endpoint in endpoints:
        for instance in endpoint_instances(endpoint):
            instance["key"] = _next_free_key(instance.get("key", "endpoint"), used)
            used.add(instance["key"])
            result.append(instance)
    return result


def validate_infrastructure_layout(layout: dict | None, infrastructure: dict) -> list[str]:
    """Validate presentation-only layout data against a topology document."""
    if layout is None:
        return []
    if not isinstance(layout, dict):
        return ["infrastructure_layout must be a JSON object"]
    if len(json.dumps(layout, separators=(",", ":"), allow_nan=True).encode()) > 262_144:
        return ["infrastructure_layout exceeds 262144 bytes"]
    errors: list[str] = []
    if layout.get("version") != 1:
        errors.append("infrastructure_layout.version must be 1")
    nodes = layout.get("nodes")
    if not isinstance(nodes, dict):
        errors.append("infrastructure_layout.nodes must be an object")
        return errors
    valid_ids = infrastructure_node_ids(infrastructure)
    for node_id, position in nodes.items():
        path = f"infrastructure_layout.nodes.{node_id}"
        if node_id not in valid_ids:
            errors.append(f"{path} references an unknown node id")
        if not isinstance(position, dict):
            errors.append(f"{path} must be an object")
            continue
        for axis in ("x", "y"):
            value = position.get(axis)
            if (not isinstance(value, (int, float)) or isinstance(value, bool)
                    or not math.isfinite(value)):
                errors.append(f"{path}.{axis} must be a finite number")
    return errors


def _humanize(value: str) -> str:
    return " ".join(part.capitalize() for part in value.replace("-", "_").split("_") if part)


def _next_free_key(candidate: str, used: set[str]) -> str:
    if candidate not in used:
        return candidate
    suffix = 2
    while f"{candidate}_{suffix}" in used:
        suffix += 1
    return f"{candidate}_{suffix}"
