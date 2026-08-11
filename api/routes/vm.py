import asyncio
import io
import json
import logging
import os
import re
import shutil
import socket
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from api.database import get_db
from api.models import Event, Team, VM, VMModule, VMGoal
from api.routes.admin import require_admin
from api.services.secrets import decrypt_secret, encrypt_secret

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
TEMPLATES_DIR = _HERE / "templates"

router = APIRouter(prefix="/admin/api", tags=["admin"])


def _record_vm_failure(vm_id: int, error: str, *, agent: bool = False) -> None:
    """Persist background-worker failure using a clean transaction."""
    from api.database import SessionLocal
    from api.models import utcnow

    failure_db = SessionLocal()
    try:
        failed_vm = failure_db.query(VM).filter(VM.id == vm_id).first()
        if not failed_vm:
            return
        if agent:
            failed_vm.agent_status = "failed"
        else:
            failed_vm.status = "failed"
            failed_vm.provision_step = "failed"
            failed_vm.provision_error = error[:4000]
        failed_vm.updated_at = utcnow()
        failure_db.commit()
    except Exception:
        failure_db.rollback()
        _log.exception("Could not persist failure state for VM %d", vm_id)
    finally:
        failure_db.close()


def _vultr_safe_hostname(value: str, fallback: str) -> str:
    """Return a hostname that is also safe to use as a DNS record label.

    Vultr rejects hostnames containing spaces and Cloudflare record labels have
    the same practical restriction. Team names are user-controlled, so never
    pass them through to either provider unchanged.
    """
    hostname = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    hostname = hostname[:63].rstrip("-")
    return hostname or fallback


