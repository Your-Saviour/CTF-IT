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


def test_showcase_chain_progresses_from_recon_to_foothold(db_session):
    import json

    from api.main import seed_showcase_event
    from api.services.operation_runner import decide_node_execution
    from builder.fact_contract import recon_fact_trait
    from builder.module_loader import load_all_modules
    from builder.operation_compiler import compile_operation, next_ready_nodes

    seed_showcase_event(db_session)
    event = db_session.query(Event).filter(Event.name == "Operation Chaining Demo").first()
    op = db_session.query(EventOperation).filter(EventOperation.event_id == event.id).first()
    modules_by_id = {m.id: m for m in load_all_modules()}
    compiled = compile_operation(json.loads(op.operation_plan), modules_by_id)

    assert set(next_ready_nodes(compiled.nodes, compiled.edges, {})) == {"recon"}
    recon = compiled.nodes["recon"]
    assert recon.phase == "recon"
    assert [s.trait for s in recon.output_specs] == [recon_fact_trait("weak_ssh_credentials")]

    foothold = compiled.nodes["foothold"]
    assert foothold.phase == "exploit"
    assert foothold.input_traits == [recon_fact_trait("weak_ssh_credentials")]

    assert decide_node_execution(foothold, {}).skipped is True
    decision = decide_node_execution(foothold, {recon_fact_trait("weak_ssh_credentials"): "svc-monitor"})
    assert decision.skipped is False

    ready = next_ready_nodes(compiled.nodes, compiled.edges, {"recon": "success"})
    assert "foothold" in ready
    assert "privesc" not in ready
