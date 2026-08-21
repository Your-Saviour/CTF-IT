import copy
import hashlib
import json
import random

VERSION = 1
MAX_BYTES = 262_144


def empty_module_plan():
    return {"version": VERSION, "assignments": {}}


def _ids(value, field):
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"{field} must be a list of module IDs")
    if len(value) != len(set(value)):
        raise ValueError(f"{field} contains duplicate module IDs")
    return list(value)


def normalize_module_plan(value):
    if value is None:
        return empty_module_plan()
    if not isinstance(value, dict) or value.get("version") != VERSION:
        raise ValueError("module_plan.version must be 1")
    if len(json.dumps(value).encode()) > MAX_BYTES:
        raise ValueError(f"module_plan exceeds {MAX_BYTES} bytes")
    assignments = value.get("assignments")
    if not isinstance(assignments, dict):
        raise ValueError("module_plan.assignments must be an object")
    result = empty_module_plan()
    for vm_id, row in assignments.items():
        if not isinstance(vm_id, str) or not vm_id.startswith("vm:") or vm_id.count("/") != 2:
            raise ValueError("assignment keys must be stable VM IDs")
        if not isinstance(row, dict) or row.get("mode") not in {"random_fill", "manual_only"}:
            raise ValueError(f"{vm_id}.mode must be random_fill or manual_only")
        normalized = {
            "mode": row["mode"],
            "pinned_module_ids": _ids(row.get("pinned_module_ids", []), f"{vm_id}.pinned_module_ids"),
            "resolved_module_ids": _ids(row.get("resolved_module_ids", []), f"{vm_id}.resolved_module_ids"),
        }
        if row.get("resolution_fingerprint"):
            normalized["resolution_fingerprint"] = str(row["resolution_fingerprint"])
        result["assignments"][vm_id] = normalized
    return result


def assignable_endpoints(infrastructure):
    rows = []
    for site in (infrastructure or {}).get("sites", []):
        for zone in site.get("zones", []):
            for endpoint in zone.get("endpoints", []):
                rows.append({"id": f"vm:{site['key']}/{zone['key']}/{endpoint['key']}",
                             "name": endpoint.get("name", endpoint["key"]),
                             "base_type": endpoint.get("base_type"), "role": zone.get("team", "blue"),
                             "site": site.get("name", site["key"]), "zone": zone.get("name", zone["key"])})
    return rows


def reconcile_module_plan(plan, infrastructure):
    result = normalize_module_plan(copy.deepcopy(plan))
    valid = {row["id"] for row in assignable_endpoints(infrastructure)}
    issues = [{"code": "unknown_vm", "vm_id": vm_id,
               "message": "Assignment references a VM that is no longer planned"}
              for vm_id in result["assignments"] if vm_id not in valid]
    return result, issues


def _compatible(module, base_type):
    return not module.disabled and (not module.supported_bases or base_type in module.supported_bases)


def _conflicts(module, selected):
    ids = {item.id for item in selected}
    return bool(set(module.conflicts) & ids or any(module.id in item.conflicts for item in selected))


def resolution_fingerprint(quota, endpoint, pinned_ids, library):
    signature = [{"id": m.id, "disabled": m.disabled, "requires": m.requires,
                  "conflicts": m.conflicts, "bases": m.supported_bases}
                 for m in sorted(library, key=lambda item: item.id)]
    raw = json.dumps({"quota": quota, "base_type": endpoint.get("base_type"),
                      "role": endpoint.get("role"), "pins": pinned_ids,
                      "catalogue": signature}, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()


def resolve_assignment(endpoint, assignment, quota, library, *, refill):
    by_id = {m.id: m for m in library}
    pins = list(assignment.get("pinned_module_ids", []))
    selected = []
    issues = []

    def add(module_id, pinned=False):
        module = by_id.get(module_id)
        if module is None:
            issues.append({"code": "unknown_module", "module_id": module_id,
                           "message": f"Module '{module_id}' is unavailable"})
            return
        if not _compatible(module, endpoint.get("base_type")):
            issues.append({"code": "incompatible_base", "module_id": module_id,
                           "message": f"Module '{module_id}' is incompatible with this base"})
        for required in module.requires:
            add(required)
        if module not in selected:
            if _conflicts(module, selected) and pinned:
                issues.append({"code": "pinned_conflict", "module_id": module_id,
                               "message": f"Pinned module '{module_id}' conflicts with another pin"})
            selected.append(module)

    for module_id in pins:
        add(module_id, pinned=True)

    if refill and assignment.get("mode", "random_fill") == "random_fill":
        for module_type, tiers in quota.items():
            if not isinstance(tiers, dict):
                continue
            for difficulty, count in tiers.items():
                have = sum(m.type == module_type and m.difficulty == difficulty for m in selected)
                pool = [m for m in library if m.type == module_type and m.difficulty == difficulty
                        and _compatible(m, endpoint.get("base_type")) and m not in selected and not _conflicts(m, selected)]
                for _ in range(max(0, int(count) - have)):
                    if not pool:
                        issues.append({"code": "quota_unfilled", "message": f"Cannot fill {difficulty} {module_type} quota"})
                        break
                    picked = random.choice(pool); add(picked.id); pool.remove(picked)
    result = {"mode": assignment.get("mode", "random_fill"), "pinned_module_ids": pins,
              "resolved_module_ids": [m.id for m in selected], "issues": issues}
    result["resolution_fingerprint"] = resolution_fingerprint(quota, endpoint, pins, library)
    return result


def resolved_module_ids(plan, stable_vm_id):
    return list(normalize_module_plan(plan)["assignments"].get(stable_vm_id, {}).get("resolved_module_ids", []))
