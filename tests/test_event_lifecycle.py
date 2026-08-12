from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, StaticPool
from sqlalchemy.orm import sessionmaker

from api.database import Base
from api.models import Event
from api.services.event_lifecycle import expire_due_events


def test_event_model_defaults_to_draft_and_closed():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    event = Event(name="Fresh", quota="{}")
    db.add(event)
    db.commit()
    db.refresh(event)

    assert event.status == "draft"
    assert event.open is False

    db.close()
    Base.metadata.drop_all(engine)


def test_only_due_open_events_are_stopped():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    now = datetime.now(timezone.utc)
    due = Event(name="Due", quota="{}", status="open", open=True, ends_at=now - timedelta(seconds=1))
    future = Event(name="Future", quota="{}", status="open", open=True, ends_at=now + timedelta(hours=1))
    untimed = Event(name="Untimed", quota="{}", status="open", open=True)
    db.add_all([due, future, untimed])
    db.commit()

    assert expire_due_events(db, now) == 1
    db.refresh(due)
    db.refresh(future)
    db.refresh(untimed)
    assert due.status == "stopped" and due.open is False
    assert future.status == "open"
    assert untimed.status == "open"

    db.close()
    Base.metadata.drop_all(engine)
