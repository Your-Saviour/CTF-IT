# api/routes/caldera_ops.py
"""Caldera operation management API endpoints."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from api.database import get_db
from api.models import Event, Team, VM, VMModule
from api.routes.admin import require_admin
from api.services.caldera import CalderaClient, get_caldera_api_key
from builder.caldera import build_ability_uuid_map
from builder.fact_contract import fact_summary
from builder.module_loader import load_all_modules

router = APIRouter(prefix="/admin/api/caldera", tags=["admin"])


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

    Optional controls:
      {"planner_name": "atomic", "autonomous": false, "state": "running",
       "obfuscator": "plain-text", "jitter": "2/8", "visibility": 50}
    """
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    body = await request.json()
    adversary_name = body.get("adversary_name", "CTF Full Exploit Chain")
    planner_name = body.get("planner_name", "atomic")
    autonomous = body.get("autonomous", True)
    state = body.get("state")
    obfuscator = body.get("obfuscator", "plain-text")
    jitter = body.get("jitter", "2/8")
    visibility = body.get("visibility", 50)

    async with _make_client() as caldera:
        # Ensure basic source exists
        try:
            await caldera.ensure_source()
        except Exception as e:
            return JSONResponse({"error": f"Could not ensure fact source: {e}"}, status_code=502)

        # Seed known facts (VM/event metadata) so abilities don't need recon
        # to learn things the platform already knows.
        try:
            from api.services.caldera import vm_source_facts
            if "vm_id" in body:
                vm = db.query(VM).filter(VM.id == body["vm_id"]).first()
                facts = vm_source_facts(vm) if vm else []
                if facts:
                    await caldera.seed_facts(facts)
            elif "event_id" in body:
                event_vms = db.query(VM).filter(VM.event_id == body["event_id"]).all()
                all_facts: list[dict] = []
                for ev_vm in event_vms:
                    all_facts.extend(vm_source_facts(ev_vm))
                if all_facts:
                    await caldera.seed_facts(all_facts)
        except Exception as e:
            return JSONResponse({"error": f"Could not seed facts: {e}"}, status_code=502)

        # Get planner
        try:
            planner = await caldera.get_planner_by_name(planner_name)
            planner_id = planner["id"]
        except Exception as e:
            return JSONResponse({"error": f"Could not find planner '{planner_name}': {e}"}, status_code=502)

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
                autonomous=autonomous,
                state=state,
                obfuscator=obfuscator,
                jitter=jitter,
                visibility=visibility,
            )
        except Exception as e:
            return JSONResponse({"error": f"Failed to create operation: {e}"}, status_code=502)

    return {
        "id": operation.get("id"),
        "name": operation.get("name"),
        "state": operation.get("state"),
        "group": operation.get("group"),
    }


# ── Planners ────────────────────────────────────────────────────────────────────

@router.get("/planners")
async def list_planners(request: Request, db: Session = Depends(get_db)):
    """List available Caldera planners."""
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    async with _make_client() as caldera:
        try:
            planners = await caldera.list_planners()
        except Exception as e:
            return JSONResponse({"error": f"Caldera unavailable: {e}"}, status_code=502)

    return [
        {
            "id": p.get("planner_id") or p.get("id"),
            "name": p.get("name"),
            "description": (p.get("description") or "")[:200],
        }
        for p in planners
    ]


@router.get("/obfuscators")
async def list_obfuscators(request: Request, db: Session = Depends(get_db)):
    """List available Caldera obfuscators (stealth options)."""
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    async with _make_client() as caldera:
        try:
            obfuscators = await caldera.list_obfuscators()
        except Exception as e:
            return JSONResponse({"error": f"Caldera unavailable: {e}"}, status_code=502)

    return [
        {
            "name": o.get("name"),
            "description": (o.get("description") or "")[:200],
        }
        for o in obfuscators
    ]


# ── Operation Control (human-in-loop) ───────────────────────────────────────────

