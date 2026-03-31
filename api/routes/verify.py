from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from api.database import get_db
from api.models import User, UserImage, UserModule
from api.routes.auth import get_current_user
from api.schemas import VerifyPayload
from builder.module_loader import load_all_modules

router = APIRouter(tags=["verify"])


def check_module(verification: dict, collected: dict) -> bool:
    vtype = verification.get("type")

    if vtype == "file_permissions":
        return collected.get("permissions") == verification["expected"]

    if vtype == "file_contains":
        content = collected.get("content", "")
        return verification["pattern"] in content

    if vtype == "file_not_contains":
        content = collected.get("content", "")
        return verification["pattern"] not in content

    if vtype == "service_running":
        return collected.get("status") == verification["expected"]

    if vtype == "package_installed":
        return collected.get("installed") is True

    if vtype == "port_closed":
        return collected.get("listening") is False

    if vtype == "flag_contents":
        return False  # validated separately via stored flag

    if vtype == "password_not_default":
        return collected.get("is_default") is False

    if vtype == "password_changed":
        current = collected.get("current_hash", "")
        original = collected.get("original_hash", "")
        return current != "" and original != "" and current != original

    return False


@router.post("/api/verify")
async def verify(
    payload: VerifyPayload,
    request: Request,
    db: Session = Depends(get_db),
):
    # Look up user from payload
    user = db.query(User).filter(User.username == payload.user_id).first()
    if not user:
        return JSONResponse({"error": "User not found"}, status_code=404)

    # Verify the request includes the flag as proof it comes from a valid container
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
        has_valid_flag = any(
            r.collected.get("contents") == stored_flag
            for r in payload.results
        )
        if not has_valid_flag:
            return JSONResponse(
                {"error": "Unauthorized: invalid flag or session"}, status_code=403
            )

    # Load module library for verification specs
    library = {m.id: m for m in load_all_modules()}

    results = []
    for result in payload.results:
        module = library.get(result.module_id)
        if not module:
            results.append({
                "module_id": result.module_id,
                "passed": False,
                "points_awarded": 0,
                "error": "Unknown module",
            })
            continue

        # Handle flag_contents specially
        if module.verification.get("type") == "flag_contents":
            passed = result.collected.get("contents") == stored_flag
        else:
            passed = check_module(module.verification, result.collected)

        points_awarded = 0
        if passed:
            user_module = (
                db.query(UserModule)
                .filter(
                    UserModule.user_id == user.id,
                    UserModule.module_id == result.module_id,
                )
                .first()
            )
            if user_module and not user_module.completed:
                user_module.completed = True
                user_module.completed_at = datetime.now(timezone.utc)
                points_awarded = user_module.points

        results.append({
            "module_id": result.module_id,
            "passed": passed,
            "points_awarded": points_awarded,
        })

    db.commit()

    total_points = (
        db.query(UserModule)
        .filter(UserModule.user_id == user.id, UserModule.completed == True)
        .with_entities(UserModule.points)
        .all()
    )
    total = sum(p[0] for p in total_points)

    return {"results": results, "total_points": total}
