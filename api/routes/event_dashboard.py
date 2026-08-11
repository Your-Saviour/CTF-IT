"""Read-only operational data for an event command centre."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from api.database import get_db
from api.models import Event, Team, User, VM, VMGoal, VMModule
from api.routes.admin import require_admin
from api.services.caldera import CalderaClient, get_caldera_api_key


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
        elif stalled or (vm_status == "active" and not agent_alive):
            health = "degraded"
        elif vm_status == "active" and agent_alive:
            health = "healthy"
        else:
            health = "pending"

        team_name = team_map[vm.team_id].name if vm.team_id in team_map else str(vm.team_id)
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
        elif vm_status == "active" and caldera_available and not agent:
            alerts.append({
                "type": "agent_missing", "severity": "warning", "vm_id": vm.id,
                "team_name": team_name,
                "message": f"{vm.hostname or 'VM ' + str(vm.id)} has no Caldera agent.",
            })

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
        team_modules = [m for m in modules if m.vm_id in team_vm_ids and m.stage == "preapplied"]
        team_goals = [g for g in goals if g.vm_id in team_vm_ids]
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

    preapplied = [m for m in modules if m.stage == "preapplied"]
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
    blue_reactive_total = sum(goal.defend_points * goal.defend_count for goal in goals)
    red_total = sum(goal.red_points * goal.achievement_count for goal in goals)
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
