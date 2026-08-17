"""Scenario capture, fingerprinting, instantiation, and plan health."""

from __future__ import annotations

import hashlib
import json

from builder.infrastructure_planner import default_infrastructure, normalize_infrastructure
from builder.module_plan import assignable_endpoints, empty_module_plan, normalize_module_plan
from builder.operation_plan import empty_operation_plan, normalize_operation_plan
from builder.timeline import empty_timeline, normalize_timeline, validate_timeline


def scenario_fingerprint(quota, infrastructure, infrastructure_layout, module_plan, operations, timeline):
    raw = json.dumps(
        {"quota": quota, "infrastructure": infrastructure, "infrastructure_layout": infrastructure_layout,
         "module_plan": module_plan, "operations": operations, "timeline": timeline},
        sort_keys=True, separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()


def capture_scenario_from_event(event) -> dict:
    infrastructure = normalize_infrastructure(
        json.loads(event.infrastructure) if event.infrastructure else default_infrastructure()
    )
    module_plan = normalize_module_plan(
        json.loads(event.module_plan) if event.module_plan else empty_module_plan()
    )
    operations = [
        {"name": op.name, "description": op.description, "position": op.position,
         "operation_plan": normalize_operation_plan(json.loads(op.operation_plan))}
        for op in sorted(event.operations, key=lambda o: (o.position, o.id))
    ]
    timeline = normalize_timeline(json.loads(event.timeline) if event.timeline else empty_timeline())
    return {
        "quota": json.loads(event.quota) if event.quota else {},
        "infrastructure": infrastructure,
        "infrastructure_layout": json.loads(event.infrastructure_layout) if event.infrastructure_layout else None,
        "module_plan": module_plan,
        "operations": operations,
        "timeline": timeline,
    }


def validate_scenario_catalogue(module_plan, infrastructure, modules_by_id):
    issues = []
    targets = {row["id"]: row for row in assignable_endpoints(infrastructure)}
    for vm_id, assignment in module_plan["assignments"].items():
        target = targets.get(vm_id)
        for module_id in [*assignment.get("pinned_module_ids", []),
                          *assignment.get("resolved_module_ids", [])]:
            module = modules_by_id.get(module_id)
            if module is None:
                issues.append({"code": "unknown_module", "vm_id": vm_id, "module_id": module_id,
                               "message": f"Module '{module_id}' is unavailable"})
                continue
            if module.disabled:
                issues.append({"code": "disabled_module", "vm_id": vm_id, "module_id": module_id,
                               "message": f"Module '{module_id}' is disabled"})
            if target and module.supported_bases and target["base_type"] not in module.supported_bases:
                issues.append({"code": "incompatible_base", "vm_id": vm_id, "module_id": module_id,
                               "message": f"Module '{module_id}' is incompatible with the target base"})
    return issues


def instantiate_scenario(db, scenario, name=None):
    quota = json.loads(scenario.quota) if scenario.quota else {}
    infrastructure = json.loads(scenario.infrastructure) if scenario.infrastructure else default_infrastructure()
    module_plan = json.loads(scenario.module_plan) if scenario.module_plan else empty_module_plan()
    operations = json.loads(scenario.operations_json) if scenario.operations_json else []
    timeline = json.loads(scenario.timeline) if scenario.timeline else empty_timeline()

    from api.models import Event, EventOperation
    from builder.module_loader import load_all_modules

    event = Event(
        name=(name or scenario.name),
        description=scenario.description,
        quota=json.dumps(quota),
        infrastructure=json.dumps(infrastructure),
        infrastructure_layout=json.dumps(json.loads(scenario.infrastructure_layout))
            if scenario.infrastructure_layout else None,
        module_plan=json.dumps(module_plan),
        timeline=json.dumps(timeline),
        scenario_id=scenario.id,
        scenario_version=scenario.version,
        scenario_fingerprint=scenario.content_fingerprint,
    )
    db.add(event); db.flush()
    for position, op in enumerate(sorted(operations, key=lambda o: o.get("position", 0))):
        db.add(EventOperation(
            event_id=event.id,
            name=op["name"],
            description=op.get("description"),
            position=position,
            operation_plan=json.dumps(normalize_operation_plan(op.get("operation_plan") or empty_operation_plan())),
        ))
    db.commit(); db.refresh(event)

    modules_by_id = {m.id: m for m in load_all_modules()}
    report = validate_scenario_catalogue(module_plan, infrastructure, modules_by_id)
    return event.id, report


def plan_health(event, modules_by_id):
    from builder.operation_plan import validate_operation_plan

    infrastructure = normalize_infrastructure(
        json.loads(event.infrastructure) if event.infrastructure else default_infrastructure()
    )
    module_plan = normalize_module_plan(
        json.loads(event.module_plan) if event.module_plan else empty_module_plan()
    )
    operation_names = {op.name for op in event.operations}
    timeline = json.loads(event.timeline) if event.timeline else empty_timeline()
    return {
        "module_issues": validate_scenario_catalogue(module_plan, infrastructure, modules_by_id),
        "timeline_issues": validate_timeline(timeline, infrastructure, operation_names,
                                             modules_by_id, event.time_limit_minutes),
        "operation_issues": [
            {"operation_id": op.id, "name": op.name,
             "issues": validate_operation_plan(json.loads(op.operation_plan), infrastructure,
                                               module_plan, list(modules_by_id.values()),
                                               event.time_limit_minutes)}
            for op in sorted(event.operations, key=lambda o: o.position)
        ],
    }