@router.patch("/operations/{op_id}")
async def update_operation(op_id: str, request: Request, db: Session = Depends(get_db)):
    """Update an operation: pause/resume, step (run_one_link), or toggle autonomous.

    Body (any subset):
      {"state": "paused" | "running" | "run_one_link" | "finished"}
      {"autonomous": false}
      {"jitter": "2/8", "obfuscator": "plain-text", "visibility": 50}
    """
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    body = await request.json()
    allowed = {"state", "autonomous", "jitter", "obfuscator", "visibility", "name"}
    fields = {k: v for k, v in body.items() if k in allowed}
    if not fields:
        return JSONResponse({"error": "No updatable fields provided"}, status_code=400)

    async with _make_client() as caldera:
        try:
            op = await caldera.update_operation(op_id, **fields)
        except Exception as e:
            return JSONResponse({"error": f"Failed to update operation: {e}"}, status_code=502)

    return {
        "id": op.get("id"),
        "name": op.get("name"),
        "state": op.get("state"),
    }


@router.patch("/operations/{op_id}/links/{link_id}")
async def update_operation_link(
    op_id: str, link_id: str, request: Request, db: Session = Depends(get_db)
):
    """Approve or reject a pending (untrusted) link in a manual operation.

    Body:
      {"action": "approve"}  → set status EXECUTE
      {"action": "reject"}   → set status DISCARD
      {"status": -3}         → raw status override (any valid link status)
    """
    from api.services.caldera import LINK_STATUS_DISCARD, LINK_STATUS_EXECUTE

    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    body = await request.json()
    action = body.get("action")
    status = body.get("status")
    if action == "approve":
        status = LINK_STATUS_EXECUTE
    elif action == "reject":
        status = LINK_STATUS_DISCARD
    if status is None:
        return JSONResponse(
            {"error": "Provide 'action' (approve/reject) or 'status'"},
            status_code=400,
        )

    async with _make_client() as caldera:
        try:
            link = await caldera.update_operation_link(op_id, link_id, status=status)
        except Exception as e:
            return JSONResponse({"error": f"Failed to update link: {e}"}, status_code=502)

    return {"link_id": link_id, "status": status}


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
            "status": link.get("status"),  # exit-code: 0=success, >0=fail; -3=collecting, -2=caldera-fail, -5=discard
            "output": (link.get("output") or "")[:2000],  # truncate long outputs
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
        f = link.get("finish")
        if f and s == 0:
            agent_summary[paw]["success"] += 1
        elif f and (s != 0):
            agent_summary[paw]["failed"] += 1
        else:
            agent_summary[paw]["pending"] += 1

    # Optional: include attack trees annotated with results
    attack_trees = {}
    include_tree = request.query_params.get("include_tree", "").lower() == "true"
    if include_tree:
        from builder.attack_tree import build_attack_tree, serialize_tree
        library = {m.id: m for m in modules}
        # Collect unique VM IDs from chain
        vm_ids = {link["vm_id"] for link in annotated_chain if link.get("vm_id")}
        for vid in vm_ids:
            vm = db.query(VM).filter(VM.id == vid).first()
            if not vm:
                continue
            vm_mods = db.query(VMModule).filter(VMModule.vm_id == vid).all()
            vm_module_objects = [library.get(vmm.module_id) for vmm in vm_mods]
            vm_module_objects = [m for m in vm_module_objects if m]

            tree = build_attack_tree(vm_module_objects)
            tree_data = serialize_tree(tree)

            # Annotate node statuses from chain results. Exploit links whose
            # recon fact was absent are trimmed at planning time and never enter
            # the chain, so a missing exploit with a non-confirming recon output
            # is inferred as "skipped" (native fact gating).
            from builder.attack_tree import annotate_tree_statuses
            tree_data = annotate_tree_statuses(tree_data, annotated_chain)
            attack_trees[vid] = tree_data

    response = {
        "id": op.get("id"),
        "name": op.get("name"),
        "state": op.get("state"),
        "group": op.get("group"),
        "start": op.get("start"),
        "finish": op.get("finish"),
        "adversary": op.get("adversary", {}).get("name"),
        "planner": op.get("planner", {}).get("name"),
        "autonomous": bool(op.get("autonomous")),
        "obfuscator": op.get("obfuscator"),
        "jitter": op.get("jitter"),
        "visibility": op.get("visibility"),
        "chain": annotated_chain,
        "agents": list(agent_summary.values()),
    }
    if attack_trees:
        response["attack_trees"] = attack_trees

    return response


