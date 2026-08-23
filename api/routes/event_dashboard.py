"""Read-only operational data for an event command centre."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from api.database import get_db
from api.models import (
    Event, EventIntegration, GreenDeploymentState, HintReveal, IntegrationDestination,
    Team, User, VerificationAttempt, VM, VMGoal, VMModule,
)
from api.routes.admin import require_admin
from api.services.caldera import CalderaClient, get_caldera_api_key
from api.services.verifier_account import scoring_enabled_vm_ids


router = APIRouter(prefix="/admin/api/events", tags=["admin"])


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _agent_alive(agent: dict | None) -> bool:
    if not agent:
        return False
    status = str(agent.get("status") or "").lower()
    return status not in {"dead", "failed", "inactive", "untrusted"} and agent.get("trusted") is not False


def _make_client() -> CalderaClient:
    return CalderaClient(get_caldera_api_key())


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def _analytics_metrics(assignments, attempts_by_assignment, hinted_ids, event_start):
    completed = [item for item in assignments if item.status == "completed" and item.first_completed_at is not None]
    completion_minutes = []
    start = _utc(event_start)
    if start:
        for item in completed:
            finished = _utc(item.first_completed_at)
            if finished and finished >= start:
                completion_minutes.append((finished - start).total_seconds() / 60)

    attempts = [attempt for item in assignments for attempt in attempts_by_assignment.get(item.id, [])]
    learner_attempts = [attempt for attempt in attempts if attempt.trigger_type == "learner"]
    learner_failures = sum(attempt.result == "fail" for attempt in learner_attempts)
    operational_errors = sum(attempt.result in {"invalid", "unavailable"} for attempt in attempts)
    regression_count = 0
    currently_regressed = 0
    for item in assignments:
        state = False
        regressed = False
        first_completed = _utc(item.first_completed_at)
        completion_seeded = False
        for attempt in attempts_by_assignment.get(item.id, []):
            created = _utc(attempt.created_at)
            if not completion_seeded and first_completed and created and first_completed <= created:
                state = True
                completion_seeded = True
            if attempt.result == "pass":
                state, regressed = True, False
            elif attempt.trigger_type == "periodic" and attempt.result == "fail" and state and not regressed:
                regression_count += 1
                state, regressed = False, True
        currently_regressed += int(regressed or item.status == "regressed")

    assigned = len(assignments)
    hint_assisted = sum(item.id in hinted_ids for item in assignments)
    mean = sum(completion_minutes) / len(completion_minutes) if completion_minutes else None
    return {
        "assigned_exercises": assigned,
        "completed_exercises": len(completed),
        "completion_percentage": round(len(completed) * 100 / assigned, 1) if assigned else 0,
        "mean_completion_minutes": round(mean, 1) if mean is not None else None,
        "median_completion_minutes": round(_median(completion_minutes), 1) if completion_minutes else None,
        "learner_verification_attempts": len(learner_attempts),
        "learner_failures": learner_failures,
        "failure_rate": round(learner_failures * 100 / len(learner_attempts), 1) if learner_attempts else 0,
        "hint_reveals": sum(len({row.hint_index for row in hinted_ids.get(item.id, [])}) for item in assignments) if isinstance(hinted_ids, dict) else 0,
        "hint_assisted_assignments": hint_assisted if not isinstance(hinted_ids, dict) else sum(item.id in hinted_ids for item in assignments),
        "hint_use_rate": round(hint_assisted * 100 / assigned, 1) if assigned and not isinstance(hinted_ids, dict) else 0,
        "regression_count": regression_count,
        "currently_regressed_assignments": currently_regressed,
        "operational_verification_errors": operational_errors,
    }


@router.get("/{event_id}/training-analytics")
async def training_analytics(event_id: int, request: Request, db: Session = Depends(get_db)):
    if not require_admin(request, db):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    event = db.get(Event, event_id)
    if not event:
        return JSONResponse({"error": "Event not found"}, status_code=404)

    teams = db.query(Team).filter(Team.event_id == event_id).all()
    vms = db.query(VM).filter(VM.event_id == event_id).all()
    vm_map = {vm.id: vm for vm in vms}
    assignments = db.query(VMModule).filter(
        VMModule.vm_id.in_(vm_map), VMModule.stage == "preapplied"
    ).all() if vm_map else []
    assignment_ids = [item.id for item in assignments]
    attempts = db.query(VerificationAttempt).filter(
        VerificationAttempt.module_assignment_id.in_(assignment_ids)
    ).order_by(VerificationAttempt.module_assignment_id, VerificationAttempt.created_at, VerificationAttempt.id).all() if assignment_ids else []
    reveals = db.query(HintReveal).filter(HintReveal.module_assignment_id.in_(assignment_ids)).all() if assignment_ids else []
    attempts_by_assignment = {}
    for attempt in attempts:
        attempts_by_assignment.setdefault(attempt.module_assignment_id, []).append(attempt)
    reveals_by_assignment = {}
    for reveal in reveals:
        reveals_by_assignment.setdefault(reveal.module_assignment_id, []).append(reveal)

    def metrics(items):
        result = _analytics_metrics(items, attempts_by_assignment, set(reveals_by_assignment), event.started_at)
        result["hint_reveals"] = sum(len({row.hint_index for row in reveals_by_assignment.get(item.id, [])}) for item in items)
        result["hint_assisted_assignments"] = sum(item.id in reveals_by_assignment for item in items)
        result["hint_use_rate"] = round(result["hint_assisted_assignments"] * 100 / len(items), 1) if items else 0
        return result

    try:
        from builder.module_loader import load_all_modules
        names = {module.id: module.name for module in load_all_modules()}
    except Exception:
        names = {}
    team_rows = []
    for team in teams:
        items = [item for item in assignments if vm_map[item.vm_id].team_id == team.id]
        team_rows.append({"team_id": team.id, "team_name": team.name, **metrics(items)})
    team_rows.sort(key=lambda row: row["team_name"].lower())

    module_rows = []
    for module_id in sorted({item.module_id for item in assignments}):
        items = [item for item in assignments if item.module_id == module_id]
        module_rows.append({"module_id": module_id, "module_name": names.get(module_id, module_id), **metrics(items)})
    module_rows.sort(key=lambda row: (-row["learner_failures"] / row["assigned_exercises"], -row["hint_use_rate"], row["completion_percentage"], row["module_name"].lower()))
    return {"summary": metrics(assignments), "teams": team_rows, "modules": module_rows,
            "generated_at": datetime.now(timezone.utc).isoformat()}


@router.get("/{event_id}/dashboard-data")
async def event_dashboard_data(
    event_id: int, request: Request, db: Session = Depends(get_db)
):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        return JSONResponse({"error": "Event not found"}, status_code=404)

    now = datetime.now(timezone.utc)
    teams = db.query(Team).filter(Team.event_id == event_id).all()
    vms = db.query(VM).filter(VM.event_id == event_id).all()
    vm_ids = [vm.id for vm in vms]
    modules = db.query(VMModule).filter(VMModule.vm_id.in_(vm_ids)).all() if vm_ids else []
    goals = db.query(VMGoal).filter(VMGoal.vm_id.in_(vm_ids)).all() if vm_ids else []
    enabled_vm_ids = scoring_enabled_vm_ids(db, vm_ids)
    scoring_modules = [module for module in modules if module.vm_id in enabled_vm_ids]
    scoring_goals = [goal for goal in goals if goal.vm_id in enabled_vm_ids]

    try:
        from builder.module_loader import load_all_modules
        module_names = {module.id: module.name for module in load_all_modules()}
    except Exception:
        # Historical assignments must remain visible even if the module library
        # has a malformed or subsequently removed definition.
        module_names = {}

    caldera_available = True
    caldera_error = None
    agents: list[dict] = []
    try:
        async with _make_client() as caldera:
            agents = await caldera.list_agents()
    except Exception as exc:
        caldera_available = False
        caldera_error = str(exc)

    agents_by_ip: dict[str, dict] = {}
    for agent in agents:
        for ip in agent.get("host_ip_addrs", []) or []:
            agents_by_ip[ip] = agent

    team_map = {team.id: team for team in teams}
    vm_map = {vm.id: vm for vm in vms}
    green_states = {row.vm_id: row for row in db.query(GreenDeploymentState).filter(
        GreenDeploymentState.vm_id.in_(vm_ids),
    ).order_by(GreenDeploymentState.id).all()} if vm_ids else {}
    green_bindings = {row.destination.owner_green_vm_id: row for row in db.query(EventIntegration).join(
        IntegrationDestination,
    ).filter(EventIntegration.event_id == event_id,
             IntegrationDestination.owner_green_vm_id.is_not(None)).all()}
    modules_by_vm: dict[int, list[VMModule]] = {}
    goals_by_vm: dict[int, list[VMGoal]] = {}
    for module in modules:
        modules_by_vm.setdefault(module.vm_id, []).append(module)
    for goal in goals:
        goals_by_vm.setdefault(goal.vm_id, []).append(goal)

    alerts = []
    if not caldera_available:
        alerts.append({
            "type": "caldera_unavailable", "severity": "warning",
            "message": "Caldera is unavailable; agent health is degraded.",
            "detail": caldera_error,
        })

    vm_results = []
    for vm in vms:
        assigned = [m for m in modules_by_vm.get(vm.id, []) if m.stage == "preapplied"]
        completed = sum(1 for m in assigned if m.completed)
        agent = agents_by_ip.get(vm.ip_address) if vm.ip_address else None
        is_green = vm.role == "green_service"
        agent_alive = _agent_alive(agent) if caldera_available else False
        vm_status = (vm.status or "registered").lower()
        stored_agent_status = (vm.agent_status or "").lower()
        stalled = (
            vm_status in {"creating", "provisioning"}
            and _utc(vm.updated_at) is not None
            and (now - _utc(vm.updated_at)).total_seconds() > 600
        )

        if vm_status == "failed" or stored_agent_status == "failed" or str((agent or {}).get("status", "")).lower() == "failed":
            health = "failed"
        elif stalled or (vm_status == "active" and not is_green and not agent_alive):
            health = "degraded"
        elif vm_status == "active" and (is_green or agent_alive):
            health = "healthy"
        else:
            health = "pending"

        team_name = "Shared green infrastructure" if is_green else (
            team_map[vm.team_id].name if vm.team_id in team_map else str(vm.team_id)
        )
        if health == "failed":
            alerts.append({
                "type": "vm_failed", "severity": "critical", "vm_id": vm.id,
                "team_name": team_name, "message": f"{vm.hostname or 'VM ' + str(vm.id)} has failed.",
                "detail": vm.provision_error,
            })
        elif stalled:
            alerts.append({
                "type": "provisioning_stalled", "severity": "warning", "vm_id": vm.id,
                "team_name": team_name,
                "message": f"{vm.hostname or 'VM ' + str(vm.id)} provisioning has stalled for more than 10 minutes.",
                "detail": vm.provision_step,
            })
        elif vm_status == "active" and not is_green and caldera_available and not agent:
            alerts.append({
                "type": "agent_missing", "severity": "warning", "vm_id": vm.id,
                "team_name": team_name,
                "message": f"{vm.hostname or 'VM ' + str(vm.id)} has no Caldera agent.",
            })

        green_state = green_states.get(vm.id)
        green_binding = green_bindings.get(vm.id)
        vm_results.append({
            "id": vm.id,
            "hostname": vm.hostname,
            "ip_address": vm.ip_address,
            "team_id": vm.team_id,
            "team_name": team_name,
            "status": vm.status,
            "health": health,
            "provision_step": vm.provision_step,
            "provision_error": vm.provision_error,
            "ownership": "event" if is_green else "team",
            "green_key": vm.green_key,
            "resolved_commit": green_state.resolved_commit if green_state else None,
            "service_url": green_state.service_url if green_state else None,
            "service_health": green_state.health_status if green_state else None,
            "integration_status": green_binding.last_status if green_binding and green_binding.last_status else (
                "configured" if green_binding and green_binding.enabled else None
            ),
            "module_progress": {
                "assigned": len(assigned), "completed": completed,
                "percentage": round(completed * 100 / len(assigned), 1) if assigned else 0,
            },
            "caldera_agent": {
                "available": caldera_available,
                "present": agent is not None,
                "alive": agent_alive,
                "paw": agent.get("paw") if agent else None,
                "status": agent.get("status") if agent else None,
                "last_seen": agent.get("last_seen") if agent else None,
            },
        })

    vm_results.sort(key=lambda item: (item["team_name"].lower(), (item["hostname"] or "").lower(), item["id"]))

    team_results = []
    for team in teams:
        team_vm_ids = [vm.id for vm in vms if vm.team_id == team.id]
        team_modules = [m for m in scoring_modules if m.vm_id in team_vm_ids and m.stage == "preapplied"]
        team_goals = [g for g in scoring_goals if g.vm_id in team_vm_ids]
        completed = sum(1 for m in team_modules if m.completed)
        blue_defensive = sum(m.points for m in team_modules if m.completed)
        blue_reactive = sum(g.defend_points * g.defend_count for g in team_goals)
        red_offensive = sum(g.red_points * g.achievement_count for g in team_goals)
        team_results.append({
            "team_id": team.id, "team_name": team.name, "vm_count": len(team_vm_ids),
            "assigned_modules": len(team_modules), "completed_modules": completed,
            "completion_percentage": round(completed * 100 / len(team_modules), 1) if team_modules else 0,
            "blue_defensive": blue_defensive, "blue_reactive": blue_reactive,
            "blue_total": blue_defensive + blue_reactive, "red_offensive": red_offensive,
        })
    team_results.sort(key=lambda item: (-item["blue_total"], item["team_name"].lower()))

    preapplied = [m for m in scoring_modules if m.stage == "preapplied"]
    bottleneck_groups: dict[str, list[VMModule]] = {}
    for module in preapplied:
        bottleneck_groups.setdefault(module.module_id, []).append(module)
    bottlenecks = []
    for module_id, assignments in bottleneck_groups.items():
        complete = sum(1 for assignment in assignments if assignment.completed)
        bottlenecks.append({
            "module_id": module_id,
            "module_name": module_names.get(module_id, module_id),
            "assigned": len(assignments), "completed": complete,
            "completion_percentage": round(complete * 100 / len(assignments), 1),
        })
    bottlenecks.sort(key=lambda item: (item["completion_percentage"], item["module_name"].lower()))

    activity = []
    for module in modules:
        if module.completed and module.completed_at:
            vm = vm_map.get(module.vm_id)
            if vm:
                activity.append({
                    "type": "module_completed", "timestamp": _iso(module.completed_at),
                    "module_id": module.module_id,
                    "module_name": module_names.get(module.module_id, module.module_id),
                    "vm_id": vm.id, "hostname": vm.hostname,
                    "team_id": vm.team_id,
                    "team_name": team_map[vm.team_id].name if vm.team_id in team_map else str(vm.team_id),
                })
    for goal in goals:
        vm = vm_map.get(goal.vm_id)
        if not vm:
            continue
        common = {
            "module_id": goal.module_id,
            "module_name": module_names.get(goal.module_id, goal.module_id),
            "vm_id": vm.id, "hostname": vm.hostname, "team_id": vm.team_id,
            "team_name": team_map[vm.team_id].name if vm.team_id in team_map else str(vm.team_id),
        }
        if goal.achieved_at:
            activity.append({**common, "type": "goal_achieved", "timestamp": _iso(goal.achieved_at)})
        if goal.defended_at:
            activity.append({**common, "type": "goal_defended", "timestamp": _iso(goal.defended_at)})
    activity.sort(key=lambda item: item["timestamp"], reverse=True)

    completed_total = sum(1 for module in preapplied if module.completed)
    blue_defensive_total = sum(module.points for module in preapplied if module.completed)
    blue_reactive_total = sum(goal.defend_points * goal.defend_count for goal in scoring_goals)
    red_total = sum(goal.red_points * goal.achievement_count for goal in scoring_goals)
    ends_at = _utc(event.ends_at)

    return {
        "event": {
            "id": event.id, "name": event.name, "status": event.status,
            "started_at": _iso(event.started_at), "ends_at": _iso(event.ends_at),
            "remaining_seconds": max(0, int((ends_at - now).total_seconds())) if ends_at else None,
        },
        "refreshed_at": now.isoformat(),
        "health": {"caldera_available": caldera_available, "caldera_error": caldera_error},
        "summary": {
            "participants": db.query(User).filter(User.event_id == event_id, User.is_admin.is_(False)).count(),
            "teams": len(teams), "vms": len(vms),
            "assigned_modules": len(preapplied), "completed_modules": completed_total,
            "completion_percentage": round(completed_total * 100 / len(preapplied), 1) if preapplied else 0,
            "red_offensive": red_total, "blue_defensive": blue_defensive_total,
            "blue_reactive": blue_reactive_total,
            "blue_total": blue_defensive_total + blue_reactive_total,
        },
        "teams": team_results,
        "modules": bottlenecks,
        "vms": vm_results,
        "alerts": alerts,
        "activity": activity[:20],
    }
