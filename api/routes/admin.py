import asyncio
import json
import os
import secrets
from datetime import timedelta
from typing import Optional
from urllib.parse import urljoin

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.database import get_db
from api.models import AccountToken, AdminAudit, Event, User, utcnow
from api.routes.auth import _token_digest, get_current_user

router = APIRouter(prefix="/admin", tags=["admin"])


class PlanPreviewRequest(BaseModel):
    quota: Optional[dict] = None
    vm_quota: Optional[dict] = None


class UserUpdateRequest(BaseModel):
    role: Optional[str] = None
    event_id: Optional[int] = None


class ActivationRequest(BaseModel):
    active: bool


class InvitationRequest(BaseModel):
    event_id: int
    intended_username: Optional[str] = None
    role: str = "participant"


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


def _user_payload(user: User, audit_summary=None):
    return {
        "id": user.id,
        "username": user.username,
        "is_admin": user.is_admin,
        "role": "administrator" if user.is_admin else "participant",
        "active": user.active,
        "event_id": user.event_id,
        "event_name": user.event.name if user.event else None,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "updated_at": user.updated_at.isoformat() if user.updated_at else None,
        "deactivated_at": user.deactivated_at.isoformat() if user.deactivated_at else None,
        "audit_summary": audit_summary,
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
        query = query.filter(User.username.ilike(f"%{q.strip()}%"))
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
    changed = new_admin != target.is_admin or new_event_id != target.event_id
    target.is_admin = new_admin
    target.event_id = new_event_id
    if changed:
        target.session_version += 1
        target.updated_at = utcnow()
        _audit(
            db, admin, "user_access_updated", target,
            old_role=old_role, new_role=body.role or old_role,
            old_event_id=old_event_id, new_event_id=new_event_id,
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
    intended = body.intended_username.strip() if body.intended_username else None
    if intended and not 3 <= len(intended) <= 64:
        return JSONResponse({"error": "invalid intended username"}, status_code=422)
    raw = secrets.token_urlsafe(32)
    expires = utcnow() + timedelta(days=7)
    db.add(AccountToken(
        token_hash=_token_digest(raw), purpose="invitation", event_id=event.id,
        created_by_id=admin.id, intended_username=intended,
        intended_is_admin=body.role == "administrator", expires_at=expires,
    ))
    _audit(db, admin, "invitation_created", event_id=event.id, role=body.role,
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

    modules_dir = Path(__file__).resolve().parent.parent.parent / "modules"
    yaml_matches = list(modules_dir.rglob(f"{module_id}.yaml"))
    if not yaml_matches:
        return JSONResponse({"error": "module not found"}, status_code=404)

    source_dir = yaml_matches[0].parent
    # Prevent path traversal — only allow files within the module's own directory
    file_path = (source_dir / filename).resolve()
    if not file_path.is_relative_to(source_dir.resolve()):
        return JSONResponse({"error": "forbidden"}, status_code=403)

    if not file_path.exists() or not file_path.is_file():
        return JSONResponse({"error": "file not found"}, status_code=404)

    return {"filename": filename, "content": file_path.read_text(errors="replace")}


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
            "vm_quota": json.loads(e.vm_quota) if e.vm_quota else None,
            "user_count": user_count,
            "created_at": e.created_at.isoformat() if e.created_at else None,
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
        "vm_quota": json.loads(event.vm_quota) if event.vm_quota else None,
        "user_count": user_count,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


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

    if "vm_quota" in body:
        from builder.vm_quota_validation import validate_vm_quota
        from builder.base_loader import load_all_bases
        valid_base_ids = {b.id for b in load_all_bases() if not b.disabled}
        errors = validate_vm_quota(body["vm_quota"], valid_base_ids)
        if errors:
            return JSONResponse(
                {"error": "Invalid vm_quota", "details": errors},
                status_code=422,
            )

    event = Event(
        name=body.get("name", "CTF Event"),
        quota=json.dumps(body.get("quota", {})),
        vm_quota=json.dumps(body["vm_quota"]) if "vm_quota" in body else None,
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

    if "vm_quota" in body:
        if body["vm_quota"] is None:
            event.vm_quota = None
        else:
            from builder.vm_quota_validation import validate_vm_quota
            from builder.base_loader import load_all_bases
            valid_base_ids = {b.id for b in load_all_bases() if not b.disabled}
            errors = validate_vm_quota(body["vm_quota"], valid_base_ids)
            if errors:
                return JSONResponse(
                    {"error": "Invalid vm_quota", "details": errors},
                    status_code=422,
                )
            event.vm_quota = json.dumps(body["vm_quota"])

    if "name" in body:
        event.name = body["name"]
    if "description" in body:
        event.description = body["description"]
    if "welcome_message" in body:
        event.welcome_message = body["welcome_message"]
    if "time_limit_minutes" in body:
        event.time_limit_minutes = body["time_limit_minutes"]

    db.commit()
    return {"status": "updated"}


@router.post("/events/{event_id}/start")
async def start_event(event_id: int, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        return JSONResponse({"error": "Event not found"}, status_code=404)

    # If vm_quota is defined, kick off auto-provisioning for all teams
    if event.vm_quota:
        from api.models import Team

        vm_quota = json.loads(event.vm_quota)
        teams = db.query(Team).filter(Team.event_id == event_id).all()

        if not teams:
            return JSONResponse(
                {"error": "Cannot auto-provision VMs: no teams defined for this event"},
                status_code=422,
            )

        total_vms = sum(spec["count"] for spec in vm_quota.values()) * len(teams)

        from api.routes.vm import _provision_event_vms

        from api.models import utcnow
        event.started_at = utcnow()
        event.ends_at = (
            event.started_at + timedelta(minutes=event.time_limit_minutes)
            if event.time_limit_minutes else None
        )
        event.status = "open"
        event.open = True
        db.commit()

        asyncio.create_task(asyncio.to_thread(_provision_event_vms, event_id))

        return {
            "status": "started",
            "provisioning": True,
            "vm_count": total_vms,
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
    return {"status": "started", "ends_at": event.ends_at.isoformat() if event.ends_at else None}


@router.get("/events/{event_id}/provision-status")
async def event_provision_status(
    event_id: int, request: Request, db: Session = Depends(get_db)
):
    """Return aggregate provisioning progress for all VMs in an event."""
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    from api.models import Team, VM

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

    return {
        "total": len(vms),
        **counts,
        "vms": vm_list,
    }


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
        vm_quota = body.vm_quota if body.vm_quota is not None else (json.loads(event.vm_quota) if event.vm_quota else None)
    except (json.JSONDecodeError, TypeError) as e:
        return JSONResponse({"error": f"invalid quota JSON: {e}"}, status_code=422)

    if not vm_quota:
        return JSONResponse({"error": "no vm_quota configured"}, status_code=422)

    from builder.vm_quota_validation import validate_vm_quota
    from builder.base_loader import load_all_bases
    valid_base_ids = {b.id for b in load_all_bases() if not b.disabled}
    vm_quota_errors = validate_vm_quota(vm_quota, valid_base_ids)
    if vm_quota_errors:
        return JSONResponse({"error": "invalid vm_quota", "details": vm_quota_errors}, status_code=422)

    from api.models import Team
    teams = db.query(Team).filter(Team.event_id == event_id).all()
    if not teams:
        return JSONResponse({"error": "no teams defined"}, status_code=422)

    from builder.module_loader import load_all_modules
    from builder.selector import select_modules
    from builder.plan_sizing import plan_for_vm
    from builder.attack_tree import build_attack_tree, serialize_tree

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
            for p in resp.json().get("plans", []):
                if p["id"].startswith("vc2-"):
                    available_plans.append({
                        "id": p["id"],
                        "ram": p["ram"],
                        "vcpu_count": p["vcpu_count"],
                        "monthly_cost": p["monthly_cost"],
                    })
                    plan_costs[p["id"]] = p["monthly_cost"]
        except Exception:
            pass

    _TEAM_COLORS = [
        "#e040fb", "#00bcd4", "#69f0ae", "#ff6d00", "#2979ff",
        "#ff4081", "#ffea00", "#00e5ff", "#76ff03", "#ff6e40",
    ]

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

    for type_key, spec in vm_quota.items():
        role = spec.get("role", "target")
        count_per_team = int(spec.get("count", 1))
        default_plan = spec.get("default_plan", "vc2-1c-1gb")
        region = spec.get("region", "")
        base_type_id = spec.get("base_type")
        from builder.base_loader import load_base_type as _load_base_type
        _base_type_obj = _load_base_type(base_type_id) if base_type_id else None
        os_name = spec.get("os", "") or (_base_type_obj.os if _base_type_obj else "")
        icon_name = (_base_type_obj.icon if _base_type_obj else None)

        vms = []

        for team in teams:
            for i in range(count_per_team):
                hostname = f"ctf-e{event_id}-t{team.id}-{type_key}-{i + 1}"

                if role == "target":
                    try:
                        selected = select_modules(quota, library_list, base_type_id=None)
                    except ValueError as e:
                        return JSONResponse({"error": str(e)}, status_code=422)
                    sized_plan = plan_for_vm(None, selected, default_plan, available_plans) if available_plans else default_plan
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
            "total_vms": sum(t["total_count"] for t in vm_types),
            "teams": len(teams),
            "estimated_monthly_cost": round(total_cost, 2),
            "total_modules": total_modules,
            "total_attack_paths": total_attack_paths,
        },
        "teams": [t.name for t in teams],
        "vm_types": vm_types,
        "topology": {"nodes": topology_nodes, "links": topology_links},
        "warnings": warnings,
    }


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
    return {"status": "stopped"}


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

    db.delete(event)
    db.commit()
    return {"status": "deleted"}
