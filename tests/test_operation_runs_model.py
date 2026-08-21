import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.database import Base
from api.models import OperationRun, OperationRunStep


@pytest.fixture
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def test_run_and_step_persist(db_session):
    run = OperationRun(event_id=1, operation_id=2, team_id=None, status="queued",
                       plan_snapshot="{}", fact_store="{}", trigger="{}")
    db_session.add(run)
    db_session.commit()
    step = OperationRunStep(run_id=run.id, node_id="a", node_type="ability", status="queued")
    db_session.add(step)
    db_session.commit()
    assert db_session.get(OperationRun, run.id).status == "queued"
    assert db_session.get(OperationRunStep, step.id).node_id == "a"
