import json
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.database import Base, get_db
from api.models import Event, EventOperation, OperationRun, OperationRunStep, Team, User
from api.routes.admin import router
from builder.operation_plan import empty_operation_plan


def _valid_plan():
    plan = empty_operation_plan()
    plan["edges"] = [{"id": "e1", "source": "trigger", "target": "finish", "condition": "always"}]
    return plan


@pytest.fixture
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    app = FastAPI()
    app.include_router(router)

    def override_db():
        session = sessions()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def seeded_client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    app = FastAPI()
    app.include_router(router)

    def override_db():
        session = sessions()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as test_client:
        yield test_client, sessions


def test_run_endpoint_rejects_non_admin(client):
    resp = client.post("/admin/api/events/1/operations/1/run", json={})
    assert resp.status_code == 403


async def _noop_launch(run_id: int) -> None:
    return None


def test_run_endpoint_starts_runs_for_all_teams(seeded_client):
    test_client, sessions = seeded_client
    db = sessions()
    event = Event(name="Exercise", quota="{}", status="open")
    db.add(event)
    db.commit()
    db.refresh(event)
    operation = EventOperation(
        event_id=event.id, name="Phase 1", position=0, operation_plan=json.dumps(_valid_plan())
    )
    db.add(operation)
    db.commit()
    db.refresh(operation)
    first = Team(name="Red One", event_id=event.id)
    second = Team(name="Red Two", event_id=event.id)
    db.add_all([first, second])
    db.commit()
    db.close()

    with patch("api.routes.admin.require_admin", return_value=User(is_admin=True)), patch(
        "api.routes.admin.launch_run", new=_noop_launch
    ):
        resp = test_client.post(
            f"/admin/api/events/{event.id}/operations/{operation.id}/run", json={}
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "started"
    assert len(body["run_ids"]) == 2

    db = sessions()
    runs = db.query(OperationRun).filter(OperationRun.operation_id == operation.id).all()
    assert sorted(run.team_id for run in runs) == sorted([first.id, second.id])
    assert all(run.status == "queued" for run in runs)
    db.close()


def test_operation_run_detail_returns_steps(seeded_client):
    test_client, sessions = seeded_client
    db = sessions()
    event = Event(name="Exercise", quota="{}", status="open")
    db.add(event)
    db.commit()
    db.refresh(event)
    operation = EventOperation(
        event_id=event.id, name="Phase 1", position=0, operation_plan=json.dumps(_valid_plan())
    )
    db.add(operation)
    db.commit()
    db.refresh(operation)
    team = Team(name="Red One", event_id=event.id)
    db.add(team)
    db.commit()
    db.refresh(team)
    run = OperationRun(
        event_id=event.id, operation_id=operation.id, team_id=team.id,
        status="running", plan_snapshot=json.dumps(_valid_plan()),
        fact_store=json.dumps({"ctf.vuln.demo": "found"}), trigger="{}",
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    step = OperationRunStep(run_id=run.id, node_id="n1", node_type="ability",
                            status="running", result=None, attempts=1)
    db.add(step)
    db.commit()
    db.close()

    with patch("api.routes.admin.require_admin", return_value=User(is_admin=True)):
        resp = test_client.get(f"/admin/api/operation-runs/{run.id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == run.id
    assert body["status"] == "running"
    assert body["team_id"] == team.id
    assert body["fact_store"] == {"ctf.vuln.demo": "found"}
    assert body["steps"] == [
        {"node_id": "n1", "node_type": "ability", "status": "running",
         "result": None, "output": None, "attempts": 1}
    ]
