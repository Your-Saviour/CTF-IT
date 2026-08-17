# builder/operation_compiler.py
from __future__ import annotations

from dataclasses import dataclass, field

from builder.fact_contract import FactSpec, ability_facts
from builder.caldera import ability_uuid
from builder.module_loader import Module
from builder.operation_plan import normalize_operation_plan


@dataclass
class CompiledNode:
    node_id: str
    node_type: str
    label: str
    config: dict
    module_id: str | None = None
    phase: str | None = None
    ability_id: str | None = None
    command: str | None = None
    tactic: str | None = None
    input_traits: list[str] = field(default_factory=list)
    output_specs: list[FactSpec] = field(default_factory=list)


@dataclass
class CompiledPlan:
    nodes: dict[str, CompiledNode] = field(default_factory=dict)
    edges: list[dict] = field(default_factory=list)
    trigger: dict = field(default_factory=dict)
    policy: dict = field(default_factory=dict)


def compile_operation(plan: dict, modules_by_id: dict[str, Module]) -> CompiledPlan:
    plan = normalize_operation_plan(plan)
    compiled = CompiledPlan(edges=plan["edges"], policy=plan["policy"])
    compiled.trigger = next((n for n in plan["nodes"] if n["type"].endswith("_trigger")), {})

    for node in plan["nodes"]:
        node_type = node["type"]
        config = node["config"]
        cnode = CompiledNode(
            node_id=node["id"], node_type=node_type, label=node["label"],
            config=config, tactic=None,
        )
        if node_type == "ability":
            module = modules_by_id[config["module_id"]]
            phase = config["ability"]
            facts = ability_facts(module, phase)
            cnode.module_id = module.id
            cnode.phase = phase
            cnode.ability_id = ability_uuid(module.id, phase)
            cnode.command = (module.caldera or {}).get(phase, {}).get("command")
            cnode.tactic = (module.caldera or {}).get("tactic")
            cnode.input_traits = list(facts.inputs)
            cnode.output_specs = list(facts.outputs)
        elif node_type == "objective":
            cnode.module_id = config["module_id"]
        compiled.nodes[node["id"]] = cnode
    return compiled


def edge_activated(condition: str, result: str) -> bool:
    if condition == "always":
        return True
    if condition == "success":
        return result == "success"
    if condition == "failure":
        return result in ("failure", "skipped")
    return False


def _satisfied(node: CompiledNode, incoming: list[dict], status: dict[str, str]) -> bool:
    activated = [e for e in incoming if e["source"] in status and edge_activated(e["condition"], status[e["source"]])]
    if node.node_type == "gate":
        mode = node.config.get("mode", "all")
        if mode == "all":
            return len(activated) == len(incoming) and all(e["source"] in status for e in incoming)
        return bool(activated)
    return bool(activated)


def next_ready_nodes(nodes: dict[str, CompiledNode], edges: list[dict], completed: dict[str, str]) -> list[str]:
    status: dict[str, str] = dict(completed)
    for node_id, node in nodes.items():
        if node.node_type.endswith("_trigger"):
            status.setdefault(node_id, "success")

    changed = True
    while changed:
        changed = False
        for node_id, node in nodes.items():
            if node_id in status:
                continue
            incoming = [e for e in edges if e["target"] == node_id]
            if not incoming:
                continue
            if not all(e["source"] in status for e in incoming):
                continue
            if not _satisfied(node, incoming, status):
                status[node_id] = "skipped"
                changed = True

    ready: list[str] = []
    for node_id, node in nodes.items():
        if node_id in status:
            continue
        incoming = [e for e in edges if e["target"] == node_id]
        if not incoming:
            continue
        if _satisfied(node, incoming, status):
            ready.append(node_id)
    return sorted(ready)
