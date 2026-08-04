"""Tests for the event command-centre API."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.database import Base, get_db
from api.main import app
from api.models import Event, Team, User, VM, VMGoal, VMModule


engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
Session = sessionmaker(bind=engine)


class FakeCaldera:
    def __init__(self, agents=None, error=None):
        self.agents = agents or []
        self.error = error
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def list_agents(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.agents


@pytest.fixture(autouse=True)
def database():
    Base.metadata.create_all(engine)

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    yield
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)


def seed_event(name="Dashboard Event"):
    db = Session()
    event = Event(name=name, quota="{}", status="open")
    db.add(event)
    db.flush()
    admin = User(username=f"dash-admin-{event.id}", password_hash="x", is_admin=True, event_id=event.id)
    db.add(admin)
    db.commit()
    ids = event.id, admin.id
    db.close()
    return ids


def request_dashboard(event_id, admin_id, caldera):
    db = Session()
    admin = db.get(User, admin_id)
    with patch("api.routes.event_dashboard.require_admin", return_value=admin), patch(
        "api.routes.event_dashboard._make_client", return_value=caldera
    ):
        with TestClient(app) as client:
            response = client.get(f"/admin/events/{event_id}/dashboard-data")
    db.close()
    return response


def test_authorization_and_missing_event():
    event_id, admin_id = seed_event()
    with patch("api.routes.event_dashboard.require_admin", return_value=None):
        with TestClient(app) as client:
            assert client.get(f"/admin/events/{event_id}/dashboard-data").status_code == 403

    response = request_dashboard(999999, admin_id, FakeCaldera())
    assert response.status_code == 404
    assert response.json() == {"error": "Event not found"}


def test_dashboard_page_auth_redirects_and_browser_contract():
    event_id, admin_id = seed_event()
    db = Session()
    admin = db.get(User, admin_id)
    with TestClient(app) as client:
        response = client.get(f"/admin/events/{event_id}/dashboard", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/"

        with patch("api.main.get_current_user", return_value=admin):
            response = client.get(f"/admin/events/{event_id}/dashboard")
            assert response.status_code == 200
            assert "Live event command centre" in response.text
            assert "window.setInterval(poll, 10000)" in response.text
            assert "Stale · " in response.text
            assert 'href="/admin/vm/' in response.text
            missing = client.get("/admin/events/999999/dashboard", follow_redirects=False)
            assert missing.status_code == 303
            assert missing.headers["location"] == "/admin"
    db.close()


def test_empty_event_returns_zero_collections_and_fetches_agents_once():
    event_id, admin_id = seed_event()
    caldera = FakeCaldera()
    response = request_dashboard(event_id, admin_id, caldera)
    assert response.status_code == 200
    data = response.json()
    assert caldera.calls == 1
    assert data["summary"] == {
        "participants": 0, "teams": 0, "vms": 0,
        "assigned_modules": 0, "completed_modules": 0,
        "completion_percentage": 0,
        "red_offensive": 0, "blue_defensive": 0,
        "blue_reactive": 0, "blue_total": 0,
    }
    assert data["teams"] == data["modules"] == data["vms"] == data["alerts"] == data["activity"] == []


def test_scores_completion_team_aggregation_bottlenecks_and_event_isolation():
    event_id, admin_id = seed_event()
    other_event_id, _ = seed_event("Other Event")
    db = Session()
    alpha = Team(name="Alpha", event_id=event_id)
    bravo = Team(name="Bravo", event_id=event_id)
    outsider = Team(name="Outsider", event_id=other_event_id)
    db.add_all([alpha, bravo, outsider]); db.flush()
    vms = [
        VM(hostname="a1", ip_address="10.0.0.1", status="active", team_id=alpha.id, event_id=event_id),
        VM(hostname="a2", ip_address="10.0.0.2", status="active", team_id=alpha.id, event_id=event_id),
        VM(hostname="b1", ip_address="10.0.0.3", status="active", team_id=bravo.id, event_id=event_id),
        VM(hostname="x1", ip_address="10.9.0.1", status="active", team_id=outsider.id, event_id=other_event_id),
    ]
    db.add_all(vms); db.flush()
    db.add_all([
        VMModule(vm_id=vms[0].id, module_id="missing-a", module_type="hardening", difficulty="easy", points=20, completed=True, completed_at=datetime.now(timezone.utc), stage="preapplied"),
        VMModule(vm_id=vms[1].id, module_id="missing-a", module_type="hardening", difficulty="easy", points=20, completed=False, stage="preapplied"),
        VMModule(vm_id=vms[2].id, module_id="missing-b", module_type="hardening", difficulty="easy", points=10, completed=False, stage="preapplied"),
        VMModule(vm_id=vms[0].id, module_id="ignored-stage", module_type="vulnerability", difficulty="easy", points=999, completed=True, stage="caldera"),
        VMModule(vm_id=vms[3].id, module_id="outsider", module_type="hardening", difficulty="easy", points=999, completed=True, stage="preapplied"),
    ])
    db.add_all([
        VMGoal(vm_id=vms[0].id, module_id="goal-a", red_points=7, defend_points=5, achievement_count=2, defend_count=1),
        VMGoal(vm_id=vms[2].id, module_id="goal-b", red_points=3, defend_points=20, achievement_count=1, defend_count=1),
        VMGoal(vm_id=vms[3].id, module_id="outsider-goal", red_points=999, defend_points=999, achievement_count=1, defend_count=1),
    ])
    db.commit(); db.close()

    agents = [{"paw": str(i), "host_ip_addrs": [f"10.0.0.{i}"], "trusted": True} for i in range(1, 4)]
    data = request_dashboard(event_id, admin_id, FakeCaldera(agents)).json()
    assert data["summary"]["assigned_modules"] == 3
    assert data["summary"]["completed_modules"] == 1
    assert data["summary"]["completion_percentage"] == pytest.approx(33.3)
    assert data["summary"]["blue_defensive"] == 20
    assert data["summary"]["blue_reactive"] == 25
    assert data["summary"]["red_offensive"] == 17
    assert [team["team_name"] for team in data["teams"]] == ["Alpha", "Bravo"]
    assert data["teams"][0]["vm_count"] == 2
    assert [module["module_id"] for module in data["modules"]] == ["missing-b", "missing-a"]
    assert data["modules"][0]["module_name"] == "missing-b"
    assert all(vm["team_name"] != "Outsider" for vm in data["vms"])


def test_health_alerts_activity_order_and_caldera_degradation():
    event_id, admin_id = seed_event()
    now = datetime.now(timezone.utc)
    db = Session()
    team = Team(name="Ops", event_id=event_id); db.add(team); db.flush()
    healthy = VM(hostname="healthy", ip_address="10.0.0.1", status="active", team_id=team.id, event_id=event_id)
    missing = VM(hostname="missing", ip_address="10.0.0.2", status="active", team_id=team.id, event_id=event_id)
    failed = VM(hostname="failed", status="failed", team_id=team.id, event_id=event_id)
    pending = VM(hostname="pending", status="provisioning", updated_at=now, team_id=team.id, event_id=event_id)
    stalled = VM(hostname="stalled", status="provisioning", updated_at=now - timedelta(minutes=11), team_id=team.id, event_id=event_id)
    db.add_all([healthy, missing, failed, pending, stalled]); db.flush()
    db.add(VMModule(vm_id=healthy.id, module_id="deleted-module", module_type="hardening", difficulty="easy", points=5, completed=True, completed_at=now - timedelta(minutes=2), stage="preapplied"))
    db.add(VMGoal(vm_id=healthy.id, module_id="deleted-goal", achieved_at=now - timedelta(minutes=3), defended_at=now - timedelta(minutes=1)))
    db.commit(); db.close()

    data = request_dashboard(event_id, admin_id, FakeCaldera([{"paw":"alive", "host_ip_addrs":["10.0.0.1"], "trusted":True}])).json()
    health = {vm["hostname"]: vm["health"] for vm in data["vms"]}
    assert health == {"failed":"failed", "healthy":"healthy", "missing":"degraded", "pending":"pending", "stalled":"degraded"}
    assert {alert["type"] for alert in data["alerts"]} == {"vm_failed", "agent_missing", "provisioning_stalled"}
    assert [item["type"] for item in data["activity"]] == ["goal_defended", "module_completed", "goal_achieved"]
    assert data["activity"][1]["module_name"] == "deleted-module"

    degraded = request_dashboard(event_id, admin_id, FakeCaldera(error=RuntimeError("offline"))).json()
    assert degraded["health"]["caldera_available"] is False
    assert degraded["alerts"][0]["type"] == "caldera_unavailable"
    assert not any(alert["type"] == "agent_missing" for alert in degraded["alerts"])
    assert next(vm for vm in degraded["vms"] if vm["hostname"] == "healthy")["health"] == "degraded"
