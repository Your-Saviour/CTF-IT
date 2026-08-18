# tests/test_showcase_event.py
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.database import Base
from api.models import Event, EventOperation


@pytest.fixture
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def test_showcase_event_seeded_idempotently(db_session):
    from api.main import seed_showcase_event  # extracted helper
    seed_showcase_event(db_session)
    seed_showcase_event(db_session)
    events = db_session.query(Event).filter(Event.name == "Operation Chaining Demo").all()
    assert len(events) == 1
    op = db_session.query(EventOperation).filter(EventOperation.event_id == events[0].id).first()
    assert op is not None
    assert op.name == "RCE → Privilege Escalation → Implant"
