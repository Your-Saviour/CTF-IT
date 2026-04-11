import asyncio
import io
import json
import logging
import os
import shutil
import tempfile
import time
import uuid
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from api.database import get_db
from api.models import Event, Team, VM, VMModule
from api.routes.admin import require_admin

_log = logging.getLogger(__name__)

SHARED_PLAYBOOK_DIR = os.environ.get("SHARED_PLAYBOOK_DIR", "/shared/playbooks")
CALDERA_INTERNAL_URL = os.environ.get("CALDERA_INTERNAL_URL", "http://ctf-caldera:8888")
CALDERA_AGENT_URL = os.environ.get("CALDERA_AGENT_URL", "http://localhost:8888")
VULTR_API_KEY = os.environ.get("VULTR_API_KEY", "")
VULTR_DEFAULT_REGION = os.environ.get("VULTR_DEFAULT_REGION", "ewr")
CLOUDFLARE_API_TOKEN = os.environ.get("CLOUDFLARE_API_TOKEN", "")
CLOUDFLARE_DOMAIN = os.environ.get("CLOUDFLARE_DOMAIN", "")

# Path to the bundled VM provisioning playbooks (relative to project root)
_HERE = Path(__file__).parent.parent.parent
PLAYBOOKS_DIR = _HERE / "playbooks"

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
        # Vultr provisioning fields
        "vultr_id": vm.vultr_id,
        "vultr_plan": vm.vultr_plan,
        "vultr_region": vm.vultr_region,
        "cloudflare_record_id": vm.cloudflare_record_id,
        "provision_step": vm.provision_step,
        "provision_error": vm.provision_error,
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


# ── Platform SSH Key ──────────────────────────────────────────────────────────

