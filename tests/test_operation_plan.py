from types import SimpleNamespace

import pytest

from builder.operation_plan import (
    compile_team_preview,
    empty_operation_plan,
    normalize_operation_plan,
    operation_catalogue,
    operation_input_fingerprint,
    validate_operation_plan,
)


VM_ID = "vm:hq/blue/web"


def infrastructure():
    return {"version": 1, "sites": [{"key": "hq", "name": "HQ", "zones": [{
        "key": "blue", "name": "Blue", "team": "blue", "endpoints": [{
            "key": "web", "name": "Web Server", "base_type": "ubuntu",
        }],
    }]}]}


def modules():
    return [SimpleNamespace(
        id="weak_ssh", name="Weak SSH", description="Use valid accounts", type="vulnerability",
        disabled=False, supported_bases=["ubuntu"], stage="caldera", caldera={
            "tactic": "credential-access", "technique": {"attack_id": "T1078", "name": "Valid Accounts"},
            "recon": {"command": "id"}, "exploit": {"command": "ssh"},
        },
    ), SimpleNamespace(
        id="exfil", name="Exfil shadow", description="Collect proof", type="goal",
        disabled=False, supported_bases=["ubuntu"], stage=None, caldera={
            "tactic": "exfiltration", "technique": {"attack_id": "T1041", "name": "Exfiltration"},
            "exploit": {"command": "cat /etc/shadow"},
        },
    )]


def module_plan():
    return {"version": 1, "assignments": {VM_ID: {
        "mode": "manual_only", "pinned_module_ids": ["weak_ssh", "exfil"],
        "resolved_module_ids": ["weak_ssh", "exfil"],
    }}}


def valid_plan():
    return {"version": 1, "policy": {"launch_mode": "manual", "start_offset_minutes": 0,
        "time_limit_minutes": 60, "max_concurrency": 1, "default_timeout_seconds": 120,
        "default_retries": 0, "default_retry_delay_seconds": 5}, "nodes": [
        {"id": "start", "type": "start", "label": "Start", "x": 40, "y": 100, "config": {}},
        {"id": "ability", "type": "ability", "label": "Exploit SSH", "x": 260, "y": 100,
         "config": {"module_id": "weak_ssh", "ability": "exploit", "target_vm_id": VM_ID}},
        {"id": "objective", "type": "objective", "label": "Exfil proof", "x": 480, "y": 100,
         "config": {"module_id": "exfil", "required": False, "target_vm_id": VM_ID}},
        {"id": "finish", "type": "finish", "label": "Finish", "x": 700, "y": 100, "config": {}},
    ], "edges": [
        {"id": "e1", "source": "start", "target": "ability", "condition": "always"},
        {"id": "e2", "source": "ability", "target": "objective", "condition": "success"},
        {"id": "e3", "source": "objective", "target": "finish", "condition": "always"},
    ]}


def test_empty_plan_has_stable_version_and_policy():
    plan = empty_operation_plan()
    assert plan["version"] == 1
    assert plan["policy"]["launch_mode"] == "manual"
    assert [node["type"] for node in plan["nodes"]] == ["start", "finish"]


def test_normalize_rejects_cycles_and_bad_edge_conditions_structurally():
    plan = valid_plan()
    plan["edges"][0]["condition"] = "maybe"
    with pytest.raises(ValueError, match="condition"):
        normalize_operation_plan(plan)


def test_catalogue_only_contains_assigned_caldera_abilities_and_planned_vms():
    catalogue = operation_catalogue(infrastructure(), module_plan(), modules())
    assert [row["id"] for row in catalogue["targets"]] == [VM_ID]
    assert {(row["module_id"], row["ability"]) for row in catalogue["abilities"]} == {
        ("weak_ssh", "recon"), ("weak_ssh", "exploit"), ("exfil", "exploit")}
    assert [row["module_id"] for row in catalogue["objectives"]] == ["exfil"]


def test_validation_accepts_optional_objective_and_rejects_cycle():
    assert validate_operation_plan(valid_plan(), infrastructure(), module_plan(), modules()) == []
    plan = valid_plan()
    plan["edges"].append({"id": "e4", "source": "objective", "target": "ability", "condition": "failure"})
    issues = validate_operation_plan(plan, infrastructure(), module_plan(), modules())
    assert "cycle" in {issue["code"] for issue in issues}


def test_validation_rejects_unassigned_ability_and_unknown_target():
    plan = valid_plan()
    plan["nodes"][1]["config"]["module_id"] = "not_assigned"
    plan["nodes"][1]["config"]["target_vm_id"] = "vm:no/such/host"
    codes = {issue["code"] for issue in validate_operation_plan(plan, infrastructure(), module_plan(), modules())}
    assert {"ability_unavailable", "unknown_target"} <= codes


def test_fingerprint_changes_with_inputs_not_layout():
    first = operation_input_fingerprint(infrastructure(), module_plan(), modules())
    moved = infrastructure()
    moved["sites"][0]["x"] = 900
    assert operation_input_fingerprint(moved, module_plan(), modules()) == first
    changed = module_plan()
    changed["assignments"][VM_ID]["resolved_module_ids"] = ["weak_ssh"]
    assert operation_input_fingerprint(infrastructure(), changed, modules()) != first


def test_compile_team_preview_is_deterministic_and_provider_neutral():
    one = compile_team_preview(valid_plan(), infrastructure(), module_plan(), modules(), {"id": 7, "name": "Blue"})
    two = compile_team_preview(valid_plan(), infrastructure(), module_plan(), modules(), {"id": 7, "name": "Blue"})
    assert one == two
    assert one["order"] == ["start", "ability", "objective", "finish"]
    assert one["manifest"]["ability"]["planned_vm_id"] == VM_ID
    assert one["manifest"]["ability"]["team_id"] == 7
    assert "caldera_id" not in str(one)
