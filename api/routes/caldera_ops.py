# api/routes/caldera_ops.py
"""Caldera operation management API endpoints."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from api.database import get_db
from api.models import Event, Team, VM, VMModule
from api.routes.admin import require_admin
from api.services.caldera import CalderaClient, get_caldera_api_key

router = APIRouter(prefix="/admin/caldera", tags=["admin"])


def _make_client() -> CalderaClient:
    return CalderaClient(get_caldera_api_key())


# ── Operations List ────────────────────────────────────────────────────────────

@router.get("/operations")
async def list_operations(request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    async with _make_client() as caldera:
        try:
            operations = await caldera.list_operations()
        except Exception as e:
            return JSONResponse({"error": f"Caldera unavailable: {e}"}, status_code=502)

    result = []
    for op in operations:
        result.append({
            "id": op.get("id"),
            "name": op.get("name"),
            "state": op.get("state"),
            "group": op.get("group"),
            "start": op.get("start"),
            "finish": op.get("finish"),
        })
    return result


# ── Create Operation ───────────────────────────────────────────────────────────

@router.post("/operations")
async def create_operation(request: Request, db: Session = Depends(get_db)):
    """Create a Caldera operation scoped to an event or a specific VM.

    Body (one of):
      {"event_id": N, "adversary_name": "..."}   → targets all agents in event-N group
      {"vm_id": N, "adversary_name": "..."}       → targets the agent on that VM
    """
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    body = await request.json()
    adversary_name = body.get("adversary_name", "CTF Full Exploit Chain")

    async with _make_client() as caldera:
        # Ensure basic source exists
        try:
            await caldera.ensure_source()
        except Exception as e:
            return JSONResponse({"error": f"Could not ensure fact source: {e}"}, status_code=502)

        # Get planner
        try:
            planner_id = await caldera.get_atomic_planner_id()
        except Exception as e:
            return JSONResponse({"error": f"Could not find atomic planner: {e}"}, status_code=502)

        # Resolve adversary
        try:
            adversary = await caldera.get_adversary_by_name(adversary_name)
        except Exception as e:
            return JSONResponse({"error": f"Could not list adversaries: {e}"}, status_code=502)
        if not adversary:
            return JSONResponse({"error": f"Adversary '{adversary_name}' not found in Caldera"}, status_code=404)

        if "event_id" in body:
            event_id = body["event_id"]
            event = db.query(Event).filter(Event.id == event_id).first()
            if not event:
                return JSONResponse({"error": "Event not found"}, status_code=404)
            group = f"event-{event_id}"
            op_name = f"CTF Event {event.name} — {adversary_name}"

        elif "vm_id" in body:
            vm_id = body["vm_id"]
            vm = db.query(VM).filter(VM.id == vm_id).first()
            if not vm:
                return JSONResponse({"error": "VM not found"}, status_code=404)
            if not vm.ip_address:
                return JSONResponse({"error": "VM has no IP address"}, status_code=422)

            # Target the agent for this specific VM via its group
            group = f"event-{vm.event_id}" if vm.event_id else "red"
            op_name = f"CTF VM {vm.hostname or vm_id} — {adversary_name}"

        else:
            return JSONResponse({"error": "Provide 'event_id' or 'vm_id'"}, status_code=400)

        try:
            operation = await caldera.create_operation(
                name=op_name,
                adversary_id=adversary["adversary_id"],
                planner_id=planner_id,
                group=group,
            )
        except Exception as e:
            return JSONResponse({"error": f"Failed to create operation: {e}"}, status_code=502)

    return {
        "id": operation.get("id"),
        "name": operation.get("name"),
        "state": operation.get("state"),
        "group": operation.get("group"),
    }


# ── Operation Detail ───────────────────────────────────────────────────────────

@router.get("/operations/{op_id}")
async def get_operation(op_id: str, request: Request, db: Session = Depends(get_db)):
    """Return operation detail with per-agent results mapped to CTF module names."""
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    async with _make_client() as caldera:
        try:
            op = await caldera.get_operation(op_id, include_chain=True)
            agents = await caldera.list_agents()
        except Exception as e:
            return JSONResponse({"error": f"Caldera unavailable: {e}"}, status_code=502)

    # Build ability UUID → module info mapping
    from builder.caldera import build_ability_uuid_map
    from builder.module_loader import load_all_modules
    modules = load_all_modules()
    uuid_to_module = build_ability_uuid_map(modules)

    # Build agent paw → VM hostname mapping
    all_vms = db.query(VM).all()
    ip_to_vm = {vm.ip_address: vm for vm in all_vms if vm.ip_address}
    paw_to_vm: dict[str, dict] = {}
    for agent in agents:
        paw = agent.get("paw")
        for ip in agent.get("host_ip_addrs", []):
            if ip in ip_to_vm:
                vm = ip_to_vm[ip]
                paw_to_vm[paw] = {"hostname": vm.hostname, "vm_id": vm.id, "ip": ip}
                break

    # Annotate chain links
    chain = op.get("chain", [])
    annotated_chain = []
    for link in chain:
        ability_id = link.get("ability", {}).get("ability_id", "")
        module_info = uuid_to_module.get(ability_id, {})
        agent_paw = link.get("paw", "")
        vm_info = paw_to_vm.get(agent_paw, {})
        annotated_chain.append({
            "id": link.get("id"),
            "paw": agent_paw,
            "vm_hostname": vm_info.get("hostname", agent_paw),
            "vm_id": vm_info.get("vm_id"),
            "ability_id": ability_id,
            "ability_name": link.get("ability", {}).get("name", ""),
            "tactic": link.get("ability", {}).get("tactic", ""),
            "technique_id": link.get("ability", {}).get("technique_id", ""),
            "module_id": module_info.get("module_id"),
            "module_name": module_info.get("module_name"),
            "phase": module_info.get("phase"),
            "status": link.get("status"),  # -3=timeout, -2=discarded, -1=fail, 0=queued, 1=success
            "output": (link.get("output") or "")[:500],  # truncate long outputs
            "collect": link.get("collect"),
            "finish": link.get("finish"),
        })

    # Per-agent summary
    agent_summary: dict[str, dict] = {}
    for link in annotated_chain:
        paw = link["paw"]
        if paw not in agent_summary:
            agent_summary[paw] = {
                "paw": paw,
                "vm_hostname": link["vm_hostname"],
                "vm_id": link["vm_id"],
                "success": 0,
                "failed": 0,
                "pending": 0,
            }
        s = link["status"]
        if s == 1:
            agent_summary[paw]["success"] += 1
        elif s in (-1, -3):
            agent_summary[paw]["failed"] += 1
        else:
            agent_summary[paw]["pending"] += 1

    return {
        "id": op.get("id"),
        "name": op.get("name"),
        "state": op.get("state"),
        "group": op.get("group"),
        "start": op.get("start"),
        "finish": op.get("finish"),
        "adversary": op.get("adversary", {}).get("name"),
        "chain": annotated_chain,
        "agents": list(agent_summary.values()),
    }


# ── Delete Operation ───────────────────────────────────────────────────────────

@router.delete("/operations/{op_id}")
async def delete_operation(op_id: str, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    async with _make_client() as caldera:
        try:
            await caldera.delete_operation(op_id)
        except Exception as e:
            return JSONResponse({"error": f"Failed to delete operation: {e}"}, status_code=502)

    return {"status": "deleted", "id": op_id}


# ── Red Team VM Summary ────────────────────────────────────────────────────────

@router.get("/vm-summary")
async def vm_attack_summary(request: Request, db: Session = Depends(get_db)):
    """Return per-VM attack summary across all operations.

    For each VM with a connected agent, shows total exploits attempted,
    succeeded, and failed — aggregated across all finished operations.
    """
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    async with _make_client() as caldera:
        try:
            operations = await caldera.list_operations()
            agents = await caldera.list_agents()
        except Exception as e:
            return JSONResponse({"error": f"Caldera unavailable: {e}"}, status_code=502)

    all_vms = db.query(VM).all()
    ip_to_vm = {vm.ip_address: vm for vm in all_vms if vm.ip_address}

    # Map paw → VM
    all_teams = {t.id: t for t in db.query(Team).all()}
    paw_to_vm: dict[str, dict] = {}
    for agent in agents:
        paw = agent.get("paw")
        for ip in agent.get("host_ip_addrs", []):
            if ip in ip_to_vm:
                vm = ip_to_vm[ip]
                team = all_teams.get(vm.team_id)
                paw_to_vm[paw] = {
                    "hostname": vm.hostname,
                    "vm_id": vm.id,
                    "team_name": team.name if team else None,
                    "ip": ip,
                }
                break

    # Aggregate results
    vm_stats: dict[int, dict] = {}
    for op in operations:
        for link in op.get("chain", []):
            paw = link.get("paw", "")
            vm_info = paw_to_vm.get(paw)
            if not vm_info:
                continue
            vm_id = vm_info["vm_id"]
            if vm_id not in vm_stats:
                vm_stats[vm_id] = {
                    "vm_id": vm_id,
                    "hostname": vm_info["hostname"],
                    "team_name": vm_info["team_name"],
                    "total_attacks": 0,
                    "exploits_succeeded": 0,
                    "exploits_failed": 0,
                    "last_seen": None,
                }
            vm_stats[vm_id]["total_attacks"] += 1
            s = link.get("status")
            if s == 1:
                vm_stats[vm_id]["exploits_succeeded"] += 1
            elif s in (-1, -3):
                vm_stats[vm_id]["exploits_failed"] += 1
            finish = link.get("finish")
            if finish and (not vm_stats[vm_id]["last_seen"] or finish > vm_stats[vm_id]["last_seen"]):
                vm_stats[vm_id]["last_seen"] = finish

    return list(vm_stats.values())


# ── VM-specific operation results ─────────────────────────────────────────────

@router.get("/vm/{vm_id}/results")
async def vm_results(vm_id: int, request: Request, db: Session = Depends(get_db)):
    """Return the most recent operation results for a specific VM."""
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    vm = db.query(VM).filter(VM.id == vm_id).first()
    if not vm:
        return JSONResponse({"error": "VM not found"}, status_code=404)
    if not vm.ip_address:
        return {"results": [], "operation_id": None}

    async with _make_client() as caldera:
        try:
            operations = await caldera.list_operations()
            agents = await caldera.list_agents()
        except Exception as e:
            return JSONResponse({"error": f"Caldera unavailable: {e}"}, status_code=502)

        # Find this VM's agent paw
        vm_paw = None
        for agent in agents:
            if vm.ip_address in agent.get("host_ip_addrs", []):
                vm_paw = agent.get("paw")
                break

        if not vm_paw:
            return {"results": [], "operation_id": None, "message": "No agent found for this VM"}

        # Find the most recent finished operation that ran against this VM's group
        vm_group = f"event-{vm.event_id}" if vm.event_id else "red"
        relevant_ops = [
            op for op in operations
            if op.get("group") == vm_group and op.get("state") in ("finished", "cleanup", "running")
        ]
        if not relevant_ops:
            return {"results": [], "operation_id": None}

        # Sort by start time descending, pick most recent
        relevant_ops.sort(key=lambda o: o.get("start") or "", reverse=True)

        # Fetch the most recent operation with chain
        try:
            op = await caldera.get_operation(relevant_ops[0]["id"], include_chain=True)
        except Exception as e:
            return JSONResponse({"error": f"Caldera unavailable: {e}"}, status_code=502)

    from builder.caldera import build_ability_uuid_map
    from builder.module_loader import load_all_modules
    uuid_to_module = build_ability_uuid_map(load_all_modules())

    results = []
    for link in op.get("chain", []):
        if link.get("paw") != vm_paw:
            continue
        ability_id = link.get("ability", {}).get("ability_id", "")
        module_info = uuid_to_module.get(ability_id, {})
        results.append({
            "ability_name": link.get("ability", {}).get("name", ""),
            "module_id": module_info.get("module_id"),
            "module_name": module_info.get("module_name"),
            "phase": module_info.get("phase"),
            "status": link.get("status"),
            "finish": link.get("finish"),
        })

    return {
        "results": results,
        "operation_id": op.get("id"),
        "operation_name": op.get("name"),
    }
