from __future__ import annotations

import json
import os

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from fastapi.responses import PlainTextResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.database import get_db
from api.models import AdminAudit, HintReveal, Team, User, VerificationAttempt, VM, VMGoal, VMModule
from api.services.authorization import learner_assignment, participant
from api.services.gamenet import site_dns_zone, vm_dns_name
from api.services.secrets import decrypt_secret
from api.services.verification import verify_assignment
from api.services.verifier_account import scoring_enabled_vm_ids
from builder.module_loader import load_all_modules

router = APIRouter(prefix="/api", tags=["learner"])


@router.get("/me/gamenet")
async def gamenet_status(request: Request, db: Session = Depends(get_db)):
    user, error = _denied(request, db)
    if error:
        return error
    gateway = user.team.vpn_gateway
    credential = user.vpn_credential
    return {
        "ready": bool(gateway and gateway.status == "active" and credential and credential.status == "active"),
        "status": gateway.status if gateway else "not_provisioned",
        "configuration_url": "/api/me/gamenet/config" if gateway and gateway.status == "active" else None,
        "resolver_address": gateway.vpn_address if gateway else None,
        "site_dns_zones": [
            {"site_key": site.key, "zone": site_dns_zone(site)}
            for site in sorted(user.team.sites, key=lambda item: item.order)
        ],
    }


@router.get("/me/gamenet/config")
async def download_gamenet_config(request: Request, db: Session = Depends(get_db)):
    user, error = _denied(request, db)
    if error:
        return error
    from api.services.gamenet import render_user_config
    try:
        config = render_user_config(db, user)
    except RuntimeError:
        return JSONResponse({"error": "GameNet VPN is not ready"}, status_code=503,
                            headers={"Cache-Control": "no-store"})
    db.add(AdminAudit(actor_id=user.id, target_user_id=user.id, action="gamenet_config_downloaded",
                      metadata_json=json.dumps({"team_id": user.team_id})))
    db.commit()
    return PlainTextResponse(config, media_type="application/x-wireguard-profile",
                             headers={"Cache-Control": "no-store, private", "Pragma": "no-cache",
                                      "Content-Disposition": 'attachment; filename="gamenet.conf"'})


def enabled() -> bool:
    return os.environ.get("LEARNER_TRAINING_ENABLED", "false").lower() in {"1", "true", "yes"}


def _denied(request: Request, db: Session):
    if not enabled():
        return None, JSONResponse({"error": "learner training is not enabled"}, status_code=404)
    user = participant(request, db)
    if not user:
        return None, JSONResponse({"error": "forbidden"}, status_code=403)
    return user, None


def _library():
    return {module.id: module for module in load_all_modules()
            if not module.disabled and module.stage == "preapplied"
            and module.type in {"vulnerability", "hardening", "payload"}}


def _module_payload(assignment: VMModule, definition, revealed: set[int]) -> dict:
    unlocked = assignment.first_completed_at is not None
    return {
        "id": definition.id,
        "assignment_id": assignment.id,
        "name": definition.name,
        "description": definition.description,
        "learning_objectives": definition.learning_objectives,
        "difficulty": definition.difficulty,
        "points": definition.points,
        "prerequisites": definition.prerequisites,
        "estimated_minutes": definition.estimated_minutes,
        "status": assignment.status,
        "last_verified_at": assignment.last_verified_at.isoformat() if assignment.last_verified_at else None,
        "hints": [{"index": index, "revealed": index in revealed,
                   "text": hint if index in revealed else None} for index, hint in enumerate(definition.hints)],
        "debrief": definition.debrief if unlocked else None,
        "references": [reference.as_dict() for reference in definition.references] if unlocked else [],
    }


def _score(db: Session, user: User, assignments: list[VMModule]) -> dict:
    goals = db.query(VMGoal).join(VM).filter(VM.team_id == user.team_id, VM.event_id == user.event_id).all()
    enabled_ids = scoring_enabled_vm_ids(db, {item.vm_id for item in assignments} | {goal.vm_id for goal in goals})
    defensive = sum(item.points for item in assignments if item.vm_id in enabled_ids and item.status == "completed")
    reactive = sum(goal.defend_points * goal.defend_count for goal in goals if goal.vm_id in enabled_ids)
    total = len(assignments)
    completed = sum(item.vm_id in enabled_ids and item.status == "completed" for item in assignments)
    return {"blue_defensive": defensive, "blue_reactive": reactive,
            "total": defensive + reactive, "completed": completed, "assigned": total,
            "completion_percentage": round(completed * 100 / total, 1) if total else 0}


