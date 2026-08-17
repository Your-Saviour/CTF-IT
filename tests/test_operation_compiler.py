# tests/test_operation_compiler.py
from types import SimpleNamespace

from builder.operation_compiler import compile_operation, edge_activated, next_ready_nodes


def module(module_id, caldera):
    return SimpleNamespace(id=module_id, type="vulnerability", caldera=caldera)


WEAK_SSH = module("weak_ssh", {
    "tactic": "initial-access",
    "recon": {"command": "echo VULNERABLE user=svc"},
    "exploit": {"command": "su #{ctf.vuln.weak_ssh}", "outputs": [{"trait": "ctf.weak_ssh.shell", "marker": "FOOTHOLD"}]},
})
NOPASSWD = module("nopasswd_sudo", {
    "tactic": "privilege-escalation",
    "recon": {"command": "echo VULNERABLE"},
    "exploit": {"command": "sudo id", "inputs": ["ctf.weak_ssh.shell"]},
})


def plan():
    return {
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


def test_compile_resolves_module_metadata_and_facts():
    compiled = compile_operation(plan(), {"weak_ssh": WEAK_SSH, "nopasswd_sudo": NOPASSWD})
    assert compiled.nodes["a"].module_id == "weak_ssh"
    assert compiled.nodes["a"].phase == "exploit"
    assert compiled.nodes["a"].command == "su #{ctf.vuln.weak_ssh}"
    assert compiled.nodes["a"].input_traits == ["ctf.vuln.weak_ssh"]
    assert [s.trait for s in compiled.nodes["a"].output_specs] == ["ctf.weak_ssh.shell"]
    assert compiled.nodes["b"].input_traits == ["ctf.weak_ssh.shell"]


def test_edge_activated_semantics():
    assert edge_activated("always", "failure")
    assert edge_activated("success", "success")
    assert not edge_activated("success", "failure")
    assert edge_activated("failure", "failure")
    assert edge_activated("failure", "skipped")   # skipped follows failure edge


def test_next_ready_nodes_sequential_chain():
    compiled = compile_operation(plan(), {"weak_ssh": WEAK_SSH, "nopasswd_sudo": NOPASSWD})
    assert set(next_ready_nodes(compiled.nodes, compiled.edges, {})) == {"a"}
    assert set(next_ready_nodes(compiled.nodes, compiled.edges, {"a": "success"})) == {"b"}
    assert set(next_ready_nodes(compiled.nodes, compiled.edges, {"a": "failure"})) == {"finish"}
