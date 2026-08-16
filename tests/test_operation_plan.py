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
    return {"version": 1, "policy": {"time_limit_minutes": 60, "max_concurrency": 1, "default_timeout_seconds": 120,
        "default_retries": 0, "default_retry_delay_seconds": 5}, "nodes": [
        {"id": "trigger", "type": "manual_trigger", "label": "Manual Trigger", "x": 40, "y": 100, "config": {}},
        {"id": "ability", "type": "ability", "label": "Exploit SSH", "x": 260, "y": 100,
         "config": {"module_id": "weak_ssh", "ability": "exploit", "target_vm_id": VM_ID}},
        {"id": "objective", "type": "objective", "label": "Exfil proof", "x": 480, "y": 100,
         "config": {"module_id": "exfil", "required": False, "target_vm_id": VM_ID}},
        {"id": "finish", "type": "finish", "label": "Finish", "x": 700, "y": 100, "config": {}},
    ], "edges": [
        {"id": "e1", "source": "trigger", "target": "ability", "condition": "always"},
        {"id": "e2", "source": "ability", "target": "objective", "condition": "success"},
        {"id": "e3", "source": "objective", "target": "finish", "condition": "always"},
    ]}


def test_empty_plan_has_stable_version_and_policy():
    plan = empty_operation_plan()
    assert plan["version"] == 1
    assert "launch_mode" not in plan["policy"]
    assert "start_offset_minutes" not in plan["policy"]
    assert [node["type"] for node in plan["nodes"]] == ["manual_trigger", "finish"]


@pytest.mark.parametrize(("launch_mode", "offset", "trigger_type", "trigger_config", "approval"), [
    ("manual", 0, "manual_trigger", {}, False),
    ("scheduled", 15, "scheduled_trigger", {"offset_minutes": 15}, False),
    ("scheduled_hold", 20, "scheduled_trigger", {"offset_minutes": 20}, True),
])
def test_normalize_migrates_legacy_start_without_changing_graph_identity(
    launch_mode, offset, trigger_type, trigger_config, approval,
):
    plan = valid_plan()
    plan["policy"].update({"launch_mode": launch_mode, "start_offset_minutes": offset})
    plan["nodes"][0] = {"id": "start", "type": "start", "label": "Start", "x": 41, "y": 99,
                        "disabled": False, "config": {}}
    plan["edges"][0]["source"] = "start"

    normalized = normalize_operation_plan(plan)

    trigger = normalized["nodes"][0]
    assert trigger == {"id": "start", "type": trigger_type,
                       "label": "Start",
                       "x": 41.0, "y": 99.0, "disabled": False, "config": trigger_config}
    assert normalized["edges"][0]["source"] == "start"
    assert "launch_mode" not in normalized["policy"]
    assert "start_offset_minutes" not in normalized["policy"]
    assert normalized["policy"]["instructor_approval"] is approval
    assert normalize_operation_plan(normalized) == normalized


def test_normalize_defaults_missing_legacy_launch_mode_to_manual_trigger():
    plan = valid_plan()
    plan["nodes"][0]["type"] = "start"
    normalized = normalize_operation_plan(plan)
    assert normalized["nodes"][0]["type"] == "manual_trigger"


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
    assert catalogue["controls"] == ["manual_trigger", "event_start_trigger", "scheduled_trigger", "finish", "delay", "gate"]


def test_catalogue_treats_pins_and_recursive_dependencies_as_effective_assignments():
    library = modules()
    library[0].requires = ["foundation"]
    library.append(SimpleNamespace(
        id="foundation", name="Foundation", description="Required service", type="vulnerability",
        disabled=False, supported_bases=["ubuntu"], stage="caldera", requires=[], caldera={
            "tactic": "discovery", "technique": {"attack_id": "T1082", "name": "System Information"},
            "recon": {"command": "uname -a"},
        },
    ))
    pinned_only = module_plan()
    pinned_only["assignments"][VM_ID]["pinned_module_ids"] = ["weak_ssh"]
    pinned_only["assignments"][VM_ID]["resolved_module_ids"] = []
    ability_modules = {row["module_id"] for row in operation_catalogue(infrastructure(), pinned_only, library)["abilities"]}
    assert ability_modules == {"weak_ssh", "foundation"}


