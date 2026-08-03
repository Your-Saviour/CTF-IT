"""Event deadline enforcement shared by request handling and background polling."""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from api.models import Event


def expire_due_events(db: Session, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    due = db.query(Event).filter(
        Event.status == "open",
        Event.ends_at.is_not(None),
        Event.ends_at <= now,
    ).all()
    for event in due:
        event.status = "stopped"
        event.open = False
    if due:
        db.commit()
    return len(due)
