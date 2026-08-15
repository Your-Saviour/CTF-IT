"""Pure helpers for editing and normalizing event infrastructure plans."""

from __future__ import annotations

from copy import deepcopy


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


def _humanize(value: str) -> str:
    return " ".join(part.capitalize() for part in value.replace("-", "_").split("_") if part)


def _next_free_key(candidate: str, used: set[str]) -> str:
    if candidate not in used:
        return candidate
    suffix = 2
    while f"{candidate}_{suffix}" in used:
        suffix += 1
    return f"{candidate}_{suffix}"
