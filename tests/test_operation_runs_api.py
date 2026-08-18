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
        {"id": step.id, "node_id": "n1", "node_type": "ability", "status": "running",
         "result": None, "output": None, "attempts": 1}
    ]


def _seed_run(sessions, run_status="running", step_status=None):
    db = sessions()
    event = Event(name="Exercise", quota="{}", status="open")
    db.add(event)
    db.commit()
    db.refresh(event)
    team = Team(name="Red One", event_id=event.id)
    db.add(team)
    db.commit()
    db.refresh(team)
    run = OperationRun(
        event_id=event.id, operation_id=1, team_id=team.id, status=run_status,
        plan_snapshot="{}", fact_store="{}", trigger="{}",
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    step_id = None
    if step_status is not None:
        step = OperationRunStep(run_id=run.id, node_id="a", node_type="ability", status=step_status)
        db.add(step)
        db.commit()
        db.refresh(step)
        step_id = step.id
    db.close()
    return run.id, step_id


def test_approve_run_step_transitions_awaiting_approval_to_queued(seeded_client):
    test_client, sessions = seeded_client
    run_id, step_id = _seed_run(sessions, step_status="awaiting_approval")

    with patch("api.routes.admin.require_admin", return_value=User(is_admin=True)):
        resp = test_client.post(f"/admin/api/operation-runs/{run_id}/steps/{step_id}/approve")

    assert resp.status_code == 200
    assert resp.json() == {"status": "approved"}
    db = sessions()
    assert db.get(OperationRunStep, step_id).status == "queued"
    db.close()


def test_approve_run_step_returns_409_when_not_awaiting_approval(seeded_client):
    test_client, sessions = seeded_client
    run_id, step_id = _seed_run(sessions, step_status="running")

    with patch("api.routes.admin.require_admin", return_value=User(is_admin=True)):
        resp = test_client.post(f"/admin/api/operation-runs/{run_id}/steps/{step_id}/approve")

    assert resp.status_code == 409


def test_reject_run_step_transitions_awaiting_approval_to_rejected(seeded_client):
    test_client, sessions = seeded_client
    run_id, step_id = _seed_run(sessions, step_status="awaiting_approval")

    with patch("api.routes.admin.require_admin", return_value=User(is_admin=True)):
        resp = test_client.post(f"/admin/api/operation-runs/{run_id}/steps/{step_id}/reject")

    assert resp.status_code == 200
    assert resp.json() == {"status": "rejected"}
    db = sessions()
    assert db.get(OperationRunStep, step_id).status == "rejected"
    db.close()


def test_cancel_operation_run_marks_run_cancelled(seeded_client):
    test_client, sessions = seeded_client
    run_id, _ = _seed_run(sessions, run_status="running")

    with patch("api.routes.admin.require_admin", return_value=User(is_admin=True)):
        resp = test_client.post(f"/admin/api/operation-runs/{run_id}/cancel")

    assert resp.status_code == 200
    assert resp.json() == {"status": "cancelled"}
    db = sessions()
    assert db.get(OperationRun, run_id).status == "cancelled"
    db.close()


def test_list_operation_runs_returns_all_runs(seeded_client):
    test_client, sessions = seeded_client
    db = sessions()
    event = Event(name="Exercise", quota="{}", status="open")
    db.add(event)
    db.commit()
    db.refresh(event)
    first = Team(name="Red One", event_id=event.id)
    second = Team(name="Red Two", event_id=event.id)
    db.add_all([first, second])
    db.commit()
    db.refresh(first)
    db.refresh(second)
    operation = EventOperation(
        event_id=event.id, name="Phase 1", position=0, operation_plan=json.dumps(_valid_plan())
    )
    db.add(operation)
    db.commit()
    db.refresh(operation)
    r1 = OperationRun(
        event_id=event.id, operation_id=operation.id, team_id=first.id, status="queued",
        plan_snapshot="{}", fact_store="{}", trigger="{}",
    )
    r2 = OperationRun(
        event_id=event.id, operation_id=operation.id, team_id=second.id, status="completed",
        plan_snapshot="{}", fact_store="{}", trigger="{}",
    )
    db.add_all([r1, r2])
    db.commit()
    db.refresh(r1)
    db.refresh(r2)
    db.close()

    with patch("api.routes.admin.require_admin", return_value=User(is_admin=True)):
        resp = test_client.get(f"/admin/api/events/{event.id}/operations/{operation.id}/runs")

    assert resp.status_code == 200
    rows = resp.json()["runs"]
    assert {row["id"] for row in rows} == {r1.id, r2.id}
    assert {row["team_id"] for row in rows} == {first.id, second.id}
    assert {row["status"] for row in rows} == {"queued", "completed"}
