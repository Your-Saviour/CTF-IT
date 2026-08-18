# tests/test_operation_runner.py
import asyncio
import copy
import json
import threading
import time
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.database import Base
from api.models import OperationRun, OperationRunStep, utcnow
from api.services.operation_driver import AbilityResult
from api.services.operation_runner import decide_node_execution, _finalize_run, _run_node, _wait_for_approval
from builder.operation_compiler import CompiledNode, compile_operation


def _module(module_id, caldera):
    return SimpleNamespace(id=module_id, type="vulnerability", caldera=caldera)


MODULES = {
    "weak_ssh": _module("weak_ssh", {
        "tactic": "initial-access",
        "recon": {"command": "echo VULNERABLE user=svc"},
        "exploit": {"command": "su #{ctf.vuln.weak_ssh}",
                    "outputs": [{"trait": "ctf.weak_ssh.shell", "marker": "FOOTHOLD"}]},
    }),
    "nopasswd_sudo": _module("nopasswd_sudo", {
        "tactic": "privilege-escalation",
        "recon": {"command": "echo VULNERABLE"},
        "exploit": {"command": "sudo id", "inputs": ["ctf.weak_ssh.shell"]},
    }),
}

PLAN = {
    "version": 1,
    "policy": {"time_limit_minutes": 60, "max_concurrency": 1, "default_timeout_seconds": 120,
               "default_retries": 0, "default_retry_delay_seconds": 5},
    "nodes": [
        {"id": "trigger", "type": "manual_trigger", "label": "Manual", "config": {}},
        {"id": "a", "type": "ability", "label": "Foothold",
         "config": {"module_id": "weak_ssh", "ability": "exploit", "target_vm_id": "vm:hq/blue/web"}},
        {"id": "b", "type": "ability", "label": "Privesc",
         "config": {"module_id": "nopasswd_sudo", "ability": "exploit", "target_vm_id": "vm:hq/blue/web"}},
        {"id": "finish", "type": "finish", "label": "Finish", "config": {}},
    ],
    "edges": [
        {"id": "e1", "source": "trigger", "target": "a", "condition": "always"},
        {"id": "e2", "source": "a", "target": "b", "condition": "success"},
        {"id": "e3", "source": "b", "target": "finish", "condition": "always"},
    ],
}


def test_missing_input_is_skipped():
    compiled = compile_operation(PLAN, MODULES)
    node = compiled.nodes["b"]  # inputs: ["ctf.weak_ssh.shell"]
    decision = decide_node_execution(node, {"ctf.vuln.weak_ssh": "svc"})
    assert decision.skipped is True


def test_present_inputs_do_not_skip():
    compiled = compile_operation(PLAN, MODULES)
    node = compiled.nodes["b"]
    decision = decide_node_execution(node, {"ctf.weak_ssh.shell": "svc", "ctf.vuln.weak_ssh": "svc"})
    assert decision.skipped is False


@pytest.fixture
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _compiled_plan(nodes):
    return SimpleNamespace(nodes=nodes)


def _run(team_id=None, status="running"):
    return OperationRun(event_id=1, operation_id=2, team_id=team_id, status=status,
                        plan_snapshot="{}", fact_store="{}", trigger="{}")


def test_finalize_run_preserves_cancelled(db_session):
    run = _run(status="cancelled")
    db_session.add(run)
    db_session.commit()

    _finalize_run(db_session, run.id, {}, _compiled_plan({}))

    refreshed = db_session.get(OperationRun, run.id)
    assert refreshed.status == "cancelled"
    assert refreshed.finished_at is not None


def test_finalize_run_completes_on_finish_success(db_session):
    run = _run()
    db_session.add(run)
    db_session.commit()
    finish = CompiledNode("finish", "finish", "Finish", {})

    _finalize_run(db_session, run.id, {"finish": "success"}, _compiled_plan({"finish": finish}))

    assert db_session.get(OperationRun, run.id).status == "completed"


