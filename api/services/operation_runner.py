# api/services/operation_runner.py
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from api.database import SessionLocal
from api.models import OperationRun, OperationRunStep, Site, VM, Zone, utcnow
from api.services.caldera import vm_source_facts
from api.services.operation_driver import OperationDriver
from builder.caldera import single_ability_adversary_id
from builder.fact_contract import extract_facts
from builder.operation_compiler import CompiledNode, CompiledPlan, next_ready_nodes


@dataclass
class NodeResult:
    skipped: bool = False


def decide_node_execution(node: CompiledNode, fact_store: dict[str, str]) -> NodeResult:
    if node.node_type != "ability":
        return NodeResult()
    if any(trait not in fact_store for trait in node.input_traits):
        return NodeResult(skipped=True)
    return NodeResult()


async def launch_run(run_id: int) -> None:
    db = SessionLocal()
    try:
        run = db.query(OperationRun).filter(OperationRun.id == run_id).first()
        if not run or run.status in ("completed", "failed", "cancelled"):
            return
        # Compile the frozen plan snapshot.
        from builder.operation_compiler import compile_operation
        from builder.module_loader import load_all_modules
        modules_by_id = {m.id: m for m in load_all_modules()}
        compiled = compile_operation(json.loads(run.plan_snapshot), modules_by_id)

        fact_store = json.loads(run.fact_store or "{}")
        run.status = "running"
        run.started_at = run.started_at or utcnow()
        db.commit()

        completed: dict[str, str] = {}
        driver = OperationDriver()
        async with driver.caldera:
            source_id = await driver.ensure_run_source(run_id)
            await driver.seed_run_facts(source_id, _platform_facts(db, run))

            while True:
                run = db.query(OperationRun).get(run_id)
                if run.status == "cancelled":
                    break
                ready = next_ready_nodes(compiled.nodes, compiled.edges, completed)
                if not ready:
                    break
                for node_id in ready:
                    node = compiled.nodes[node_id]
                    result = await _run_node(db, run, node, compiled, fact_store, driver, source_id)
                    completed[node_id] = result
                    fact_store = json.loads((db.query(OperationRun).get(run_id)).fact_store)
                if any(node_id not in completed for node_id in compiled.nodes):
                    if not ready:
                        break
        _finalize_run(db, run_id, completed, compiled)
    finally:
        db.close()


async def _run_node(db, run, node, compiled, fact_store, driver, source_id) -> str:
    step = db.query(OperationRunStep).filter_by(run_id=run.id, node_id=node.node_id).first()
    if not step:
        step = OperationRunStep(run_id=run.id, node_id=node.node_id, node_type=node.node_type)
        db.add(step)
    step.status = "running"
    step.started_at = utcnow()
    db.commit()

    if node.node_type in ("trigger", "target", "finish"):
        return _finish_step(db, step, "success")

    if node.node_type == "delay":
        await asyncio.sleep(int(node.config.get("seconds", 0)))
        return _finish_step(db, step, "success")

    if node.node_type == "objective":
        achieved = f"ctf.goal.{node.module_id}" in fact_store
        return _finish_step(db, step, "success" if achieved else "failure")

    decision = decide_node_execution(node, fact_store)
    if decision.skipped:
        step.output = "SKIPPED: missing prerequisite facts"
        return _finish_step(db, step, "skipped")

    vm = _resolve_target_vm(db, run, node)
    if vm is None or not vm.ip_address:
        step.output = "SKIPPED: target VM has no agent"
        return _finish_step(db, step, "failure")
    agent_paw = await driver.resolve_agent_paw(vm.ip_address)
    if agent_paw is None:
        step.output = "SKIPPED: no Caldera agent for target VM"
        return _finish_step(db, step, "failure")

    timeout_seconds = int(node.config.get("timeout_seconds", compiled.policy["default_timeout_seconds"]))
    ability_result = await driver.execute(
        node.ability_id, single_ability_adversary_id(node.ability_id), agent_paw,
        f"event-{run.event_id}", source_id, timeout_seconds,
    )
    step.attempts += 1
    step.output = (ability_result.output or "")[:2000]
    new_facts = extract_facts(ability_result.output or "", node.output_specs)
    run = db.query(OperationRun).get(run.id)
    store = json.loads(run.fact_store or "{}")
    store.update(new_facts)
    run.fact_store = json.dumps(store)
    result = "success" if (ability_result.finished and ability_result.status == 0) else "failure"
    _finish_step(db, step, result)
    await driver.seed_run_facts(source_id, store)
    return result


def _finish_step(db, step, result) -> str:
    step.status = result
    step.result = result
    step.finished_at = utcnow()
    db.commit()
    return result


def _resolve_target_vm(db, run, node):
    target = node.config.get("target_vm_id", "")
    parts = target.split("/")  # "vm:<site>/<zone>/<endpoint>"
    if len(parts) != 3:
        return None
    site_key = parts[0].split(":", 1)[1]
    zone_key = parts[1]
    endpoint_key = parts[2]
    query = (db.query(VM)
             .join(Site, VM.site_id == Site.id)
             .join(Zone, VM.zone_id == Zone.id)
             .filter(VM.event_id == run.event_id, Site.key == site_key,
                     Zone.key == zone_key, VM.vm_type == endpoint_key))
    if run.team_id is not None:
        query = query.filter(VM.team_id == run.team_id)
    return query.first()


def _platform_facts(db, run) -> dict[str, str]:
    facts: dict[str, str] = {}
    vms = db.query(VM).filter(VM.event_id == run.event_id)
    if run.team_id is not None:
        vms = vms.filter(VM.team_id == run.team_id)
    for vm in vms.all():
        for f in vm_source_facts(vm):
            facts[f["trait"]] = f["value"]
    return facts

def _finalize_run(db, run_id, completed, compiled):
    run = db.query(OperationRun).get(run_id)
    finish_node = next((n for n in compiled.nodes.values() if n.node_type == "finish"), None)
    run.status = "completed" if (finish_node and completed.get(finish_node.node_id) == "success") else "failed"
    run.finished_at = utcnow()
    db.commit()


def mark_interrupted_runs(db) -> None:
    runs = db.query(OperationRun).filter(OperationRun.status.in_(["running", "awaiting_approval"])).all()
    for run in runs:
        run.status = "failed"
    db.commit()
