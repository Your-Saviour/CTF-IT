import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.database import Base
from api.models import Event, Scenario


@pytest.fixture
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    yield sessions
    sessions().close()


def test_scenario_version_and_event_provenance(db_session):
    db = db_session()
    scenario = Scenario(name="Locked Shields", version=1, quota="{}",
                        infrastructure="{}", module_plan=None, operations_json="[]", timeline=None)
    db.add(scenario); db.commit(); db.refresh(scenario)

    event = Event(name="Exercise", quota="{}", status="draft",
                  scenario_id=scenario.id, scenario_version=scenario.version)
    db.add(event); db.commit(); db.refresh(event)

    assert event.scenario_id == scenario.id
    assert event.scenario_version == 1
    assert event.timeline is None


def test_scenario_name_is_unique(db_session):
    import sqlalchemy.exc
    db = db_session()
    db.add(Scenario(name="Unique", version=1, quota="{}", infrastructure="{}"))
    db.commit()
    db.add(Scenario(name="Unique", version=1, quota="{}", infrastructure="{}"))
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        db.commit()
