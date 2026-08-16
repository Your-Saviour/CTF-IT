"""Provider-neutral event operation plans and deterministic team previews."""

from __future__ import annotations

import copy
import hashlib
import json
from collections import defaultdict, deque

from builder.module_plan import assignable_endpoints, normalize_module_plan

VERSION = 1
MAX_BYTES = 524_288
TRIGGER_TYPES = {"manual_trigger", "event_start_trigger", "scheduled_trigger"}
NODE_TYPES = TRIGGER_TYPES | {"finish", "target", "ability", "objective", "delay", "gate"}
EDGE_CONDITIONS = {"success", "failure", "always"}
GATE_MODES = {"all", "any", "first"}


def _default_policy():
    return {"time_limit_minutes": 60, "max_concurrency": 1,
            "default_timeout_seconds": 120, "default_retries": 0,
            "default_retry_delay_seconds": 5, "unreachable_required": "stop",
            "missing_agent": "wait", "instructor_approval": False}


def empty_operation_plan():
    return {"version": VERSION, "input_fingerprint": None, "policy": _default_policy(), "nodes": [
        {"id": "trigger", "type": "manual_trigger", "label": "Manual Trigger", "x": 80, "y": 160, "config": {}},
        {"id": "finish", "type": "finish", "label": "Finish", "x": 680, "y": 160, "config": {}},
    ], "edges": []}


def _integer(value, field, minimum=0):
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(f"{field} must be an integer greater than or equal to {minimum}")
    return value


def normalize_operation_plan(value):
    if value is None:
        return empty_operation_plan()
    if not isinstance(value, dict) or value.get("version") != VERSION:
        raise ValueError("operation_plan.version must be 1")
    if len(json.dumps(value).encode()) > MAX_BYTES:
        raise ValueError(f"operation_plan exceeds {MAX_BYTES} bytes")
    source_policy = value.get("policy") or {}
    legacy_launch_mode = source_policy.get("launch_mode", "manual")
    if legacy_launch_mode not in {"manual", "scheduled", "scheduled_hold"}:
        raise ValueError("policy.launch_mode is invalid")
    legacy_offset = source_policy.get("start_offset_minutes", 0)
    policy = {**_default_policy(), **source_policy}
    policy.pop("launch_mode", None)
    policy.pop("start_offset_minutes", None)
    for field, minimum in (("time_limit_minutes", 1), ("max_concurrency", 1), ("default_timeout_seconds", 1),
                           ("default_retries", 0), ("default_retry_delay_seconds", 0)):
        policy[field] = _integer(policy[field], f"policy.{field}", minimum)
    policy["instructor_approval"] = bool(policy.get("instructor_approval", False))
    nodes = value.get("nodes")
    edges = value.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise ValueError("operation_plan nodes and edges must be lists")
    result = {"version": VERSION, "input_fingerprint": value.get("input_fingerprint"),
              "policy": policy, "nodes": [], "edges": []}
    node_ids = set()
    for index, node in enumerate(nodes):
        if not isinstance(node, dict) or not isinstance(node.get("id"), str) or not node["id"]:
            raise ValueError(f"nodes[{index}].id must be a non-empty string")
        if node["id"] in node_ids:
            raise ValueError(f"duplicate node ID '{node['id']}'")
        node_type = node.get("type")
        node_config = copy.deepcopy(node.get("config") or {})
        if node_type == "start":
            node_type = "manual_trigger" if legacy_launch_mode == "manual" else "scheduled_trigger"
            if node_type == "scheduled_trigger":
                node_config["offset_minutes"] = legacy_offset
            if legacy_launch_mode == "scheduled_hold":
                policy["instructor_approval"] = True
        if node_type not in NODE_TYPES:
            raise ValueError(f"nodes[{index}].type is invalid")
        node_ids.add(node["id"])
        result["nodes"].append({"id": node["id"], "type": node_type,
            "label": str(node.get("label") or node["type"].title()),
            "x": float(node.get("x", 0)), "y": float(node.get("y", 0)),
            "disabled": bool(node.get("disabled", False)), "config": node_config})
    edge_ids = set()
    pairs = set()
    for index, edge in enumerate(edges):
        if not isinstance(edge, dict) or not isinstance(edge.get("id"), str) or not edge["id"]:
            raise ValueError(f"edges[{index}].id must be a non-empty string")
        if edge["id"] in edge_ids:
            raise ValueError(f"duplicate edge ID '{edge['id']}'")
        if edge.get("source") not in node_ids or edge.get("target") not in node_ids:
            raise ValueError(f"edges[{index}] references an unknown node")
        if edge.get("condition") not in EDGE_CONDITIONS:
            raise ValueError(f"edges[{index}].condition is invalid")
        pair = (edge["source"], edge["target"], edge["condition"])
        if pair in pairs:
            raise ValueError("duplicate typed edge")
        pairs.add(pair); edge_ids.add(edge["id"])
        result["edges"].append({"id": edge["id"], "source": edge["source"],
            "target": edge["target"], "condition": edge["condition"],
            "label": str(edge.get("label") or "")})
    return result