def test_validation_accepts_optional_objective_and_rejects_cycle():
    assert validate_operation_plan(valid_plan(), infrastructure(), module_plan(), modules()) == []
    plan = valid_plan()
    plan["edges"].append({"id": "e4", "source": "objective", "target": "ability", "condition": "failure"})
    issues = validate_operation_plan(plan, infrastructure(), module_plan(), modules())
    assert "cycle" in {issue["code"] for issue in issues}


def test_validation_requires_one_root_trigger_with_no_incoming_edge():
    missing = valid_plan()
    missing["nodes"][0]["disabled"] = True
    assert "trigger_count" in {issue["code"] for issue in validate_operation_plan(
        missing, infrastructure(), module_plan(), modules())}

    multiple = valid_plan()
    multiple["nodes"].append({"id": "event-trigger", "type": "event_start_trigger",
                              "label": "Event Start Trigger", "x": 0, "y": 0, "config": {}})
    assert "trigger_count" in {issue["code"] for issue in validate_operation_plan(
        multiple, infrastructure(), module_plan(), modules())}

    incoming = valid_plan()
    incoming["edges"].append({"id": "incoming", "source": "ability", "target": "trigger", "condition": "failure"})
    codes = {issue["code"] for issue in validate_operation_plan(incoming, infrastructure(), module_plan(), modules())}
    assert "trigger_incoming" in codes


@pytest.mark.parametrize("offset", [-1, 1.5, True, "5"])
def test_validation_rejects_invalid_scheduled_trigger_offset(offset):
    plan = valid_plan()
    plan["nodes"][0].update({"type": "scheduled_trigger", "label": "Scheduled Trigger",
                             "config": {"offset_minutes": offset}})
    assert "invalid_trigger_offset" in {issue["code"] for issue in validate_operation_plan(
        plan, infrastructure(), module_plan(), modules())}


def test_validation_bounds_scheduled_trigger_to_event_duration():
    plan = valid_plan()
    plan["nodes"][0].update({"type": "scheduled_trigger", "label": "Scheduled Trigger",
                             "config": {"offset_minutes": 11}})
    assert "outside_event" in {issue["code"] for issue in validate_operation_plan(
        plan, infrastructure(), module_plan(), modules(), event_minutes=70)}
    plan["nodes"][0]["config"]["offset_minutes"] = 10
    assert validate_operation_plan(plan, infrastructure(), module_plan(), modules(), event_minutes=70) == []


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
    changed["assignments"][VM_ID]["pinned_module_ids"] = ["weak_ssh"]
    changed["assignments"][VM_ID]["resolved_module_ids"] = ["weak_ssh"]
    assert operation_input_fingerprint(infrastructure(), changed, modules()) != first


def test_compile_team_preview_is_deterministic_and_provider_neutral():
    one = compile_team_preview(valid_plan(), infrastructure(), module_plan(), modules(), {"id": 7, "name": "Blue"})
    two = compile_team_preview(valid_plan(), infrastructure(), module_plan(), modules(), {"id": 7, "name": "Blue"})
    assert one == two
    assert one["order"] == ["trigger", "ability", "objective", "finish"]
    assert one["trigger"] == {"type": "manual", "once": True}
    assert one["manifest"]["ability"]["planned_vm_id"] == VM_ID
    assert one["manifest"]["ability"]["team_id"] == 7
    assert "caldera_id" not in str(one)


@pytest.mark.parametrize(("node_type", "config", "expected"), [
    ("event_start_trigger", {}, {"type": "event_start", "once": True}),
    ("scheduled_trigger", {"offset_minutes": 15},
     {"type": "scheduled", "offset_minutes": 15, "once": True}),
])
def test_compile_team_preview_emits_provider_neutral_trigger_contract(node_type, config, expected):
    plan = valid_plan()
    plan["nodes"][0].update({"type": node_type, "label": node_type.replace("_", " ").title(), "config": config})
    preview = compile_team_preview(plan, infrastructure(), module_plan(), modules(), {"id": 7, "name": "Blue"})
    assert preview["trigger"] == expected
