"""Tests for GET /admin/caldera/scoreboard."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, StaticPool
from sqlalchemy.orm import sessionmaker
from unittest.mock import patch

from api.database import Base, get_db
from api.models import Event, Team, VM, VMGoal, VMModule, User, PlatformSettings
from api.main import app


# ── Shared in-memory SQLite (StaticPool keeps one connection) ─────────────────

_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_Session = sessionmaker(bind=_engine)


@pytest.fixture(scope="module", autouse=True)
def setup_db():
    Base.metadata.create_all(bind=_engine)
    yield
    Base.metadata.drop_all(bind=_engine)


@pytest.fixture(scope="module")
def seeded_data():
    """Seed event, team, admin user, and VM once for the test module."""
    db = _Session()
    event = Event(name="Scoreboard Test Event", quota="{}", status="open")
    db.add(event)
    db.flush()

    team = Team(name="Blue Squad", event_id=event.id)
    db.add(team)
    db.flush()

    admin_user = User(
        username="admin_sb",
        password_hash="x",
        is_admin=True,
        event_id=event.id,
    )
    db.add(admin_user)
    db.flush()

    vm = VM(
        hostname="target-01",
        ip_address="10.0.1.1",
        team_id=team.id,
        event_id=event.id,
        status="active",
    )
    db.add(vm)
    db.commit()
    yield {"admin": admin_user, "vm": vm, "team": team, "event": event}
    db.close()


@pytest.fixture()
def client(seeded_data):
    """TestClient with DB and admin auth overrides."""
    admin_user = seeded_data["admin"]

    def override_get_db():
        db = _Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    with patch("api.routes.caldera_ops.require_admin", return_value=admin_user):
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c, seeded_data

    app.dependency_overrides.clear()


# ── Test 1: Empty scoreboard (no VMs for a nonexistent event) ─────────────────

def test_scoreboard_no_vms(seeded_data):
    """When filtering to an event with no VMs, returns empty teams list."""
    admin_user = seeded_data["admin"]

    # Create a fresh event with no VMs
    db = _Session()
    empty_event = Event(name="Empty Event", quota="{}", status="open")
    db.add(empty_event)
    db.commit()
    empty_event_id = empty_event.id
    db.close()

    def override_get_db():
        db = _Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    with patch("api.routes.caldera_ops.require_admin", return_value=admin_user):
        with TestClient(app, raise_server_exceptions=True) as c:
            resp = c.get(f"/admin/caldera/scoreboard?event_id={empty_event_id}")

    app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert data["event_id"] == empty_event_id
    assert data["teams"] == []
    assert data["totals"]["red"] == 0
    assert data["totals"]["blue_defensive"] == 0
    assert data["totals"]["blue_reactive"] == 0


# ── Test 2: Scoreboard with one team/VM with goals and modules ────────────────

def test_scoreboard_with_goals_and_modules(client, seeded_data):
    """blue_defensive = completed preapplied module points,
    blue_reactive = defend_points * defend_count,
    red_offensive = red_points * achievement_count."""
    c, data = client
    vm = data["vm"]
    event = data["event"]

    db = _Session()
    # Clear any existing data for this VM
    db.query(VMGoal).filter(VMGoal.vm_id == vm.id).delete()
    db.query(VMModule).filter(VMModule.vm_id == vm.id).delete()
    db.commit()

    # Add a completed preapplied module (worth 20 points) → blue_defensive += 20
    mod1 = VMModule(
        vm_id=vm.id,
        module_id="ssh_hardening",
        module_type="hardening",
        difficulty="easy",
        points=20,
        completed=True,
        stage="preapplied",
    )
    # Add an incomplete preapplied module → should NOT count
    mod2 = VMModule(
        vm_id=vm.id,
        module_id="firewall_rules",
        module_type="hardening",
        difficulty="medium",
        points=30,
        completed=False,
        stage="preapplied",
    )
    # Add a completed module with stage=None → should NOT count toward blue_defensive
    mod3 = VMModule(
        vm_id=vm.id,
        module_id="some_vuln",
        module_type="vulnerability",
        difficulty="easy",
        points=10,
        completed=True,
        stage=None,
    )
    db.add_all([mod1, mod2, mod3])
    db.commit()

    # Goal 1: achieved 2 times (red_points=15), defended 1 time (defend_points=10)
    # → red_offensive += 15*2=30, blue_reactive += 10*1=10
    goal1 = VMGoal(
        vm_id=vm.id,
        module_id="web_rce",
        status="defended",
        red_points=15,
        defend_points=10,
        achievement_count=2,
        defend_count=1,
    )
    # Goal 2: achieved 1 time, never defended
    # → red_offensive += 5*1=5, blue_reactive += 0
    goal2 = VMGoal(
        vm_id=vm.id,
        module_id="privesc",
        status="achieved",
        red_points=5,
        defend_points=8,
        achievement_count=1,
        defend_count=0,
    )
    db.add_all([goal1, goal2])
    db.commit()
    db.close()

    resp = c.get(f"/admin/caldera/scoreboard?event_id={event.id}")
    assert resp.status_code == 200
    result = resp.json()

    assert result["event_id"] == event.id
    assert len(result["teams"]) == 1

    team_data = result["teams"][0]
    assert team_data["team_name"] == "Blue Squad"
    assert team_data["blue_defensive"] == 20      # only mod1 counts
    assert team_data["blue_reactive"] == 10       # 10*1
    assert team_data["blue_total"] == 30
    assert team_data["red_offensive"] == 35       # 15*2 + 5*1

    assert result["totals"]["blue_defensive"] == 20
    assert result["totals"]["blue_reactive"] == 10
    assert result["totals"]["blue_total"] == 30
    assert result["totals"]["red"] == 35

    # Check VM-level breakdown
    assert len(team_data["vms"]) == 1
    vm_data = team_data["vms"][0]
    assert vm_data["hostname"] == "target-01"
    assert vm_data["blue_defensive"] == 20
    assert vm_data["blue_reactive"] == 10
    assert vm_data["red_offensive"] == 35
    assert len(vm_data["goals"]) == 2


# ── Test 3: Unknown event_id returns 404 ─────────────────────────────────────

def test_scoreboard_unknown_event(client):
    """Requesting a nonexistent event_id returns 404."""
    c, _ = client
    resp = c.get("/admin/caldera/scoreboard?event_id=999999")
    assert resp.status_code == 404
    assert resp.json()["error"] == "Event not found"
