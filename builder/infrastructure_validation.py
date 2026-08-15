"""Validation and deterministic sizing for the GameNet infrastructure model."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from ipaddress import ip_network

SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
TEAM_ROLES = {"blue", "red"}
PLANNER_ICONS = {
    "server", "desktop", "laptop", "mobile", "appliance",
    "gateway", "router", "switch", "firewall", "vpn", "proxy", "load_balancer",
    "web", "database", "dns", "mail", "directory", "file_share", "storage",
    "certificate_authority", "identity", "attacker", "target", "siem", "ids",
    "monitoring", "logging", "honeypot", "malware", "bastion", "vulnerable",
    "cloud", "container", "kubernetes", "backup", "git", "cicd", "linux",
    "ubuntu", "debian", "kali", "redhat", "windows", "macos", "freebsd",
    "opnsense", "pfsense", "aws", "azure", "gcp",
}
VPC_LIMIT_PER_REGION = 5


def gamenet_hostname(event_id: int, team_id: int, *parts: object) -> str:
    """Return a stable Vultr-compatible hostname for a GameNet machine."""
    raw = "-".join(["gamenet", f"e{event_id}", f"t{team_id}", *(str(part) for part in parts)])
    normalized = re.sub(r"[^a-z0-9-]+", "-", raw.lower()).strip("-")
    normalized = re.sub(r"-+", "-", normalized)
    if len(normalized) <= 63:
        return normalized
    suffix = hashlib.sha256(normalized.encode()).hexdigest()[:8]
    return normalized[:54].rstrip("-") + "-" + suffix


def validate_infrastructure(
    infrastructure: dict,
    valid_base_ids: set[str],
    valid_regions: set[str] | None = None,
    *,
    team_count: int = 1,
    live_vpcs_by_region: dict[str, int] | None = None,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(infrastructure, dict):
        return ["infrastructure must be a JSON object"]
    extra = set(infrastructure) - {"vpn_gateway", "sites"}
    if extra:
        errors.append(f"infrastructure has unknown keys: {', '.join(sorted(extra))}")

    gateway = infrastructure.get("vpn_gateway")
    if not isinstance(gateway, dict):
        errors.append("vpn_gateway is required and must be an object")
    else:
        _validate_machine(gateway, "vpn_gateway", valid_base_ids, errors, require_region=True)
        port = gateway.get("listen_port")
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            errors.append("vpn_gateway.listen_port must be an integer from 1 to 65535")

    sites = infrastructure.get("sites")
    if not isinstance(sites, list) or not sites:
        errors.append("sites must be a non-empty array")
        return errors
    site_keys: set[str] = set()
    region_counts: Counter[str] = Counter()
    for si, site in enumerate(sites):
        path = f"sites[{si}]"
        if not isinstance(site, dict):
            errors.append(f"{path} must be an object")
            continue
        _key(site.get("key"), f"{path}.key", site_keys, errors)
        if not isinstance(site.get("name"), str) or not site["name"].strip():
            errors.append(f"{path}.name is required")
        region = site.get("region")
        if not isinstance(region, str) or not region:
            errors.append(f"{path}.region is required")
        else:
            region_counts[region] += max(team_count, 0)
            if valid_regions is not None and region not in valid_regions:
                errors.append(f"{path}.region references unavailable region '{region}'")
        firewall = site.get("firewall")
        if not isinstance(firewall, dict):
            errors.append(f"{path}.firewall is required and must be an object")
        else:
            _validate_machine(firewall, f"{path}.firewall", valid_base_ids, errors)
        zones = site.get("zones")
        if not isinstance(zones, list) or not zones:
            errors.append(f"{path}.zones must be a non-empty array")
            continue
        if len(zones) > 15:  # first /24 of the /20 belongs to infrastructure
            errors.append(f"{path}.zones exceeds /20 address capacity (maximum 15)")
        zone_keys: set[str] = set()
        for zi, zone in enumerate(zones):
            zpath = f"{path}.zones[{zi}]"
            if not isinstance(zone, dict):
                errors.append(f"{zpath} must be an object")
                continue
            _key(zone.get("key"), f"{zpath}.key", zone_keys, errors)
            if not isinstance(zone.get("name"), str) or not zone["name"].strip():
                errors.append(f"{zpath}.name is required")
            if zone.get("team") not in TEAM_ROLES:
                errors.append(f"{zpath}.team must be one of: blue, red")
            endpoints = zone.get("endpoints")
            if not isinstance(endpoints, list):
                errors.append(f"{zpath}.endpoints must be an array")
                continue
            endpoint_keys: set[str] = set()
            addresses = 10
            for ei, endpoint in enumerate(endpoints):
                epath = f"{zpath}.endpoints[{ei}]"
                if not isinstance(endpoint, dict):
                    errors.append(f"{epath} must be an object")
                    continue
                _key(endpoint.get("key"), f"{epath}.key", endpoint_keys, errors)
                _validate_machine(endpoint, epath, valid_base_ids, errors)
                count = endpoint.get("count")
                if count is None:
                    if not isinstance(endpoint.get("name"), str) or not endpoint["name"].strip():
                        errors.append(f"{epath}.name is required")
                    addresses += 1
                elif not isinstance(count, int) or isinstance(count, bool) or count < 1:
                    errors.append(f"{epath}.count must be a positive integer")
                else:
                    addresses += count
            if addresses > 254:
                errors.append(f"{zpath} endpoints exhaust its /24 (maximum 245 endpoints)")

    live = live_vpcs_by_region or {}
    for region, planned in region_counts.items():
        used = int(live.get(region, 0))
        if used + planned > VPC_LIMIT_PER_REGION:
            errors.append(
                f"region '{region}' would use {used + planned} VPCs ({used} live + {planned} planned); limit is 5"
            )
    return errors


def infrastructure_summary(infrastructure: dict, team_count: int = 1) -> dict:
    sites = infrastructure.get("sites", [])
    endpoint_count = sum(
        endpoint.get("count", 1)
        for site in sites for zone in site.get("zones", []) for endpoint in zone.get("endpoints", [])
    )
    per_region = Counter(site.get("region") for site in sites if site.get("region"))
    return {
        "teams": team_count,
        "sites": len(sites) * team_count,
        "gateways": team_count,
        "firewalls": len(sites) * team_count,
        "endpoints": endpoint_count * team_count,
        "vms": (1 + len(sites) + endpoint_count) * team_count,
        "vpcs_by_region": {region: count * team_count for region, count in sorted(per_region.items())},
    }


def site_subnets(cidr: str, zone_count: int) -> tuple[str, list[tuple[str, str]]]:
    network = ip_network(cidr)
    if network.prefixlen != 20:
        raise ValueError("site CIDR must be a /20")
    blocks = list(network.subnets(new_prefix=24))
    if zone_count > 15:
        raise ValueError("a site supports at most 15 zones")
    return str(blocks[0]), [(str(blocks[i + 1]), str(blocks[i + 1].network_address + 1)) for i in range(zone_count)]


def _key(value, path: str, seen: set[str], errors: list[str]) -> None:
    if not isinstance(value, str) or not SLUG_RE.fullmatch(value):
        errors.append(f"{path} must match ^[a-z][a-z0-9_]{{0,63}}$")
    elif value in seen:
        errors.append(f"{path} duplicates key '{value}'")
    else:
        seen.add(value)


def _validate_machine(spec: dict, path: str, bases: set[str], errors: list[str], require_region=False) -> None:
    prompt = spec.get("ust_prompt")
    if prompt is not None and (not isinstance(prompt, str) or len(prompt) > 8000):
        errors.append(f"{path}.ust_prompt must be a string of at most 8000 characters")
    for field in ("primary_icon", "icon"):
        icon = spec.get(field)
        if icon is not None and (not isinstance(icon, str) or icon not in PLANNER_ICONS):
            errors.append(f"{path}.{field} must reference a supported planner icon")
    base = spec.get("base_type")
    if not isinstance(base, str) or base not in bases:
        errors.append(f"{path}.base_type references unknown or disabled base type '{base}'")
    plan = spec.get("default_plan")
    if not isinstance(plan, str) or not plan:
        errors.append(f"{path}.default_plan is required")
    if require_region and (not isinstance(spec.get("region"), str) or not spec.get("region")):
        errors.append(f"{path}.region is required")