def operation_catalogue(infrastructure, module_plan, modules):
    targets = assignable_endpoints(infrastructure)
    assignments = normalize_module_plan(module_plan)["assignments"]
    assigned = set()
    for row in assignments.values():
        assigned.update(row["pinned_module_ids"])
        assigned.update(row["resolved_module_ids"])
    by_id = {module.id: module for module in modules}
    pending = list(assigned)
    while pending:
        module = by_id.get(pending.pop())
        if not module:
            continue
        for required_id in getattr(module, "requires", []):
            if required_id not in assigned:
                assigned.add(required_id)
                pending.append(required_id)
    abilities, objectives = [], []
    for module in sorted(modules, key=lambda item: item.id):
        if module.id not in assigned or module.disabled or not module.caldera:
            continue
        caldera = module.caldera
        for phase in ("recon", "exploit"):
            row = caldera.get(phase) or {}
            if row.get("command"):
                abilities.append({"id": f"ability:{module.id}:{phase}", "module_id": module.id,
                    "ability": phase, "name": f"{phase.title()}: {module.name}",
                    "description": row.get("description", module.description),
                    "tactic": caldera.get("tactic"), "supported_bases": list(module.supported_bases)})
        if module.type == "goal":
            objectives.append({"id": f"objective:{module.id}", "module_id": module.id,
                               "name": module.name, "description": module.description})
    return {"targets": targets, "abilities": abilities, "objectives": objectives,
            "controls": ["manual_trigger", "event_start_trigger", "scheduled_trigger", "finish", "delay", "gate"]}


def _topological(nodes, edges):
    enabled = {node["id"] for node in nodes if not node.get("disabled")}
    outgoing = defaultdict(list); indegree = {node_id: 0 for node_id in enabled}
    for edge in edges:
        if edge["source"] in enabled and edge["target"] in enabled:
            outgoing[edge["source"]].append(edge["target"]); indegree[edge["target"]] += 1
    queue = deque(sorted(node_id for node_id, degree in indegree.items() if degree == 0)); order = []
    while queue:
        node_id = queue.popleft(); order.append(node_id)
        for target in sorted(outgoing[node_id]):
            indegree[target] -= 1
            if indegree[target] == 0: queue.append(target)
    return order if len(order) == len(enabled) else None


