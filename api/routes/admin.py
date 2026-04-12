import asyncio
import json
import os
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.database import get_db
from api.models import Event, User, UserImage, UserModule
from api.routes.auth import get_current_user

REGISTRY_INTERNAL = os.environ.get("REGISTRY_INTERNAL", "http://registry:5000")

router = APIRouter(prefix="/admin", tags=["admin"])


class PlanPreviewRequest(BaseModel):
    quota: Optional[dict] = None
    vm_quota: Optional[dict] = None


def require_admin(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return None
    return user


@router.get("/users")
async def list_users(request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    users = db.query(User).all()
    result = []
    for u in users:
        image = (
            db.query(UserImage)
            .filter(UserImage.user_id == u.id)
            .order_by(UserImage.created_at.desc())
            .first()
        )
        total_points = sum(
            m.points for m in db.query(UserModule).filter(
                UserModule.user_id == u.id, UserModule.completed == True
            ).all()
        )
        result.append({
            "id": u.id,
            "username": u.username,
            "is_admin": u.is_admin,
            "build_status": image.status if image else "none",
            "total_points": total_points,
            "event_name": u.event.name if u.event else None,
        })

    return result


@router.post("/rebuild/{user_id}")
async def rebuild_user(
    user_id: int, request: Request, db: Session = Depends(get_db)
):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return JSONResponse({"error": "User not found"}, status_code=404)

    # Reset modules
    db.query(UserModule).filter(UserModule.user_id == user_id).delete()

    # Create new image record
    image = UserImage(user_id=user_id, status="queued")
    db.add(image)
    db.commit()

    if user.event:
        quota = json.loads(user.event.quota)
    else:
        quota = json.loads(os.environ.get(
            "EVENT_QUOTA",
            '{"vulnerability":{"easy":1,"medium":0,"hard":0},"hardening":{"easy":0,"medium":1,"hard":0}}',
        ))

    from api.routes.auth import _run_build
    asyncio.create_task(_run_build(user.id, user.username, quota))

    return {"status": "rebuild_queued"}


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
    if not str(file_path).startswith(str(source_dir.resolve())):
        return JSONResponse({"error": "forbidden"}, status_code=403)

    if not file_path.exists() or not file_path.is_file():
        return JSONResponse({"error": "file not found"}, status_code=404)

    return {"filename": filename, "content": file_path.read_text(errors="replace")}


@router.get("/registry")
async def list_registry_images(request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            catalog_resp = await client.get(f"{REGISTRY_INTERNAL}/v2/_catalog")
            catalog_resp.raise_for_status()
            repos = catalog_resp.json().get("repositories", [])

            images = []
            for repo in repos:
                tags_resp = await client.get(
                    f"{REGISTRY_INTERNAL}/v2/{repo}/tags/list"
                )
                tags = tags_resp.json().get("tags", []) if tags_resp.status_code == 200 else []
                for tag in tags:
                    # Get manifest for size/digest info
                    digest = None
                    created = None
                    try:
                        manifest_resp = await client.get(
                            f"{REGISTRY_INTERNAL}/v2/{repo}/manifests/{tag}",
                            headers={"Accept": "application/vnd.docker.distribution.manifest.v2+json"},
                        )
                        if manifest_resp.status_code == 200:
                            digest = manifest_resp.headers.get("Docker-Content-Digest", "")
                    except Exception:
                        pass
                    images.append({
                        "repository": repo,
                        "tag": tag,
                        "full_ref": f"{repo}:{tag}",
                        "digest": digest[:19] + "…" if digest and len(digest) > 19 else digest,
                    })

            return images
    except httpx.ConnectError:
        return JSONResponse({"error": "Cannot connect to registry"}, status_code=502)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


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

    event.status = "open"
    event.open = True
    db.commit()

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

        asyncio.create_task(asyncio.to_thread(_provision_event_vms, event_id))

        return {"status": "started", "provisioning": True, "vm_count": total_vms}

    return {"status": "started"}


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

    topology_nodes = [
        {"id": f"event-{event_id}", "type": "event", "label": event.name, "status": event.status}
    ]
    topology_links = []

    for idx, team in enumerate(teams):
        color = _TEAM_COLORS[idx % len(_TEAM_COLORS)]
        topology_nodes.append({
            "id": f"team-{team.id}", "type": "team", "label": team.name,
            "event_id": f"event-{event_id}", "color": color,
        })
        topology_links.append({"source": f"event-{event_id}", "target": f"team-{team.id}"})

    vm_types = []
    total_modules = 0
    total_attack_paths = 0
    total_cost = 0.0

    for type_key, spec in vm_quota.items():
        role = spec.get("role", "target")
        count_per_team = int(spec.get("count", 1))
        os_name = spec.get("os", "")
        default_plan = spec.get("default_plan", "vc2-1c-1gb")
        region = spec.get("region", "")

        vms = []

        for team in teams:
            for i in range(count_per_team):
                hostname = f"{team.name}-{type_key}-{i + 1}"

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
                topology_nodes.append({
                    "id": vm_node_id, "type": "vm",
                    "label": hostname, "hostname": hostname,
                    "ip": None, "status": "projected", "os": os_name,
                    "team_id": f"team-{team.id}", "event_id": f"event-{event_id}",
                    "modules_total": len(module_list), "modules_completed": 0,
                })
                topology_links.append({"source": f"team-{team.id}", "target": vm_node_id})

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
