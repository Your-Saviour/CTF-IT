import asyncio
import json
import os
import secrets
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urljoin

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.database import get_db
from api.models import AccountToken, AdminAudit, Event, OpnsenseImage, PlatformSettings, Team, User, VerificationAttempt, VM, VMModule, utcnow
from api.routes.auth import _token_digest, get_current_user

router = APIRouter(prefix="/admin/api", tags=["admin"])


def _utc_instant(value) -> datetime:
    """Parse a revision timestamp and normalize it for semantic comparison."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        raise ValueError("revision must be an ISO-8601 timestamp")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def _live_vpc_counts() -> dict[str, int]:
    """Return current Vultr VPC consumption by location for capacity gating."""
    key = os.environ.get("VULTR_API_KEY")
    if not key:
        return {}
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get("https://api.vultr.com/v2/vpcs", params={"per_page": 500},
                                    headers={"Authorization": f"Bearer {key}"})
    response.raise_for_status()
    return dict(Counter(vpc.get("region") for vpc in response.json().get("vpcs", []) if vpc.get("region")))


class PlanPreviewRequest(BaseModel):
    quota: Optional[dict] = None
    infrastructure: Optional[dict] = None


class UserUpdateRequest(BaseModel):
    role: Optional[str] = None
    event_id: Optional[int] = None
    team_id: Optional[int] = None


class ActivationRequest(BaseModel):
    active: bool


class InvitationRequest(BaseModel):
    event_id: int
    team_id: Optional[int] = None
    intended_username: Optional[str] = None
    role: str = "participant"


class TeamAssignmentRequest(BaseModel):
    user_id: int


class BulkVerificationRequest(BaseModel):
    event_id: int
    team_id: Optional[int] = None


class OpnsenseSyncRequest(BaseModel):
    version: str


class OpnsenseRetireRequest(BaseModel):
    delete_artifacts: bool = False
    confirm: bool = False


def require_admin(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return None
    return user


def _audit(db: Session, actor: User, action: str, target: User | None = None, **metadata):
    db.add(AdminAudit(
        actor_id=actor.id,
        target_user_id=target.id if target else None,
        action=action,
        metadata_json=json.dumps(metadata, sort_keys=True) if metadata else None,
    ))


def _active_admin_count(db: Session) -> int:
    return db.query(User).filter(User.is_admin.is_(True), User.active.is_(True)).count()


def _run_image_job(image_id: int, operation: str):
    from api.database import SessionLocal
    from api.services.opnsense_images import run_image_build
    session = SessionLocal()
    try:
        run_image_build(session, image_id)
    finally:
        session.close()


@router.get("/opnsense-images")
async def list_opnsense_images(request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    from api.services.opnsense_images import image_payload
    rows = db.query(OpnsenseImage).order_by(OpnsenseImage.created_at.desc()).all()
    return [image_payload(row) for row in rows]


@router.post("/opnsense-images/sync")
async def sync_opnsense_image(body: OpnsenseSyncRequest, request: Request, background: BackgroundTasks,
                              db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    from api.services.opnsense_images import ImageWorkflowError, new_image
    try:
        image = new_image(db, body.version)
    except (ValueError, ImageWorkflowError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=409 if isinstance(exc, ImageWorkflowError) else 422)
    _audit(db, admin, "opnsense_image_sync", image_id=image.id, version=image.version); db.commit()
    background.add_task(_run_image_job, image.id, "sync")
    return JSONResponse({"id": image.id, "status": image.status}, status_code=202)


@router.post("/opnsense-images/{image_id}/resume")
async def resume_opnsense_image(image_id: int, request: Request, background: BackgroundTasks,
                                db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    image = db.get(OpnsenseImage, image_id)
    if not image:
        return JSONResponse({"error": "image not found"}, status_code=404)
    if image.status not in {"interrupted", "failed"}:
        return JSONResponse({"error": "only an interrupted or failed job can be resumed"}, status_code=409)
    _audit(db, admin, "opnsense_image_resume", image_id=image.id, phase=image.phase); db.commit()
    background.add_task(_run_image_job, image.id, "resume")
    return JSONResponse({"id": image.id, "status": "resuming"}, status_code=202)


@router.post("/opnsense-images/{image_id}/activate")
async def activate_opnsense_image(image_id: int, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    image = db.get(OpnsenseImage, image_id)
    if not image:
        return JSONResponse({"error": "image not found"}, status_code=404)
    if image.status not in {"ready", "active"} or not image.validated_at or not image.snapshot_id:
        return JSONResponse({"error": "only a validated ready image can be activated"}, status_code=409)
    for old in db.query(OpnsenseImage).filter(OpnsenseImage.status == "active", OpnsenseImage.id != image.id):
        old.status = old.phase = "ready"; old.activated_at = None
    setting = db.query(PlatformSettings).filter_by(key="active_opnsense_image_id").first()
    if not setting:
        setting = PlatformSettings(key="active_opnsense_image_id", value=str(image.id)); db.add(setting)
    else:
        setting.value = str(image.id)
    image.status = image.phase = "active"; image.activated_at = utcnow()
    _audit(db, admin, "opnsense_image_activate", image_id=image.id, snapshot_id=image.snapshot_id); db.commit()
    return {"id": image.id, "status": image.status}


@router.post("/opnsense-images/{image_id}/retire")
async def retire_opnsense_image(image_id: int, body: OpnsenseRetireRequest, request: Request,
                                db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    image = db.get(OpnsenseImage, image_id)
    if not image:
        return JSONResponse({"error": "image not found"}, status_code=404)
    if body.delete_artifacts and not body.confirm:
        return JSONResponse({"error": "artifact deletion requires confirm=true"}, status_code=409)
    if body.delete_artifacts and db.query(VM).filter_by(opnsense_image_id=image.id).first():
        return JSONResponse({"error": "artifacts cannot be deleted while firewall records reference this image"}, status_code=409)
    if image.status in {"creating_builder", "bootstrapping", "validating", "snapshotting"}:
        return JSONResponse({"error": "a running job cannot be retired"}, status_code=409)
    setting = db.query(PlatformSettings).filter_by(key="active_opnsense_image_id").first()
    if setting and setting.value == str(image.id):
        db.delete(setting)
    if body.delete_artifacts:
        from api.services.opnsense_images import VultrImageClient, cleanup_local, cleanup_remote
        client = VultrImageClient()
        try:
            cleanup_remote(image, client, preserve_snapshot=False)
        finally:
            client.close()
        cleanup_local(image)
    image.status = image.phase = "retired"; image.retired_at = utcnow(); image.builder_config_token = None
    _audit(db, admin, "opnsense_image_retire", image_id=image.id, artifacts_deleted=body.delete_artifacts); db.commit()
    return {"id": image.id, "status": image.status}


def _user_payload(user: User, audit_summary=None):
    return {
        "id": user.id,
        "username": user.username,
        "is_admin": user.is_admin,
        "role": "administrator" if user.is_admin else "participant",
        "active": user.active,
        "event_id": user.event_id,
        "event_name": user.event.name if user.event else None,
        "team_id": user.team_id,
        "team_name": user.team.name if user.team else None,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "updated_at": user.updated_at.isoformat() if user.updated_at else None,
        "deactivated_at": user.deactivated_at.isoformat() if user.deactivated_at else None,
        "audit_summary": audit_summary,
    }


@router.get("/overview")
async def overview(request: Request, db: Session = Depends(get_db)):
    """Aggregate stored operational health without blocking on external services."""
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    events = db.query(Event).order_by(Event.created_at.desc()).all()
    vms = db.query(VM).all()
    users = db.query(User).all()
    event_counts = {state: sum(e.status == state for e in events)
                    for state in ("draft", "provisioning", "provision_failed", "open", "stopped")}
    vm_states = sorted({vm.status or "unknown" for vm in vms})
    agent_states = sorted({vm.agent_status or "not_deployed" for vm in vms})
    attention = []
    for event in events:
        if event.status == "provision_failed":
            attention.append({
                "severity": "critical", "kind": "provisioning",
                "message": f"{event.name} provisioning failed",
                "href": f"/admin/events/{event.id}/dashboard",
            })
    for vm in vms:
        if vm.status == "failed" or vm.provision_error:
            attention.append({
                "severity": "critical", "kind": "provisioning",
                "message": f"{vm.hostname or 'VM ' + str(vm.id)} requires provisioning attention",
                "href": f"/admin/infrastructure/vms/{vm.id}",
            })
        elif vm.agent_status == "failed":
            attention.append({
                "severity": "warning", "kind": "agent",
                "message": f"Agent is unhealthy on {vm.hostname or 'VM ' + str(vm.id)}",
                "href": f"/admin/infrastructure/vms/{vm.id}",
            })
    now = utcnow()
    active_events = []
    for event in events:
        if event.status == "open":
            if event.ends_at and event.ends_at <= now:
                attention.append({
                    "severity": "critical", "kind": "deadline",
                    "message": f"{event.name} has passed its deadline",
                    "href": f"/admin/events/{event.id}/dashboard",
                })
            active_events.append({
                "id": event.id, "name": event.name, "status": event.status,
                "started_at": event.started_at.isoformat() if event.started_at else None,
                "ends_at": event.ends_at.isoformat() if event.ends_at else None,
                "team_count": len(event.teams), "vm_count": len(event.vms),
                "user_count": len(event.users),
            })
    recent = db.query(AdminAudit).order_by(AdminAudit.created_at.desc()).limit(12).all()
    actor_ids = {entry.actor_id for entry in recent if entry.actor_id}
    actors = {u.id: u.username for u in db.query(User).filter(User.id.in_(actor_ids)).all()} if actor_ids else {}
    return {
        "counts": {
            "events": {"total": len(events), **event_counts},
            "users": {"total": len(users), "active": sum(u.active for u in users),
                      "administrators": sum(u.is_admin for u in users)},
            "vms": {"total": len(vms), **{state: sum((vm.status or "unknown") == state for vm in vms) for state in vm_states}},
        },
        "active_events": active_events,
        "provisioning": {state: sum((vm.status or "unknown") == state for vm in vms) for state in vm_states},
        "agents": {state: sum((vm.agent_status or "not_deployed") == state for vm in vms) for state in agent_states},
        "attention": attention,
        "recent_activity": [{
            "id": entry.id, "action": entry.action,
            "actor": actors.get(entry.actor_id, "system"),
            "created_at": entry.created_at.isoformat(),
        } for entry in recent],
    }


@router.get("/users")
async def list_users(
    request: Request,
    q: Optional[str] = None,
    role: Optional[str] = None,
    active: Optional[bool] = None,
    event_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    query = db.query(User)
    if q:
        # Sanitize search query and limit length to prevent injection
        sanitized = (q.strip() or "")[:64]
        if sanitized:
            query = query.filter(User.username.ilike(f"%{sanitized}%"))
    if role == "administrator":
        query = query.filter(User.is_admin.is_(True))
    elif role == "participant":
        query = query.filter(User.is_admin.is_(False))
    if active is not None:
        query = query.filter(User.active.is_(active))
    if event_id is not None:
        query = query.filter(User.event_id == event_id)
    users = query.order_by(User.username).all()
    result = []
    for user in users:
        latest = db.query(AdminAudit).filter(
            AdminAudit.target_user_id == user.id
        ).order_by(AdminAudit.created_at.desc()).first()
        summary = ({
            "action": latest.action,
            "created_at": latest.created_at.isoformat(),
        } if latest else None)
        result.append(_user_payload(user, summary))
    return result


@router.patch("/users/{user_id}")
async def update_user(
    user_id: int, body: UserUpdateRequest, request: Request, db: Session = Depends(get_db)
):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        return JSONResponse({"error": "user not found"}, status_code=404)
    if body.role not in {None, "participant", "administrator"}:
        return JSONResponse({"error": "invalid role"}, status_code=422)
    event_was_supplied = "event_id" in body.model_fields_set
    team_was_supplied = "team_id" in body.model_fields_set
    if event_was_supplied and body.event_id is not None and not db.query(Event).filter(Event.id == body.event_id).first():
        return JSONResponse({"error": "event not found"}, status_code=404)

    new_admin = target.is_admin if body.role is None else body.role == "administrator"
    if target.id == admin.id and target.is_admin and not new_admin:
        return JSONResponse({"error": "administrators cannot demote themselves"}, status_code=409)
    if target.is_admin and not new_admin and target.active and _active_admin_count(db) <= 1:
        return JSONResponse({"error": "the final active administrator cannot be demoted"}, status_code=409)

    old_role = "administrator" if target.is_admin else "participant"
    old_event_id = target.event_id
    new_event_id = body.event_id if event_was_supplied else target.event_id
    new_team_id = body.team_id if team_was_supplied else target.team_id
    if event_was_supplied and new_event_id != target.event_id and not team_was_supplied:
        new_team_id = None
    if new_team_id is not None:
        team = db.query(Team).filter(Team.id == new_team_id).first()
        if not team:
            return JSONResponse({"error": "team not found"}, status_code=404)
        if team.event_id != new_event_id:
            return JSONResponse({"error": "team does not belong to the selected event"}, status_code=422)
    clearing_for_event_change = event_was_supplied and new_event_id != target.event_id and not team_was_supplied
    if not new_admin and new_team_id is None and not clearing_for_event_change:
        return JSONResponse({"error": "participants must belong to a team"}, status_code=422)
    changed = new_admin != target.is_admin or new_event_id != target.event_id or new_team_id != target.team_id
    target.is_admin = new_admin
    target.event_id = new_event_id
    target.team_id = None if new_admin else new_team_id
    if changed:
        from api.services.gamenet import ensure_user_vpn_credential, revoke_user_vpn
        revoke_user_vpn(db, target.id)
        if not target.is_admin and target.team_id:
            ensure_user_vpn_credential(db, target)
        target.session_version += 1
        target.updated_at = utcnow()
        _audit(
            db, admin, "user_access_updated", target,
            old_role=old_role, new_role=body.role or old_role,
            old_event_id=old_event_id, new_event_id=new_event_id, new_team_id=target.team_id,
        )
        db.commit()
    return _user_payload(target)


@router.patch("/users/{user_id}/activation")
async def set_user_activation(
    user_id: int, body: ActivationRequest, request: Request, db: Session = Depends(get_db)
):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        return JSONResponse({"error": "user not found"}, status_code=404)
    if not body.active and target.id == admin.id:
        return JSONResponse({"error": "administrators cannot deactivate themselves"}, status_code=409)
    if not body.active and target.is_admin and target.active and _active_admin_count(db) <= 1:
        return JSONResponse({"error": "the final active administrator cannot be deactivated"}, status_code=409)
    if target.active != body.active:
        target.active = body.active
        target.deactivated_at = None if body.active else utcnow()
        target.updated_at = utcnow()
        target.session_version += 1
        if not body.active:
            from api.services.gamenet import revoke_user_vpn
            revoke_user_vpn(db, target.id)
        _audit(db, admin, "user_reactivated" if body.active else "user_deactivated", target)
        db.commit()
    return _user_payload(target)


@router.post("/invitations")
async def create_invitation(
    body: InvitationRequest, request: Request, db: Session = Depends(get_db)
):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    event = db.query(Event).filter(Event.id == body.event_id).first()
    if not event:
        return JSONResponse({"error": "event not found"}, status_code=404)
    if body.role not in {"participant", "administrator"}:
        return JSONResponse({"error": "invalid role"}, status_code=422)
    team = None
    if body.role == "participant":
        training_enabled = os.environ.get("LEARNER_TRAINING_ENABLED", "false").lower() in {"1", "true", "yes"}
        if body.team_id is None and training_enabled:
            return JSONResponse({"error": "team_id is required for participant invitations"}, status_code=422)
        if body.team_id is not None:
            team = db.query(Team).filter(Team.id == body.team_id).first()
            if not team:
                return JSONResponse({"error": "team not found"}, status_code=404)
            if team.event_id != event.id:
                return JSONResponse({"error": "team does not belong to event"}, status_code=422)
    intended = body.intended_username.strip() if body.intended_username else None
    if intended and not 3 <= len(intended) <= 64:
        return JSONResponse({"error": "invalid intended username"}, status_code=422)
    raw = secrets.token_urlsafe(32)
    expires = utcnow() + timedelta(days=7)
    db.add(AccountToken(
        token_hash=_token_digest(raw), purpose="invitation", event_id=event.id, team_id=team.id if team else None,
        created_by_id=admin.id, intended_username=intended,
        intended_is_admin=body.role == "administrator", expires_at=expires,
    ))
    _audit(db, admin, "invitation_created", event_id=event.id, team_id=team.id if team else None, role=body.role,
           intended_username=intended, expires_at=expires.isoformat())
    db.commit()
    link = urljoin(str(request.base_url), "invite/" + raw)
    return {"link": link, "expires_at": expires.isoformat()}


@router.post("/users/{user_id}/reset-link")
async def create_reset_link(user_id: int, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        return JSONResponse({"error": "user not found"}, status_code=404)
    raw = secrets.token_urlsafe(32)
    expires = utcnow() + timedelta(hours=1)
    db.add(AccountToken(
        token_hash=_token_digest(raw), purpose="password_reset", event_id=target.event_id,
        target_user_id=target.id,
        created_by_id=admin.id, expires_at=expires,
    ))
    _audit(db, admin, "password_reset_created", target, expires_at=expires.isoformat())
    db.commit()
    return {"link": urljoin(str(request.base_url), "reset/" + raw), "expires_at": expires.isoformat()}


@router.get("/audit")
async def list_audit(
    request: Request, user_id: Optional[int] = None, limit: int = 30,
    db: Session = Depends(get_db),
):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    query = db.query(AdminAudit)
    if user_id is not None:
        query = query.filter(AdminAudit.target_user_id == user_id)
    entries = query.order_by(AdminAudit.created_at.desc()).limit(min(max(limit, 1), 100)).all()
    user_ids = {i for entry in entries for i in (entry.actor_id, entry.target_user_id) if i}
    usernames = {u.id: u.username for u in db.query(User).filter(User.id.in_(user_ids)).all()} if user_ids else {}
    return [{
        "id": entry.id,
        "actor": usernames.get(entry.actor_id, "system"),
        "target": usernames.get(entry.target_user_id),
        "action": entry.action,
        "metadata": json.loads(entry.metadata_json) if entry.metadata_json else {},
        "created_at": entry.created_at.isoformat(),
    } for entry in entries]


@router.get("/modules")
async def list_modules(request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    from builder.module_loader import load_all_modules
    modules = load_all_modules()
    return [
        {
            "id": m.id,
            "name": m.name,
            "description": m.description,
            "type": m.type,
            "difficulty": m.difficulty,
            "points": m.points,
            "category": m.category,
            "tags": m.tags,
            "stage": m.stage,
            "learning_objectives": m.learning_objectives,
            "estimated_minutes": m.estimated_minutes,
            "disabled": m.disabled,
        }
        for m in modules
    ]


@router.get("/modules/{module_id}")
async def get_module(module_id: str, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    from builder.module_loader import load_all_modules, CopyStep, RunStep
    modules = load_all_modules()
    module = next((m for m in modules if m.id == module_id), None)
    if not module:
        return JSONResponse({"error": "module not found"}, status_code=404)

    steps = []
    for step in module.steps:
        if isinstance(step, RunStep):
            steps.append({"type": "run", "script": step.script})
        elif isinstance(step, CopyStep):
            steps.append({"type": "copy", "src": step.src, "dest": step.dest, "mode": step.mode})

    return {
        "id": module.id,
        "name": module.name,
        "description": module.description,
        "type": module.type,
        "difficulty": module.difficulty,
        "points": module.points,
        "category": module.category,
        "tags": module.tags,
        "conflicts": module.conflicts,
        "requires": module.requires,
        "verification": module.verification,
        "hints": module.hints,
        "suggested_fix": module.suggested_fix,
        "learning_objectives": module.learning_objectives,
        "estimated_minutes": module.estimated_minutes,
        "prerequisites": module.prerequisites,
        "references": [reference.as_dict() for reference in module.references],
        "debrief": module.debrief,
        "stage": module.stage,
        "caldera": module.caldera,
        "steps": steps,
        "disabled": module.disabled,
    }


@router.put("/modules/{module_id}/disable")
async def toggle_module_disabled(module_id: str, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    body = await request.json()

    disabled = bool(body.get("disabled", False))

    from pathlib import Path
    import yaml

    modules_dir = Path(__file__).resolve().parent.parent.parent / "modules"
    yaml_matches = list(modules_dir.rglob(f"{module_id}.yaml"))
    if not yaml_matches:
        return JSONResponse({"error": "module not found"}, status_code=404)

    yaml_path = yaml_matches[0]
    with open(yaml_path) as f:
        data = yaml.safe_load(f)

    if disabled:
        data["disabled"] = True
    else:
        data.pop("disabled", None)

    with open(yaml_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    return {"id": module_id, "disabled": disabled}


@router.get("/modules/{module_id}/file")
async def get_module_file(module_id: str, filename: str, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    from pathlib import Path
    import re

    modules_dir = Path(__file__).resolve().parent.parent.parent / "modules"
    yaml_matches = list(modules_dir.rglob(f"{module_id}.yaml"))
    if not yaml_matches:
        return JSONResponse({"error": "module not found"}, status_code=404)

    source_dir = yaml_matches[0].parent
    # Prevent path traversal and injection — validate filename format
    # Allow only alphanumeric, underscore, hyphen, dot, and slash (for subdirectories)
    filename_clean = re.sub(r'[^a-zA-Z0-9/_\-\.]', '', filename)
    if filename_clean != filename:
        return JSONResponse({"error": "invalid filename"}, status_code=422)

    # Resolve and verify the file path is within source_dir
    file_path = (source_dir / filename_clean).resolve()
    if not file_path.is_relative_to(source_dir.resolve()):
        return JSONResponse({"error": "forbidden"}, status_code=403)

    if not file_path.exists() or not file_path.is_file():
        return JSONResponse({"error": "file not found"}, status_code=404)

    return {"filename": filename_clean, "content": file_path.read_text(errors="replace")}


@router.get("/events")
async def list_events(request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    events = db.query(Event).order_by(Event.created_at.desc()).all()
    result = []
    for e in events:
        user_count = db.query(User).filter(User.event_id == e.id).count()
        result.append({
            "id": e.id,
            "name": e.name,
            "status": e.status,
            "description": e.description,
            "welcome_message": e.welcome_message,
            "time_limit_minutes": e.time_limit_minutes,
            "started_at": e.started_at.isoformat() if e.started_at else None,
            "ends_at": e.ends_at.isoformat() if e.ends_at else None,
            "quota": json.loads(e.quota),
            "infrastructure": json.loads(e.infrastructure) if e.infrastructure else None,
            "infrastructure_layout": json.loads(e.infrastructure_layout) if e.infrastructure_layout else None,
            "updated_at": e.updated_at.isoformat(),
            "user_count": user_count,
            "created_at": e.created_at.isoformat() if e.created_at else None,
            "expo_sync_status": e.expo_sync_status,
            "expo_sync_last_error": e.expo_sync_last_error,
            "expo_sync_attempts": e.expo_sync_attempts,
            "expo_sync_completed_at": e.expo_sync_completed_at.isoformat() if e.expo_sync_completed_at else None,
        })
    return result


@router.get("/events/{event_id}")
async def get_event(event_id: int, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        return JSONResponse({"error": "Event not found"}, status_code=404)
    user_count = db.query(User).filter(User.event_id == event.id).count()
    return {
        "id": event.id,
        "name": event.name,
        "status": event.status,
        "description": event.description,
        "welcome_message": event.welcome_message,
        "time_limit_minutes": event.time_limit_minutes,
        "started_at": event.started_at.isoformat() if event.started_at else None,
        "ends_at": event.ends_at.isoformat() if event.ends_at else None,
        "quota": json.loads(event.quota),
        "infrastructure": json.loads(event.infrastructure) if event.infrastructure else None,
        "infrastructure_layout": json.loads(event.infrastructure_layout) if event.infrastructure_layout else None,
        "operation_plan": json.loads(event.operation_plan) if event.operation_plan else None,
        "updated_at": event.updated_at.isoformat(),
        "user_count": user_count,
        "created_at": event.created_at.isoformat() if event.created_at else None,
        "expo_sync_status": event.expo_sync_status,
        "expo_sync_last_error": event.expo_sync_last_error,
        "expo_sync_attempts": event.expo_sync_attempts,
        "expo_sync_completed_at": event.expo_sync_completed_at.isoformat() if event.expo_sync_completed_at else None,
    }


@router.get("/events/{event_id}/module-plan")
async def get_module_plan(event_id: int, request: Request, db: Session = Depends(get_db)):
    if not require_admin(request, db):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        return JSONResponse({"error": "Event not found"}, status_code=404)
    from builder.infrastructure_planner import normalize_infrastructure
    from builder.module_loader import load_all_modules
    from builder.module_plan import assignable_endpoints, empty_module_plan, reconcile_module_plan
    infrastructure = normalize_infrastructure(json.loads(event.infrastructure))
    plan, issues = reconcile_module_plan(json.loads(event.module_plan) if event.module_plan else empty_module_plan(), infrastructure)
    modules = load_all_modules()
    return {"module_plan": plan, "vms": assignable_endpoints(infrastructure), "issues": issues,
            "updated_at": event.updated_at.isoformat(), "quota": json.loads(event.quota),
            "modules": [{"id": m.id, "name": m.name, "description": m.description, "type": m.type,
                         "difficulty": m.difficulty, "category": m.category, "tags": m.tags,
                         "stage": m.stage, "points": m.points, "requires": m.requires,
                         "conflicts": m.conflicts, "supported_bases": m.supported_bases,
                         "disabled": m.disabled, "learning_objectives": m.learning_objectives,
                         "estimated_minutes": m.estimated_minutes, "prerequisites": m.prerequisites,
                         "verification_type": (m.verification or {}).get("type")} for m in modules]}


@router.put("/events/{event_id}/module-plan")
async def save_module_plan(event_id: int, request: Request, db: Session = Depends(get_db)):
    if not require_admin(request, db):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        return JSONResponse({"error": "Event not found"}, status_code=404)
    if event.status != "draft":
        return JSONResponse({"error": "module assignments are read only"}, status_code=409)
    body = await request.json()
    try:
        if _utc_instant(body.get("expected_updated_at")) != _utc_instant(event.updated_at):
            return JSONResponse({"error": "event draft has changed", "current_updated_at": event.updated_at.isoformat()}, status_code=409)
        from builder.module_plan import normalize_module_plan
        plan = normalize_module_plan(body.get("module_plan"))
    except (TypeError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    event.module_plan = json.dumps(plan); event.updated_at = utcnow(); db.commit(); db.refresh(event)
    return {"status": "saved", "updated_at": event.updated_at.isoformat()}


@router.post("/events/{event_id}/module-plan/resolve")
async def resolve_module_plan(event_id: int, request: Request, db: Session = Depends(get_db)):
    if not require_admin(request, db):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event or event.status != "draft":
        return JSONResponse({"error": "draft event not found"}, status_code=404)
    body = await request.json()
    from builder.infrastructure_planner import normalize_infrastructure
    from builder.module_loader import load_all_modules
    from builder.module_plan import assignable_endpoints, resolve_assignment
    endpoints = {row["id"]: row for row in assignable_endpoints(normalize_infrastructure(json.loads(event.infrastructure)))}
    endpoint = endpoints.get(body.get("vm_id"))
    if not endpoint:
        return JSONResponse({"error": "planned VM not found"}, status_code=404)
    refill = bool(body.get("refill", False))
    if endpoint["role"] == "red" and refill:
        return JSONResponse({"error": "red-team VMs are manual-only"}, status_code=422)
    return resolve_assignment(endpoint, body.get("assignment", {}), json.loads(event.quota), load_all_modules(), refill=refill)


def _operation_context(event):
    from builder.infrastructure_planner import default_infrastructure, normalize_infrastructure
    from builder.module_plan import empty_module_plan
    from builder.module_loader import load_all_modules
    infrastructure = normalize_infrastructure(json.loads(event.infrastructure) if event.infrastructure else default_infrastructure())
    module_plan = json.loads(event.module_plan) if event.module_plan else empty_module_plan()
    return infrastructure, module_plan, load_all_modules()


@router.get("/events/{event_id}/operation-plan")
async def get_operation_plan(event_id: int, request: Request, db: Session = Depends(get_db)):
    if not require_admin(request, db):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        return JSONResponse({"error": "Event not found"}, status_code=404)
    from builder.operation_plan import (empty_operation_plan, operation_catalogue,
        operation_input_fingerprint, validate_operation_plan)
    infrastructure, module_plan, modules = _operation_context(event)
    plan = json.loads(event.operation_plan) if event.operation_plan else empty_operation_plan()
    fingerprint = operation_input_fingerprint(infrastructure, module_plan, modules)
    issues = validate_operation_plan(plan, infrastructure, module_plan, modules, event.time_limit_minutes)
    if plan.get("input_fingerprint") and plan["input_fingerprint"] != fingerprint:
        issues.insert(0, {"code": "stale_inputs", "message": "Network or module assignments changed after this operation draft was saved"})
    return {"operation_plan": plan, "catalogue": operation_catalogue(infrastructure, module_plan, modules),
            "issues": issues, "input_fingerprint": fingerprint, "updated_at": event.updated_at.isoformat(),
            "event_minutes": event.time_limit_minutes, "read_only": event.status != "draft"}


@router.put("/events/{event_id}/operation-plan")
async def save_operation_plan(event_id: int, request: Request, db: Session = Depends(get_db)):
    if not require_admin(request, db):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        return JSONResponse({"error": "Event not found"}, status_code=404)
    if event.status != "draft":
        return JSONResponse({"error": "operation plan is read only"}, status_code=409)
    body = await request.json()
    try:
        if _utc_instant(body.get("expected_updated_at")) != _utc_instant(event.updated_at):
            return JSONResponse({"error": "event draft has changed", "current_updated_at": event.updated_at.isoformat()}, status_code=409)
        from builder.operation_plan import normalize_operation_plan, operation_input_fingerprint
        infrastructure, module_plan, modules = _operation_context(event)
        plan = normalize_operation_plan(body.get("operation_plan"))
        plan["input_fingerprint"] = operation_input_fingerprint(infrastructure, module_plan, modules)
    except (TypeError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    event.operation_plan = json.dumps(plan); event.updated_at = utcnow(); db.commit(); db.refresh(event)
    return {"status": "saved", "operation_plan": plan, "updated_at": event.updated_at.isoformat()}


@router.post("/events/{event_id}/operation-plan/validate")
async def validate_event_operation_plan(event_id: int, request: Request, db: Session = Depends(get_db)):
    if not require_admin(request, db):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        return JSONResponse({"error": "Event not found"}, status_code=404)
    body = await request.json()
    from builder.operation_plan import validate_operation_plan
    infrastructure, module_plan, modules = _operation_context(event)
    issues = validate_operation_plan(body.get("operation_plan"), infrastructure, module_plan, modules, event.time_limit_minutes)
    return {"valid": not issues, "issues": issues}


@router.post("/events/{event_id}/operation-plan/preview")
async def preview_event_operation_plan(event_id: int, request: Request, db: Session = Depends(get_db)):
    if not require_admin(request, db):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        return JSONResponse({"error": "Event not found"}, status_code=404)
    body = await request.json()
    from builder.operation_plan import compile_team_preview, validate_operation_plan
    infrastructure, module_plan, modules = _operation_context(event)
    plan = body.get("operation_plan")
    issues = validate_operation_plan(plan, infrastructure, module_plan, modules, event.time_limit_minutes)
    if issues:
        return JSONResponse({"error": "operation plan is invalid", "issues": issues}, status_code=422)
    team = None
    if body.get("team_id") is not None:
        team = db.query(Team).filter(Team.id == body["team_id"], Team.event_id == event_id).first()
        if not team:
            return JSONResponse({"error": "Team not found"}, status_code=404)
    team_data = {"id": team.id, "name": team.name} if team else {"id": None, "name": "Canonical team"}
    return compile_team_preview(plan, infrastructure, module_plan, modules, team_data)


@router.post("/events")
async def create_event(request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    body = await request.json()

    time_limit = body.get("time_limit_minutes")
    if "time_limit_minutes" in body and time_limit is not None and (
        not isinstance(time_limit, int) or isinstance(time_limit, bool) or time_limit < 1
    ):
        return JSONResponse(
            {"error": "time_limit_minutes must be a positive integer or null"},
            status_code=422,
        )

    if "quota" in body:
        from builder.quota_validation import validate_quota
        errors = validate_quota(body["quota"])
        if errors:
            return JSONResponse(
                {"error": "Invalid quota", "details": errors},
                status_code=422,
            )

    if "infrastructure" in body:
        from builder.infrastructure_validation import validate_infrastructure
        from builder.base_loader import load_all_bases
        valid_base_ids = {b.id for b in load_all_bases() if not b.disabled}
        errors = validate_infrastructure(body["infrastructure"], valid_base_ids)
        if errors:
            return JSONResponse(
                {"error": "Invalid infrastructure", "details": errors},
                status_code=422,
            )

    from builder.infrastructure_planner import default_infrastructure
    infrastructure = body.get("infrastructure", default_infrastructure())
    event = Event(
        name=body.get("name", "CTF Event"),
        quota=json.dumps(body.get("quota", {})),
        infrastructure=json.dumps(infrastructure),
        status="draft",
        description=body.get("description"),
        welcome_message=body.get("welcome_message"),
        time_limit_minutes=body.get("time_limit_minutes"),
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return {"status": "created", "id": event.id}


@router.put("/events/{event_id}")
async def update_event(
    event_id: int, request: Request, db: Session = Depends(get_db)
):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        return JSONResponse({"error": "Event not found"}, status_code=404)

    body = await request.json()
    planner_fields = {"infrastructure", "infrastructure_layout"} & set(body)
    original_updated_at = event.updated_at
    if event.status != "draft" and planner_fields:
        return JSONResponse(
            {"error": "infrastructure cannot be edited after provisioning begins; destroy and reprovision the GameNet"},
            status_code=409,
        )
    expected_updated_at = body.get("expected_updated_at")
    if planner_fields and expected_updated_at is None:
        return JSONResponse({
            "error": "expected_updated_at is required for planner updates",
            "current_updated_at": event.updated_at.isoformat(),
        }, status_code=409)
    try:
        revision_matches = (
            expected_updated_at is None
            or _utc_instant(expected_updated_at) == _utc_instant(event.updated_at)
        )
    except (TypeError, ValueError):
        revision_matches = False
    if not revision_matches:
        return JSONResponse({
            "error": "event draft has changed",
            "current_updated_at": event.updated_at.isoformat(),
        }, status_code=409)

    time_limit = body.get("time_limit_minutes")
    if "time_limit_minutes" in body and time_limit is not None and (
        not isinstance(time_limit, int) or isinstance(time_limit, bool) or time_limit < 1
    ):
        return JSONResponse(
            {"error": "time_limit_minutes must be a positive integer or null"},
            status_code=422,
        )

    if "quota" in body:
        from builder.quota_validation import validate_quota
        errors = validate_quota(body["quota"])
        if errors:
            return JSONResponse(
                {"error": "Invalid quota", "details": errors},
                status_code=422,
            )
        event.quota = json.dumps(body["quota"])

    if "infrastructure" in body:
        if body["infrastructure"] is None:
            event.infrastructure = None
        else:
            from builder.infrastructure_validation import validate_infrastructure
            from builder.base_loader import load_all_bases
            valid_base_ids = {b.id for b in load_all_bases() if not b.disabled}
            errors = validate_infrastructure(body["infrastructure"], valid_base_ids)
            if errors:
                return JSONResponse(
                    {"error": "Invalid infrastructure", "details": errors},
                    status_code=422,
                )
            event.infrastructure = json.dumps(body["infrastructure"])

    if "infrastructure_layout" in body:
        from builder.infrastructure_planner import normalize_infrastructure, validate_infrastructure_layout
        infrastructure = body.get("infrastructure")
        if infrastructure is None:
            infrastructure = json.loads(event.infrastructure) if event.infrastructure else None
        if infrastructure is None:
            return JSONResponse({"error": "layout requires infrastructure"}, status_code=422)
        layout_errors = validate_infrastructure_layout(
            body["infrastructure_layout"], normalize_infrastructure(infrastructure)
        )
        if layout_errors:
            return JSONResponse(
                {"error": "Invalid infrastructure layout", "details": layout_errors}, status_code=422
            )
        event.infrastructure_layout = json.dumps(body["infrastructure_layout"])

    if "name" in body:
        event.name = body["name"]
    if "description" in body:
        event.description = body["description"]
    if "welcome_message" in body:
        event.welcome_message = body["welcome_message"]
    if "time_limit_minutes" in body:
        event.time_limit_minutes = body["time_limit_minutes"]

    event.updated_at = utcnow()
    if planner_fields:
        # Detach the mutated ORM instance before issuing the compare-and-swap,
        # otherwise an autoflush could defeat the revision predicate.
        values = {
            "quota": event.quota,
            "infrastructure": event.infrastructure,
            "infrastructure_layout": event.infrastructure_layout,
            "name": event.name,
            "description": event.description,
            "welcome_message": event.welcome_message,
            "time_limit_minutes": event.time_limit_minutes,
            "updated_at": event.updated_at,
        }
        new_updated_at = event.updated_at
        db.expunge(event)
        changed = db.query(Event).filter(
            Event.id == event_id, Event.updated_at == original_updated_at,
        ).update(values, synchronize_session=False)
        if changed != 1:
            db.rollback()
            current = db.query(Event).filter(Event.id == event_id).first()
            return JSONResponse({
                "error": "event draft has changed",
                "current_updated_at": current.updated_at.isoformat(),
            }, status_code=409)
        db.commit()
        return {"status": "updated", "updated_at": new_updated_at.isoformat()}
    db.commit()
    return {"status": "updated", "updated_at": event.updated_at.isoformat()}


@router.post("/events/{event_id}/start")
async def start_event(event_id: int, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        return JSONResponse({"error": "Event not found"}, status_code=404)
    if event.status != "draft":
        return JSONResponse(
            {"error": f"Event is already {event.status}; only draft events can be started"},
            status_code=409,
        )

    # A GameNet event is not public until lockdown and connectivity checks pass.
    if event.infrastructure:
        from api.models import Team
        from api.services.opnsense_images import active_image

        if not active_image(db):
            return JSONResponse({
                "error": "No active validated OPNsense image",
                "detail": "Build and activate an OPNsense image before provisioning GameNet firewalls.",
                "settings_url": "/admin/settings#opnsense-images",
            }, status_code=409)

        infrastructure = json.loads(event.infrastructure)
        teams = db.query(Team).filter(Team.event_id == event_id).all()

        if not teams:
            return JSONResponse(
                {"error": "Cannot auto-provision VMs: no teams defined for this event"},
                status_code=422,
            )

        from builder.base_loader import load_all_bases
        from builder.infrastructure_validation import infrastructure_summary, validate_infrastructure
        valid_base_ids = {base.id for base in load_all_bases() if not base.disabled}
        try:
            live_vpcs = await _live_vpc_counts()
        except Exception:
            return JSONResponse({"error": "Could not verify live Vultr VPC capacity"}, status_code=502)
        errors = validate_infrastructure(infrastructure, valid_base_ids, team_count=len(teams),
                                         live_vpcs_by_region=live_vpcs)
        if errors:
            return JSONResponse({"error": "Invalid infrastructure", "details": errors}, status_code=422)
        summary = infrastructure_summary(infrastructure, len(teams))

        from api.services.gamenet import allocate_event_networks
        from api.services.gamenet_provisioning import ensure_vm_placeholders, provision_event_gamenets
        allocate_event_networks(db, event, teams, infrastructure)
        ensure_vm_placeholders(db, event, infrastructure)

        from api.models import utcnow
        event.started_at = None
        event.ends_at = None
        event.status = "provisioning"
        event.open = False
        db.commit()

        asyncio.create_task(asyncio.to_thread(provision_event_gamenets, event_id))

        return {
            "status": "provisioning",
            "provisioning": True,
            "vm_count": summary["vms"],
            "ends_at": event.ends_at.isoformat() if event.ends_at else None,
        }

    from api.models import utcnow
    event.started_at = utcnow()
    event.ends_at = (
        event.started_at + timedelta(minutes=event.time_limit_minutes)
        if event.time_limit_minutes else None
    )
    event.status = "open"
    event.open = True
    db.commit()
    from api.services.expo_ust import schedule
    scheduled = schedule(event.id)
    return {"status": "started", "ends_at": event.ends_at.isoformat() if event.ends_at else None,
            "warning": None if scheduled else "Expo-IT integration is not configured"}


@router.post("/events/{event_id}/expo-sync/retry")
async def retry_expo_sync(event_id: int, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    event = db.query(Event).filter_by(id=event_id).first()
    if not event:
        return JSONResponse({"error": "Event not found"}, status_code=404)
    if event.status != "open":
        return JSONResponse({"error": "Only an open event can be synchronized"}, status_code=409)
    from api.services.expo_ust import configured, schedule
    if not configured():
        return JSONResponse({"error": "Expo-IT integration is not configured"}, status_code=503)
    schedule(event_id)
    return JSONResponse({"status": "syncing"}, status_code=202)


@router.get("/events/{event_id}/provision-status")
async def event_provision_status(
    event_id: int, request: Request, db: Session = Depends(get_db)
):
    """Return aggregate provisioning progress for all VMs in an event."""
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    from api.models import PrivateBootCertification, Site, Team, VM

    vms = db.query(VM).filter(VM.event_id == event_id).all()

    # Build team name lookup
    teams = db.query(Team).filter(Team.event_id == event_id).all()
    team_names = {t.id: t.name for t in teams}

    # Count by status
    counts = {"creating": 0, "registered": 0, "provisioning": 0, "active": 0, "failed": 0, "stopped": 0, "destroying": 0}
    vm_list = []
    for vm in vms:
        status_key = vm.status if vm.status in counts else "creating"
        counts[status_key] = counts.get(status_key, 0) + 1
        vm_list.append({
            "id": vm.id,
            "hostname": vm.hostname,
            "team": team_names.get(vm.team_id, ""),
            "vm_type": vm.vm_type,
            "status": vm.status,
            "provision_step": vm.provision_step,
            "provision_error": vm.provision_error,
            "ip_address": vm.ip_address,
        })

    certifications = (
        db.query(PrivateBootCertification)
        .join(Site, PrivateBootCertification.site_id == Site.id)
        .filter(Site.event_id == event_id)
        .order_by(PrivateBootCertification.site_id, PrivateBootCertification.base_type)
        .all()
    )
    current_phase = next(
        (vm.provision_step for vm in vms if vm.status not in {"active", "stopped"} and vm.provision_step),
        None,
    )

    return {
        "total": len(vms),
        **counts,
        "phase": current_phase,
        "private_boot_certifications": [{
            "site_id": cert.site_id,
            "base_type": cert.base_type,
            "os_id": cert.os_id,
            "region": cert.region,
            "firewall_instance_id": cert.firewall_instance_id,
            "status": cert.status,
            "phase": cert.phase,
            "instance_id": cert.instance_id,
            "provider_ip": cert.provider_ip,
            "started_at": cert.started_at,
            "completed_at": cert.completed_at,
            "cleanup_completed_at": cert.cleanup_completed_at,
            "diagnostic_detail": cert.diagnostic_detail,
        } for cert in certifications],
        "vms": vm_list,
    }


@router.post("/events/{event_id}/retry-provisioning")
async def retry_event_provisioning(event_id: int, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    event = db.query(Event).filter_by(id=event_id).first()
    if not event:
        return JSONResponse({"error": "Event not found"}, status_code=404)
    if event.status != "provision_failed" or not event.infrastructure:
        return JSONResponse({"error": "only failed GameNet provisioning can be retried"}, status_code=409)
    from api.services.gamenet_provisioning import provision_event_gamenets
    event.status, event.open = "provisioning", False
    db.commit()
    asyncio.create_task(asyncio.to_thread(provision_event_gamenets, event.id))
    return {"status": "provisioning"}


@router.post("/events/{event_id}/plan-preview")
async def plan_preview(event_id: int, body: PlanPreviewRequest, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        return JSONResponse({"error": "not found"}, status_code=404)

    try:
        quota = body.quota if body.quota is not None else json.loads(event.quota)
        infrastructure = body.infrastructure if body.infrastructure is not None else (json.loads(event.infrastructure) if event.infrastructure else None)
    except (json.JSONDecodeError, TypeError) as e:
        return JSONResponse({"error": f"invalid quota JSON: {e}"}, status_code=422)

    if not infrastructure:
        return JSONResponse({"error": "no infrastructure configured"}, status_code=422)

    from builder.infrastructure_validation import validate_infrastructure
    from builder.base_loader import load_all_bases
    valid_base_ids = {b.id for b in load_all_bases() if not b.disabled}
    infrastructure_errors = validate_infrastructure(infrastructure, valid_base_ids)
    if infrastructure_errors:
        return JSONResponse({"error": "invalid infrastructure", "details": infrastructure_errors}, status_code=422)

    from builder.infrastructure_validation import infrastructure_summary, site_subnets
    teams = db.query(Team).filter(Team.event_id == event_id).all()
    if not teams:
        return JSONResponse({"error": "no teams defined"}, status_code=422)
    infrastructure_counts = infrastructure_summary(infrastructure, len(teams))
    address_plan = []
    # Preview addresses are illustrative; committed allocation is global and
    # occurs atomically when provisioning starts.
    from ipaddress import ip_network
    blocks = iter(ip_network("10.128.0.0/9").subnets(new_prefix=20))
    for team in teams:
        for site in infrastructure["sites"]:
            cidr = str(next(blocks))
            infra_subnet, zones = site_subnets(cidr, len(site["zones"]))
            address_plan.append({
                "team": team.name, "site_key": site["key"], "site": site["name"],
                "region": site["region"], "cidr": cidr, "infrastructure_subnet": infra_subnet,
                "zones": [{"key": definition["key"], "role": definition["team"],
                           "subnet": subnet, "gateway": gateway}
                          for definition, (subnet, gateway) in zip(site["zones"], zones)],
            })
    from builder.module_loader import load_all_modules
    from builder.selector import select_modules
    from builder.plan_sizing import plan_for_vm
    from builder.attack_tree import build_attack_tree, serialize_tree
    from builder.base_loader import load_base_type
    from builder.infrastructure_validation import gamenet_hostname
    from builder.infrastructure_planner import zone_endpoint_instances

    library_list = load_all_modules()
    library = {m.id: m for m in library_list}

    # Fetch Vultr plans for sizing + cost estimates (optional)
    available_plans = []
    plan_costs = {}
    vultr_key = os.environ.get("VULTR_API_KEY")
    if vultr_key:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    "https://api.vultr.com/v2/plans?type=vc2&per_page=500",
                    headers={"Authorization": f"Bearer {vultr_key}"},
                )
            try:
                plans_data = resp.json()
                for p in plans_data.get("plans", []):
                    if p.get("id", "").startswith("vc2-"):
                        available_plans.append({
                            "id": p["id"],
                            "ram": p["ram"],
                            "vcpu_count": p["vcpu_count"],
                            "monthly_cost": p["monthly_cost"],
                        })
                        plan_costs[p["id"]] = p["monthly_cost"]
            except (json.JSONDecodeError, KeyError, TypeError):
                available_plans = []
                plan_costs = {}
        except Exception:
            # A preview remains useful without a live provider price response.
            available_plans = []
            plan_costs = {}

    team_count = len(teams)
    first_team = teams[0] if teams else None
    topology_nodes = [
        {"id": f"event-{event_id}", "type": "event", "label": event.name, "status": event.status,
         "team_count": team_count}
    ]
    topology_links = []

    vm_types = []
    total_modules = 0
    total_attack_paths = 0
    total_cost = 0.0

    machine_types = [{
        "type_key": "vpn_gateway", "role": "infrastructure", "count": 1,
        "spec": infrastructure["vpn_gateway"], "region": infrastructure["vpn_gateway"]["region"],
        "hostname": lambda team, index: gamenet_hostname(event_id, team.id, "gateway"),
    }]
    for site in infrastructure["sites"]:
        machine_types.append({
            "type_key": f"{site['key']}_firewall", "role": "infrastructure", "count": 1,
            "spec": site["firewall"], "region": site["region"],
            "hostname": lambda team, index, site_key=site["key"]: gamenet_hostname(event_id, team.id, site_key, "fw"),
        })
        for zone in site["zones"]:
            for endpoint in zone_endpoint_instances(zone["endpoints"]):
                machine_types.append({
                    "type_key": f"{site['key']}_{zone['key']}_{endpoint['key']}",
                    "role": "attacker" if zone["team"] == "red" else "target",
                    "count": 1, "spec": endpoint, "region": site["region"],
                    "hostname": lambda team, index, site_key=site["key"], zone_key=zone["key"], endpoint_key=endpoint["key"]:
                        gamenet_hostname(event_id, team.id, site_key, zone_key, endpoint_key),
                })

    for definition in machine_types:
        type_key = definition["type_key"]
        role = definition["role"]
        count_per_team = definition["count"]
        spec = definition["spec"]
        default_plan = spec["default_plan"]
        region = definition["region"]
        base_type_id = spec["base_type"]
        base_type = load_base_type(base_type_id)
        os_name = base_type.os
        icon_name = base_type.icon

        vms = []

        for team in teams:
            for i in range(count_per_team):
                hostname = definition["hostname"](team, i)

                if role == "target":
                    try:
                        selected = select_modules(quota, library_list, base_type_id=base_type_id)
                    except ValueError as e:
                        return JSONResponse({"error": str(e)}, status_code=422)
                    sized_plan = plan_for_vm(base_type, selected, default_plan, available_plans)
                    module_objs = [library[m.id] for m in selected if m.id in library]
                    tree = build_attack_tree(module_objs)
                    serialized_tree = serialize_tree(tree)
                    module_list = [
                        {"id": m.id, "name": m.name, "type": m.type,
                         "difficulty": m.difficulty, "points": m.points}
                        for m in selected
                    ]
                    total_modules += len(module_list)
                    total_attack_paths += len(serialized_tree.get("paths", []))
                else:
                    selected = []
                    sized_plan = default_plan
                    serialized_tree = None
                    module_list = []

                total_cost += plan_costs.get(sized_plan, 0)

                vm_node_id = f"vm-projected-{type_key}-{team.name}-{i + 1}"
                # Only include first team's VMs in topology (canonical — all teams identical)
                if first_team and team.id == first_team.id:
                    topology_nodes.append({
                        "id": vm_node_id, "type": "vm",
                        "label": hostname, "hostname": hostname,
                        "ip": None, "status": "projected", "os": os_name,
                        "icon": icon_name,
                        "event_id": f"event-{event_id}",
                        "modules_total": len(module_list), "modules_completed": 0,
                    })
                    topology_links.append({"source": f"event-{event_id}", "target": vm_node_id})

                vms.append({
                    "hostname": hostname,
                    "team": team.name,
                    "plan": sized_plan,
                    "modules": module_list,
                    "attack_tree": serialized_tree,
                })

        vm_types.append({
            "type_key": type_key,
            "role": role,
            "os": os_name,
            "default_plan": default_plan,
            "region": region,
            "count_per_team": count_per_team,
            "total_count": count_per_team * len(teams),
            "vms": vms,
        })

    # Variation check: warn if target VMs within a type share identical module sets
    warnings = []
    for vm_type_entry in vm_types:
        if vm_type_entry["role"] != "target":
            continue
        sets = [frozenset(m["id"] for m in vm["modules"]) for vm in vm_type_entry["vms"]]
        if not sets:
            continue
        unique_count = len(set(sets))
        total_count = len(sets)
        if total_count > 1 and unique_count / total_count < 0.5:
            saturated = []
            for module_type, tiers in quota.items():
                if not isinstance(tiers, dict):
                    continue
                for difficulty, requested in tiers.items():
                    available = sum(
                        1 for m in library_list
                        if m.type == module_type and m.difficulty == difficulty and not m.disabled
                    )
                    if available > 0 and requested / available >= 0.8:
                        saturated.append(f"{difficulty} {module_type} ({requested}/{available})")
            tier_msg = f" Tiers at capacity: {', '.join(saturated)}." if saturated else ""
            warnings.append(
                f"Low module variation in '{vm_type_entry['type_key']}': "
                f"{unique_count} unique assignment{'s' if unique_count != 1 else ''} across "
                f"{total_count} VMs.{tier_msg} "
                f"Add more modules or reduce quota counts for greater diversity."
            )

    return {
        "summary": {
            **infrastructure_counts,
            "total_vms": infrastructure_counts["vms"],
            "teams": len(teams),
            "estimated_monthly_cost": round(total_cost, 2),
            "total_modules": total_modules,
            "total_attack_paths": total_attack_paths,
        },
        "teams": [t.name for t in teams],
        "vm_types": vm_types,
        "topology": {"nodes": topology_nodes, "links": topology_links},
        "warnings": warnings,
        "address_plan": address_plan,
        "infrastructure": infrastructure,
        "total_cost": round(total_cost, 2),
    }


async def _cleanup_caldera_operations_for_event(event_id: int) -> dict:
    """Delete all Caldera operations belonging to an event group. Returns summary."""
    from api.services.caldera import CalderaClient
    group = f"event-{event_id}"
    deleted = 0
    errors = []
    try:
        async with CalderaClient() as caldera:
            operations = await caldera.list_operations()
            for op in operations:
                if op.get("group") == group:
                    try:
                        await caldera.delete_operation(op["id"])
                        deleted += 1
                    except Exception as e:
                        errors.append(f"Failed to delete operation {op.get('id')}: {e}")
    except Exception as e:
        errors.append(f"Caldera unavailable: {e}")
    return {"deleted": deleted, "errors": errors}


@router.post("/events/{event_id}/stop")
async def stop_event(event_id: int, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        return JSONResponse({"error": "Event not found"}, status_code=404)

    event.status = "stopped"
    event.open = False
    db.commit()

    caldera_cleanup = await _cleanup_caldera_operations_for_event(event_id)

    return {
        "status": "stopped",
        "caldera_operations_cleaned": caldera_cleanup["deleted"],
    }


@router.get("/base-types")
async def get_base_types(request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    from builder.base_loader import load_all_bases
    bases = [b for b in load_all_bases() if not b.disabled]
    return [
        {
            "id": b.id,
            "name": b.name,
            "description": b.description,
            "default_plan": b.default_plan,
            "icon": b.icon,
        }
        for b in bases
    ]


@router.delete("/events/{event_id}")
async def delete_event(event_id: int, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        return JSONResponse({"error": "Event not found"}, status_code=404)

    user_count = db.query(User).filter(User.event_id == event.id).count()
    if user_count > 0:
        return JSONResponse(
            {"error": f"Cannot delete event with {user_count} assigned users"},
            status_code=409,
        )

    caldera_cleanup = await _cleanup_caldera_operations_for_event(event_id)

    db.delete(event)
    db.commit()

    return {
        "status": "deleted",
        "caldera_operations_cleaned": caldera_cleanup["deleted"],
    }


@router.post("/teams/{team_id}/participants")
async def assign_participant(team_id: int, body: TeamAssignmentRequest, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    team = db.query(Team).filter(Team.id == team_id).first()
    user = db.query(User).filter(User.id == body.user_id).first()
    if not team or not user:
        return JSONResponse({"error": "team or user not found"}, status_code=404)
    if user.is_admin:
        return JSONResponse({"error": "administrators cannot be assigned to teams"}, status_code=422)
    old = user.team_id
    user.event_id = team.event_id
    user.team_id = team.id
    user.updated_at = utcnow()
    user.session_version += 1
    _audit(db, admin, "participant_team_assigned", user, old_team_id=old, team_id=team.id, event_id=team.event_id)
    db.commit()
    return _user_payload(user)


@router.post("/teams/{team_id}/training-credential/rotate")
async def rotate_training_credential(team_id: int, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        return JSONResponse({"error": "team not found"}, status_code=404)
    from api.services.training_credentials import rotate_team_credential
    credential, report = rotate_team_credential(db, team)
    _audit(db, admin, "team_credential_rotated" if report["rotated"] else "team_credential_rotation_failed",
           team_id=team.id, succeeded_vm_ids=report["succeeded_vm_ids"], failed_vm_ids=report["failed_vm_ids"],
           rollback_failed_vm_ids=report.get("rollback_failed_vm_ids", []))
    db.commit()
    status = 200 if report["rotated"] else 409
    return JSONResponse({**report, "credential_status": credential.status if credential else "missing"}, status_code=status,
                        headers={"Cache-Control": "no-store"})


@router.get("/events/{event_id}/readiness")
async def event_readiness(event_id: int, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        return JSONResponse({"error": "event not found"}, status_code=404)
    from builder.catalogue_validation import validate_catalogue
    from builder.module_loader import load_all_modules
    unassigned = db.query(User).filter(User.event_id == event.id, User.is_admin.is_(False), User.team_id.is_(None)).all()
    missing_credentials = [team.id for team in event.teams if not team.training_credential or team.training_credential.status != "active"]
    async def reachable(vm):
        if vm.status != "active" or not vm.ip_address:
            return False
        try:
            _reader, writer = await asyncio.wait_for(
                asyncio.open_connection(vm.ip_address, vm.ssh_port or 22), timeout=1.0
            )
            writer.close()
            await writer.wait_closed()
            return True
        except Exception:
            return False
    reachability = await asyncio.gather(*(reachable(vm) for vm in event.vms))
    unreachable = [vm.id for vm, is_reachable in zip(event.vms, reachability) if not is_reachable]
    verifier_missing = [vm.id for vm in event.vms if not db.query(PlatformSettings).filter_by(key=f"verifier_vm_{vm.id}", value="provisioned").first()]
    catalogue = load_all_modules()
    invalid = validate_catalogue(catalogue)
    from builder.preset_loader import validate_presets
    invalid_presets = validate_presets({module.id for module in catalogue}, catalogue)
    checks = {
        "unassigned_participants": [{"id": user.id, "username": user.username} for user in unassigned],
        "missing_team_credentials": missing_credentials,
        "unprovisioned_verifier_vms": verifier_missing,
        "invalid_modules": invalid,
        "invalid_presets": invalid_presets,
        "unreachable_vms": unreachable,
    }
    return {"event_id": event.id, "ready": not any(checks.values()), "learner_training_enabled": os.environ.get("LEARNER_TRAINING_ENABLED", "false").lower() in {"1", "true", "yes"}, **checks}


@router.get("/verification-health")
async def verification_health(request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    from api.services.verification_scheduler import health_metrics
    invalid_count = db.query(VMModule).filter(VMModule.verification_error_code == "invalid_specification").count()
    unavailable_vms = db.query(VM).filter(VM.status != "active").count()
    return {**health_metrics(), "invalid_assignments": invalid_count, "unavailable_vms": unavailable_vms}


@router.get("/event-presets")
async def event_presets(request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    from builder.preset_loader import load_presets
    return [{"id": preset.id, "name": preset.name, "description": preset.description,
             "modules": preset.modules} for preset in load_presets()]


@router.get("/events/{event_id}/verification-history")
async def verification_history(event_id: int, request: Request, limit: int = 100, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    attempts = db.query(VerificationAttempt).join(VMModule).join(VM).filter(VM.event_id == event_id).order_by(
        VerificationAttempt.created_at.desc()).limit(min(max(limit, 1), 500)).all()
    return [{"id": item.id, "module_assignment_id": item.module_assignment_id, "user_id": item.user_id,
             "trigger": item.trigger_type, "result": item.result, "summary": item.safe_summary,
             "error_code": item.error_code, "duration_ms": item.duration_ms,
             "created_at": item.created_at.isoformat()} for item in attempts]


@router.post("/verifications/bulk")
async def bulk_verification(body: BulkVerificationRequest, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    event = db.query(Event).filter(Event.id == body.event_id).first()
    if not event:
        return JSONResponse({"error": "event not found"}, status_code=404)
    if event.status == "stopped":
        return JSONResponse({"error": "stopped events are read-only"}, status_code=409)
    query = db.query(VMModule).join(VM).filter(VM.event_id == event.id, VMModule.stage == "preapplied")
    if body.team_id is not None:
        query = query.filter(VM.team_id == body.team_id)
    from builder.module_loader import load_all_modules
    definitions = {module.id: module for module in load_all_modules()}
    results = []
    from api.services.verification import verify_assignment
    for assignment in query.all():
        definition = definitions.get(assignment.module_id)
        if not definition:
            continue
        result = await verify_assignment(db, assignment, definition.verification, "admin", admin)
        results.append({"assignment_id": assignment.id, "result": result.result, "error_code": result.error_code})
    return {"checked": len(results), "results": results}
