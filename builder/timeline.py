"""Pure helpers for event timelines (phases + injects)."""

from __future__ import annotations

import copy
import json
import re

from builder.module_plan import assignable_endpoints

VERSION = 1
MAX_BYTES = 262_144
INJECT_KINDS = {"apply_module", "start_operation", "notify", "milestone"}
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def empty_timeline():
    return {"version": VERSION, "phases": [], "injects": []}


def _integer(value, field, minimum=0):
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(f"{field} must be an integer greater than or equal to {minimum}")
    return value


def normalize_timeline(value):
    if value is None:
        return empty_timeline()
    if not isinstance(value, dict) or value.get("version") != VERSION:
        raise ValueError("timeline.version must be 1")
    if len(json.dumps(value).encode()) > MAX_BYTES:
        raise ValueError(f"timeline exceeds {MAX_BYTES} bytes")
    phases = value.get("phases")
    injects = value.get("injects")
    if not isinstance(phases, list) or not isinstance(injects, list):
        raise ValueError("timeline phases and injects must be lists")

    result = {"version": VERSION, "phases": [], "injects": []}
    seen = set()
    for index, phase in enumerate(phases):
        if not isinstance(phase, dict):
            raise ValueError(f"phases[{index}] must be an object")
        if not isinstance(phase.get("id"), str) or not phase["id"]:
            raise ValueError(f"phases[{index}].id must be a non-empty string")
        if phase["id"] in seen:
            raise ValueError(f"duplicate phase id '{phase['id']}'")
        seen.add(phase["id"])
        color = phase.get("color")
        if not isinstance(color, str) or not _COLOR.fullmatch(color):
            raise ValueError(f"phases[{index}].color must be a six-digit hex colour")
        result["phases"].append({
            "id": phase["id"],
            "name": str(phase.get("name") or phase["id"]),
            "start_offset_minutes": _integer(phase.get("start_offset_minutes"), f"phases[{index}].start_offset_minutes"),
            "end_offset_minutes": _integer(phase.get("end_offset_minutes"), f"phases[{index}].end_offset_minutes"),
            "color": color,
            "description": str(phase.get("description") or ""),
        })

    seen = set()
    for index, inject in enumerate(injects):
        if not isinstance(inject, dict):
            raise ValueError(f"injects[{index}] must be an object")
        if not isinstance(inject.get("id"), str) or not inject["id"]:
            raise ValueError(f"injects[{index}].id must be a non-empty string")
        if inject["id"] in seen:
            raise ValueError(f"duplicate inject id '{inject['id']}'")
        seen.add(inject["id"])
        kind = inject.get("kind")
        if kind not in INJECT_KINDS:
            raise ValueError(f"injects[{index}].kind is invalid")
        payload = copy.deepcopy(inject.get("payload") or {})
        if not isinstance(payload, dict):
            raise ValueError(f"injects[{index}].payload must be an object")
        result["injects"].append({
            "id": inject["id"],
            "name": str(inject.get("name") or inject["id"]),
            "offset_minutes": _integer(inject.get("offset_minutes"), f"injects[{index}].offset_minutes"),
            "kind": kind,
            "payload": payload,
            "description": str(inject.get("description") or ""),
        })
    return result


def validate_timeline(timeline, infrastructure, operation_names, modules_by_id, event_minutes=None):
    try:
        timeline = normalize_timeline(timeline)
    except ValueError as exc:
        return [{"code": "invalid_structure", "message": str(exc)}]
    issues = []
    targets = {row["id"]: row for row in assignable_endpoints(infrastructure)}

    for phase in timeline["phases"]:
        start = phase["start_offset_minutes"]
        end = phase["end_offset_minutes"]
        if end <= start:
            issues.append({"code": "phase_order", "phase_id": phase["id"],
                           "message": f"{phase['name']} end must be after its start"})
        if event_minutes is not None and end > event_minutes:
            issues.append({"code": "phase_out_of_bounds", "phase_id": phase["id"],
                           "message": f"{phase['name']} exceeds the event duration"})

    sorted_phases = sorted(timeline["phases"], key=lambda p: p["start_offset_minutes"])
    for left, right in zip(sorted_phases, sorted_phases[1:]):
        if right["start_offset_minutes"] < left["end_offset_minutes"]:
            issues.append({"code": "phase_overlap", "phase_id": right["id"],
                           "message": f"{right['name']} overlaps {left['name']}"})

    for inject in timeline["injects"]:
        offset = inject["offset_minutes"]
        if event_minutes is not None and offset > event_minutes:
            issues.append({"code": "offset_out_of_bounds", "inject_id": inject["id"],
                           "message": f"{inject['name']} fires after the event ends"})
        payload = inject["payload"]
        if inject["kind"] == "apply_module":
            module_id = payload.get("module_id")
            module = modules_by_id.get(module_id)
            if module is None:
                issues.append({"code": "unknown_module", "inject_id": inject["id"],
                               "message": f"Inject references unknown module '{module_id}'"})
            target = payload.get("target")
            if target not in targets:
                issues.append({"code": "unknown_target", "inject_id": inject["id"],
                               "message": "Inject target is not a planned VM"})
            elif module is not None and module.supported_bases and targets[target]["base_type"] not in module.supported_bases:
                issues.append({"code": "incompatible_target", "inject_id": inject["id"],
                               "message": "Inject module is incompatible with the target base"})
        elif inject["kind"] == "start_operation":
            if payload.get("operation") not in operation_names:
                issues.append({"code": "unknown_operation", "inject_id": inject["id"],
                               "message": "Inject references an unknown operation"})
        elif inject["kind"] == "notify":
            if not isinstance(payload.get("message"), str) or not payload["message"].strip():
                issues.append({"code": "missing_message", "inject_id": inject["id"],
                               "message": "Notify inject requires a message"})
    return issues