def test_run_node_gate_is_pass_through():
    class FakeQuery:
        def filter_by(self, **_kwargs):
            return self

        def first(self):
            return None

    class FakeDb:
        def query(self, _model):
            return FakeQuery()

        def add(self, _obj):
            pass

        def commit(self):
            pass

    node = CompiledNode("g", "gate", "Gate", {"mode": "all"})
    with patch("api.services.operation_runner._finish_step", return_value="success") as finish:
        result = asyncio.run(_run_node(FakeDb(), SimpleNamespace(id=1), node,
                                       _compiled_plan({}), {}, None, "src"))
    assert result == "success"
    finish.assert_called_once()
    assert finish.call_args.args[2] == "success"


def _transition_in_background(session_factory, model, obj_id, **attrs):
    def _run():
        time.sleep(0.05)
        other = session_factory()
        try:
            other.query(model).filter(model.id == obj_id).update(attrs)
            other.commit()
        finally:
            other.close()

    thread = threading.Thread(target=_run)
    thread.start()
    return thread


def _approval_step(db_session):
    run = _run()
    db_session.add(run)
    db_session.commit()
    step = OperationRunStep(run_id=run.id, node_id="a", node_type="ability", status="awaiting_approval")
    db_session.add(step)
    db_session.commit()
    return run, step


def test_wait_for_approval_polls_until_admin_approves(db_session):
    run, step = _approval_step(db_session)
    sessions = sessionmaker(bind=db_session.bind)
    thread = _transition_in_background(sessions, OperationRunStep, step.id, status="queued")

    try:
        result = asyncio.run(_wait_for_approval(db_session, run.id, step.id, poll_seconds=0.05))
    finally:
        thread.join()

    assert result == "running"
    assert db_session.get(OperationRunStep, step.id).status == "running"


def test_wait_for_approval_returns_rejected_when_admin_rejects(db_session):
    run, step = _approval_step(db_session)
    sessions = sessionmaker(bind=db_session.bind)
    thread = _transition_in_background(sessions, OperationRunStep, step.id, status="rejected")

    try:
        result = asyncio.run(_wait_for_approval(db_session, run.id, step.id, poll_seconds=0.05))
    finally:
        thread.join()

    assert result == "rejected"
    assert db_session.get(OperationRunStep, step.id).status == "rejected"


def test_wait_for_approval_returns_failure_when_run_cancelled(db_session):
    run, step = _approval_step(db_session)
    sessions = sessionmaker(bind=db_session.bind)
    thread = _transition_in_background(sessions, OperationRun, run.id, status="cancelled")

    try:
        result = asyncio.run(_wait_for_approval(db_session, run.id, step.id, poll_seconds=0.05))
    finally:
        thread.join()

    assert result == "failure"


def test_run_node_pauses_on_ability_when_instructor_approval_and_cancel_fails_step(db_session):
    run = _run()
    db_session.add(run)
    db_session.commit()
    step = OperationRunStep(run_id=run.id, node_id="a", node_type="ability", status="queued")
    db_session.add(step)
    db_session.commit()
    node = CompiledNode("a", "ability", "Ability", {
        "module_id": "weak_ssh", "ability": "exploit", "target_vm_id": "vm:hq/blue/web",
    })
    compiled = SimpleNamespace(policy={"instructor_approval": True})
    sessions = sessionmaker(bind=db_session.bind)
    thread = _transition_in_background(sessions, OperationRun, run.id, status="cancelled")

    try:
        result = asyncio.run(_run_node(db_session, run, node, compiled, {}, None, "src"))
    finally:
        thread.join()

    assert result == "failure"
    assert db_session.get(OperationRunStep, step.id).status == "failure"


class _FakeCaldera:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None


class _FakeDriver:
    def __init__(self, results):
        self.results = list(results)
        self.caldera = _FakeCaldera()

    async def ensure_run_source(self, run_id):
        return f"ctf-run-{run_id}"

    async def seed_run_facts(self, source_id, facts):
        self.seeded = facts

    async def resolve_agent_paw(self, ip):
        return "paw-1"

    async def execute(self, ability_id, adversary_id, agent_paw, group, source_id, timeout_seconds):
        return self.results.pop(0)