@router.get("/platform/ssh-key")
async def get_platform_ssh_key(request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    from api.services.ssh_keys import get_or_create_platform_keypair
    _, public_key = get_or_create_platform_keypair(db)
    return {"public_key": public_key}


# ── Connection Test ───────────────────────────────────────────────────────────

@router.post("/vms/{vm_id}/test-connection")
async def test_connection(vm_id: int, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    vm = db.query(VM).filter(VM.id == vm_id).first()
    if not vm:
        return JSONResponse({"error": "VM not found"}, status_code=404)

    if not vm.ip_address:
        return JSONResponse({"error": "No IP address set on this VM"}, status_code=422)

    from api.services.ssh_keys import get_or_create_platform_keypair
    private_key_pem, _ = get_or_create_platform_keypair(db)

    try:
        import io as _io
        import paramiko

        pkey = paramiko.Ed25519Key.from_private_key(_io.StringIO(private_key_pem))

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=vm.ip_address,
            port=vm.ssh_port or 22,
            username=vm.ssh_user or "root",
            pkey=pkey,
            timeout=10,
            banner_timeout=10,
            auth_timeout=10,
        )
        _, stdout, stderr = client.exec_command("echo ok && hostname && id")
        out = stdout.read().decode().strip()
        err = stderr.read().decode().strip()
        client.close()

        return {"status": "ok", "output": out, "error": err or None}

    except Exception as exc:
        return JSONResponse({"status": "failed", "error": str(exc)}, status_code=200)


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


# ── VM Provisioning ───────────────────────────────────────────────────────────

def _update_provision_step(db, vm, step: str) -> None:
    """Update provision_step and updated_at, commit immediately."""
    from api.models import utcnow
    vm.provision_step = step
    vm.updated_at = utcnow()
    db.commit()


def _run_provision(vm_id: int) -> None:
    """Synchronous background task: provision a VM via Ansible Semaphore.

    Follows the asyncio.to_thread pattern from api/routes/auth.py::_run_build.
    """
    from api.database import SessionLocal
    from api.models import utcnow
    from api.services.semaphore import SemaphoreClient, SemaphoreError
    from api.services.ssh_keys import get_or_create_platform_keypair
    from builder.ansible import _stage_files, render_playbook
    from builder.module_loader import load_all_modules

    db = SessionLocal()
    playbook_dir = None
    try:
        vm = db.query(VM).filter(VM.id == vm_id).first()
        if not vm:
            return

        # ── Step 1: Generate playbook files ──────────────────────────────────
        _update_provision_step(db, vm, "generating_playbook")

        library = {m.id: m for m in load_all_modules()}
        selected = [library[m.module_id] for m in vm.modules if m.module_id in library]
        if not selected:
            raise ValueError("No matching modules found in library")

        export_id = f"vm_{vm_id}_{uuid.uuid4().hex[:8]}"
        playbook_dir = Path(SHARED_PLAYBOOK_DIR) / export_id
        playbook_dir.mkdir(parents=True, exist_ok=True)

        playbook_content = render_playbook(selected)
        (playbook_dir / "playbook.yml").write_text(playbook_content)
        _stage_files(selected, playbook_dir)

        # ── Step 2: Configure Semaphore ───────────────────────────────────────
        _update_provision_step(db, vm, "configuring_semaphore")

        private_key, _ = get_or_create_platform_keypair(db)
        event = vm.event

        with SemaphoreClient() as client:
            client.login()

            # Get-or-create the event-level Semaphore project and SSH key.
            # These are shared across all VMs in the same event.
            if event.semaphore_project_id:
                project_id = event.semaphore_project_id
                key_id = event.semaphore_key_id
            else:
                project_id = client.create_project(f"CTF Event {event.id}: {event.name}")
                key_id = client.create_key(
                    project_id,
                    name="platform-key",
                    private_key_pem=private_key,
                )
                event.semaphore_project_id = project_id
                event.semaphore_key_id = key_id
                db.commit()

            # Per-VM: inventory (target host) + repo (this VM's playbook dir) + template
            inventory_id = client.create_inventory(
                project_id,
                name=f"vm-{vm_id}",
                ip=vm.ip_address,
                ssh_user=vm.ssh_user or "root",
                ssh_port=vm.ssh_port or 22,
                key_id=key_id,
            )
            repo_id = client.create_repository(
                project_id,
                name=f"playbook-vm-{vm_id}",
                local_path=str(playbook_dir),
                key_id=key_id,
            )
            template_id = client.create_template(
                project_id,
                name=f"provision-vm-{vm_id}",
                playbook="playbook.yml",
                inventory_id=inventory_id,
                repository_id=repo_id,
                key_id=key_id,
            )

            # ── Step 3: Run playbook ──────────────────────────────────────────
            _update_provision_step(db, vm, "running_playbook")

            task_id = client.run_task(project_id, template_id)
            vm.semaphore_project_id = project_id
            vm.semaphore_task_id = task_id
            vm.updated_at = utcnow()
            db.commit()

            # ── Step 4: Poll until complete ───────────────────────────────────
            while True:
                status = client.get_task_status(project_id, task_id)
                if status == "success":
                    break
                elif status in ("error", "stopped"):
                    output_lines = client.get_task_output(project_id, task_id)
                    tail = "\n".join(output_lines[-10:]) if output_lines else "(no output)"
                    raise RuntimeError(f"Playbook failed (status={status}):\n{tail}")
                time.sleep(5)

        # ── Success ───────────────────────────────────────────────────────────
        _update_provision_step(db, vm, "completed")
        vm.status = "active"
        vm.updated_at = utcnow()
        db.commit()

    except Exception as exc:
        from api.models import utcnow as _utcnow
        _log.exception("Provision failed for VM %d", vm_id)
        try:
            vm.status = "failed"
            vm.provision_step = "failed"
            vm.provision_error = str(exc)
            vm.updated_at = _utcnow()
            db.commit()
        except Exception:
            pass
    finally:
        db.close()
        # Clean up playbook files after completion (success or failure)
        if playbook_dir and playbook_dir.exists():
            shutil.rmtree(playbook_dir, ignore_errors=True)


@router.post("/vms/{vm_id}/provision")
async def provision_vm(vm_id: int, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    vm = db.query(VM).filter(VM.id == vm_id).first()
    if not vm:
        return JSONResponse({"error": "VM not found"}, status_code=404)

    if not vm.modules:
        return JSONResponse({"error": "No modules assigned to this VM"}, status_code=422)

    if not vm.ip_address:
        return JSONResponse({"error": "No IP address set on this VM"}, status_code=422)

    if vm.status == "provisioning":
        return JSONResponse({"error": "Already provisioning"}, status_code=409)

    from api.models import utcnow
    vm.status = "provisioning"
    vm.provision_step = "generating_playbook"
    vm.provision_error = None
    vm.updated_at = utcnow()
    db.commit()

    asyncio.create_task(asyncio.to_thread(_run_provision, vm_id))
    return {"status": "provisioning", "vm_id": vm_id}


@router.get("/vms/{vm_id}/provision-status")
async def provision_status(vm_id: int, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    vm = db.query(VM).filter(VM.id == vm_id).first()
    if not vm:
        return JSONResponse({"error": "VM not found"}, status_code=404)

    result = {
        "status": vm.status,
        "provision_step": vm.provision_step,
        "provision_error": vm.provision_error,
        "task_output": None,
    }

    # If a Semaphore task is running, try to fetch recent output
    if vm.semaphore_project_id and vm.semaphore_task_id and vm.status == "provisioning":
        try:
            from api.services.semaphore import SemaphoreClient
            with SemaphoreClient() as client:
                client.login()
                lines = client.get_task_output(vm.semaphore_project_id, vm.semaphore_task_id)
                result["task_output"] = lines[-20:] if lines else []
        except Exception:
            pass

    return result


# ── Caldera Agent Deployment ──────────────────────────────────────────────────

def _run_deploy_agent(vm_id: int) -> None:
    """Synchronous background task: deploy Sandcat agent to VM via Semaphore.

    Downloads the Sandcat binary from Caldera, creates a minimal Ansible
    playbook that transfers and starts it on the target, then verifies the
    agent checks in to Caldera.
    """
    import httpx as _httpx

    from api.database import SessionLocal
    from api.models import utcnow
    from api.services.semaphore import SemaphoreClient
    from api.services.ssh_keys import get_or_create_platform_keypair

    db = SessionLocal()
    playbook_dir = None
    try:
        vm = db.query(VM).filter(VM.id == vm_id).first()
        if not vm:
            return

        # ── Step 1: Download Sandcat binary ───────────────────────────────────
        try:
            sandcat_resp = _httpx.get(
                f"{CALDERA_INTERNAL_URL}/file/download",
                headers={"platform": "linux", "file": "sandcat.go"},
                timeout=120.0,
                follow_redirects=True,
            )
            sandcat_resp.raise_for_status()
        except Exception as exc:
            raise RuntimeError(f"Failed to download Sandcat from Caldera: {exc}") from exc

        sandcat_binary = sandcat_resp.content

        # ── Step 2: Write deploy playbook + binary to shared volume ───────────
        export_id = f"agent_{vm_id}_{uuid.uuid4().hex[:8]}"
        playbook_dir = Path(SHARED_PLAYBOOK_DIR) / export_id
        playbook_dir.mkdir(parents=True, exist_ok=True)

        # Write binary
        sandcat_path = playbook_dir / "sandcat"
        sandcat_path.write_bytes(sandcat_binary)

        # Write minimal deploy playbook
        caldera_group = f"event-{vm.event_id}" if vm.event_id else "red"
        deploy_playbook = f"""---
- hosts: all
  become: true
  tasks:
    - name: Copy Sandcat agent binary
      ansible.builtin.copy:
        src: sandcat
        dest: /tmp/sandcat
        mode: '0755'

    - name: Start Sandcat agent in background
      ansible.builtin.shell: >
        nohup /tmp/sandcat
        -server {CALDERA_AGENT_URL}
        -group {caldera_group}
        > /tmp/sandcat.log 2>&1 &
      async: 0
      poll: 0
"""
        (playbook_dir / "playbook.yml").write_text(deploy_playbook)

        # ── Step 3: Run via Semaphore ─────────────────────────────────────────
        private_key, _ = get_or_create_platform_keypair(db)

        with SemaphoreClient() as client:
            client.login()

            project_name = f"CTF Agent {vm.hostname or vm_id}"
            project_id = client.create_project(project_name)

            key_id = client.create_key(project_id, "platform-key", private_key)
            inventory_id = client.create_inventory(
                project_id, "vm-target",
                ip=vm.ip_address,
                ssh_user=vm.ssh_user or "root",
                ssh_port=vm.ssh_port or 22,
                key_id=key_id,
            )
            repo_id = client.create_repository(
                project_id, "agent-deploy", local_path=str(playbook_dir), key_id=key_id,
            )
            template_id = client.create_template(
                project_id, "deploy-agent", "playbook.yml",
                inventory_id, repo_id, key_id,
            )

            task_id = client.run_task(project_id, template_id)
            vm.semaphore_task_id = task_id
            vm.updated_at = utcnow()
            db.commit()

            # Poll until done
            while True:
                status = client.get_task_status(project_id, task_id)
                if status == "success":
                    break
                elif status in ("error", "stopped"):
                    output_lines = client.get_task_output(project_id, task_id)
                    tail = "\n".join(output_lines[-10:]) if output_lines else "(no output)"
                    raise RuntimeError(f"Agent deploy playbook failed:\n{tail}")
                time.sleep(5)

        # ── Step 4: Verify agent check-in ─────────────────────────────────────
        # Poll Caldera for up to 60 seconds
        caldera_api_key = _get_caldera_api_key()
        agent_found = False
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                agents_resp = _httpx.get(
                    f"{CALDERA_INTERNAL_URL}/api/v2/agents",
                    headers={"KEY": caldera_api_key},
                    timeout=10.0,
                )
                if agents_resp.status_code == 200:
                    agents = agents_resp.json()
                    for a in agents:
                        host = a.get("host_ip_addrs", [])
                        if vm.ip_address in host:
                            agent_found = True
                            break
                if agent_found:
                    break
            except Exception:
                pass
            time.sleep(5)

        vm.agent_status = "connected" if agent_found else "deployed"
        vm.updated_at = utcnow()
        db.commit()

    except Exception as exc:
        _log.exception("Agent deploy failed for VM %d", vm_id)
        try:
            from api.models import utcnow as _utcnow
            vm.agent_status = "failed"
            vm.updated_at = _utcnow()
            db.commit()
        except Exception:
            pass
    finally:
        db.close()
        if playbook_dir and playbook_dir.exists():
            shutil.rmtree(playbook_dir, ignore_errors=True)


def _get_caldera_api_key() -> str:
    """Read Caldera API key from local.yml config file."""
    from api.services.caldera import get_caldera_api_key as _get_key
    return _get_key()


@router.post("/vms/{vm_id}/deploy-agent")
async def deploy_agent(vm_id: int, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    vm = db.query(VM).filter(VM.id == vm_id).first()
    if not vm:
        return JSONResponse({"error": "VM not found"}, status_code=404)

    if not vm.ip_address:
        return JSONResponse({"error": "No IP address set on this VM"}, status_code=422)

    if vm.agent_status == "deploying":
        return JSONResponse({"error": "Agent deployment already in progress"}, status_code=409)

    from api.models import utcnow
    vm.agent_status = "deploying"
    vm.updated_at = utcnow()
    db.commit()

    asyncio.create_task(asyncio.to_thread(_run_deploy_agent, vm_id))
    return {"status": "deploying", "vm_id": vm_id}


@router.get("/vms/{vm_id}/agent-status")
async def agent_status(vm_id: int, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    vm = db.query(VM).filter(VM.id == vm_id).first()
    if not vm:
        return JSONResponse({"error": "VM not found"}, status_code=404)

    result: dict = {"agent_status": vm.agent_status}

    # Live check against Caldera if we have an IP
    if vm.ip_address:
        try:
            import httpx as _httpx
            api_key = _get_caldera_api_key()
            if api_key:
                resp = _httpx.get(
                    f"{CALDERA_INTERNAL_URL}/api/v2/agents",
                    headers={"KEY": api_key},
                    timeout=10.0,
                )
                if resp.status_code == 200:
                    for agent in resp.json():
                        if vm.ip_address in agent.get("host_ip_addrs", []):
                            result["caldera_agent"] = {
                                "paw": agent.get("paw"),
                                "last_seen": agent.get("last_seen"),
                                "alive": agent.get("alive", False),
                            }
                            break
        except Exception:
            pass

    return result


# ── Vultr Reference Data ───────────────────────────────────────────────────────

@router.get("/vultr/plans")
async def vultr_plans(request: Request, db: Session = Depends(get_db)):
    """Return Vultr vc2 plans for the VM creation dropdown."""
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    if not VULTR_API_KEY:
        return JSONResponse({"error": "VULTR_API_KEY not configured"}, status_code=503)

    try:
        import httpx as _httpx
        resp = _httpx.get(
            "https://api.vultr.com/v2/plans",
            headers={"Authorization": f"Bearer {VULTR_API_KEY}"},
            params={"type": "vc2", "per_page": 500},
            timeout=15.0,
        )
        resp.raise_for_status()
        plans = resp.json().get("plans", [])
        result = sorted(
            [
                {
                    "id": p["id"],
                    "vcpu_count": p["vcpu_count"],
                    "ram": p["ram"],
                    "disk": p["disk"],
                    "monthly_cost": p["monthly_cost"],
                    "label": (
                        f"{p['id']} — {p['vcpu_count']} vCPU, "
                        f"{p['ram'] // 1024}GB RAM, {p['disk']}GB disk "
                        f"(${p['monthly_cost']}/mo)"
                    ),
                }
                for p in plans
                if p.get("id", "").startswith("vc2-")
            ],
            key=lambda x: x["monthly_cost"],
        )
        return {"plans": result}
    except Exception as exc:
        return JSONResponse({"error": f"Failed to fetch Vultr plans: {exc}"}, status_code=502)


@router.get("/vultr/os")
async def vultr_os_list(request: Request, db: Session = Depends(get_db)):
    """Return Vultr OS options for the VM creation dropdown."""
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    if not VULTR_API_KEY:
        return JSONResponse({"error": "VULTR_API_KEY not configured"}, status_code=503)

    try:
        import httpx as _httpx
        resp = _httpx.get(
            "https://api.vultr.com/v2/os",
            headers={"Authorization": f"Bearer {VULTR_API_KEY}"},
            params={"per_page": 500},
            timeout=15.0,
        )
        resp.raise_for_status()
        os_list = resp.json().get("os", [])
        result = sorted(
            [{"id": o["id"], "name": o["name"], "family": o.get("family", "")} for o in os_list],
            key=lambda x: x["name"],
        )
        return {"os": result}
    except Exception as exc:
        return JSONResponse({"error": f"Failed to fetch Vultr OS list: {exc}"}, status_code=502)


# ── Vultr VM Creation ──────────────────────────────────────────────────────────

def _run_vultr_create(vm_id: int) -> None:
    """Synchronous background task: create a Vultr VPS and update the VM record.

    Runs the bundled create-vm.yml Ansible playbook via Semaphore, then
    parses task output to extract the assigned IP and Vultr instance ID.
    """
    import re as _re
    import shutil as _shutil

    from api.database import SessionLocal
    from api.models import utcnow
    from api.services.semaphore import SemaphoreClient
    from api.services.ssh_keys import get_or_create_platform_keypair

    db = SessionLocal()
    playbook_dir = None
    try:
        vm = db.query(VM).filter(VM.id == vm_id).first()
        if not vm:
            return

        # ── Step 1: Stage playbook files ─────────────────────────────────────
        _update_provision_step(db, vm, "staging_playbook")

        export_id = f"vultr_{vm_id}_{uuid.uuid4().hex[:8]}"
        playbook_dir = Path(SHARED_PLAYBOOK_DIR) / export_id
        playbook_dir.mkdir(parents=True, exist_ok=True)

        _shutil.copy(PLAYBOOKS_DIR / "create-vm.yml", playbook_dir / "create-vm.yml")
        collections_dir = playbook_dir / "collections"
        collections_dir.mkdir(exist_ok=True)
        _shutil.copy(
            PLAYBOOKS_DIR / "collections" / "requirements.yml",
            collections_dir / "requirements.yml",
        )

        # ── Step 2: Build extra vars ──────────────────────────────────────────
        _update_provision_step(db, vm, "configuring_semaphore")

        private_key, public_key = get_or_create_platform_keypair(db)

        extra_vars: dict = {
            "vm_hostname": vm.hostname or f"ctf-vm-{vm_id}",
            "vm_plan": vm.vultr_plan or "vc2-1c-1gb",
            "vm_os": vm.os or "Ubuntu 24.04 LTS x64",
            "vm_region": vm.vultr_region or VULTR_DEFAULT_REGION,
            "ssh_key_name": "ctf-platform",
            "ssh_public_key": public_key,
            "vultr_api_key": VULTR_API_KEY,
        }
        if CLOUDFLARE_API_TOKEN and CLOUDFLARE_DOMAIN:
            extra_vars["cloudflare_api_key"] = CLOUDFLARE_API_TOKEN
            extra_vars["domain_name"] = CLOUDFLARE_DOMAIN

        # ── Step 3: Create Semaphore project + run playbook ───────────────────
        _update_provision_step(db, vm, "creating_instance")

        with SemaphoreClient() as client:
            client.login()

            project_id = client.create_project(f"CTF Vultr Create VM {vm_id}")
            key_id = client.create_key(project_id, "platform-key", private_key)
            inventory_id = client.create_localhost_inventory(project_id, "localhost", key_id)
            repo_id = client.create_repository(
                project_id, f"create-vm-{vm_id}", str(playbook_dir), key_id
            )
            template_id = client.create_template(
                project_id, f"create-vm-{vm_id}", "create-vm.yml",
                inventory_id, repo_id, key_id,
                extra_vars=extra_vars,
            )

            task_id = client.run_task(project_id, template_id)
            vm.semaphore_task_id = task_id
            vm.updated_at = utcnow()
            db.commit()

            # Poll until complete
            while True:
                status = client.get_task_status(project_id, task_id)
                if status == "success":
                    break
                elif status in ("error", "stopped"):
                    output_lines = client.get_task_output(project_id, task_id)
                    tail = "\n".join(output_lines[-10:]) if output_lines else "(no output)"
                    raise RuntimeError(f"create-vm.yml failed (status={status}):\n{tail}")
                time.sleep(10)

            output_lines = client.get_task_output(project_id, task_id)

        # ── Step 4: Extract results from playbook output ──────────────────────
        _update_provision_step(db, vm, "extracting_results")

        vultr_result = None
        for line in reversed(output_lines):
            match = _re.search(r'VULTR_RESULT=(\{.*\})', line)
            if match:
                try:
                    vultr_result = json.loads(match.group(1))
                except json.JSONDecodeError:
                    pass
                break

        if not vultr_result or not vultr_result.get("ip"):
            raise RuntimeError(
                "Could not extract VM IP from playbook output. "
                "Check Semaphore task logs for details."
            )

        vm.ip_address = vultr_result["ip"]
        vm.vultr_id = vultr_result.get("vultr_id", "")
        vm.cloudflare_record_id = vultr_result.get("dns_record_id") or None
        vm.status = "registered"
        vm.provision_step = "completed"
        vm.updated_at = utcnow()
        db.commit()

    except Exception as exc:
        from api.models import utcnow as _utcnow
        _log.exception("Vultr VM creation failed for VM %d", vm_id)
        try:
            vm.status = "failed"
            vm.provision_step = "failed"
            vm.provision_error = str(exc)
            vm.updated_at = _utcnow()
            db.commit()
        except Exception:
            pass
    finally:
        db.close()
        if playbook_dir and playbook_dir.exists():
            shutil.rmtree(playbook_dir, ignore_errors=True)


@router.post("/vms/create-vultr")
async def create_vm_on_vultr(request: Request, db: Session = Depends(get_db)):
    """Create a new Vultr VPS and register it as a VM."""
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    if not VULTR_API_KEY:
        return JSONResponse({"error": "VULTR_API_KEY not configured"}, status_code=503)

    body = await request.json()
    team_id = body.get("team_id")
    if not team_id:
        return JSONResponse({"error": "team_id is required"}, status_code=422)

    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        return JSONResponse({"error": "Team not found"}, status_code=404)

    hostname = (body.get("hostname") or "").strip()
    if not hostname:
        return JSONResponse({"error": "hostname is required"}, status_code=422)

    vultr_plan = (body.get("vultr_plan") or "").strip()
    if not vultr_plan:
        return JSONResponse({"error": "vultr_plan is required"}, status_code=422)

    from api.models import utcnow
    vm = VM(
        hostname=hostname,
        os=body.get("vultr_os") or "Ubuntu 24.04 LTS x64",
        status="creating",
        ssh_user=body.get("ssh_user") or "root",
        ssh_port=22,
        notes=body.get("notes") or None,
        team_id=team_id,
        event_id=team.event_id,
        vultr_plan=vultr_plan,
        vultr_region=VULTR_DEFAULT_REGION,
        provision_step="staging_playbook",
    )
    db.add(vm)
    db.commit()
    db.refresh(vm)

    asyncio.create_task(asyncio.to_thread(_run_vultr_create, vm.id))
    return {"status": "creating", "id": vm.id}


@router.get("/vms/{vm_id}/create-status")
async def create_status(vm_id: int, request: Request, db: Session = Depends(get_db)):
    """Poll Vultr VM creation progress."""
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    vm = db.query(VM).filter(VM.id == vm_id).first()
    if not vm:
        return JSONResponse({"error": "VM not found"}, status_code=404)

    return {
        "status": vm.status,
        "provision_step": vm.provision_step,
        "provision_error": vm.provision_error,
        "ip_address": vm.ip_address,
        "vultr_id": vm.vultr_id,
    }


# ── Vultr VM Destruction ───────────────────────────────────────────────────────

def _run_vultr_destroy(vm_id: int) -> None:
    """Synchronous background task: destroy a Vultr VPS and clean up DNS."""
    import shutil as _shutil

    from api.database import SessionLocal
    from api.models import utcnow
    from api.services.semaphore import SemaphoreClient
    from api.services.ssh_keys import get_or_create_platform_keypair

    db = SessionLocal()
    playbook_dir = None
    try:
        vm = db.query(VM).filter(VM.id == vm_id).first()
        if not vm:
            return

        if not vm.vultr_id and not vm.hostname:
            raise ValueError("VM has no Vultr ID or hostname — cannot destroy")

        export_id = f"vultr_destroy_{vm_id}_{uuid.uuid4().hex[:8]}"
        playbook_dir = Path(SHARED_PLAYBOOK_DIR) / export_id
        playbook_dir.mkdir(parents=True, exist_ok=True)

        _shutil.copy(PLAYBOOKS_DIR / "destroy-vm.yml", playbook_dir / "destroy-vm.yml")
        collections_dir = playbook_dir / "collections"
        collections_dir.mkdir(exist_ok=True)
        _shutil.copy(
            PLAYBOOKS_DIR / "collections" / "requirements.yml",
            collections_dir / "requirements.yml",
        )

        private_key, _ = get_or_create_platform_keypair(db)

        extra_vars: dict = {
            "instance_label": vm.hostname or f"ctf-vm-{vm_id}",
            "instance_region": vm.vultr_region or VULTR_DEFAULT_REGION,
            "vultr_api_key": VULTR_API_KEY,
        }
        if vm.cloudflare_record_id and CLOUDFLARE_API_TOKEN and CLOUDFLARE_DOMAIN:
            extra_vars["dns_hostname"] = vm.hostname or ""
            extra_vars["domain_name"] = CLOUDFLARE_DOMAIN
            extra_vars["cloudflare_api_key"] = CLOUDFLARE_API_TOKEN

        with SemaphoreClient() as client:
            client.login()

            project_id = client.create_project(f"CTF Vultr Destroy VM {vm_id}")
            key_id = client.create_key(project_id, "platform-key", private_key)
            inventory_id = client.create_localhost_inventory(project_id, "localhost", key_id)
            repo_id = client.create_repository(
                project_id, f"destroy-vm-{vm_id}", str(playbook_dir), key_id
            )
            template_id = client.create_template(
                project_id, f"destroy-vm-{vm_id}", "destroy-vm.yml",
                inventory_id, repo_id, key_id,
                extra_vars=extra_vars,
            )
            task_id = client.run_task(project_id, template_id)

            while True:
                status = client.get_task_status(project_id, task_id)
                if status == "success":
                    break
                elif status in ("error", "stopped"):
                    output_lines = client.get_task_output(project_id, task_id)
                    tail = "\n".join(output_lines[-10:]) if output_lines else "(no output)"
                    raise RuntimeError(f"destroy-vm.yml failed (status={status}):\n{tail}")
                time.sleep(10)

        db.delete(vm)
        db.commit()

    except Exception as exc:
        from api.models import utcnow as _utcnow
        _log.exception("Vultr VM destruction failed for VM %d", vm_id)
        try:
            vm.status = "failed"
            vm.provision_error = str(exc)
            vm.updated_at = _utcnow()
            db.commit()
        except Exception:
            pass
    finally:
        db.close()
        if playbook_dir and playbook_dir.exists():
            shutil.rmtree(playbook_dir, ignore_errors=True)


@router.post("/vms/{vm_id}/destroy-vultr")
async def destroy_vm_on_vultr(vm_id: int, request: Request, db: Session = Depends(get_db)):
    """Destroy the Vultr VPS backing this VM and delete the VM record."""
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    vm = db.query(VM).filter(VM.id == vm_id).first()
    if not vm:
        return JSONResponse({"error": "VM not found"}, status_code=404)

    if not vm.vultr_id and not vm.hostname:
        return JSONResponse(
            {"error": "VM has no Vultr instance associated"}, status_code=422
        )

    if vm.status in ("creating", "destroying"):
        return JSONResponse(
            {"error": f"VM is currently {vm.status}, cannot destroy now"}, status_code=409
        )

    from api.models import utcnow
    vm.status = "destroying"
    vm.updated_at = utcnow()
    db.commit()

    asyncio.create_task(asyncio.to_thread(_run_vultr_destroy, vm_id))
    return {"status": "destroying", "vm_id": vm_id}


# ── Topology Data ─────────────────────────────────────────────────────────────

# Predefined team colors cycled by index
_TEAM_COLORS = [
    "#ffb400", "#b400ff", "#00c8ff", "#ff6b6b", "#00ff88",
    "#ff9100", "#7c4dff", "#00bfa5", "#ff4081", "#64dd17",
]


@router.get("/topology-data")
async def topology_data(
    request: Request,
    event_id: int = None,
    db: Session = Depends(get_db),
):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    # Query events (non-draft unless filtered)
    eq = db.query(Event)
    if event_id:
        eq = eq.filter(Event.id == event_id)
    else:
        eq = eq.filter(Event.status != "draft")
    events = eq.all()

    nodes = []
    links = []
    team_color_map = {}
    color_idx = 0

    for event in events:
        event_node_id = f"event-{event.id}"
        nodes.append({
            "id": event_node_id,
            "type": "event",
            "label": event.name,
            "status": event.status,
        })

        teams = db.query(Team).filter(Team.event_id == event.id).all()
        for team in teams:
            team_node_id = f"team-{team.id}"
            if team.id not in team_color_map:
                team_color_map[team.id] = _TEAM_COLORS[color_idx % len(_TEAM_COLORS)]
                color_idx += 1

            nodes.append({
                "id": team_node_id,
                "type": "team",
                "label": team.name,
                "event_id": event_node_id,
                "color": team_color_map[team.id],
            })
            links.append({"source": event_node_id, "target": team_node_id})

            vms = db.query(VM).filter(VM.team_id == team.id).all()
            for vm in vms:
                total = len(vm.modules)
                completed = sum(1 for m in vm.modules if m.completed)
                vm_node_id = f"vm-{vm.id}"
                nodes.append({
                    "id": vm_node_id,
                    "type": "vm",
                    "label": vm.hostname or f"vm-{vm.id}",
                    "hostname": vm.hostname,
                    "ip": vm.ip_address,
                    "status": vm.status,
                    "os": vm.os,
                    "team_id": team_node_id,
                    "event_id": event_node_id,
                    "modules_total": total,
                    "modules_completed": completed,
                })
                links.append({"source": team_node_id, "target": vm_node_id})

    return {"nodes": nodes, "links": links}
