# tests/test_operation_runner.py
from types import SimpleNamespace

from api.services.operation_runner import decide_node_execution
from builder.operation_compiler import compile_operation


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