@router.get("/me/training")
async def my_training(request: Request, db: Session = Depends(get_db)):
    user, error = _denied(request, db)
    if error:
        return error
    definitions = _library()
    vms = db.query(VM).filter(VM.team_id == user.team_id, VM.event_id == user.event_id).order_by(VM.hostname).all()
    vm_ids = [vm.id for vm in vms]
    assignments = db.query(VMModule).filter(
        VMModule.vm_id.in_(vm_ids), VMModule.stage == "preapplied",
        VMModule.module_type.in_(("vulnerability", "hardening", "payload")),
    ).all() if vm_ids else []
    visible = [item for item in assignments if item.module_id in definitions]
    reveal_rows = db.query(HintReveal).filter(
        HintReveal.user_id == user.id,
        HintReveal.module_assignment_id.in_([item.id for item in visible]),
    ).all() if visible else []
    reveals: dict[int, set[int]] = {}
    for row in reveal_rows:
        reveals.setdefault(row.module_assignment_id, set()).add(row.hint_index)
    attempts = db.query(VerificationAttempt).join(VMModule).filter(
        VMModule.vm_id.in_(vm_ids)
    ).order_by(VerificationAttempt.created_at.desc()).limit(20).all() if vm_ids else []
    by_vm: dict[int, list[VMModule]] = {vm.id: [] for vm in vms}
    for item in visible:
        by_vm[item.vm_id].append(item)
    event = user.event
    return {
        "event": {"id": event.id, "name": event.name, "status": event.status,
                  "description": event.description, "ends_at": event.ends_at.isoformat() if event.ends_at else None},
        "team": {"id": user.team.id, "name": user.team.name},
        "vms": [{"id": vm.id, "hostname": vm.hostname, "address": vm.private_ip or vm.ip_address,
                 "dns_name": vm_dns_name(vm),
                 "ssh_port": vm.ssh_port or 22, "status": vm.status,
                 "connection_command": f"ssh -p {vm.ssh_port or 22} ctf-trainee@{vm_dns_name(vm) or vm.private_ip or vm.ip_address}",
                 "modules": [_module_payload(item, definitions[item.module_id], reveals.get(item.id, set()))
                             for item in sorted(by_vm[vm.id], key=lambda value: value.id)]} for vm in vms],
        "score": _score(db, user, visible),
        "regressions": sum(item.status == "regressed" for item in visible),
        "recent_activity": [{"module_assignment_id": attempt.module_assignment_id,
                             "result": attempt.result, "summary": attempt.safe_summary,
                             "trigger": attempt.trigger_type, "created_at": attempt.created_at.isoformat()}
                            for attempt in attempts],
        "read_only": event.status != "open",
    }


@router.get("/me/team-access")
async def team_access(request: Request, db: Session = Depends(get_db)):
    user, error = _denied(request, db)
    if error:
        return error
    credential = user.team.training_credential
    if not credential or credential.status != "active":
        return JSONResponse({"error": "team credentials are not ready"}, status_code=503,
                            headers={"Cache-Control": "no-store"})
    vms = db.query(VM).filter(VM.team_id == user.team_id, VM.event_id == user.event_id).all()
    db.add(AdminAudit(actor_id=user.id, target_user_id=user.id, action="team_credential_revealed",
                      metadata_json=json.dumps({"team_id": user.team_id})))
    db.commit()
    return JSONResponse({
        "username": credential.username,
        "private_key": decrypt_secret(credential.private_key_encrypted),
        "public_key": credential.public_key,
        "sudo_password": decrypt_secret(credential.sudo_password_encrypted),
        "connections": [{"vm_id": vm.id, "hostname": vm.hostname,
                         "dns_name": vm_dns_name(vm), "address": vm.private_ip or vm.ip_address,
                         "command": f"ssh -i ./ctf-team-key -p {vm.ssh_port or 22} {credential.username}@{vm_dns_name(vm) or vm.private_ip or vm.ip_address}"}
                        for vm in vms],
    }, headers={"Cache-Control": "no-store, private", "Pragma": "no-cache"})