@router.get("/operations/{op_id}/ability-facts")
async def get_operation_ability_facts(op_id: str, request: Request, db: Session = Depends(get_db)):
    """Return structured input/output facts for each ability in the operation chain."""
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    async with _make_client() as caldera:
        try:
            op = await caldera.get_operation(op_id, include_chain=True)
        except Exception as e:
            return JSONResponse({"error": f"Caldera unavailable: {e}"}, status_code=502)

    modules = load_all_modules()
    uuid_to_module = build_ability_uuid_map(modules)
    modules_by_id = {m.id: m for m in modules}

    fact_data = {}
    for link in op.get("chain", []):
        ability_id = (link.get("ability") or {}).get("ability_id", "")
        info = uuid_to_module.get(ability_id)
        if not info:
            continue
        module = modules_by_id.get(info["module_id"])
        if not module:
            continue
        fact_data[ability_id] = {
            "module_id": info["module_id"],
            "module_name": info["module_name"],
            "phase": info["phase"],
            **fact_summary(module, info["phase"]),
        }

    return {"fact_data": fact_data}

@router.get("/operations/{op_id}/report")
async def get_operation_report(op_id: str, request: Request, db: Session = Depends(get_db)):
    """Return a finished operation's debrief report (steps, facts, objectives)."""
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    include_output = request.query_params.get("output", "").lower() == "true"

    async with _make_client() as caldera:
        try:
            report = await caldera.get_operation_report(op_id, include_output=include_output)
        except Exception as e:
            return JSONResponse({"error": f"Failed to fetch report: {e}"}, status_code=502)

    return report


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

    import asyncio as _asyncio
    async with _make_client() as caldera:
        try:
            operations = await caldera.list_operations()
            agents = await caldera.list_agents()
            # Fetch chain data for each operation concurrently (list API omits chains)
            ops_with_chain = await _asyncio.gather(
                *[caldera.get_operation(op["id"], include_chain=True) for op in operations],
                return_exceptions=True,
            )
            operations = [op for op in ops_with_chain if not isinstance(op, Exception)]
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
            f = link.get("finish")
            if f and s == 0:
                vm_stats[vm_id]["exploits_succeeded"] += 1
            elif f and s != 0:
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


# ── Red vs Blue Scoreboard ─────────────────────────────────────────────────────

