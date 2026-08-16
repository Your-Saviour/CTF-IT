from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.database import Base
from api.models import Event, EventOperation


def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_event_returns_operations_in_organisational_order():
    db = session()
    event = Event(name="Exercise", quota="{}")
    event.operations = [
        EventOperation(name="Lateral movement", position=1, operation_plan="{}"),
        EventOperation(name="Initial foothold", position=0, operation_plan="{}"),
    ]
    db.add(event)
    db.commit()
    event_id = event.id
    db.expire_all()

    assert [row.name for row in db.get(Event, event_id).operations] == [
        "Initial foothold",
        "Lateral movement",
    ]


def test_deleting_event_deletes_its_operations():
    db = session()
    event = Event(name="Exercise", quota="{}")
    event.operations = [EventOperation(name="Initial foothold", position=0, operation_plan="{}")]
    db.add(event)
    db.commit()
    operation_id = event.operations[0].id

    db.delete(event)
    db.commit()

    assert db.get(EventOperation, operation_id) is None
