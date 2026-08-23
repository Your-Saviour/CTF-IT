import pytest

from builder.module_plan import (
    assignable_endpoints,
    empty_module_plan,
    normalize_module_plan,
    reconcile_module_plan,
    resolve_assignment,
    validate_module_plan_for_start,
    validate_green_assignments,
)
from builder.module_loader import Module


def infrastructure():
    return {"vpn_gateway": {}, "green_infrastructure": {"vms": [{
        "key": "expo_it", "name": "Expo-IT", "base_type": "ubuntu",
        "default_plan": "small", "region": "syd",
    }]}, "sites": [{"key": "hq", "name": "HQ", "zones": [
        {"key": "blue", "name": "Blue", "team": "blue", "endpoints": [
            {"key": "analyst", "name": "Analyst", "base_type": "ubuntu"}]},
        {"key": "red", "name": "Red", "team": "red", "endpoints": [
            {"key": "operator", "name": "Operator", "base_type": "kali"}]},
    ]}]}


def test_empty_plan_is_versioned():
    assert empty_module_plan() == {"version": 1, "assignments": {}}


def test_assignable_endpoints_include_blue_and_red():
    assert [(row["id"], row["role"]) for row in assignable_endpoints(infrastructure())] == [
        ("green:expo_it", "green"), ("vm:hq/blue/analyst", "blue"),
        ("vm:hq/red/operator", "red")]


def test_normalize_rejects_duplicate_module_ids():
    with pytest.raises(ValueError, match="duplicate"):
        normalize_module_plan({"version": 1, "assignments": {"vm:hq/blue/analyst": {
            "mode": "random_fill", "pinned_module_ids": ["one", "one"], "resolved_module_ids": []}}})


def test_reconcile_keeps_deleted_assignment_visible_as_issue():
    plan = {"version": 1, "assignments": {"vm:gone/zone/host": {
        "mode": "manual_only", "pinned_module_ids": [], "resolved_module_ids": []}}}
    reconciled, issues = reconcile_module_plan(plan, infrastructure())
    assert "vm:gone/zone/host" in reconciled["assignments"]
    assert issues == [{"code": "unknown_vm", "vm_id": "vm:gone/zone/host",
                       "message": "Assignment references a VM that is no longer planned"}]


def mod(module_id, *, difficulty="easy", conflicts=None, requires=None):
    return Module(module_id, module_id, module_id, "vulnerability", difficulty, 100, "test",
                  conflicts=conflicts or [], requires=requires or [])


def test_green_nodes_only_accept_green_infrastructure_modules():
    deployment = Module(
        "expo_it", "Expo-IT", "Shared service", "green_infrastructure", "medium", 0,
        "exercise-management", supported_bases=["ubuntu"], deployment={"inputs": [], "outputs": []},
    )
    result = resolve_assignment(
        {"base_type": "ubuntu", "role": "green"},
        {"mode": "manual_only", "pinned_module_ids": ["expo_it"], "resolved_module_ids": []},
        {}, [deployment, mod("endpoint_only")], refill=False,
    )
    assert result["resolved_module_ids"] == ["expo_it"]

    rejected = resolve_assignment(
        {"base_type": "ubuntu", "role": "green"},
        {"mode": "manual_only", "pinned_module_ids": ["endpoint_only"], "resolved_module_ids": []},
        {}, [deployment, mod("endpoint_only")], refill=False,
    )
    assert rejected["issues"][0]["code"] == "incompatible_base"


def test_green_assignments_require_manual_deployment_modules():
    plan = {"version": 1, "assignments": {"green:expo_it": {
        "mode": "random_fill", "pinned_module_ids": ["endpoint_only"],
        "resolved_module_ids": ["endpoint_only"],
    }}}
    assert {issue["code"] for issue in validate_green_assignments(
        plan, infrastructure(), [mod("endpoint_only")],
    )} == {"green_manual_only", "invalid_green_module"}


def test_pins_override_quota_and_fill_only_the_deficit():
    library = [mod("pinned"), mod("random")]
    result = resolve_assignment({"base_type": "ubuntu", "role": "blue"},
        {"mode": "random_fill", "pinned_module_ids": ["pinned"], "resolved_module_ids": []},
        {"vulnerability": {"easy": 1}}, library, refill=True)
    assert result["resolved_module_ids"] == ["pinned"]


def test_dependencies_precede_pinned_consumer():
    library = [mod("dependency"), mod("consumer", requires=["dependency"])]
    result = resolve_assignment({"base_type": "ubuntu", "role": "red"},
        {"mode": "manual_only", "pinned_module_ids": ["consumer"], "resolved_module_ids": []},
        {}, library, refill=False)
    assert result["resolved_module_ids"] == ["dependency", "consumer"]


def test_conflicting_pins_are_preserved_and_reported():
    library = [mod("left", conflicts=["right"]), mod("right")]
    result = resolve_assignment({"base_type": "ubuntu", "role": "blue"},
        {"mode": "random_fill", "pinned_module_ids": ["left", "right"], "resolved_module_ids": []},
        {}, library, refill=True)
    assert result["pinned_module_ids"] == ["left", "right"]
    assert result["issues"][0]["code"] == "pinned_conflict"


def test_start_validation_rejects_stale_vms_and_resolved_modules():
    plan = {"version": 1, "assignments": {
        "vm:hq/blue/analyst": {
            "mode": "manual_only", "pinned_module_ids": ["missing"],
            "resolved_module_ids": ["missing"],
        },
        "vm:gone/blue/host": {
            "mode": "manual_only", "pinned_module_ids": [], "resolved_module_ids": [],
        },
    }}

    issues = validate_module_plan_for_start(plan, infrastructure(), [mod("available")])

    assert {issue["code"] for issue in issues} == {"unknown_vm", "unknown_module"}