def validate_operation_plan(plan, infrastructure, module_plan, modules, event_minutes=None):
    try:
        plan = normalize_operation_plan(plan)
    except ValueError as exc:
        return [{"code": "invalid_structure", "message": str(exc)}]
    issues = []
    active_nodes = [node for node in plan["nodes"] if not node["disabled"]]
    by_id = {node["id"]: node for node in active_nodes}
    active_edges = [edge for edge in plan["edges"] if edge["source"] in by_id and edge["target"] in by_id]
    triggers = [node for node in active_nodes if node["type"] in TRIGGER_TYPES]
    finishes = [node for node in active_nodes if node["type"] == "finish"]
    if len(triggers) != 1:
        issues.append({"code": "trigger_count", "message": "Graph requires exactly one enabled trigger"})
    if len(finishes) != 1: issues.append({"code": "finish_count", "message": "Graph requires exactly one Finish node"})
    order = _topological(active_nodes, active_edges)
    if order is None: issues.append({"code": "cycle", "message": "Operation graph must be acyclic"})
    outgoing = defaultdict(set); incoming = defaultdict(set)
    for edge in active_edges: outgoing[edge["source"]].add(edge["target"]); incoming[edge["target"]].add(edge["source"])
    reachable = set()
    for trigger_node in triggers:
        if incoming[trigger_node["id"]]:
            issues.append({"code": "trigger_incoming", "node_id": trigger_node["id"],
                           "message": "Trigger nodes cannot have incoming transitions"})
    if triggers:
        trigger_id = triggers[0]["id"]
        stack = [trigger_id]
        while stack:
            current = stack.pop()
            if current in reachable: continue
            reachable.add(current); stack.extend(outgoing[current])
    for node in active_nodes:
        if triggers and node["id"] not in reachable:
            issues.append({"code": "unreachable", "node_id": node["id"], "message": f"{node['label']} is unreachable from the trigger"})
    catalogue = operation_catalogue(infrastructure, module_plan, modules)
    targets = {row["id"]: row for row in catalogue["targets"]}
    abilities = {(row["module_id"], row["ability"]): row for row in catalogue["abilities"]}
    objectives = {row["module_id"] for row in catalogue["objectives"]}
    for node in active_nodes:
        config = node["config"]
        if node["type"] == "scheduled_trigger":
            offset = config.get("offset_minutes")
            if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
                issues.append({"code": "invalid_trigger_offset", "node_id": node["id"],
                               "message": "Scheduled trigger offset must be a non-negative integer"})
        elif node["type"] == "ability":
            target = config.get("target_vm_id")
            ability = abilities.get((config.get("module_id"), config.get("ability")))
            if target not in targets: issues.append({"code": "unknown_target", "node_id": node["id"], "message": "Ability target is not a planned VM"})
            if ability is None: issues.append({"code": "ability_unavailable", "node_id": node["id"], "message": "Ability is not supplied by an assigned module"})
            elif target in targets and ability["supported_bases"] and targets[target]["base_type"] not in ability["supported_bases"]:
                issues.append({"code": "incompatible_target", "node_id": node["id"], "message": "Ability is incompatible with the target base"})
            for field, minimum in (("timeout_seconds", 1), ("retries", 0), ("retry_delay_seconds", 0)):
                if field in config and (not isinstance(config[field], int) or isinstance(config[field], bool) or config[field] < minimum):
                    issues.append({"code": "invalid_timing", "node_id": node["id"], "message": f"{field} is invalid"})
        elif node["type"] == "objective":
            if config.get("module_id") not in objectives:
                issues.append({"code": "objective_unavailable", "node_id": node["id"], "message": "Objective is not supplied by an assigned goal module"})
            if config.get("target_vm_id") not in targets:
                issues.append({"code": "unknown_target", "node_id": node["id"], "message": "Objective target is not a planned VM"})
            if config.get("required") and node["id"] not in reachable:
                issues.append({"code": "required_objective_unreachable", "node_id": node["id"], "message": "Required objective is unreachable"})
        elif node["type"] == "delay" and (not isinstance(config.get("seconds"), int) or config.get("seconds", -1) < 0):
            issues.append({"code": "invalid_timing", "node_id": node["id"], "message": "Delay seconds must be non-negative"})
        elif node["type"] == "gate" and config.get("mode", "all") not in GATE_MODES:
            issues.append({"code": "invalid_gate", "node_id": node["id"], "message": "Gate mode is invalid"})
    if event_minutes is not None and len(triggers) == 1 and triggers[0]["type"] == "scheduled_trigger":
        offset = triggers[0]["config"].get("offset_minutes")
        if isinstance(offset, int) and not isinstance(offset, bool) and offset >= 0 \
                and offset + plan["policy"]["time_limit_minutes"] > event_minutes:
            issues.append({"code": "outside_event", "node_id": triggers[0]["id"],
                           "message": "Operation timing exceeds the event duration"})
    return issues


def operation_input_fingerprint(infrastructure, module_plan, modules):
    targets = [{key: row.get(key) for key in ("id", "base_type", "role")} for row in assignable_endpoints(infrastructure)]
    catalogue = operation_catalogue(infrastructure, module_plan, modules)
    raw = json.dumps({"targets": targets, "abilities": catalogue["abilities"], "objectives": catalogue["objectives"]},
                     sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()


def compile_team_preview(plan, infrastructure, module_plan, modules, team):
    issues = validate_operation_plan(plan, infrastructure, module_plan, modules)
    if issues:
        raise ValueError("operation plan is invalid")
    plan = normalize_operation_plan(plan)
    active_nodes = [node for node in plan["nodes"] if not node["disabled"]]
    active_ids = {node["id"] for node in active_nodes}
    edges = [edge for edge in plan["edges"] if edge["source"] in active_ids and edge["target"] in active_ids]
    order = _topological(active_nodes, edges)
    trigger_node = next(node for node in active_nodes if node["type"] in TRIGGER_TYPES)
    trigger = {"type": {"manual_trigger": "manual", "event_start_trigger": "event_start",
                        "scheduled_trigger": "scheduled"}[trigger_node["type"]], "once": True}
    if trigger_node["type"] == "scheduled_trigger":
        trigger["offset_minutes"] = trigger_node["config"]["offset_minutes"]
    manifest = {}
    for node in active_nodes:
        config = node["config"]
        row = {"team_id": team.get("id"), "team_name": team.get("name"), "node_type": node["type"]}
        if config.get("target_vm_id"): row["planned_vm_id"] = config["target_vm_id"]
        if config.get("module_id"): row["module_id"] = config["module_id"]
        if config.get("ability"): row["ability"] = config["ability"]
        if node["type"] == "objective": row["required"] = bool(config.get("required"))
        manifest[node["id"]] = row
    return {"team": {"id": team.get("id"), "name": team.get("name")}, "order": order, "trigger": trigger,
            "policy": plan["policy"], "edges": edges, "manifest": manifest,
            "input_fingerprint": operation_input_fingerprint(infrastructure, module_plan, modules)}
