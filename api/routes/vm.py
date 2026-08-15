import asyncio
import io
import json
import logging
import os
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

# Path to the bundled VM provisioning playbooks (relative to project root)
_HERE = Path(__file__).parent.parent.parent
PLAYBOOKS_DIR = _HERE / "playbooks"
TEMPLATES_DIR = _HERE / "templates"

router = APIRouter(prefix="/admin/api", tags=["admin"])


def _gamenet_gateway_proxy(db, vm: VM) -> str | None:
    """Return the public team gateway used to route all endpoint automation."""
    if not vm.zone_id or not vm.team or not vm.team.vpn_gateway or not vm.team.vpn_gateway.vm_id:
        return None
    gateway_vm = db.get(VM, vm.team.vpn_gateway.vm_id)
    return gateway_vm.public_ip if gateway_vm else None


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


def _wait_for_tcp_port(host: str, port: int, timeout_seconds: int = 600) -> None:
    """Wait for a newly-created cloud VM to accept connections.

    EC2 returns an instance address before its operating system has completed its
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
        "ust_prompt": vm.ust_prompt,
        "team_id": vm.team_id,
        "team_name": vm.team.name if vm.team else None,
        "event_id": vm.event_id,
        "event_name": vm.event.name if vm.event else None,
        "modules": modules,
        "created_at": vm.created_at.isoformat() if vm.created_at else None,
        "updated_at": vm.updated_at.isoformat() if vm.updated_at else None,
        "cloud_instance_id": vm.cloud_instance_id,
        "instance_type": vm.instance_type,
        "cloud_region": vm.cloud_region,
        "availability_zone": vm.availability_zone,
        "primary_eni_id": vm.primary_eni_id,
        "wan_eni_id": vm.wan_eni_id,
        "lan_eni_id": vm.lan_eni_id,
        "subnet_id": vm.subnet_id,
        "eip_allocation_id": vm.eip_allocation_id,
        "cloudflare_record_id": vm.cloudflare_record_id,
        "provision_step": vm.provision_step,
        "provision_error": vm.provision_error,
        "vm_type": vm.vm_type,
        "base_type": vm.base_type,
        "vpc_ip": vm.vpc_ip,
        "role": vm.role,
        "site_id": vm.site_id,
        "zone_id": vm.zone_id,
        "public_ip": vm.public_ip,
        "private_ip": vm.private_ip,
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
        ust_prompt=body.get("ust_prompt") or None,
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
    if len(body.get("ust_prompt") or "") > 8000:
        return JSONResponse({"error": "ust_prompt must be at most 8000 characters"}, status_code=422)
    for field in ("hostname", "ip_address", "os", "status", "ssh_port", "ssh_user", "notes", "ust_prompt"):
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
        vm.provision_error = None
        db.commit()

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
                    proxy_host=_gamenet_gateway_proxy(db, vm),
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
                proxy_host=_gamenet_gateway_proxy(db, vm),
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
        vm.provision_error = None
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


# ── AWS compute lifecycle ─────────────────────────────────────────────────────

def _cloud_providers():
    """Build AWS lifecycle dependencies without accepting explicit credentials."""
    from api.database import SessionLocal
    from api.services.aws import AwsComputeProvider, AwsConfig, AwsSessionFactory
    from api.services.cloud_provisioning import CloudProviders
    from api.services.ssh_keys import get_or_create_platform_keypair

    config = AwsConfig.from_env()
    sessions = AwsSessionFactory(config)
    security_groups = tuple(
        value.strip() for value in os.environ.get("AWS_STANDARD_SECURITY_GROUP_IDS", "").split(",")
        if value.strip()
    )
    if not security_groups:
        from api.services.aws import AwsConfigurationError
        raise AwsConfigurationError("AWS_STANDARD_SECURITY_GROUP_IDS is required")
    key_db = SessionLocal()
    try:
        _, public_key = get_or_create_platform_keypair(key_db)
    finally:
        key_db.close()
    return CloudProviders(
        config=config,
        compute=AwsComputeProvider(sessions.client("ec2")),
        session_factory=SessionLocal,
        key_name=os.environ.get("AWS_KEY_PAIR_NAME", "ctf-it"),
        public_key=public_key,
        security_group_ids=security_groups,
        configure_guest=_run_provision,
        wait_for_ssh=lambda vm: _wait_for_tcp_port(vm.public_ip, vm.ssh_port or 22),
        reconcile_dns=lambda vm: None,
        delete_dns=lambda vm: None,
    )


def _run_cloud_create(vm_id: int) -> None:
    from api.services.cloud_provisioning import create_cloud_vm
    try:
        create_cloud_vm(vm_id, providers=_cloud_providers())
    except Exception:
        _log.exception("AWS VM creation failed for VM %d", vm_id)


def _run_cloud_destroy(vm_id: int) -> None:
    from api.services.cloud_provisioning import destroy_cloud_vm
    try:
        destroy_cloud_vm(vm_id, providers=_cloud_providers())
    except Exception:
        _log.exception("AWS VM destruction failed for VM %d", vm_id)


@router.get("/aws/instance-types")
async def aws_instance_types(request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    try:
        from api.services.aws import AwsConfig
        config = AwsConfig.from_env()
        return {"instance_types": [{"id": value, "label": value} for value in config.instance_types]}
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)


@router.get("/aws/amis")
async def aws_amis(request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    try:
        from api.services.aws import AwsConfig
        config = AwsConfig.from_env()
        return {"amis": [
            {"region": region, "ami_id": ami_id, "family": "ubuntu"}
            for region, ami_id in sorted(config.ubuntu_amis.items())
        ]}
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)


@router.post("/vms/create-cloud")
async def create_vm_on_cloud(request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    try:
        providers = _cloud_providers()
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)
    body = await request.json()
    team = db.get(Team, body.get("team_id")) if body.get("team_id") else None
    if not team:
        return JSONResponse({"error": "Team not found"}, status_code=404)
    hostname = (body.get("hostname") or "").strip()
    instance_type = (body.get("instance_type") or "").strip()
    region = (body.get("region") or providers.config.default_region).strip()
    if not hostname or instance_type not in providers.config.instance_types:
        return JSONResponse({"error": "valid hostname and instance_type are required"}, status_code=422)
    try:
        providers.config.ubuntu_ami(region)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    vm = VM(
        hostname=hostname, os="Ubuntu 24.04 LTS", status="creating",
        ssh_user=body.get("ssh_user") or "ubuntu", ssh_port=22,
        notes=body.get("notes") or None, team_id=team.id, event_id=team.event_id,
        instance_type=instance_type, cloud_region=region,
        subnet_id=providers.config.standard_subnet_id, provision_step="queued",
    )
    db.add(vm); db.commit(); db.refresh(vm)
    asyncio.create_task(asyncio.to_thread(_run_cloud_create, vm.id))
    return {"status": "creating", "id": vm.id}


@router.post("/vms/{vm_id}/retry-cloud")
async def retry_cloud_create(vm_id: int, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    vm = db.get(VM, vm_id)
    if not vm:
        return JSONResponse({"error": "VM not found"}, status_code=404)
    if vm.status not in ("failed", "destroy_failed"):
        return JSONResponse({"error": f"VM is {vm.status}; retry is not allowed"}, status_code=409)
    vm.status, vm.provision_step, vm.provision_error = "creating", "queued", None
    db.commit()
    asyncio.create_task(asyncio.to_thread(_run_cloud_create, vm.id))
    return {"status": "creating", "vm_id": vm.id}


@router.post("/vms/{vm_id}/destroy-cloud")
async def destroy_cloud_instance(vm_id: int, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    vm = db.get(VM, vm_id)
    if not vm:
        return JSONResponse({"error": "VM not found"}, status_code=404)
    if not vm.cloud_instance_id:
        return JSONResponse({"error": "VM has no AWS instance associated"}, status_code=422)
    if vm.status in ("creating", "provisioning", "destroying"):
        return JSONResponse({"error": f"VM is currently {vm.status}"}, status_code=409)
    vm.status = "destroying"; db.commit()
    asyncio.create_task(asyncio.to_thread(_run_cloud_destroy, vm.id))
    return {"status": "destroying", "vm_id": vm.id}


@router.get("/vms/{vm_id}/create-status")
async def create_status(vm_id: int, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    vm = db.get(VM, vm_id)
    if not vm:
        return JSONResponse({"error": "VM not found"}, status_code=404)
    return {
        "status": vm.status, "provision_step": vm.provision_step,
        "provision_error": vm.provision_error, "ip_address": vm.ip_address,
        "cloud_instance_id": vm.cloud_instance_id,
    }


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
