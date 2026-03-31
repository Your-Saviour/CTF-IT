from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from api.database import get_db
from api.routes.auth import get_current_user

router = APIRouter(prefix="/api/images", tags=["images"])


@router.get("/status")
async def image_status(
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    from api.models import UserImage
    image = (
        db.query(UserImage)
        .filter(UserImage.user_id == user.id)
        .order_by(UserImage.created_at.desc())
        .first()
    )

    if not image:
        return JSONResponse({"status": "none", "image_tag": None})

    return JSONResponse({
        "status": image.status,
        "image_tag": image.image_tag,
    })


@router.get("/pull-command")
async def pull_command(
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/", status_code=303)

    from api.models import UserImage
    image = (
        db.query(UserImage)
        .filter(UserImage.user_id == user.id)
        .order_by(UserImage.created_at.desc())
        .first()
    )

    if not image or image.status != "ready":
        return JSONResponse({"error": "Image not ready"}, status_code=400)

    return JSONResponse({
        "run_command": f"docker run -it {image.image_tag}",
    })
