import asyncio
from datetime import datetime, timezone
import shlex

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from api.database import get_db
from api.models import VM, VMGoal
from api.routes.admin import require_admin
from builder.module_loader import load_all_modules

router = APIRouter(prefix="/admin/api", tags=["vm_goals"])


def _goal_dict(goal: VMGoal) -> dict:
    return {
        "id": goal.id,
        "vm_id": goal.vm_id,
        "module_id": goal.module_id,
        "status": goal.status,
        "red_points": goal.red_points,
        "defend_points": goal.defend_points,
        "achievement_count": goal.achievement_count,
        "defend_count": goal.defend_count,
        "achieved_at": goal.achieved_at.isoformat() if goal.achieved_at else None,
        "defended_at": goal.defended_at.isoformat() if goal.defended_at else None,
    }


@router.get("/vms/{vm_id}/goals")
async def list_vm_goals(vm_id: int, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    vm = db.query(VM).filter(VM.id == vm_id).first()
    if not vm:
        return JSONResponse({"error": "VM not found"}, status_code=404)

    goals = db.query(VMGoal).filter(VMGoal.vm_id == vm_id).all()
    return [_goal_dict(g) for g in goals]


@router.post("/vms/{vm_id}/goals/{goal_id}/check")
async def check_vm_goal(
    vm_id: int,
    goal_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    vm = db.query(VM).filter(VM.id == vm_id).first()
    if not vm:
        return JSONResponse({"error": "VM not found"}, status_code=404)

    goal = db.query(VMGoal).filter(VMGoal.id == goal_id, VMGoal.vm_id == vm_id).first()
    if not goal:
        return JSONResponse({"error": "Goal not found"}, status_code=404)

    # Load the module definition
    library = {m.id: m for m in load_all_modules()}
    module = library.get(goal.module_id)
    if not module:
        return JSONResponse({"error": f"Module '{goal.module_id}' not found"}, status_code=404)

    now = datetime.now(timezone.utc)

    # Run verification check (goal achieved by red team)
    verification_passed, err = await _run_remote_verification(module.verification, vm, db)
    if err:
        return JSONResponse({"error": err}, status_code=501)

    # Run revert verification check (blue team reverted)
    revert_passed = False
    if module.revert_verification:
        revert_passed, err = await _run_remote_verification(module.revert_verification, vm, db)
        if err:
            return JSONResponse({"error": err}, status_code=501)

    # State machine transitions
    if verification_passed and goal.status in ("pending", "defended"):
        goal.status = "achieved"
        goal.achievement_count += 1
        goal.achieved_at = now

    elif revert_passed and goal.status == "achieved":
        goal.status = "defended"
        goal.defend_count += 1
        goal.defended_at = now

    db.commit()
    db.refresh(goal)
    return _goal_dict(goal)


async def _run_remote_verification(
    verification: dict,
    vm: VM,
    db: Session | None = None,
) -> tuple[bool, str | None]:
    """Run a verification spec against a remote VM.

    Returns (passed: bool, error: str | None).
    error is set (and passed is False) when the verification type is not
    supported for remote VMs — the caller should surface a 501.
    """
    vtype = verification.get("type")
    if not vtype:
        return False, "verification spec is missing 'type' field"

    if vtype == "http_response":
        port = verification.get("port", 80)
        path = verification.get("path", "/").lstrip("/")
        url = f"http://{vm.ip_address}:{port}/{path}"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url)
        except Exception as exc:
            # Network error — treat as not-passed but not a 501
            return False, None

        body = resp.text
        if "status_code" in verification:
            if resp.status_code != verification["status_code"]:
                return False, None
        if "body_contains" in verification:
            if verification["body_contains"] not in body:
                return False, None
        if "body_not_contains" in verification:
            if verification["body_not_contains"] in body:
                return False, None
        return True, None

    if vtype in {"service_running", "file_exists", "file_absent"}:
        if db is None:
            return False, "database session required for SSH verification"

        if vtype == "service_running":
            service = verification.get("service")
            if not isinstance(service, str) or not service:
                return False, "service_running verification requires 'service'"
            expected = verification.get("expected", "active")
            if expected not in {"active", "inactive"}:
                return False, "service_running expected must be 'active' or 'inactive'"
            command = f"systemctl is-active --quiet -- {shlex.quote(service)}"
            exit_status = await _run_ssh_command(vm, db, command)
            return (exit_status == 0 if expected == "active" else exit_status != 0), None

        path = verification.get("path")
        if not isinstance(path, str) or not path.startswith("/"):
            return False, f"{vtype} verification requires an absolute 'path'"
        command = f"test -e {shlex.quote(path)}"
        exists = await _run_ssh_command(vm, db, command) == 0
        return (exists if vtype == "file_exists" else not exists), None

    return False, f"verification type '{vtype}' not supported for remote VMs"


async def _run_ssh_command(vm: VM, db: Session, command: str) -> int:
    """Execute a read-only verification command using the platform SSH key."""
    def execute() -> int:
        from api.database import SessionLocal
        from api.services.ssh_connection import connect_vm

        thread_db = SessionLocal()
        client = None
        try:
            thread_vm = thread_db.query(VM).filter(VM.id == vm.id).first()
            if not thread_vm:
                return 255
            client = connect_vm(thread_vm, thread_db)
            _, stdout, _ = client.exec_command(command, timeout=10)
            return stdout.channel.recv_exit_status()
        except Exception:
            return 255
        finally:
            if client:
                client.close()
            thread_db.close()

    return await asyncio.to_thread(execute)