class _ExplodingDriver:
    def __init__(self):
        self.caldera = _FakeCaldera()

    async def ensure_run_source(self, run_id):
        return f"ctf-run-{run_id}"

    async def seed_run_facts(self, source_id, facts):
        self.seeded = facts

    async def resolve_agent_paw(self, ip):
        return "paw-1"

    async def execute(self, ability_id, adversary_id, agent_paw, group, source_id, timeout_seconds):
        raise RuntimeError("caldera down")


def _retry_plan(retries=2):
    plan = copy.deepcopy(PLAN)
    plan["policy"]["default_retries"] = retries
    plan["policy"]["default_retry_delay_seconds"] = 0
    return plan


def _seeded_run(db_session, plan_snapshot, fact_store="{}", started_at=None):
    run = _run()
    run.plan_snapshot = json.dumps(plan_snapshot)
    run.fact_store = fact_store
    run.started_at = started_at
    db_session.add(run)
    db_session.commit()
    return run.id


@pytest.fixture
def runner_env(db_session, monkeypatch):
    sessions = sessionmaker(bind=db_session.bind, expire_on_commit=False)
    monkeypatch.setattr("api.services.operation_runner.SessionLocal", sessions)
    monkeypatch.setattr("api.services.operation_runner._resolve_target_vm",
                        lambda db, run, node: SimpleNamespace(ip_address="10.0.0.5"))
    monkeypatch.setattr("builder.module_loader.load_all_modules",
                        lambda: [MODULES["weak_ssh"], MODULES["nopasswd_sudo"]])
    return sessions


def test_run_node_retries_until_success(db_session, monkeypatch):
    compiled = compile_operation(_retry_plan(), MODULES)
    node = compiled.nodes["a"]
    run = _run()
    run.fact_store = json.dumps({"ctf.vuln.weak_ssh": "svc"})
    db_session.add(run)
    db_session.commit()
    driver = _FakeDriver([
        AbilityResult(status=1, output="boom", finished=True),
        AbilityResult(status=0, output="OK FOOTHOLD", finished=True),
    ])
    monkeypatch.setattr("api.services.operation_runner._resolve_target_vm",
                        lambda db, run, node: SimpleNamespace(ip_address="10.0.0.5"))

    result = asyncio.run(_run_node(db_session, run, node, compiled,
                                   {"ctf.vuln.weak_ssh": "svc"}, driver, "src"))

    assert result == "success"
    step = db_session.query(OperationRunStep).filter_by(run_id=run.id).first()
    assert step.attempts == 2
    assert "ctf.weak_ssh.shell" in json.loads(db_session.get(OperationRun, run.id).fact_store)


def test_run_node_exhausts_retries_then_fails(db_session, monkeypatch):
    compiled = compile_operation(_retry_plan(), MODULES)
    node = compiled.nodes["a"]
    run = _run()
    run.fact_store = json.dumps({"ctf.vuln.weak_ssh": "svc"})
    db_session.add(run)
    db_session.commit()
    driver = _FakeDriver([
        AbilityResult(status=1, output="boom", finished=True),
        AbilityResult(status=1, output="boom", finished=True),
        AbilityResult(status=1, output="boom", finished=True),
    ])
    monkeypatch.setattr("api.services.operation_runner._resolve_target_vm",
                        lambda db, run, node: SimpleNamespace(ip_address="10.0.0.5"))

    result = asyncio.run(_run_node(db_session, run, node, compiled,
                                   {"ctf.vuln.weak_ssh": "svc"}, driver, "src"))

    assert result == "failure"
    step = db_session.query(OperationRunStep).filter_by(run_id=run.id).first()
    assert step.attempts == 3
    assert step.status == "failure"