@router.get("/scoreboard")
async def caldera_scoreboard(
    event_id: int | None = None,
    request: Request = None,
    db: Session = Depends(get_db),
):
    """Red vs blue scoreboard.

    Returns per-team breakdown of:
    - Blue defensive score: preapplied module completions
    - Blue reactive score: goal reverts (defend_count × defend_points)
    - Red offensive score: goal achievements (achievement_count × red_points)
    """
    from api.models import Event, Team, VM, VMModule, VMGoal
    from api.services.verifier_account import scoring_enabled_vm_ids

    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    # Build VM filter
    vm_query = db.query(VM)
    if event_id is not None:
        event = db.query(Event).filter(Event.id == event_id).first()
        if not event:
            return JSONResponse({"error": "Event not found"}, status_code=404)
        vm_query = vm_query.filter(VM.event_id == event_id)

    vms = vm_query.all()
    vm_ids = [v.id for v in vms]

    if not vm_ids:
        return {"event_id": event_id, "teams": [], "totals": {"red": 0, "blue_defensive": 0, "blue_reactive": 0, "blue_total": 0}}

    # Load all VMModules for these VMs
    modules = db.query(VMModule).filter(VMModule.vm_id.in_(vm_ids)).all()
    # Load all VMGoals for these VMs
    goals = db.query(VMGoal).filter(VMGoal.vm_id.in_(vm_ids)).all()
    enabled_vm_ids = scoring_enabled_vm_ids(db, vm_ids)

    # Index by vm_id
    vm_modules: dict[int, list[VMModule]] = {}
    for m in modules:
        vm_modules.setdefault(m.vm_id, []).append(m)

    vm_goals: dict[int, list[VMGoal]] = {}
    for g in goals:
        vm_goals.setdefault(g.vm_id, []).append(g)

    # Build per-VM scores
    vm_map = {v.id: v for v in vms}

    def _vm_scores(vm_id: int) -> dict:
        mods = vm_modules.get(vm_id, [])
        gls = vm_goals.get(vm_id, [])
        scoring_enabled = vm_id in enabled_vm_ids

        blue_defensive = sum(
            m.points for m in mods
            if scoring_enabled and m.completed and m.stage == "preapplied"
        )
        blue_reactive = sum(
            g.defend_points * g.defend_count for g in gls if scoring_enabled
        )
        red_offensive = sum(
            g.red_points * g.achievement_count for g in gls if scoring_enabled
        )
        return {
            "vm_id": vm_id,
            "hostname": vm_map[vm_id].hostname,
            "scoring_enabled": scoring_enabled,
            "blue_defensive": blue_defensive,
            "blue_reactive": blue_reactive,
            "blue_total": blue_defensive + blue_reactive,
            "red_offensive": red_offensive,
            "goals": [
                {
                    "module_id": g.module_id,
                    "status": g.status,
                    "achievement_count": g.achievement_count,
                    "defend_count": g.defend_count,
                }
                for g in gls
            ],
        }

    # Group VMs by team
    team_ids = {v.team_id for v in vms}
    teams_data = db.query(Team).filter(Team.id.in_(team_ids)).all()
    team_map = {t.id: t for t in teams_data}

    team_vms: dict[int, list[int]] = {}
    for v in vms:
        team_vms.setdefault(v.team_id, []).append(v.id)

    teams_result = []
    for team_id, t_vm_ids in team_vms.items():
        vm_score_list = [_vm_scores(vid) for vid in t_vm_ids]
        team_blue_def = sum(s["blue_defensive"] for s in vm_score_list)
        team_blue_react = sum(s["blue_reactive"] for s in vm_score_list)
        team_red = sum(s["red_offensive"] for s in vm_score_list)
        teams_result.append({
            "team_id": team_id,
            "team_name": team_map[team_id].name if team_id in team_map else str(team_id),
            "blue_defensive": team_blue_def,
            "blue_reactive": team_blue_react,
            "blue_total": team_blue_def + team_blue_react,
            "red_offensive": team_red,
            "vms": vm_score_list,
        })

    # Sort teams: highest blue total first
    teams_result.sort(key=lambda t: t["blue_total"], reverse=True)

    totals = {
        "red": sum(t["red_offensive"] for t in teams_result),
        "blue_defensive": sum(t["blue_defensive"] for t in teams_result),
        "blue_reactive": sum(t["blue_reactive"] for t in teams_result),
        "blue_total": sum(t["blue_total"] for t in teams_result),
    }

    return {
        "event_id": event_id,
        "teams": teams_result,
        "totals": totals,
    }


# ── Orphaned Operations Cleanup ────────────────────────────────────────────────

@router.post("/operations/cleanup-orphaned")
async def cleanup_orphaned_operations(request: Request, db: Session = Depends(get_db)):
    """Find and delete Caldera operations whose event no longer exists in the database."""
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    existing_event_ids = {row[0] for row in db.query(Event.id).all()}

    orphaned = []
    deleted = 0
    errors = []

    try:
        async with _make_client() as caldera:
            operations = await caldera.list_operations()
            for op in operations:
                group = op.get("group", "")
                if not group.startswith("event-"):
                    continue
                try:
                    event_id = int(group.split("-", 1)[1])
                except (ValueError, IndexError):
                    continue
                if event_id not in existing_event_ids:
                    orphaned.append({
                        "id": op.get("id"),
                        "name": op.get("name"),
                        "group": group,
                        "state": op.get("state"),
                    })
                    try:
                        if op.get("state") in ("running", "paused"):
                            await caldera.update_operation(op["id"], state="finished")
                        await caldera.delete_operation(op["id"])
                        deleted += 1
                    except Exception as e:
                        errors.append(f"Failed to delete {op.get('id')}: {e}")
    except Exception as e:
        errors.append(f"Caldera unavailable: {e}")

    return {
        "orphaned_found": len(orphaned),
        "deleted": deleted,
        "orphaned_operations": orphaned,
        "errors": errors,
    }
