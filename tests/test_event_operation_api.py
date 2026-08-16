from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.database import Base
from api.models import Event


@pytest.fixture
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def test_event_model_persists_operation_plan(db_session):
    event = Event(name="Exercise", quota="{}", operation_plan='{"version": 1}')
    db_session.add(event)
    db_session.commit()
    db_session.expire_all()
    assert db_session.get(Event, event.id).operation_plan == '{"version": 1}'


def test_operation_plan_migration_is_guarded_for_existing_databases():
    source = Path("migrations/versions/0012_event_operation_plan.py").read_text()
    assert 'revision = "0012_event_operation_plan"' in source
    assert 'down_revision = "0011_event_module_plan"' in source
    assert '"operation_plan" not in columns' in source
