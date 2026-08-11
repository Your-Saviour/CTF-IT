"""Tests for vm_goals API: GET /admin/api/vms/{vm_id}/goals and
POST /admin/api/vms/{vm_id}/goals/{goal_id}/check."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, StaticPool
from sqlalchemy.orm import sessionmaker

from api.database import Base, get_db
from api.models import Event, Team, VM, VMGoal, User, VMModule, PlatformSettings
from api.main import app
from api.routes.vm_goals import _run_remote_verification


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
    event = Event(name="Test Event", quota="{}", status="open")
    db.add(event)
    db.flush()

    team = Team(name="Team A", event_id=event.id)
    db.add(team)
    db.flush()

    admin_user = User(
        username="admin",
        password_hash="x",
        is_admin=True,
        event_id=event.id,
    )
    db.add(admin_user)
    db.flush()

    vm = VM(
        hostname="test-vm",
        ip_address="10.0.0.1",
        team_id=team.id,
        event_id=event.id,
        status="active",
    )
    db.add(vm)
    db.commit()
    yield {"admin": admin_user, "vm": vm}
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

    with patch("api.routes.vm_goals.require_admin", return_value=admin_user):
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c, seeded_data["vm"]

    app.dependency_overrides.clear()


# ── Test 1: GET returns empty list for VM with no goals ──────────────────────

def test_get_goals_empty(client, seeded_data):
    c, vm = client
    db = _Session()
    db.query(VMGoal).filter(VMGoal.vm_id == vm.id).delete()
    db.commit()
    db.close()

    resp = c.get(f"/admin/api/vms/{vm.id}/goals")
    assert resp.status_code == 200
    assert resp.json() == []


# ── Test 2: GET returns goals when they exist ────────────────────────────────

def test_get_goals_returns_records(client, seeded_data):
    c, vm = client
    db = _Session()
    db.query(VMGoal).filter(VMGoal.vm_id == vm.id).delete()
    db.commit()

    goal = VMGoal(
        vm_id=vm.id,
        module_id="goal_one",
        status="pending",
        red_points=10,
        defend_points=5,
    )
    db.add(goal)
    db.commit()
    goal_id = goal.id
    db.close()

    resp = c.get(f"/admin/api/vms/{vm.id}/goals")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["id"] == goal_id
    assert data[0]["vm_id"] == vm.id
    assert data[0]["module_id"] == "goal_one"
    assert data[0]["status"] == "pending"
    assert data[0]["red_points"] == 10
    assert data[0]["defend_points"] == 5
    assert data[0]["achievement_count"] == 0
    assert data[0]["defend_count"] == 0


# ── Test 3: GET returns 404 for unknown VM ───────────────────────────────────

def test_get_goals_unknown_vm(client):
    c, vm = client
    resp = c.get("/admin/api/vms/99999/goals")
    assert resp.status_code == 404


# ── Test 4: POST check returns 404 for unknown VM ────────────────────────────

def test_check_goal_unknown_vm(client):
    c, vm = client
    resp = c.post("/admin/api/vms/99999/goals/1/check")
    assert resp.status_code == 404


# ── Test 5: POST check http_response — goal transitions to "achieved" ────────

def _make_http_response_module(module_id="http_goal"):
    from builder.module_loader import Module
    return Module(
        id=module_id,
        name="HTTP Goal",
        description="",
        type="goal",
        difficulty="easy",
        points=0,
        category="web",
        red_points=10,
        defend_points=5,
        verification={
            "type": "http_response",
            "port": 80,
            "path": "/health",
            "body_contains": "OK",
        },
        revert_verification={},
    )


def test_check_goal_http_response_achieved(client, seeded_data):
    c, vm = client
    db = _Session()
    goal = VMGoal(
        vm_id=vm.id,
        module_id="http_goal",
        status="pending",
        red_points=10,
        defend_points=5,
    )
    db.add(goal)
    db.commit()
    goal_id = goal.id
    db.close()

    mock_module = _make_http_response_module("http_goal")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = "OK"

    mock_client_instance = AsyncMock()
    mock_client_instance.get = AsyncMock(return_value=mock_resp)
    mock_client_cm = MagicMock()
    mock_client_cm.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("api.routes.vm_goals.load_all_modules", return_value=[mock_module]):
        with patch("api.routes.vm_goals.httpx.AsyncClient", return_value=mock_client_cm):
            resp = c.post(f"/admin/api/vms/{vm.id}/goals/{goal_id}/check")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "achieved"
    assert data["achievement_count"] == 1
    assert data["achieved_at"] is not None

    db = _Session()
    updated = db.query(VMGoal).filter(VMGoal.id == goal_id).first()
    assert updated.status == "achieved"
    assert updated.achievement_count == 1
    db.close()


# ── Test 6: POST check revert — goal transitions to "defended" ───────────────

def _make_revert_module(module_id="revert_goal"):
    from builder.module_loader import Module
    return Module(
        id=module_id,
        name="Revert Goal",
        description="",
        type="goal",
        difficulty="easy",
        points=0,
        category="web",
        red_points=10,
        defend_points=5,
        verification={
            "type": "http_response",
            "port": 80,
            "path": "/exploit",
            "body_contains": "PWNED",
        },
        revert_verification={
            "type": "http_response",
            "port": 80,
            "path": "/exploit",
            "body_not_contains": "PWNED",
        },
    )


def test_check_goal_revert_to_defended(client, seeded_data):
    c, vm = client
    db = _Session()
    goal = VMGoal(
        vm_id=vm.id,
        module_id="revert_goal",
        status="achieved",
        red_points=10,
        defend_points=5,
        achievement_count=1,
        achieved_at=datetime.now(timezone.utc),
    )
    db.add(goal)
    db.commit()
    goal_id = goal.id
    db.close()

    mock_module = _make_revert_module("revert_goal")

    # Body does NOT contain "PWNED" → verification fails, revert passes
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = "Page not found"

    mock_client_instance = AsyncMock()
    mock_client_instance.get = AsyncMock(return_value=mock_resp)
    mock_client_cm = MagicMock()
    mock_client_cm.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("api.routes.vm_goals.load_all_modules", return_value=[mock_module]):
        with patch("api.routes.vm_goals.httpx.AsyncClient", return_value=mock_client_cm):
            resp = c.post(f"/admin/api/vms/{vm.id}/goals/{goal_id}/check")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "defended"
    assert data["defend_count"] == 1
    assert data["defended_at"] is not None

    db = _Session()
    updated = db.query(VMGoal).filter(VMGoal.id == goal_id).first()
    assert updated.status == "defended"
    assert updated.defend_count == 1
    db.close()


# ── Test 7: POST check — re-exploitation from "defended" back to "achieved" ───

def test_check_goal_reexploit_from_defended(client, seeded_data):
    """A goal in 'defended' status should transition back to 'achieved' when
    verification passes, with achievement_count incremented."""
    c, vm = client
    db = _Session()
    goal = VMGoal(
        vm_id=vm.id,
        module_id="reexploit_goal",
        status="defended",
        red_points=10,
        defend_points=5,
        achievement_count=1,
        defend_count=1,
        achieved_at=datetime.now(timezone.utc),
        defended_at=datetime.now(timezone.utc),
    )
    db.add(goal)
    db.commit()
    goal_id = goal.id
    db.close()

    mock_module = _make_http_response_module("reexploit_goal")

    # Response body contains "OK" → verification passes → re-exploitation
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = "OK"

    mock_client_instance = AsyncMock()
    mock_client_instance.get = AsyncMock(return_value=mock_resp)
    mock_client_cm = MagicMock()
    mock_client_cm.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("api.routes.vm_goals.load_all_modules", return_value=[mock_module]):
        with patch("api.routes.vm_goals.httpx.AsyncClient", return_value=mock_client_cm):
            resp = c.post(f"/admin/api/vms/{vm.id}/goals/{goal_id}/check")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "achieved"
    assert data["achievement_count"] == 2
    assert data["achieved_at"] is not None

    db = _Session()
    updated = db.query(VMGoal).filter(VMGoal.id == goal_id).first()
    assert updated.status == "achieved"
    assert updated.achievement_count == 2
    db.close()


@pytest.mark.parametrize(
    ("verification", "exit_status", "expected"),
    [
        ({"type": "service_running", "service": "sysmon-helper", "expected": "active"}, 0, True),
        ({"type": "service_running", "service": "sysmon-helper", "expected": "inactive"}, 3, True),
        ({"type": "file_exists", "path": "/tmp/.exfil_shadow"}, 0, True),
        ({"type": "file_absent", "path": "/tmp/.exfil_shadow"}, 1, True),
        ({"type": "file_exists", "path": "/tmp/.exfil_shadow"}, 1, False),
    ],
)
def test_ssh_verification_types(seeded_data, verification, exit_status, expected):
    vm = seeded_data["vm"]
    db = _Session()
    with patch(
        "api.routes.vm_goals._run_ssh_command",
        new=AsyncMock(return_value=exit_status),
    ):
        passed, error = asyncio.run(_run_remote_verification(verification, vm, db))
    db.close()
    assert error is None
    assert passed is expected


def test_ssh_verification_rejects_unsafe_or_incomplete_specs(seeded_data):
    vm = seeded_data["vm"]
    db = _Session()
    cases = [
        {"type": "file_exists", "path": "relative/path"},
        {"type": "service_running", "service": ""},
        {"type": "service_running", "service": "sshd", "expected": "maybe"},
    ]
    for verification in cases:
        passed, error = asyncio.run(_run_remote_verification(verification, vm, db))
        assert passed is False
        assert error
    db.close()
