import io
import json
import os
import shutil
import tempfile
import uuid
import zipfile

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from api.database import get_db
from api.models import Event, Team, VM, VMModule
from api.routes.admin import require_admin

router = APIRouter(prefix="/admin", tags=["admin"])


# ── Teams ──────────────────────────────────────────────────────────────────────

@router.get("/teams")
async def list_teams(request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    teams = db.query(Team).order_by(Team.created_at.desc()).all()
    result = []
    for t in teams:
        vm_count = db.query(VM).filter(VM.team_id == t.id).count()
        result.append({
            "id": t.id,
            "name": t.name,
            "event_id": t.event_id,
            "event_name": t.event.name if t.event else None,
            "vm_count": vm_count,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        })
    return result


@router.post("/teams")
async def create_team(request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    body = await request.json()
    name = body.get("name", "").strip()
    event_id = body.get("event_id")

    if not name:
        return JSONResponse({"error": "name is required"}, status_code=422)
    if not event_id:
        return JSONResponse({"error": "event_id is required"}, status_code=422)

    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        return JSONResponse({"error": "Event not found"}, status_code=404)

    team = Team(name=name, event_id=event_id)
    db.add(team)
    db.commit()
    db.refresh(team)
    return {"status": "created", "id": team.id}


@router.put("/teams/{team_id}")
async def update_team(team_id: int, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        return JSONResponse({"error": "Team not found"}, status_code=404)

    body = await request.json()
    if "name" in body:
        team.name = body["name"].strip()

    db.commit()
    return {"status": "updated"}


@router.delete("/teams/{team_id}")
async def delete_team(team_id: int, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        return JSONResponse({"error": "Team not found"}, status_code=404)

    vm_count = db.query(VM).filter(VM.team_id == team_id).count()
    if vm_count > 0:
        return JSONResponse(
            {"error": f"Cannot delete team with {vm_count} VMs. Remove VMs first."},
            status_code=409,
        )

    db.delete(team)
    db.commit()
    return {"status": "deleted"}


# ── VMs ────────────────────────────────────────────────────────────────────────

@router.get("/vms")
async def list_vms(request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    vms = db.query(VM).order_by(VM.created_at.desc()).all()
    result = []
    for v in vms:
        total = len(v.modules)
        completed = sum(1 for m in v.modules if m.completed)
        result.append({
            "id": v.id,
            "hostname": v.hostname,
            "ip_address": v.ip_address,
            "os": v.os,
            "status": v.status,
            "team_id": v.team_id,
            "team_name": v.team.name if v.team else None,
            "event_id": v.event_id,
            "event_name": v.event.name if v.event else None,
            "modules_total": total,
            "modules_completed": completed,
            "created_at": v.created_at.isoformat() if v.created_at else None,
        })
    return result


@router.get("/vms/{vm_id}")
async def get_vm(vm_id: int, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    vm = db.query(VM).filter(VM.id == vm_id).first()
    if not vm:
        return JSONResponse({"error": "VM not found"}, status_code=404)

    from builder.module_loader import load_all_modules
    library = {m.id: m for m in load_all_modules()}

    modules = []
    for m in vm.modules:
        lib_mod = library.get(m.module_id)
        modules.append({
            "id": m.id,
            "module_id": m.module_id,
            "name": lib_mod.name if lib_mod else m.module_id,
            "module_type": m.module_type,
            "difficulty": m.difficulty,
            "points": m.points,
            "completed": m.completed,
            "completed_at": m.completed_at.isoformat() if m.completed_at else None,
        })

    return {
        "id": vm.id,
        "hostname": vm.hostname,
        "ip_address": vm.ip_address,
        "os": vm.os,
        "status": vm.status,
        "ssh_port": vm.ssh_port,
        "ssh_user": vm.ssh_user,
        "notes": vm.notes,
        "team_id": vm.team_id,
        "team_name": vm.team.name if vm.team else None,
        "event_id": vm.event_id,
        "event_name": vm.event.name if vm.event else None,
        "modules": modules,
        "created_at": vm.created_at.isoformat() if vm.created_at else None,
        "updated_at": vm.updated_at.isoformat() if vm.updated_at else None,
    }


@router.post("/vms")
async def create_vm(request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    body = await request.json()
    team_id = body.get("team_id")
    if not team_id:
        return JSONResponse({"error": "team_id is required"}, status_code=422)

    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        return JSONResponse({"error": "Team not found"}, status_code=404)

    vm = VM(
        hostname=body.get("hostname") or None,
        ip_address=body.get("ip_address") or None,
        os=body.get("os") or "Ubuntu 22.04",
        status="registered",
        ssh_port=body.get("ssh_port") or 22,
        ssh_user=body.get("ssh_user") or "root",
        notes=body.get("notes") or None,
        team_id=team_id,
        event_id=team.event_id,
    )
    db.add(vm)
    db.commit()
    db.refresh(vm)
    return {"status": "created", "id": vm.id}


@router.put("/vms/{vm_id}")
async def update_vm(vm_id: int, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    vm = db.query(VM).filter(VM.id == vm_id).first()
    if not vm:
        return JSONResponse({"error": "VM not found"}, status_code=404)

    body = await request.json()
    for field in ("hostname", "ip_address", "os", "status", "ssh_port", "ssh_user", "notes"):
        if field in body:
            setattr(vm, field, body[field] or None if field not in ("ssh_port",) else body[field])

    from api.models import utcnow
    vm.updated_at = utcnow()
    db.commit()
    return {"status": "updated"}


@router.delete("/vms/{vm_id}")
async def delete_vm(vm_id: int, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    vm = db.query(VM).filter(VM.id == vm_id).first()
    if not vm:
        return JSONResponse({"error": "VM not found"}, status_code=404)

    db.delete(vm)
    db.commit()
    return {"status": "deleted"}


# ── Module Assignment ──────────────────────────────────────────────────────────

@router.post("/vms/{vm_id}/assign-modules")
async def assign_modules(vm_id: int, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    vm = db.query(VM).filter(VM.id == vm_id).first()
    if not vm:
        return JSONResponse({"error": "VM not found"}, status_code=404)

    event = db.query(Event).filter(Event.id == vm.event_id).first()
    if not event:
        return JSONResponse({"error": "Event not found for this VM"}, status_code=404)

    quota = json.loads(event.quota)

    from builder.module_loader import load_all_modules
    from builder.selector import select_modules

    try:
        library = load_all_modules()
        selected = select_modules(quota, library)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=422)

    # Clear existing modules
    db.query(VMModule).filter(VMModule.vm_id == vm_id).delete()

    # Assign selected modules
    for m in selected:
        db.add(VMModule(
            vm_id=vm_id,
            module_id=m.id,
            module_type=m.type,
            difficulty=m.difficulty,
            points=m.points,
        ))

    from api.models import utcnow
    vm.updated_at = utcnow()
    db.commit()

    return {"status": "assigned", "count": len(selected), "modules": [m.id for m in selected]}


@router.post("/vms/{vm_id}/add-module")
async def add_module(vm_id: int, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    vm = db.query(VM).filter(VM.id == vm_id).first()
    if not vm:
        return JSONResponse({"error": "VM not found"}, status_code=404)

    body = await request.json()
    module_id = body.get("module_id", "").strip()
    if not module_id:
        return JSONResponse({"error": "module_id is required"}, status_code=422)

    # Check not already assigned
    existing = db.query(VMModule).filter(
        VMModule.vm_id == vm_id, VMModule.module_id == module_id
    ).first()
    if existing:
        return JSONResponse({"error": "Module already assigned"}, status_code=409)

    from builder.module_loader import load_all_modules
    library = {m.id: m for m in load_all_modules()}
    mod = library.get(module_id)
    if not mod:
        return JSONResponse({"error": f"Module '{module_id}' not found"}, status_code=404)

    db.add(VMModule(
        vm_id=vm_id,
        module_id=mod.id,
        module_type=mod.type,
        difficulty=mod.difficulty,
        points=mod.points,
    ))
    from api.models import utcnow
    vm.updated_at = utcnow()
    db.commit()
    return {"status": "added"}


@router.delete("/vms/{vm_id}/modules/{module_id}")
async def remove_module(vm_id: int, module_id: str, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    vm = db.query(VM).filter(VM.id == vm_id).first()
    if not vm:
        return JSONResponse({"error": "VM not found"}, status_code=404)

    row = db.query(VMModule).filter(
        VMModule.vm_id == vm_id, VMModule.module_id == module_id
    ).first()
    if not row:
        return JSONResponse({"error": "Module not assigned to this VM"}, status_code=404)

    db.delete(row)
    from api.models import utcnow
    vm.updated_at = utcnow()
    db.commit()
    return {"status": "removed"}


# ── Ansible Export for VM ──────────────────────────────────────────────────────

@router.post("/vms/{vm_id}/ansible-export")
async def vm_ansible_export(vm_id: int, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    vm = db.query(VM).filter(VM.id == vm_id).first()
    if not vm:
        return JSONResponse({"error": "VM not found"}, status_code=404)

    if not vm.modules:
        return JSONResponse({"error": "No modules assigned to this VM"}, status_code=422)

    from builder.ansible import render_playbook, _stage_files
    from builder.module_loader import load_all_modules

    library = {m.id: m for m in load_all_modules()}
    selected = [library[m.module_id] for m in vm.modules if m.module_id in library]

    if not selected:
        return JSONResponse({"error": "No matching modules found in library"}, status_code=422)

    export_id = f"vm_{vm_id}_{uuid.uuid4().hex[:8]}"
    tmpdir = tempfile.mkdtemp(prefix="ctf_vm_export_")
    try:
        from pathlib import Path
        output_dir = Path(tmpdir) / export_id
        output_dir.mkdir()

        playbook_content = render_playbook(selected)
        (output_dir / "playbook.yml").write_text(playbook_content)
        _stage_files(selected, output_dir)

        # Zip into memory
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for fpath in output_dir.rglob("*"):
                if fpath.is_file():
                    zf.write(fpath, fpath.relative_to(output_dir))
        zip_buf.seek(0)

        hostname_slug = (vm.hostname or f"vm{vm_id}").replace(" ", "_")
        filename = f"ansible_{hostname_slug}_{export_id}.zip"

        return StreamingResponse(
            zip_buf,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