def test_launch_run_marks_failed_when_driver_raises(runner_env, db_session, monkeypatch):
    from api.services.operation_runner import launch_run
    run_id = _seeded_run(db_session, PLAN, fact_store=json.dumps({"ctf.vuln.weak_ssh": "svc"}))
    monkeypatch.setattr("api.services.operation_runner.OperationDriver", _ExplodingDriver)

    asyncio.run(launch_run(run_id))

    db_session.expire_all()
    refreshed = db_session.get(OperationRun, run_id)
    assert refreshed.status == "failed"
    assert refreshed.finished_at is not None
    step = db_session.query(OperationRunStep).filter_by(run_id=run_id).first()
    assert step.status == "failure"
    assert "caldera down" in (step.output or "")


def test_launch_run_hard_stop_fails_run(runner_env, db_session, monkeypatch):
    from api.services.operation_runner import launch_run
    plan = copy.deepcopy(PLAN)
    plan["policy"]["time_limit_minutes"] = 1
    run_id = _seeded_run(db_session, plan, started_at=utcnow() - timedelta(minutes=2))
    monkeypatch.setattr("api.services.operation_runner.OperationDriver", _ExplodingDriver)

    asyncio.run(launch_run(run_id))

    db_session.expire_all()
    refreshed = db_session.get(OperationRun, run_id)
    assert refreshed.status == "failed"
    assert refreshed.finished_at is not None


def test_launch_run_seeds_platform_facts_into_fact_store(db_session, monkeypatch):
    from api.models import Team, VM
    from api.services.operation_runner import launch_run
    team = Team(name="Blue", event_id=1)
    db_session.add(team)
    db_session.commit()
    db_session.add(VM(hostname="web-1", ip_address="10.0.0.5", os="Ubuntu 24.04 LTS x64",
                      event_id=1, team_id=team.id, status="active"))
    db_session.commit()

    ipmod = _module("ipmod", {
        "tactic": "initial-access",
        "recon": {"command": "echo VULNERABLE"},
        "exploit": {"command": "echo #{ctf.ip}", "inputs": ["ctf.ip"]},
    })
    plan = {
        "version": 1,
        "policy": {"time_limit_minutes": 60, "max_concurrency": 1, "default_timeout_seconds": 120,
                   "default_retries": 0, "default_retry_delay_seconds": 5},
        "nodes": [
            {"id": "trigger", "type": "manual_trigger", "label": "Manual", "config": {}},
            {"id": "a", "type": "ability", "label": "A",
             "config": {"module_id": "ipmod", "ability": "exploit", "target_vm_id": "vm:hq/blue/web"}},
            {"id": "finish", "type": "finish", "label": "Finish", "config": {}},
        ],
        "edges": [
            {"id": "e1", "source": "trigger", "target": "a", "condition": "always"},
            {"id": "e2", "source": "a", "target": "finish", "condition": "always"},
        ],
    }
    run_id = _seeded_run(db_session, plan)

    executed = []

    class RecordingDriver(_ExplodingDriver):
        async def execute(self, ability_id, adversary_id, agent_paw, group, source_id, timeout_seconds):
            executed.append(ability_id)
            return AbilityResult(status=0, output="ok", finished=True)

    sessions = sessionmaker(bind=db_session.bind, expire_on_commit=False)
    monkeypatch.setattr("api.services.operation_runner.SessionLocal", sessions)
    monkeypatch.setattr("api.services.operation_runner.OperationDriver", RecordingDriver)
    monkeypatch.setattr("api.services.operation_runner._resolve_target_vm",
                        lambda db, run, node: SimpleNamespace(ip_address="10.0.0.5"))
    monkeypatch.setattr("builder.module_loader.load_all_modules", lambda: [ipmod])

    asyncio.run(launch_run(run_id))

    assert executed, "ability gating on a platform fact must not be skipped"
    db_session.expire_all()
    refreshed = db_session.get(OperationRun, run_id)
    assert refreshed.status == "completed"
    store = json.loads(refreshed.fact_store)
    assert store["ctf.ip"] == "10.0.0.5"
