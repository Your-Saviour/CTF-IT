from datetime import datetime, timezone

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


async def _run_control_verification(
    verification: dict, vm: VM
) -> tuple[bool, str | None]:
    """Run through the VM-local gt checker; fail closed if it was modified."""
    from api.services.verification import verify_spec
    result = await verify_spec(verification, vm)
    return result.passed, result.summary if result.result in {"invalid", "unavailable"} else None
