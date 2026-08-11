"""Central authorization predicates for event, team, VM and module access."""

from fastapi import Request
from sqlalchemy.orm import Session

from api.models import User, VM, VMModule
from api.routes.auth import get_current_user


def authenticated_user(request: Request, db: Session) -> User | None:
    return get_current_user(request, db)


def administrator(request: Request, db: Session) -> User | None:
    user = authenticated_user(request, db)
    return user if user and user.is_admin else None


def participant(request: Request, db: Session) -> User | None:
    user = authenticated_user(request, db)
    if not user or user.is_admin or not user.event_id or not user.team_id:
        return None
    if not user.team or user.team.event_id != user.event_id:
        return None
    return user


def owns_vm(user: User, vm: VM) -> bool:
    return bool(
        user.active
        and not user.is_admin
        and user.team_id
        and user.event_id
        and vm.team_id == user.team_id
        and vm.event_id == user.event_id
    )


def learner_assignment(db: Session, user: User, vm_id: int, module_id: str) -> VMModule | None:
    return (
        db.query(VMModule)
        .join(VM, VM.id == VMModule.vm_id)
        .filter(
            VM.id == vm_id,
            VM.team_id == user.team_id,
            VM.event_id == user.event_id,
            VMModule.module_id == module_id,
            VMModule.stage == "preapplied",
            VMModule.module_type.in_(("vulnerability", "hardening", "payload")),
        )
        .first()
    )
