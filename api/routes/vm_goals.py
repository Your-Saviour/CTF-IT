import asyncio
from datetime import datetime, timezone

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
    if vm.event.status != "open":
        return JSONResponse({"error": "event is read-only"}, status_code=409)

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
    verification_passed, err = await _run_control_verification(module.verification, vm)
    if err:
        return JSONResponse({"error": err}, status_code=501)

    # Run revert verification check (blue team reverted)
    revert_passed = False
    if module.revert_verification:
        revert_passed, err = await _run_control_verification(module.revert_verification, vm)
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
    """Compatibility wrapper over the platform-wide verification service."""
    from api.services.verification import _command, verify_spec

    async def legacy_executor(spec):
        status = await _run_ssh_command(vm, db, _command(spec))
        kind = spec.get("type")
        if kind in {"file_absent", "file_not_contains", "user_not_exists", "port_closed"}:
            status = 0 if status != 0 else 1
        elif kind in {"service_running", "service_state"} and spec.get("expected") in {"inactive", "failed"}:
            status = 0 if status != 0 else 1
        return status, ""

    result = await verify_spec(verification, vm, ssh_executor=legacy_executor)
    error = result.summary if result.result == "invalid" else None
    return result.passed, error


async def _run_control_verification(verification: dict, vm: VM) -> tuple[bool, str | None]:
    """Production goal checks use the same restricted verifier as training."""
    from api.services.verification import verify_spec
    result = await verify_spec(verification, vm)
    return result.passed, result.summary if result.result == "invalid" else None


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
