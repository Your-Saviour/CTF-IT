# tests/test_operation_runner.py
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.database import Base
from api.models import OperationRun
from api.services.operation_runner import decide_node_execution, _finalize_run, _run_node
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