def _wait_for_tcp_port(host: str, port: int, timeout_seconds: int = 600) -> None:
    """Wait for a newly-created cloud VM to accept connections.

    Vultr returns an instance IP before its operating system has completed its
    first boot. Starting Ansible immediately turns that normal delay into an
    avoidable provisioning failure.
    """
    deadline = time.monotonic() + timeout_seconds
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=5):
                return
        except OSError as exc:
            last_error = exc
            time.sleep(5)
    raise RuntimeError(
        f"Timed out waiting for {host}:{port} to accept connections"
        + (f" ({last_error})" if last_error else "")
    )


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
async def get_vm(vm_id: int, request: Request, db: Session = Depends(get_db), include_password: bool = False):
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

    result = {
        "id": vm.id,
        "hostname": vm.hostname,
        "ip_address": vm.ip_address,
        "os": vm.os,
        "status": vm.status,
        "ssh_port": vm.ssh_port,
        "ssh_user": vm.ssh_user,
        "ssh_host_key": vm.ssh_host_key,
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
        "vm_type": vm.vm_type,
        "base_type": vm.base_type,
        "vpc_ip": vm.vpc_ip,
    }

    # Only include password when explicitly requested
    if include_password and vm.admin_password:
        result["admin_password"] = decrypt_secret(vm.admin_password)

    return result


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
        selected = select_modules(quota, library, base_type_id=vm.base_type)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=422)

    # Clear existing modules and goals
    db.query(VMModule).filter(VMModule.vm_id == vm_id).delete()
    db.query(VMGoal).filter(VMGoal.vm_id == vm_id).delete()

    # Assign selected modules; create VMGoal records for goal-type modules
    for m in selected:
        db.add(VMModule(
            vm_id=vm_id,
            module_id=m.id,
            module_type=m.type,
            difficulty=m.difficulty,
            points=m.points,
            stage=m.stage,
        ))
        if m.type == "goal":
            db.add(VMGoal(
                vm_id=vm_id,
                module_id=m.id,
                status="pending",
                red_points=m.red_points,
                defend_points=m.defend_points,
            ))

    from api.models import utcnow
    vm.updated_at = utcnow()
    db.commit()

    goal_count = sum(1 for m in selected if m.type == "goal")
    return {
        "status": "assigned",
        "count": len(selected),
        "goal_count": goal_count,
        "modules": [m.id for m in selected],
    }


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
        stage=mod.stage,
    ))
    if mod.type == "goal":
        db.add(VMGoal(
            vm_id=vm_id,
            module_id=mod.id,
            status="pending",
            red_points=mod.red_points,
            defend_points=mod.defend_points,
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

    try:
        from api.services.ssh_connection import connect_vm
        client = connect_vm(vm, db)
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

    Two-phase provisioning:
    1. Base playbook (if vm.base_type is set) — installs base packages/config
    2. Module playbook — applies the VM's assigned CTF modules

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
    base_playbook_dir = None
    try:
        vm = db.query(VM).filter(VM.id == vm_id).first()
        if not vm:
            return

        # A cloud API can report the VM and its IP before sshd is accepting
        # connections. Wait here so both base and module playbooks start only
        # after the target is actually reachable.
        if vm.ip_address:
            _update_provision_step(db, vm, "waiting_for_ssh")
            _wait_for_tcp_port(vm.ip_address, vm.ssh_port or 22)

        vm.status = "provisioning"

        # ── Phase 0: Base playbook (optional) ────────────────────────────────
        if vm.base_type:
            from builder.base_ansible import render_base_playbook, stage_base_files
            from builder.base_loader import load_base_type

            _update_provision_step(db, vm, "generating_base_playbook")

            base_type_obj = load_base_type(vm.base_type)
            base_export_id = f"base_{vm_id}_{uuid.uuid4().hex[:8]}"
            base_playbook_dir = Path(SHARED_PLAYBOOK_DIR) / base_export_id
            base_playbook_dir.mkdir(parents=True, exist_ok=True)

            base_playbook_content = render_base_playbook(base_type_obj)
            (base_playbook_dir / "playbook.yml").write_text(base_playbook_content)
            stage_base_files(base_type_obj, base_playbook_dir)

            _update_provision_step(db, vm, "running_base_playbook")

            private_key, _ = get_or_create_platform_keypair(db)
            event = vm.event

            with SemaphoreClient() as client:
                client.login()

                # Get-or-create the event-level Semaphore project and SSH key.
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

                inventory_id = client.create_inventory(
                    project_id,
                    name=f"vm-{vm_id}-base",
                    ip=vm.ip_address,
                    ssh_user=vm.ssh_user or "root",
                    ssh_port=vm.ssh_port or 22,
                    key_id=key_id,
                )
                repo_id = client.create_repository(
                    project_id,
                    name=f"base-playbook-vm-{vm_id}",
                    local_path=str(base_playbook_dir),
                    key_id=key_id,
                )
                template_id = client.create_template(
                    project_id,
                    name=f"base-provision-vm-{vm_id}",
                    playbook="playbook.yml",
                    inventory_id=inventory_id,
                    repository_id=repo_id,
                    key_id=key_id,
                )

                task_id = client.run_task(project_id, template_id)
                vm.semaphore_project_id = project_id
                vm.semaphore_task_id = task_id
                vm.updated_at = utcnow()
                db.commit()

                while True:
                    status = client.get_task_status(project_id, task_id)
                    if status == "success":
                        break
                    elif status in ("error", "stopped"):
                        output_lines = client.get_task_output(project_id, task_id)
                        tail = "\n".join(output_lines[-10:]) if output_lines else "(no output)"
                        raise RuntimeError(f"Base playbook failed (status={status}):\n{tail}")
                    time.sleep(5)

            # Clean up base playbook files now that it succeeded
            if base_playbook_dir and base_playbook_dir.exists():
                shutil.rmtree(base_playbook_dir, ignore_errors=True)
                base_playbook_dir = None

        # ── Phase 1: Module playbook ─────────────────────────────────────────
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

        # Learner and verification accounts are distinct from platform
        # automation and are installed only after all module content exists.
        from api.services.training_provisioning import finalize_training_vm
        training_report = finalize_training_vm(db, vm)
        if training_report["verifier"] == "failed" or training_report["credential"] == "failed":
            _log.warning("Training controls are not ready on VM %d", vm.id)

        # If this VM has a VPC IP assigned, configure its VPC network interface.
        # Spawned as a daemon thread so _run_provision's DB session can close
        # before the (potentially long) netplan config task begins.
        if vm.vpc_ip:
            import threading as _threading
            _threading.Thread(
                target=_run_configure_vpc_interface, args=(vm_id,), daemon=True
            ).start()

    except Exception as exc:
        _log.exception("Provision failed for VM %d", vm_id)
        db.rollback()
        _record_vm_failure(vm_id, str(exc))
    finally:
        db.close()
        # Clean up playbook files after completion (success or failure)
        if playbook_dir and playbook_dir.exists():
            shutil.rmtree(playbook_dir, ignore_errors=True)
        if base_playbook_dir and base_playbook_dir.exists():
            shutil.rmtree(base_playbook_dir, ignore_errors=True)


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
    vm.provision_step = "generating_base_playbook" if vm.base_type else "generating_playbook"
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
        db.rollback()
        _record_vm_failure(vm_id, str(exc), agent=True)
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
                        f"{p['ram'] / 1024:g}GB RAM, {p['disk']}GB disk "
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


# ── Shared Vultr Semaphore project ─────────────────────────────────────────────

_VULTR_PROJECT_ID_KEY = "vultr_semaphore_project_id"
_VULTR_KEY_ID_KEY = "vultr_semaphore_key_id"
_VULTR_PROJECT_LOCK = threading.Lock()


def _get_or_create_vultr_semaphore_project(db, client, private_key: str) -> tuple[int, int]:
    """Return (project_id, key_id) for the shared 'CTF Vultr Operations' Semaphore project.

    Creates the project and SSH key on first call and persists their IDs in
    PlatformSettings so subsequent calls reuse the same project.
    """
    from api.models import PlatformSettings

    # Event provisioning creates several VMs concurrently. Serialize the
    # shared Semaphore bootstrap so only one worker creates the project/key;
    # later workers then reuse the committed settings.
    with _VULTR_PROJECT_LOCK:
        proj_row = db.query(PlatformSettings).filter_by(key=_VULTR_PROJECT_ID_KEY).first()
        key_row = db.query(PlatformSettings).filter_by(key=_VULTR_KEY_ID_KEY).first()

        if proj_row and key_row:
            return int(proj_row.value), int(key_row.value)

        project_id = client.create_project("CTF Vultr Operations")
        key_id = client.create_key(project_id, "platform-key", private_key)

        db.add(PlatformSettings(key=_VULTR_PROJECT_ID_KEY, value=str(project_id)))
        db.add(PlatformSettings(key=_VULTR_KEY_ID_KEY, value=str(key_id)))
        db.commit()

        return project_id, key_id


# ── VPC Creation ───────────────────────────────────────────────────────────────

def _create_team_vpc(team_id: int, event_id: int, region: str) -> None:
    """Create a Vultr VPC for a team and store the VPC ID on the team record.

    Uses the Vultr REST API directly (no Semaphore needed for a single API call).
    VPC description format: "ctf-event-{event_id}-team-{team_index}"
    Subnet format: "10.{team_index}.1.0/24"
    """
    import httpx as _httpx

    from api.database import SessionLocal

    if not VULTR_API_KEY:
        _log.error("_create_team_vpc: VULTR_API_KEY is not configured — skipping VPC creation for team %d", team_id)
        return

    db = SessionLocal()
    try:
        team = db.query(Team).filter(Team.id == team_id).first()
        if not team or team.team_index is None:
            _log.error("_create_team_vpc: team %d not found or missing team_index", team_id)
            return

        vpc_description = f"ctf-event-{event_id}-team-{team.team_index}"
        v4_subnet = f"10.{team.team_index}.1.0"

        _log.info(
            "Creating VPC '%s' (%s/24) in region %s for team %d",
            vpc_description, v4_subnet, region, team_id,
        )

        resp = _httpx.post(
            "https://api.vultr.com/v2/vpcs",
            headers={"Authorization": f"Bearer {VULTR_API_KEY}"},
            json={
                "region": region,
                "v4_subnet": v4_subnet,
                "v4_subnet_mask": 24,
                "description": vpc_description,
            },
            timeout=30.0,
        )
        resp.raise_for_status()

        vpc_id = resp.json()["vpc"]["id"]
        team.vpc_id = vpc_id
        db.commit()

        _log.info("VPC '%s' created with ID %s", vpc_description, vpc_id)

    except Exception as exc:
        _log.exception("Failed to create VPC for team %d: %s", team_id, exc)
    finally:
        db.close()


# ── OPNsense Firewall Provisioning ─────────────────────────────────────────────

def _run_firewall_create(vm_id: int) -> None:
    """Synchronous background task: create a Vultr FreeBSD VM, attach it to the team VPC,
    then bootstrap it into OPNsense.

    Stores admin password on vm.admin_password.
    Sets vm.status = "active" on success, "failed" on error.
    """
    import re as _re
    import shutil as _shutil

    import bcrypt as _bcrypt

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
        team = db.query(Team).filter(Team.id == vm.team_id).first()
        if not team:
            raise RuntimeError(f"Team {vm.team_id} not found for firewall VM {vm_id}")

        # ── Stage 1: Create Vultr VM via create-firewall.yml ────────────────
        _update_provision_step(db, vm, "staging_playbook")

        export_id = f"firewall_{vm_id}_{uuid.uuid4().hex[:8]}"
        playbook_dir = Path(SHARED_PLAYBOOK_DIR) / export_id
        playbook_dir.mkdir(parents=True, exist_ok=True)

        _shutil.copy(PLAYBOOKS_DIR / "create-firewall.yml", playbook_dir / "create-firewall.yml")
        collections_dir = playbook_dir / "collections"
        collections_dir.mkdir(exist_ok=True)
        _shutil.copy(
            PLAYBOOKS_DIR / "collections" / "requirements.yml",
            collections_dir / "requirements.yml",
        )

        _update_provision_step(db, vm, "configuring_semaphore")

        private_key, public_key = get_or_create_platform_keypair(db)

        vpc_description = f"ctf-event-{vm.event_id}-team-{team.team_index}"

        extra_vars: dict = {
            "vm_hostname": _vultr_safe_hostname(vm.hostname or "", f"ctf-fw-{vm_id}"),
            "vm_plan": vm.vultr_plan or "vc2-2c-4gb",
            "vm_region": vm.vultr_region or VULTR_DEFAULT_REGION,
            "ssh_key_name": "ctf-platform",
            "ssh_public_key": public_key,
            "vultr_api_key": VULTR_API_KEY,
            "vpc_description": vpc_description,
        }
        if CLOUDFLARE_API_TOKEN and CLOUDFLARE_DOMAIN:
            extra_vars["cloudflare_api_key"] = CLOUDFLARE_API_TOKEN
            extra_vars["domain_name"] = CLOUDFLARE_DOMAIN

        _update_provision_step(db, vm, "creating_instance")

        with SemaphoreClient() as client:
            client.login()
            project_id, key_id = _get_or_create_vultr_semaphore_project(db, client, private_key)
            inv_id = client.create_localhost_inventory(project_id, f"localhost-fw-{vm_id}", key_id)
            repo_id = client.create_repository(
                project_id, f"create-fw-{vm_id}", str(playbook_dir), key_id
            )
            tmpl_id = client.create_template(
                project_id, f"create-fw-{vm_id}", "create-firewall.yml",
                inv_id, repo_id, key_id, extra_vars=extra_vars,
            )
            task_id = client.run_task(project_id, tmpl_id)
            vm.semaphore_task_id = task_id
            vm.updated_at = utcnow()
            db.commit()

            while True:
                status = client.get_task_status(project_id, task_id)
                if status == "success":
                    break
                elif status in ("error", "stopped"):
                    output_lines = client.get_task_output(project_id, task_id)
                    tail = "\n".join(output_lines[-10:]) if output_lines else "(no output)"
                    raise RuntimeError(f"create-firewall.yml failed:\n{tail}")
                time.sleep(10)

            output_lines = client.get_task_output(project_id, task_id)

        # Parse IP from output
        _update_provision_step(db, vm, "extracting_results")
        _ansi = _re.compile(r'\x1b\[[0-9;]*[mGKHF]')
        cleaned = " ".join(_ansi.sub('', line).strip() for line in output_lines)

        # Use non-greedy match with character class to avoid catastrophic backtracking
        match = _re.search(r'VULTR_RESULT=(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})', cleaned)
        if not match:
            raise RuntimeError("Could not extract firewall VM IP from playbook output")
        try:
            vultr_result = json.loads(match.group(1))
        except json.JSONDecodeError:
            vultr_result = None
        if not vultr_result or not vultr_result.get("ip"):
            raise RuntimeError("Could not extract firewall VM IP from playbook output")

        vm.ip_address = vultr_result["ip"]
        vm.vultr_id = vultr_result.get("vultr_id", "")
        vm.cloudflare_record_id = vultr_result.get("dns_record_id") or None
        vm.status = "registered"
        vm.provision_step = "bootstrapping_opnsense"
        vm.updated_at = utcnow()
        db.commit()

        # ── Stage 2: Bootstrap OPNsense ──────────────────────────────────────
        _update_provision_step(db, vm, "waiting_for_ssh")
        _wait_for_tcp_port(vm.ip_address, 22)

        # Generate admin credentials in Python (no Ansible passlib dependency needed)
        import secrets as _secrets
        import string as _string

        # Use cryptographically secure random generation for admin password
        alphabet = _string.ascii_letters + _string.digits
        admin_password = ''.join(_secrets.choice(alphabet) for _ in range(20))

        # OPNsense expects bcrypt $2y$ format; Python bcrypt produces $2b$ which OPNsense accepts
        password_hash = _bcrypt.hashpw(
            admin_password.encode(), _bcrypt.gensalt(rounds=10)
        ).decode()

        # Persist the encrypted credential immediately to prevent plaintext exposure
        vm.admin_password = encrypt_secret(admin_password)
        vm.updated_at = utcnow()
        db.commit()

        bootstrap_dir = playbook_dir / "bootstrap"
        bootstrap_dir.mkdir(exist_ok=True)
        _shutil.copy(
            PLAYBOOKS_DIR / "bootstrap-opnsense.yml",
            bootstrap_dir / "bootstrap-opnsense.yml",
        )
        # Stage templates/ subdirectory so Ansible finds opnsense_config.xml.j2
        bootstrap_templates_dir = bootstrap_dir / "templates"
        bootstrap_templates_dir.mkdir(exist_ok=True)
        _shutil.copy(
            TEMPLATES_DIR / "opnsense_config.xml.j2",
            bootstrap_templates_dir / "opnsense_config.xml.j2",
        )
        bootstrap_collections_dir = bootstrap_dir / "collections"
        bootstrap_collections_dir.mkdir(exist_ok=True)
        _shutil.copy(
            PLAYBOOKS_DIR / "collections" / "requirements.yml",
            bootstrap_collections_dir / "requirements.yml",
        )

        team_index = team.team_index or 1
        bootstrap_extra_vars = {
            "opnsense_hostname": _vultr_safe_hostname(vm.hostname or "", f"ctf-fw-{vm_id}"),
            "opnsense_lan_ip": f"10.{team_index}.1.1",
            "opnsense_lan_subnet": 24,
            "opnsense_admin_password_hash": password_hash,
            "opnsense_ssh_pubkey": public_key,
            "opnsense_release": "25.1",
        }

        firewall_ip = vm.ip_address
        with SemaphoreClient() as client:
            client.login()
            project_id, key_id = _get_or_create_vultr_semaphore_project(db, client, private_key)
            # Remote inventory pointing to the firewall's public IP
            fw_inv_id = client.create_inventory(
                project_id, f"fw-host-{vm_id}", firewall_ip,
                ssh_user="root", ssh_port=22, key_id=key_id,
            )
            repo_id = client.create_repository(
                project_id, f"bootstrap-fw-{vm_id}", str(bootstrap_dir), key_id
            )
            tmpl_id = client.create_template(
                project_id, f"bootstrap-fw-{vm_id}", "bootstrap-opnsense.yml",
                fw_inv_id, repo_id, key_id, extra_vars=bootstrap_extra_vars,
            )
            task_id = client.run_task(project_id, tmpl_id)
            vm.semaphore_task_id = task_id
            vm.updated_at = utcnow()
            db.commit()

            # Bootstrap takes 10-15 minutes; poll patiently
            while True:
                status = client.get_task_status(project_id, task_id)
                if status == "success":
                    break
                elif status in ("error", "stopped"):
                    output_lines = client.get_task_output(project_id, task_id)
                    tail = "\n".join(output_lines[-10:]) if output_lines else "(no output)"
                    raise RuntimeError(f"bootstrap-opnsense.yml failed:\n{tail}")
                time.sleep(15)

        # Store credentials and mark active
        vm.vpc_ip = f"10.{team_index}.1.1"
        vm.status = "active"
        vm.provision_step = "completed"
        vm.updated_at = utcnow()
        db.commit()

        _log.info("Firewall VM %d (%s) provisioned and active at %s", vm_id, vm.hostname, vm.ip_address)

    except Exception as exc:
        _log.exception("Firewall VM creation failed for VM %d", vm_id)
        db.rollback()
        _record_vm_failure(vm_id, str(exc))
    finally:
        db.close()
        if playbook_dir and playbook_dir.exists():
            shutil.rmtree(playbook_dir, ignore_errors=True)


# ── VPC Interface Configuration ───────────────────────────────────────────────

def _run_configure_vpc_interface(vm_id: int) -> None:
    """Configure the VPC network interface on a target Ubuntu VM after provisioning.

    Deploys a netplan config for the VPC secondary NIC (ens7 on Vultr), assigns the
    static VPC IP (stored in vm.vpc_ip), and applies it.
    """
    import shutil as _shutil

    from api.database import SessionLocal
    from api.models import utcnow
    from api.services.semaphore import SemaphoreClient
    from api.services.ssh_keys import get_or_create_platform_keypair

    db = SessionLocal()
    playbook_dir = None
    try:
        vm = db.query(VM).filter(VM.id == vm_id).first()
        if not vm or not vm.vpc_ip or not vm.ip_address:
            return
        team = db.query(Team).filter(Team.id == vm.team_id).first()

        _update_provision_step(db, vm, "configuring_vpc_interface")

        export_id = f"vpcif_{vm_id}_{uuid.uuid4().hex[:8]}"
        playbook_dir = Path(SHARED_PLAYBOOK_DIR) / export_id
        playbook_dir.mkdir(parents=True, exist_ok=True)

        _shutil.copy(
            PLAYBOOKS_DIR / "configure-vpc-interface.yml",
            playbook_dir / "configure-vpc-interface.yml",
        )
        templates_dir = playbook_dir / "templates"
        templates_dir.mkdir(exist_ok=True)
        _shutil.copy(
            TEMPLATES_DIR / "vpc-netplan.yaml.j2",
            templates_dir / "vpc-netplan.yaml.j2",
        )
        collections_dir = playbook_dir / "collections"
        collections_dir.mkdir(exist_ok=True)
        _shutil.copy(
            PLAYBOOKS_DIR / "collections" / "requirements.yml",
            collections_dir / "requirements.yml",
        )

        private_key, _ = get_or_create_platform_keypair(db)
        team_index = team.team_index if team else 1
        vpc_gateway = f"10.{team_index}.1.1"

        extra_vars = {
            "vpc_ip": vm.vpc_ip,
            "vpc_subnet_mask": 24,
            "vpc_gateway": vpc_gateway,
            "vpc_interface": "ens7",
        }

        with SemaphoreClient() as client:
            client.login()
            project_id = vm.event.semaphore_project_id if vm.event and vm.event.semaphore_project_id else None
            if not project_id:
                project_id, key_id = _get_or_create_vultr_semaphore_project(db, client, private_key)
            else:
                key_id = vm.event.semaphore_key_id

            inv_id = client.create_inventory(
                project_id, f"vpcif-{vm_id}", vm.ip_address,
                ssh_user=vm.ssh_user or "root", ssh_port=vm.ssh_port or 22, key_id=key_id,
            )
            repo_id = client.create_repository(
                project_id, f"vpcif-{vm_id}", str(playbook_dir), key_id
            )
            tmpl_id = client.create_template(
                project_id, f"vpcif-{vm_id}", "configure-vpc-interface.yml",
                inv_id, repo_id, key_id, extra_vars=extra_vars,
            )
            task_id = client.run_task(project_id, tmpl_id)
            vm.semaphore_task_id = task_id
            vm.updated_at = utcnow()
            db.commit()

            while True:
                status = client.get_task_status(project_id, task_id)
                if status == "success":
                    break
                elif status in ("error", "stopped"):
                    output_lines = client.get_task_output(project_id, task_id)
                    tail = "\n".join(output_lines[-10:]) if output_lines else "(no output)"
                    _log.warning("VPC interface config failed for VM %d:\n%s", vm_id, tail)
                    # Non-fatal: VM is still usable via public IP
                    return
                time.sleep(5)

        _log.info("VPC interface configured on VM %d: %s via %s", vm_id, vm.vpc_ip, vpc_gateway)

    except Exception as exc:
        _log.warning("VPC interface configuration failed for VM %d (non-fatal): %s", vm_id, exc)
    finally:
        db.close()
        if playbook_dir and playbook_dir.exists():
            _shutil.rmtree(playbook_dir, ignore_errors=True)


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
            "vm_hostname": _vultr_safe_hostname(vm.hostname or "", f"ctf-vm-{vm_id}"),
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

        # Attach this VM to the team VPC if it has a private IP assigned (firewall
        # events). Without this, create-vm.yml omits the `vpcs` param and the VM
        # never gets the secondary NIC that _run_configure_vpc_interface expects.
        if vm.vpc_ip:
            team = db.query(Team).filter(Team.id == vm.team_id).first()
            if team and team.team_index is not None:
                extra_vars["vpc_description"] = (
                    f"ctf-event-{vm.event_id}-team-{team.team_index}"
                )
            else:
                _log.warning(
                    "VM %d has vpc_ip but team %s lacks team_index — "
                    "VPC attachment skipped",
                    vm_id, vm.team_id,
                )

        # ── Step 3: Create Semaphore project + run playbook ───────────────────
        _update_provision_step(db, vm, "creating_instance")

        with SemaphoreClient() as client:
            client.login()

            project_id, key_id = _get_or_create_vultr_semaphore_project(db, client, private_key)
            inventory_id = client.create_localhost_inventory(project_id, f"localhost-create-{vm_id}", key_id)
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

        # Strip ANSI escape codes and join all lines — Ansible debug output wraps
        # long messages across multiple lines with embedded colour codes.
        _ansi = _re.compile(r'\x1b\[[0-9;]*[mGKHF]')
        cleaned = " ".join(_ansi.sub('', line).strip() for line in output_lines)

        vultr_result = None
        match = _re.search(r'VULTR_RESULT=(\{.*?\})', cleaned)
        if match:
            try:
                vultr_result = json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

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

        # Auto-chain module provisioning for quota-created VMs
        if vm.vm_type:
            vm_modules = db.query(VMModule).filter(VMModule.vm_id == vm.id).all()
            if vm_modules:
                # Target VM with modules — chain into Ansible provisioning
                _log.info("Auto-chaining provision for target VM %d (%s)", vm_id, vm.vm_type)
                db.close()
                if playbook_dir and playbook_dir.exists():
                    shutil.rmtree(playbook_dir, ignore_errors=True)
                playbook_dir = None
                _run_provision(vm_id)
                return
            else:
                # Attacker VM — no modules, mark active immediately
                vm.status = "active"
                vm.updated_at = utcnow()
                db.commit()

    except Exception as exc:
        _log.exception("Vultr VM creation failed for VM %d", vm_id)
        db.rollback()
        detail = str(exc)
        minimum_memory = _re.search(
            r"requires a plan with at least\s+(\d+)\s+MB memory", detail,
            _re.IGNORECASE,
        )
        safe_error = (
            "Vultr rejected this OS and plan combination. Choose a plan with at least "
            f"{minimum_memory.group(1)} MB of memory and try again."
            if minimum_memory else
            "Vultr rejected the instance request. Review the Semaphore task logs for details."
        )
        _record_vm_failure(vm_id, safe_error)
    finally:
        db.close()
        if playbook_dir and playbook_dir.exists():
            shutil.rmtree(playbook_dir, ignore_errors=True)


def _provision_event_vms(event_id: int) -> None:
    """Synchronous background task: create all VMs for an event based on vm_quota.

    If the quota contains a 'firewall' role, provisions in phases:
      Phase 1: Create Vultr VPCs (one per team, via REST API)
      Phase 2: Create and bootstrap OPNsense firewall VMs (threads, joined)
      Phase 3: Create target + attacker VMs (threads, not joined)

    Without a firewall role, all VMs are spawned concurrently as before.
    """
    from concurrent.futures import ThreadPoolExecutor

    import httpx as _httpx

    from api.database import SessionLocal
    from api.models import utcnow

    from builder.base_loader import load_base_type
    from builder.module_loader import load_all_modules
    from builder.plan_sizing import plan_for_vm
    from builder.selector import select_modules

    db = SessionLocal()
    try:
        event = db.query(Event).filter(Event.id == event_id).first()
        if not event or not event.vm_quota:
            return

        vm_quota = json.loads(event.vm_quota)
        module_quota = json.loads(event.quota)
        teams = db.query(Team).filter(Team.event_id == event_id).all()
        if not teams:
            _log.warning("No teams for event %d — skipping VM provisioning", event_id)
            return

        has_firewall = any(spec.get("role") == "firewall" for spec in vm_quota.values())

        # Pre-create Semaphore project for target VM provisioning
        has_targets = any(spec.get("role") == "target" for spec in vm_quota.values())
        if has_targets and not event.semaphore_project_id:
            from api.services.semaphore import SemaphoreClient
            from api.services.ssh_keys import get_or_create_platform_keypair

            private_key, _ = get_or_create_platform_keypair(db)
            with SemaphoreClient() as client:
                client.login()
                project_id = client.create_project(f"CTF Event {event.id}: {event.name}")
                key_id = client.create_key(
                    project_id,
                    name="platform-key",
                    private_key_pem=private_key,
                )
                event.semaphore_project_id = project_id
                event.semaphore_key_id = key_id
                db.commit()

        # Fetch Vultr plans once for plan sizing
        available_plans = []
        if VULTR_API_KEY:
            try:
                resp = _httpx.get(
                    "https://api.vultr.com/v2/plans",
                    headers={"Authorization": f"Bearer {VULTR_API_KEY}"},
                    params={"type": "vc2", "per_page": 500},
                    timeout=15.0,
                )
                resp.raise_for_status()
                available_plans = [
                    {"id": p["id"], "ram": p["ram"], "vcpu_count": p["vcpu_count"], "monthly_cost": p["monthly_cost"]}
                    for p in resp.json().get("plans", [])
                    if p.get("id", "").startswith("vc2-")
                ]
            except Exception as exc:
                _log.warning("Failed to fetch Vultr plans for sizing: %s", exc)

        # ── Phase 0: Assign team indexes (only needed when firewall is present) ──
        if has_firewall:
            teams_ordered = sorted(teams, key=lambda t: t.id)
            for idx, team in enumerate(teams_ordered, start=1):
                team.team_index = idx
            db.commit()
            teams = teams_ordered  # use sorted order from here

            # ── Phase 1: Create Vultr VPCs ────────────────────────────────────
            # Find firewall spec to get the region
            firewall_region = VULTR_DEFAULT_REGION
            for spec in vm_quota.values():
                if spec.get("role") == "firewall":
                    firewall_region = spec.get("region") or VULTR_DEFAULT_REGION
                    break

            for team in teams:
                _log.info("Creating VPC for team %d (index %d)", team.id, team.team_index)
                _create_team_vpc(team.id, event_id, firewall_region)
            db.expire_all()  # refresh team objects with vpc_id values

        # ── Create all VM records ─────────────────────────────────────────────
        library = load_all_modules()
        firewall_vm_ids = []
        other_vm_ids = []
        # Track per-team target VM count for VPC IP assignment
        team_target_counter: dict[int, int] = {}

        for team in teams:
            for vm_type_key, vm_spec in vm_quota.items():
                count = vm_spec.get("count", 1)
                role = vm_spec.get("role", "target")
                default_plan = vm_spec.get("default_plan", "vc2-1c-1gb")
                region = vm_spec.get("region") or VULTR_DEFAULT_REGION

                base_type_id = vm_spec.get("base_type")
                loaded_base_type = load_base_type(base_type_id) if base_type_id else None

                for i in range(count):
                    # Keep provider-facing hostnames independent from the
                    # user-controlled team name: Vultr and DNS labels reject
                    # spaces and other common team-name characters.
                    hostname = f"ctf-e{event_id}-t{team.id}-{vm_type_key}-{i + 1}"
                    vm = VM(
                        hostname=hostname,
                        os=loaded_base_type.os if loaded_base_type else "Ubuntu 24.04 LTS x64",
                        status="creating",
                        vm_type=vm_type_key,
                        base_type=base_type_id,
                        vultr_plan=default_plan,
                        vultr_region=region,
                        team_id=team.id,
                        event_id=event_id,
                        provision_step="queued",
                        ssh_user=vm_spec.get("ssh_user", "root"),
                        created_at=utcnow(),
                        updated_at=utcnow(),
                    )

                    if role == "firewall":
                        # Firewall VMs get the gateway IP on the team VPC
                        team_idx = team.team_index or 1
                        vm.vpc_ip = f"10.{team_idx}.1.1"
                        vm.os = "FreeBSD 14 x64"  # OPNsense base OS
                        db.add(vm)
                        db.flush()
                        db.commit()
                        firewall_vm_ids.append(vm.id)

                    elif role == "target":
                        # Select modules for this VM
                        selected = select_modules(module_quota, library, base_type_id=base_type_id)
                        db.add(vm)
                        db.flush()
                        for mod in selected:
                            db.add(VMModule(
                                vm_id=vm.id,
                                module_id=mod.id,
                                module_type=mod.type,
                                difficulty=mod.difficulty,
                                points=mod.points,
                                stage=mod.stage,
                            ))
                            if mod.type == "goal":
                                db.add(VMGoal(
                                    vm_id=vm.id,
                                    module_id=mod.id,
                                    status="pending",
                                    red_points=mod.red_points,
                                    defend_points=mod.defend_points,
                                ))
                        if available_plans and loaded_base_type is not None:
                            sized_plan = plan_for_vm(
                                base_type=loaded_base_type,
                                modules=selected,
                                vm_quota_override_plan=vm_spec.get("default_plan"),
                                available_plans=available_plans,
                            )
                            if sized_plan != vm.vultr_plan:
                                vm.vultr_plan = sized_plan

                        # Assign VPC IP if this event has a firewall
                        if has_firewall:
                            team_idx = team.team_index or 1
                            counter = team_target_counter.get(team.id, 0)
                            vm.vpc_ip = f"10.{team_idx}.1.{10 + counter}"
                            team_target_counter[team.id] = counter + 1

                        db.commit()
                        other_vm_ids.append(vm.id)

                    else:
                        # Attacker (or any other role): no modules, no VPC
                        db.add(vm)
                        db.flush()
                        db.commit()
                        other_vm_ids.append(vm.id)

        _log.info(
            "Event %d: queued %d firewall + %d other VMs across %d teams",
            event_id, len(firewall_vm_ids), len(other_vm_ids), len(teams),
        )

        if has_firewall and firewall_vm_ids:
            # ── Phase 2: Create and bootstrap firewall VMs, then wait ────────
            _log.info("Event %d: starting firewall provisioning (%d VMs)", event_id, len(firewall_vm_ids))
            with ThreadPoolExecutor(max_workers=min(4, len(firewall_vm_ids))) as executor:
                list(executor.map(_run_firewall_create, firewall_vm_ids))
            _log.info("Event %d: all firewall VMs provisioned, starting target/attacker VMs", event_id)

        # ── Phase 3 (or only phase without firewall): target + attacker VMs ──
        if other_vm_ids:
            with ThreadPoolExecutor(max_workers=min(8, len(other_vm_ids))) as executor:
                list(executor.map(_run_vultr_create, other_vm_ids))

    except Exception as exc:
        _log.exception("Event VM provisioning failed for event %d", event_id)
        db.rollback()
        failure_db = SessionLocal()
        try:
            unfinished = failure_db.query(VM).filter(
                VM.event_id == event_id,
                VM.status.in_(("creating", "provisioning")),
            ).all()
            for unfinished_vm in unfinished:
                unfinished_vm.status = "failed"
                unfinished_vm.provision_step = "failed"
                unfinished_vm.provision_error = (
                    "Event provisioning stopped before this VM completed. "
                    "Review the API logs and retry the VM operation."
                )
                unfinished_vm.updated_at = utcnow()
            failure_db.commit()
        except Exception:
            failure_db.rollback()
            _log.exception("Could not persist event provisioning failure state")
        finally:
            failure_db.close()
    finally:
        db.close()


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


@router.post("/vms/{vm_id}/retry-create")
async def retry_vultr_create(vm_id: int, request: Request, db: Session = Depends(get_db)):
    """Retry cloud creation for a failed VM that never reached the provider."""
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    if not VULTR_API_KEY:
        return JSONResponse({"error": "VULTR_API_KEY not configured"}, status_code=503)

    vm = db.query(VM).filter(VM.id == vm_id).first()
    if not vm:
        return JSONResponse({"error": "VM not found"}, status_code=404)
    if vm.status != "failed":
        return JSONResponse(
            {"error": f"VM is {vm.status}; only failed VM creation can be retried"},
            status_code=409,
        )
    if vm.vultr_id or vm.ip_address:
        return JSONResponse(
            {"error": "VM already has cloud resources; inspect it before retrying"},
            status_code=409,
        )

    from api.models import utcnow

    vm.status = "creating"
    vm.provision_step = "queued"
    vm.provision_error = None
    vm.semaphore_task_id = None
    vm.updated_at = utcnow()
    db.commit()

    asyncio.create_task(asyncio.to_thread(_run_vultr_create, vm_id))
    return {"status": "creating", "vm_id": vm_id}


# ── Vultr VM Destruction ───────────────────────────────────────────────────────

def _maybe_cleanup_team_vpc(db: Session, team_id: int) -> None:
    """Delete the team's Vultr VPC once no VMs remain attached to it.

    Called after a VPC-attached VM is destroyed. If no other VMs for the team
    still carry a vpc_ip, the VPC is deleted via the Vultr REST API and
    team.vpc_id is cleared. Best-effort and non-fatal: concurrent destroys of
    the last two VMs may race and leave an orphan VPC, which can be removed by
    re-running a destroy or manually in the Vultr console.
    """
    if not VULTR_API_KEY:
        return
    import httpx as _httpx

    try:
        team = db.query(Team).filter(Team.id == team_id).first()
        if not team or not team.vpc_id:
            return

        remaining = (
            db.query(VM)
            .filter(VM.team_id == team_id, VM.vpc_ip.isnot(None))
            .count()
        )
        if remaining > 0:
            return

        resp = _httpx.delete(
            f"https://api.vultr.com/v2/vpcs/{team.vpc_id}",
            headers={"Authorization": f"Bearer {VULTR_API_KEY}"},
            timeout=30.0,
        )
        # 204 = deleted, 404 = already gone — both are success for our purposes
        if resp.status_code not in (204, 404):
            resp.raise_for_status()

        _log.info("Deleted VPC %s for team %d", team.vpc_id, team_id)
        team.vpc_id = None
        db.commit()

    except Exception as exc:
        _log.warning("VPC cleanup failed for team %d (non-fatal): %s", team_id, exc)


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

            project_id, key_id = _get_or_create_vultr_semaphore_project(db, client, private_key)
            inventory_id = client.create_localhost_inventory(project_id, f"localhost-destroy-{vm_id}", key_id)
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

        # Capture VPC linkage before the VM row is removed
        team_id = vm.team_id
        had_vpc = bool(vm.vpc_ip)

        db.delete(vm)
        db.commit()

        # Once the last VPC-attached VM for the team is gone, tear down the VPC
        if had_vpc and team_id:
            _maybe_cleanup_team_vpc(db, team_id)

    except Exception as exc:
        _log.exception("Vultr VM destruction failed for VM %d", vm_id)
        db.rollback()
        _record_vm_failure(vm_id, str(exc))
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

@router.get("/topology-data")
async def topology_data(
    request: Request,
    event_id: int = None,
    db: Session = Depends(get_db),
):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    from builder.base_loader import load_all_bases
    base_icons = {b.id: b.icon for b in load_all_bases()}

    eq = db.query(Event)
    if event_id:
        eq = eq.filter(Event.id == event_id)
    else:
        eq = eq.filter(Event.status != "draft")
    events = eq.all()

    nodes = []
    links = []

    for event in events:
        event_node_id = f"event-{event.id}"
        teams = db.query(Team).filter(Team.event_id == event.id).order_by(Team.id).all()
        team_count = len(teams)

        nodes.append({
            "id": event_node_id,
            "type": "event",
            "label": event.name,
            "status": event.status,
            "team_count": team_count,
        })

        # Show VMs from first team only (canonical setup — all teams identical)
        first_team = teams[0] if teams else None
        if first_team:
            vms = db.query(VM).filter(VM.team_id == first_team.id).all()
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
                    "icon": base_icons.get(vm.base_type) if vm.base_type else None,
                    "event_id": event_node_id,
                    "modules_total": total,
                    "modules_completed": completed,
                })
                links.append({"source": event_node_id, "target": vm_node_id})

    return {"nodes": nodes, "links": links}
