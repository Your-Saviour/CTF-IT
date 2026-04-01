import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from api.database import get_db
from api.models import User, UserImage, UserModule
from api.routes.auth import get_current_user
from api.schemas import SnapshotPayload
from builder.module_loader import load_all_modules

router = APIRouter(tags=["verify"])


def extract_and_check(
    verification: dict,
    snapshot: SnapshotPayload,
    stored_flag: str,
    server_build_state: dict,
) -> bool:
    """Check a single module's verification against the broad system snapshot.

    server_build_state is loaded from the DB (trusted), NOT from the client payload.
    """
    vtype = verification.get("type")

    if vtype == "file_permissions":
        path = verification["path"]
        info = snapshot.file_permissions.get(path, {})
        return info.get("permissions") == verification["expected"]

    if vtype == "file_contains":
        path = verification["path"]
        content = snapshot.file_contents.get(path, "")
        return verification["pattern"] in content

    if vtype == "file_not_contains":
        path = verification["path"]
        content = snapshot.file_contents.get(path, "")
        return verification["pattern"] not in content

    if vtype == "service_running":
        service = verification["service"]
        return snapshot.services.get(service) == verification["expected"]

    if vtype == "package_installed":
        package = verification["package"]
        return package in snapshot.packages

    if vtype == "port_closed":
        port = verification["port"]
        return port not in snapshot.listening_ports

    if vtype == "flag_contents":
        return snapshot.flag == stored_flag

    if vtype == "password_not_default":
        user = verification["user"]
        hash_val = snapshot.shadow_hashes.get(user, "")
        return hash_val not in ("", "!", "*", "!!", "!*")

    if vtype == "password_changed":
        user = verification["user"]
        current_hash = snapshot.shadow_hashes.get(user, "")
        original_hash = (
            server_build_state.get("shadow_hashes", {}).get(user, "")
        )
        return current_hash != "" and original_hash != "" and current_hash != original_hash

    return False


@router.post("/api/verify")
async def verify(
    payload: SnapshotPayload,
    request: Request,
    db: Session = Depends(get_db),
):
    # Look up user from payload
    user = db.query(User).filter(User.username == payload.user_id).first()
    if not user:
        return JSONResponse({"error": "User not found"}, status_code=404)

    # Find the user's ready image
    image = (
        db.query(UserImage)
        .filter(UserImage.user_id == user.id, UserImage.status == "ready")
        .order_by(UserImage.created_at.desc())
        .first()
    )
    if not image:
        return JSONResponse({"error": "No ready image found"}, status_code=400)

    stored_flag = image.flag

    # Authenticate: accept session cookie OR valid flag in payload
    session_user = get_current_user(request, db)
    if not session_user or session_user.id != user.id:
        if payload.flag != stored_flag:
            return JSONResponse(
                {"error": "Unauthorized: invalid flag or session"}, status_code=403
            )

    # Load server-side build state (trusted, not from client)
    server_build_state = json.loads(image.build_state) if image.build_state else {}

    # Load module library for verification specs
    library = {m.id: m for m in load_all_modules()}

    # Iterate over the user's assigned modules (server-side, not client-sent)
    user_modules = (
        db.query(UserModule).filter(UserModule.user_id == user.id).all()
    )

    results = []
    newly_completed = 0
    for um in user_modules:
        module = library.get(um.module_id)
        if not module:
            continue

        was_already_completed = um.completed

        passed = extract_and_check(
            module.verification, payload, stored_flag, server_build_state
        )

        points_awarded = 0
        if passed and not um.completed:
            um.completed = True
            um.completed_at = datetime.now(timezone.utc)
            points_awarded = um.points
            newly_completed += 1

        # Only reveal module details for completed challenges
        if um.completed:
            results.append({
                "module_id": um.module_id,
                "name": module.name,
                "passed": True,
                "points_awarded": points_awarded,
                "newly_completed": not was_already_completed and passed,
            })

    db.commit()

    total_points = sum(um.points for um in user_modules if um.completed)
    total_modules = len(user_modules)
    completed_count = sum(1 for um in user_modules if um.completed)

    return {
        "results": results,
        "total_points": total_points,
        "completed": completed_count,
        "remaining": total_modules - completed_count,
        "newly_completed": newly_completed,
    }
