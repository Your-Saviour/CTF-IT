from builder.module_loader import Module
from builder.timeline import empty_timeline, normalize_timeline, validate_timeline


def _module(module_id, bases=("ubuntu_24_server",)):
    return Module(id=module_id, name=module_id, description="", type="vulnerability",
                  difficulty="easy", points=0, category="test", supported_bases=list(bases))


INFRA = {
    "sites": [{
        "key": "head_office", "name": "Head Office", "region": "ewr", "firewall_team": "blue",
        "firewall": {"base_type": "opnsense", "default_plan": "vc2-2c-4gb"},
        "zones": [
            {"key": "corporate", "name": "Corporate", "team": "blue",
             "endpoints": [{"key": "workstation_1", "name": "Workstation 1",
                            "base_type": "ubuntu_24_server", "default_plan": "vc2-1c-1gb"}]},
            {"key": "red_team", "name": "Red Team", "team": "red", "endpoints": []},
        ],
    }]
}


def test_empty_timeline_shape():
    assert empty_timeline() == {"version": 1, "phases": [], "injects": []}


def test_normalize_rejects_unknown_kind():
    import pytest
    with pytest.raises(ValueError):
        normalize_timeline({"version": 1, "phases": [], "injects": [
            {"id": "i1", "name": "x", "offset_minutes": 5, "kind": "boom", "payload": {}}
        ]})


def test_validate_apply_module_ok():
    timeline = {"version": 1, "phases": [], "injects": [
        {"id": "i1", "name": "Deploy", "offset_minutes": 10, "kind": "apply_module",
         "payload": {"module_id": "log4shell_app", "target": "vm:head_office/corporate/workstation_1"}}
    ]}
    issues = validate_timeline(timeline, INFRA, {"Op 1"}, {"log4shell_app": _module("log4shell_app")}, 60)
    assert issues == []


def test_validate_flags_unknown_target_and_module():
    timeline = {"version": 1, "phases": [], "injects": [
        {"id": "i1", "name": "Deploy", "offset_minutes": 10, "kind": "apply_module",
         "payload": {"module_id": "nope", "target": "vm:missing/zone/vm"}}
    ]}
    issues = validate_timeline(timeline, INFRA, {"Op 1"}, {}, 60)
    codes = {i["code"] for i in issues}
    assert {"unknown_module", "unknown_target"} <= codes


def test_validate_flags_unknown_operation_and_out_of_bounds():
    timeline = {"version": 1, "phases": [
        {"id": "p1", "name": "Recon", "start_offset_minutes": 0,
         "end_offset_minutes": 90, "color": "#ff0000"}
    ], "injects": [
        {"id": "i1", "name": "Kick", "offset_minutes": 70, "kind": "start_operation",
         "payload": {"operation": "Missing Op"}}
    ]}
    issues = validate_timeline(timeline, INFRA, {"Op 1"}, {}, 60)
    codes = {i["code"] for i in issues}
    assert {"unknown_operation", "offset_out_of_bounds", "phase_out_of_bounds"} <= codes


def test_validate_flags_phase_overlap_and_order():
    timeline = {"version": 1, "injects": [], "phases": [
        {"id": "p1", "name": "A", "start_offset_minutes": 0, "end_offset_minutes": 30, "color": "#ff0000"},
        {"id": "p2", "name": "B", "start_offset_minutes": 20, "end_offset_minutes": 50, "color": "#00ff00"},
        {"id": "p3", "name": "C", "start_offset_minutes": 40, "end_offset_minutes": 40, "color": "#0000ff"},
    ]}
    issues = validate_timeline(timeline, INFRA, set(), {}, 60)
    codes = {i["code"] for i in issues}
    assert {"phase_overlap", "phase_order"} <= codes
