# tests/test_operation_compiler.py
from types import SimpleNamespace

import pytest

from builder.operation_compiler import CompiledNode, compile_operation, edge_activated, next_ready_nodes


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

MODULES = {"weak_ssh": WEAK_SSH, "nopasswd_sudo": NOPASSWD}


def gate_plan(mode):
    return {
        "version": 1,
        "policy": {"time_limit_minutes": 60, "max_concurrency": 1, "default_timeout_seconds": 120,
                   "default_retries": 0, "default_retry_delay_seconds": 5},
        "nodes": [
            {"id": "trigger", "type": "manual_trigger", "label": "Manual", "config": {}},
            {"id": "x", "type": "ability", "label": "X",
             "config": {"module_id": "weak_ssh", "ability": "exploit", "target_vm_id": "vm:hq/blue/web"}},
            {"id": "y", "type": "ability", "label": "Y",
             "config": {"module_id": "nopasswd_sudo", "ability": "exploit", "target_vm_id": "vm:hq/blue/web"}},
            {"id": "gate", "type": "gate", "label": "Gate", "config": {"mode": mode}},
            {"id": "finish", "type": "finish", "label": "Finish", "config": {}},
        ],
        "edges": [
            {"id": "e1", "source": "trigger", "target": "x", "condition": "always"},
            {"id": "e2", "source": "trigger", "target": "y", "condition": "always"},
            {"id": "e3", "source": "x", "target": "gate", "condition": "success"},
            {"id": "e4", "source": "y", "target": "gate", "condition": "success"},
            {"id": "e5", "source": "gate", "target": "finish", "condition": "always"},
        ],
    }


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


def test_gate_all_skips_on_failed_predecessor_and_cascades_to_finish():
    compiled = compile_operation(gate_plan("all"), MODULES)
    assert set(next_ready_nodes(compiled.nodes, compiled.edges, {"x": "success", "y": "failure"})) == {"finish"}
    assert set(next_ready_nodes(compiled.nodes, compiled.edges, {"x": "success", "y": "success"})) == {"gate"}


@pytest.mark.parametrize("mode", ["any", "first"])
def test_gate_any_or_first_activates_on_single_success(mode):
    compiled = compile_operation(gate_plan(mode), MODULES)
    assert set(next_ready_nodes(compiled.nodes, compiled.edges, {"x": "failure", "y": "success"})) == {"gate"}


def test_compile_skips_disabled_nodes_and_edges():
    p = plan()
    p["nodes"].append({"id": "disabled-node", "type": "ability", "label": "Disabled",
                       "config": {"module_id": "nopasswd_sudo", "ability": "exploit",
                                  "target_vm_id": "vm:hq/blue/web"}, "disabled": True})
    p["edges"].append({"id": "eD", "source": "disabled-node", "target": "finish", "condition": "success"})
    compiled = compile_operation(p, MODULES)
    assert "disabled-node" not in compiled.nodes
    assert all(e["source"] != "disabled-node" and e["target"] != "disabled-node"
               for e in compiled.edges)
    assert set(next_ready_nodes(compiled.nodes, compiled.edges, {})) == {"a"}


def test_non_gate_join_activates_on_first_incoming_edge():
    nodes = {
        "x": CompiledNode("x", "ability", "X", {}),
        "y": CompiledNode("y", "ability", "Y", {}),
        "join": CompiledNode("join", "ability", "Join", {}),
    }
    edges = [
        {"id": "e1", "source": "x", "target": "join", "condition": "success"},
        {"id": "e2", "source": "y", "target": "join", "condition": "success"},
    ]
    assert set(next_ready_nodes(nodes, edges, {"x": "success"})) == {"join"}
    assert set(next_ready_nodes(nodes, edges, {"x": "failure"})) == set()
    assert set(next_ready_nodes(nodes, edges, {"x": "failure", "y": "success"})) == {"join"}
    assert set(next_ready_nodes(nodes, edges, {"x": "failure", "y": "failure"})) == set()
