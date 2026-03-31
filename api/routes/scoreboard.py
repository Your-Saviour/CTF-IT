from fastapi import APIRouter, Depends
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from api.database import get_db
from api.models import User, UserModule

router = APIRouter(tags=["scoreboard"])


@router.get("/api/scoreboard")
async def scoreboard(db: Session = Depends(get_db)):
    results = (
        db.query(
            User.username,
            func.coalesce(
                func.sum(
                    case(
                        (UserModule.completed == True, UserModule.points),
                        else_=0,
                    )
                ),
                0,
            ).label("total_points"),
            func.coalesce(
                func.sum(
                    case(
                        (UserModule.completed == True, 1),
                        else_=0,
                    )
                ),
                0,
            ).label("modules_completed"),
        )
        .outerjoin(UserModule, User.id == UserModule.user_id)
        .group_by(User.id)
        .order_by(func.sum(
            case(
                (UserModule.completed == True, UserModule.points),
                else_=0,
            )
        ).desc())
        .all()
    )

    return [
        {
            "rank": i + 1,
            "username": r.username,
            "total_points": r.total_points,
            "modules_completed": r.modules_completed,
        }
        for i, r in enumerate(results)
    ]