@router.post("/vms/{vm_id}/modules/{module_id}/verify")
async def verify(vm_id: int, module_id: str, request: Request, db: Session = Depends(get_db)):
    user, error = _denied(request, db)
    if error:
        return error
    if user.event.status != "open":
        return JSONResponse({"error": "event is read-only"}, status_code=409)
    assignment = learner_assignment(db, user, vm_id, module_id)
    definition = _library().get(module_id)
    if not assignment or not definition:
        return JSONResponse({"error": "module not found"}, status_code=404)
    result = await verify_assignment(db, assignment, definition.verification, "learner", user)
    return {"result": result.result, "summary": result.summary, "error_code": result.error_code,
            "status": assignment.status, "score": _score(db, user, db.query(VMModule).join(VM).filter(
                VM.team_id == user.team_id, VM.event_id == user.event_id, VMModule.stage == "preapplied").all())}


@router.post("/vms/{vm_id}/modules/{module_id}/hints/{index}/reveal")
async def reveal_hint(vm_id: int, module_id: str, index: int, request: Request, db: Session = Depends(get_db)):
    user, error = _denied(request, db)
    if error:
        return error
    if user.event.status != "open":
        return JSONResponse({"error": "event is read-only"}, status_code=409)
    assignment = learner_assignment(db, user, vm_id, module_id)
    definition = _library().get(module_id)
    if not assignment or not definition:
        return JSONResponse({"error": "module not found"}, status_code=404)
    if index < 0 or index >= len(definition.hints):
        return JSONResponse({"error": "hint not found"}, status_code=404)
    existing = db.query(HintReveal).filter_by(user_id=user.id, module_assignment_id=assignment.id, hint_index=index).first()
    if index > 0 and not db.query(HintReveal).filter_by(
        user_id=user.id, module_assignment_id=assignment.id, hint_index=index - 1
    ).first():
        return JSONResponse({"error": "reveal the previous hint first"}, status_code=409)
    if not existing:
        db.add(HintReveal(user_id=user.id, module_assignment_id=assignment.id, hint_index=index))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
    return {"index": index, "text": definition.hints[index], "points_penalty": 0}


@router.get("/scoreboard")
async def scoreboard(request: Request, db: Session = Depends(get_db)):
    user, error = _denied(request, db)
    if error:
        return error
    teams = db.query(Team).filter(Team.event_id == user.event_id).all()
    rows = []
    for team in teams:
        modules = db.query(VMModule).join(VM).filter(VM.team_id == team.id, VM.event_id == user.event_id,
            VMModule.stage == "preapplied").all()
        goals = db.query(VMGoal).join(VM).filter(VM.team_id == team.id, VM.event_id == user.event_id).all()
        enabled_ids = scoring_enabled_vm_ids(db, {module.vm_id for module in modules} | {goal.vm_id for goal in goals})
        defensive = sum(module.points for module in modules if module.vm_id in enabled_ids and module.status == "completed")
        reactive = sum(goal.defend_points * goal.defend_count for goal in goals if goal.vm_id in enabled_ids)
        red = sum(goal.red_points * goal.achievement_count for goal in goals if goal.vm_id in enabled_ids)
        completed = sum(module.vm_id in enabled_ids and module.status == "completed" for module in modules)
        rows.append({"team_id": team.id, "team_name": team.name, "blue_defensive": defensive,
                     "blue_reactive": reactive, "total_score": defensive + reactive,
                     "completion_percentage": round(completed * 100 / len(modules), 1) if modules else 0,
                     "red_team_pressure": red})
    rows.sort(key=lambda row: (-row["total_score"], -row["completion_percentage"], row["team_name"].lower()))
    for rank, row in enumerate(rows, 1):
        row["rank"] = rank
    return {"event": {"id": user.event.id, "name": user.event.name, "status": user.event.status},
            "current_team_id": user.team_id, "teams": rows}
