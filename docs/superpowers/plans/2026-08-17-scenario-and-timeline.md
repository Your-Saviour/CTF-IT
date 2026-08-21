# Scenario Layer & Timeline / Inject Authoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a reusable, versioned Scenario template (bundling quota, infrastructure, module plan, operations, timeline) that instantiates into a draft event, plus an event-level timeline with phases and injects.

**Architecture:** Two pure builder modules (`builder/timeline.py`, `builder/scenario.py`) following the existing `module_plan.py`/`operation_plan.py` pattern; a new `Scenario` model + `Event` provenance/timeline columns via an Alembic migration; a new `api/routes/scenarios.py` router; two frontend pages. Instantiation copies artifacts verbatim and validates against the current catalogue (deterministic reproduction).

**Tech Stack:** FastAPI + SQLAlchemy (SQLite/Postgres), Alembic, Jinja2 templates + vanilla JS frontend, pytest.

**Spec:** `docs/superpowers/specs/2026-08-17-scenario-and-timeline-design.md`

## Global Constraints

- Follow the existing pure-module builder pattern: normalize/validate functions are pure, raise `ValueError` on malformed input, return `list[dict]` issue objects with a `code` and `message`.
- Timeline/phase/inject offsets are minutes relative to event start, non-negative integers.
- `phases`/`narrative` module fields are advisory only — never enforced in selection, scoring, or validation.
- Inject kinds are exactly: `apply_module`, `start_operation`, `notify`, `milestone`.
- Scenario deletion returns `409` when any event references it.
- All new API routes are admin-only via `require_admin`.
- Tests run in the disposable Docker test service (`docker compose --profile test run --rm tests`), but individual pytest files are runnable directly with `pytest tests/test_*.py`.

---

### Task 1: Timeline builder module

**Files:**
- Create: `builder/timeline.py`
- Test: `tests/test_timeline.py`

**Interfaces:**
- Consumes: `builder.module_plan.assignable_endpoints` (already exists), `builder.module_loader.Module` (already exists).
- Produces:
  - `empty_timeline() -> dict`
  - `normalize_timeline(value) -> dict` (raises `ValueError`)
  - `validate_timeline(timeline, infrastructure, operation_names, modules_by_id, event_minutes=None) -> list[dict]`

- [ ] **Step 1: Write the failing test**

`tests/test_timeline.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_timeline.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'builder.timeline'`.

- [ ] **Step 3: Write the implementation**

`builder/timeline.py`:
```python
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


def _id_list(value, field, existing):
    ids = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
            raise ValueError(f"{field}[{index}].id must be a non-empty string")
        if item["id"] in ids:
            raise ValueError(f"duplicate id '{item['id']}'")
        ids.add(item["id"])
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
                               "message": f"Inject target is not a planned VM"})
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_timeline.py -v`
Expected: PASS (all 6 tests).

- [ ] **Step 5: Commit**

```bash
git add builder/timeline.py tests/test_timeline.py
git commit -m "feat: add timeline builder (phases + injects) with validation"
```

---

### Task 2: Scenario model + migration

**Files:**
- Modify: `api/models.py` (add `Scenario`, add `Event.timeline`/`scenario_id`/`scenario_version`/`scenario_fingerprint`)
- Create: `migrations/versions/0014_scenario_and_timeline.py`
- Test: `tests/test_scenario_model.py`

**Interfaces:**
- Produces: `Scenario` ORM class (`id, name, description, version, quota, infrastructure, infrastructure_layout, module_plan, operations_json, timeline, content_fingerprint, created_at, updated_at`); `Event.timeline`, `Event.scenario_id`, `Event.scenario_version`, `Event.scenario_fingerprint`.

- [ ] **Step 1: Write the failing test**

`tests/test_scenario_model.py`:
```python
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.database import Base
from api.models import Event, Scenario


@pytest.fixture
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    yield sessions
    sessions().close()


def test_scenario_version_and_event_provenance(db_session):
    db = db_session()
    scenario = Scenario(name="Locked Shields", version=1, quota="{}",
                        infrastructure="{}", module_plan=None, operations_json="[]", timeline=None)
    db.add(scenario); db.commit(); db.refresh(scenario)

    event = Event(name="Exercise", quota="{}", status="draft",
                  scenario_id=scenario.id, scenario_version=scenario.version)
    db.add(event); db.commit(); db.refresh(event)

    assert event.scenario_id == scenario.id
    assert event.scenario_version == 1
    assert event.timeline is None


def test_scenario_name_is_unique(db_session):
    import sqlalchemy.exc
    db = db_session()
    db.add(Scenario(name="Unique", version=1, quota="{}", infrastructure="{}"))
    db.commit()
    db.add(Scenario(name="Unique", version=1, quota="{}", infrastructure="{}"))
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        db.commit()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_scenario_model.py -v`
Expected: FAIL with `ImportError: cannot import name 'Scenario'` (or `AttributeError` on `event.timeline`).

- [ ] **Step 3: Write the model changes**

In `api/models.py`, add the `Scenario` class after `EventOperation` and add four columns to `Event`:

```python
class Scenario(Base):
    __tablename__ = "scenarios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    quota: Mapped[str] = mapped_column(Text, nullable=False)
    infrastructure: Mapped[str] = mapped_column(Text, nullable=True)
    infrastructure_layout: Mapped[str] = mapped_column(Text, nullable=True)
    module_plan: Mapped[str] = mapped_column(Text, nullable=True)
    operations_json: Mapped[str] = mapped_column(Text, nullable=True)
    timeline: Mapped[str] = mapped_column(Text, nullable=True)
    content_fingerprint: Mapped[str] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, server_default=func.current_timestamp(), nullable=False
    )
```

Add to `Event` (alongside `operation_plan`):
```python
    timeline: Mapped[str] = mapped_column(Text, nullable=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey("scenarios.id"), nullable=True)
    scenario_version: Mapped[int] = mapped_column(Integer, nullable=True)
    scenario_fingerprint: Mapped[str] = mapped_column(String(64), nullable=True)
```

- [ ] **Step 4: Write the migration**

`migrations/versions/0014_scenario_and_timeline.py`:
```python
"""Add scenarios table and event timeline/provenance columns.

Revision ID: 0014_scenario_and_timeline
Revises: 0013_multiple_event_operations
"""

from alembic import op
import sqlalchemy as sa


revision = "0014_scenario_and_timeline"
down_revision = "0013_multiple_event_operations"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "scenarios" not in inspector.get_table_names():
        op.create_table(
            "scenarios",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("quota", sa.Text(), nullable=False),
            sa.Column("infrastructure", sa.Text(), nullable=True),
            sa.Column("infrastructure_layout", sa.Text(), nullable=True),
            sa.Column("module_plan", sa.Text(), nullable=True),
            sa.Column("operations_json", sa.Text(), nullable=True),
            sa.Column("timeline", sa.Text(), nullable=True),
            sa.Column("content_fingerprint", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("name", name="uq_scenarios_name"),
        )

    event_columns = {column["name"] for column in inspector.get_columns("events")}
    if "timeline" not in event_columns:
        op.add_column("events", sa.Column("timeline", sa.Text(), nullable=True))
    if "scenario_id" not in event_columns:
        op.add_column("events", sa.Column("scenario_id", sa.Integer(),
                                          sa.ForeignKey("scenarios.id"), nullable=True))
    if "scenario_version" not in event_columns:
        op.add_column("events", sa.Column("scenario_version", sa.Integer(), nullable=True))
    if "scenario_fingerprint" not in event_columns:
        op.add_column("events", sa.Column("scenario_fingerprint", sa.String(length=64), nullable=True))


def downgrade():
    inspector = sa.inspect(op.get_bind())
    event_columns = {column["name"] for column in inspector.get_columns("events")}
    for column in ("timeline", "scenario_id", "scenario_version", "scenario_fingerprint"):
        if column in event_columns:
            op.drop_column("events", column)
    if "scenarios" in inspector.get_table_names():
        op.drop_table("scenarios")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_scenario_model.py -v`
Expected: PASS. Note the test uses `Base.metadata.create_all` directly, so the migration is validated separately via the Docker test service (which runs `alembic upgrade head`).

- [ ] **Step 6: Commit**

```bash
git add api/models.py migrations/versions/0014_scenario_and_timeline.py tests/test_scenario_model.py
git commit -m "feat: add Scenario model and event timeline/provenance columns"
```

---

### Task 3: Scenario builder module

**Files:**
- Create: `builder/scenario.py`
- Test: `tests/test_scenario.py`

**Interfaces:**
- Consumes: `builder.module_plan` (`empty_module_plan`, `normalize_module_plan`, `assignable_endpoints`), `builder.timeline` (`empty_timeline`, `normalize_timeline`, `validate_timeline`), `builder.operation_plan` (`normalize_operation_plan`), `builder.module_loader.Module`.
- Produces:
  - `scenario_fingerprint(quota, infrastructure, infrastructure_layout, module_plan, operations, timeline) -> str`
  - `capture_scenario_from_event(event) -> dict` (keys: `quota`, `infrastructure`, `infrastructure_layout`, `module_plan`, `operations`, `timeline`)
  - `validate_scenario_catalogue(module_plan, infrastructure, modules_by_id) -> list[dict]`
  - `instantiate_scenario(db, scenario, name=None) -> tuple[int, list[dict]]` (returns `(new_event_id, report)`)
  - `plan_health(event, modules_by_id) -> dict`

- [ ] **Step 1: Write the failing test**

`tests/test_scenario.py`:
```python
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.database import Base
from api.models import Event, EventOperation, Scenario
from builder.module_loader import Module
from builder.operation_plan import empty_operation_plan
from builder.scenario import (
    capture_scenario_from_event,
    instantiate_scenario,
    scenario_fingerprint,
    validate_scenario_catalogue,
)


@pytest.fixture
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    yield sessions
    sessions().close()


def _module(module_id, bases=("ubuntu_24_server",)):
    return Module(id=module_id, name=module_id, description="", type="vulnerability",
                  difficulty="easy", points=0, category="test", supported_bases=list(bases))


INFRA = {
    "vpn_gateway": {"base_type": "ubuntu_24_server", "default_plan": "vc2-1c-1gb",
                    "region": "ewr", "listen_port": 51820},
    "sites": [{
        "key": "head_office", "name": "Head Office", "region": "ewr", "firewall_team": "blue",
        "firewall": {"base_type": "opnsense", "default_plan": "vc2-2c-4gb"},
        "zones": [
            {"key": "corporate", "name": "Corporate", "team": "blue",
             "endpoints": [{"key": "workstation_1", "name": "Workstation 1",
                            "base_type": "ubuntu_24_server", "default_plan": "vc2-1c-1gb"}]},
        ],
    }],
}

MODULE_PLAN = {"version": 1, "assignments": {
    "vm:head_office/corporate/workstation_1": {
        "mode": "manual_only",
        "pinned_module_ids": ["weak_ssh_credentials"],
        "resolved_module_ids": ["weak_ssh_credentials"],
    }
}}


def _scenario(db, module_plan=None, operations=None):
    scenario = Scenario(
        name="Base", version=1, quota="{}",
        infrastructure=json.dumps(INFRA),
        module_plan=json.dumps(module_plan if module_plan is not None else MODULE_PLAN),
        operations_json=json.dumps(operations if operations is not None else []),
        timeline=json.dumps({"version": 1, "phases": [], "injects": []}),
    )
    db.add(scenario); db.commit(); db.refresh(scenario)
    return scenario


def test_fingerprint_changes_with_content():
    a = scenario_fingerprint("{}", INFRA, None, MODULE_PLAN, [], {"version": 1, "phases": [], "injects": []})
    b = scenario_fingerprint("{}", INFRA, None, MODULE_PLAN, [{"name": "Op"}], {"version": 1, "phases": [], "injects": []})
    assert a != b
    assert a.startswith("sha256:")


def test_instantiate_creates_event_and_operations(db_session):
    db = db_session()
    ops = [{"name": "Recon", "description": None, "position": 0,
            "operation_plan": empty_operation_plan()}]
    scenario = _scenario(db, operations=ops)
    event_id, report = instantiate_scenario(db, scenario, name="Instantiated")
    event = db.get(Event, event_id)
    assert event.name == "Instantiated"
    assert event.status == "draft"
    assert event.scenario_id == scenario.id
    assert event.scenario_version == scenario.version
    rows = db.query(EventOperation).filter(EventOperation.event_id == event_id).all()
    assert [r.name for r in rows] == ["Recon"]
    assert json.loads(event.module_plan) == MODULE_PLAN
    assert report == []


def test_instantiate_reports_unknown_module(db_session):
    db = db_session()
    plan = {"version": 1, "assignments": {
        "vm:head_office/corporate/workstation_1": {
            "mode": "manual_only", "pinned_module_ids": ["gone"],
            "resolved_module_ids": ["gone"],
        }
    }}
    scenario = _scenario(db, module_plan=plan)
    _, report = instantiate_scenario(db, scenario)
    assert any(i["code"] == "unknown_module" for i in report)


def test_validate_scenario_catalogue_flags_incompatible_base():
    issues = validate_scenario_catalogue(MODULE_PLAN, INFRA, {
        "weak_ssh_credentials": _module("weak_ssh_credentials", bases=("windows",))
    })
    assert any(i["code"] == "incompatible_base" for i in issues)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_scenario.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'builder.scenario'`.

- [ ] **Step 3: Write the implementation**

`builder/scenario.py`:
```python
"""Scenario capture, fingerprinting, instantiation, and plan health."""

from __future__ import annotations

import hashlib
import json

from builder.infrastructure_planner import default_infrastructure, normalize_infrastructure
from builder.module_plan import assignable_endpoints, empty_module_plan, normalize_module_plan
from builder.operation_plan import empty_operation_plan, normalize_operation_plan
from builder.timeline import empty_timeline, normalize_timeline, validate_timeline


def scenario_fingerprint(quota, infrastructure, infrastructure_layout, module_plan, operations, timeline):
    raw = json.dumps(
        {"quota": quota, "infrastructure": infrastructure, "infrastructure_layout": infrastructure_layout,
         "module_plan": module_plan, "operations": operations, "timeline": timeline},
        sort_keys=True, separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()


def capture_scenario_from_event(event) -> dict:
    infrastructure = normalize_infrastructure(
        json.loads(event.infrastructure) if event.infrastructure else default_infrastructure()
    )
    module_plan = normalize_module_plan(
        json.loads(event.module_plan) if event.module_plan else empty_module_plan()
    )
    operations = [
        {"name": op.name, "description": op.description, "position": op.position,
         "operation_plan": normalize_operation_plan(json.loads(op.operation_plan))}
        for op in sorted(event.operations, key=lambda o: (o.position, o.id))
    ]
    timeline = normalize_timeline(json.loads(event.timeline) if event.timeline else empty_timeline())
    return {
        "quota": json.loads(event.quota) if event.quota else {},
        "infrastructure": infrastructure,
        "infrastructure_layout": json.loads(event.infrastructure_layout) if event.infrastructure_layout else None,
        "module_plan": module_plan,
        "operations": operations,
        "timeline": timeline,
    }


def validate_scenario_catalogue(module_plan, infrastructure, modules_by_id):
    issues = []
    targets = {row["id"]: row for row in assignable_endpoints(infrastructure)}
    for vm_id, assignment in module_plan["assignments"].items():
        target = targets.get(vm_id)
        for module_id in [*assignment.get("pinned_module_ids", []),
                          *assignment.get("resolved_module_ids", [])]:
            module = modules_by_id.get(module_id)
            if module is None:
                issues.append({"code": "unknown_module", "vm_id": vm_id, "module_id": module_id,
                               "message": f"Module '{module_id}' is unavailable"})
                continue
            if module.disabled:
                issues.append({"code": "disabled_module", "vm_id": vm_id, "module_id": module_id,
                               "message": f"Module '{module_id}' is disabled"})
            if target and module.supported_bases and target["base_type"] not in module.supported_bases:
                issues.append({"code": "incompatible_base", "vm_id": vm_id, "module_id": module_id,
                               "message": f"Module '{module_id}' is incompatible with the target base"})
    return issues


def instantiate_scenario(db, scenario, name=None):
    quota = json.loads(scenario.quota) if scenario.quota else {}
    infrastructure = json.loads(scenario.infrastructure) if scenario.infrastructure else default_infrastructure()
    module_plan = json.loads(scenario.module_plan) if scenario.module_plan else empty_module_plan()
    operations = json.loads(scenario.operations_json) if scenario.operations_json else []
    timeline = json.loads(scenario.timeline) if scenario.timeline else empty_timeline()

    from api.models import Event, EventOperation
    from builder.module_loader import load_all_modules

    event = Event(
        name=(name or scenario.name),
        description=scenario.description,
        quota=json.dumps(quota),
        infrastructure=json.dumps(infrastructure),
        infrastructure_layout=json.dumps(json.loads(scenario.infrastructure_layout))
            if scenario.infrastructure_layout else None,
        module_plan=json.dumps(module_plan),
        timeline=json.dumps(timeline),
        scenario_id=scenario.id,
        scenario_version=scenario.version,
        scenario_fingerprint=scenario.content_fingerprint,
    )
    db.add(event); db.flush()
    for position, op in enumerate(sorted(operations, key=lambda o: o.get("position", 0))):
        db.add(EventOperation(
            event_id=event.id,
            name=op["name"],
            description=op.get("description"),
            position=position,
            operation_plan=json.dumps(normalize_operation_plan(op.get("operation_plan") or empty_operation_plan())),
        ))
    db.commit(); db.refresh(event)

    modules_by_id = {m.id: m for m in load_all_modules()}
    report = validate_scenario_catalogue(module_plan, infrastructure, modules_by_id)
    return event.id, report


def plan_health(event, modules_by_id):
    from builder.operation_plan import validate_operation_plan

    infrastructure = normalize_infrastructure(
        json.loads(event.infrastructure) if event.infrastructure else default_infrastructure()
    )
    module_plan = normalize_module_plan(
        json.loads(event.module_plan) if event.module_plan else empty_module_plan()
    )
    operation_names = {op.name for op in event.operations}
    timeline = json.loads(event.timeline) if event.timeline else empty_timeline()
    return {
        "module_issues": validate_scenario_catalogue(module_plan, infrastructure, modules_by_id),
        "timeline_issues": validate_timeline(timeline, infrastructure, operation_names,
                                             modules_by_id, event.time_limit_minutes),
        "operation_issues": [
            {"operation_id": op.id, "name": op.name,
             "issues": validate_operation_plan(json.loads(op.operation_plan), infrastructure,
                                               module_plan, list(modules_by_id.values()),
                                               event.time_limit_minutes)}
            for op in sorted(event.operations, key=lambda o: o.position)
        ],
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_scenario.py -v`
Expected: PASS (all 4 tests). If `instantiate_scenario` hits a circular-import because `api.models` imports `builder` at module load, move the `from api.models import ...` inside the function (it already is) — confirm there is no top-level `api.models` import in `builder/scenario.py`.

- [ ] **Step 5: Commit**

```bash
git add builder/scenario.py tests/test_scenario.py
git commit -m "feat: add scenario capture/instantiate and plan health builder"
```

---

### Task 4: Scenarios API + router wiring

**Files:**
- Create: `api/routes/scenarios.py`
- Modify: `api/main.py` (import + `app.include_router`)
- Test: `tests/test_scenarios_api.py`

**Interfaces:**
- Consumes: `builder.scenario` (`capture_scenario_from_event`, `scenario_fingerprint`, `instantiate_scenario`, `plan_health`), `api.routes.admin.require_admin`, `api.models` (`Scenario`, `Event`, `EventOperation`).
- Produces (routes):
  - `GET /admin/api/scenarios` — list `{scenarios: [{id, name, description, version, created_at}]}`
  - `POST /admin/api/scenarios` — create empty scenario `{name, description?}` → 201
  - `POST /admin/api/scenarios/from-event` — `{event_id, name}` → captures event → 201
  - `GET /admin/api/scenarios/{id}` — detail (content + version + provenance)
  - `POST /admin/api/scenarios/{id}/instantiate` — `{name?}` → `{event_id, report}`
  - `DELETE /admin/api/scenarios/{id}` — 409 if referenced by any event

- [ ] **Step 1: Write the failing test**

`tests/test_scenarios_api.py`:
```python
import json
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.database import Base, get_db
from api.models import Event, EventOperation, Scenario, User
from api.routes.scenarios import router
from builder.operation_plan import empty_operation_plan


@pytest.fixture
def api_client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    db = sessions()
    event = Event(name="Source", quota="{}", status="draft")
    db.add(event); db.commit(); db.refresh(event)
    event_id = event.id

    app = FastAPI()
    app.include_router(router)

    def override_db():
        session = sessions()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    with patch("api.routes.scenarios.require_admin", return_value=User(is_admin=True)):
        with TestClient(app) as client:
            yield client, sessions, event_id


def _capture(client, event_id):
    return client.post("/admin/api/scenarios/from-event", json={"event_id": event_id, "name": "Locked"})


def test_save_from_event_and_instantiate(api_client):
    client, sessions, event_id = api_client
    created = _capture(client, event_id)
    assert created.status_code == 201
    scenario_id = created.json()["id"]

    inst = client.post(f"/admin/api/scenarios/{scenario_id}/instantiate", json={"name": "Clone"})
    assert inst.status_code == 200
    body = inst.json()
    assert body["report"] == []
    new_event_id = body["event_id"]

    db = sessions()
    clone = db.get(Event, new_event_id)
    assert clone.name == "Clone"
    assert clone.scenario_id == scenario_id
    assert clone.scenario_version == 1


def test_resave_bumps_version(api_client):
    client, sessions, event_id = api_client
    first = _capture(client, event_id).json()
    second = _capture(client, event_id).json()
    assert first["id"] == second["id"]
    assert first["version"] == 1
    assert second["version"] == 2


def test_instantiate_copies_operations(api_client):
    client, sessions, event_id = api_client
    db = sessions()
    op = EventOperation(event_id=event_id, name="Recon", position=0,
                        operation_plan=json.dumps(empty_operation_plan()))
    db.add(op); db.commit()

    created = _capture(client, event_id)
    inst = client.post(f"/admin/api/scenarios/{created.json()['id']}/instantiate")
    rows = db.query(EventOperation).filter(EventOperation.event_id == inst.json()["event_id"]).all()
    assert [r.name for r in rows] == ["Recon"]


def test_delete_referenced_scenario_is_blocked(api_client):
    client, sessions, event_id = api_client
    created = _capture(client, event_id)
    scenario_id = created.json()["id"]
    client.post(f"/admin/api/scenarios/{scenario_id}/instantiate")
    assert client.delete(f"/admin/api/scenarios/{scenario_id}").status_code == 409


def test_delete_unreferenced_scenario_succeeds(api_client):
    client, sessions, event_id = api_client
    created = _capture(client, event_id)
    assert client.delete(f"/admin/api/scenarios/{created.json()['id']}").status_code == 204
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_scenarios_api.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'api.routes.scenarios'`.

- [ ] **Step 3: Write the router**

`api/routes/scenarios.py`:
```python
"""Scenario CRUD and instantiation endpoints."""

import json

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from api.database import get_db
from api.models import Event, Scenario
from api.routes.admin import require_admin
from builder.scenario import capture_scenario_from_event, instantiate_scenario, scenario_fingerprint

router = APIRouter(prefix="/admin/api", tags=["admin"])


def _scenario_summary(scenario):
    return {"id": scenario.id, "name": scenario.name, "description": scenario.description,
            "version": scenario.version, "created_at": scenario.created_at.isoformat()}


def _validate_name(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("scenario name is required")
    name = value.strip()
    if len(name) > 128:
        raise ValueError("scenario name must be 128 characters or fewer")
    return name


@router.get("/scenarios")
async def list_scenarios(request: Request, db: Session = Depends(get_db)):
    if not require_admin(request, db):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    rows = db.query(Scenario).order_by(Scenario.name).all()
    return {"scenarios": [_scenario_summary(s) for s in rows]}


@router.post("/scenarios", status_code=201)
async def create_scenario(request: Request, db: Session = Depends(get_db)):
    if not require_admin(request, db):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    body = await request.json()
    try:
        name = _validate_name(body.get("name"))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    if db.query(Scenario).filter(Scenario.name == name).first():
        return JSONResponse({"error": "scenario name already exists"}, status_code=409)
    scenario = Scenario(name=name, description=(str(body.get("description") or "").strip() or None),
                        version=1, quota="{}")
    db.add(scenario); db.commit(); db.refresh(scenario)
    return _scenario_summary(scenario)


@router.post("/scenarios/from-event", status_code=201)
async def save_scenario_from_event(request: Request, db: Session = Depends(get_db)):
    if not require_admin(request, db):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    body = await request.json()
    try:
        name = _validate_name(body.get("name"))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    event = db.query(Event).filter(Event.id == body.get("event_id")).first()
    if not event:
        return JSONResponse({"error": "Event not found"}, status_code=404)

    captured = capture_scenario_from_event(event)
    existing = db.query(Scenario).filter(Scenario.name == name).first()
    if existing:
        scenario = existing
        scenario.version = existing.version + 1
    else:
        scenario = Scenario(name=name, version=1, quota="{}")
        db.add(scenario)
    scenario.description = str(body.get("description") or "").strip() or None
    scenario.quota = json.dumps(captured["quota"])
    scenario.infrastructure = json.dumps(captured["infrastructure"])
    scenario.infrastructure_layout = json.dumps(captured["infrastructure_layout"]) \
        if captured["infrastructure_layout"] is not None else None
    scenario.module_plan = json.dumps(captured["module_plan"])
    scenario.operations_json = json.dumps(captured["operations"])
    scenario.timeline = json.dumps(captured["timeline"])
    scenario.content_fingerprint = scenario_fingerprint(
        captured["quota"], captured["infrastructure"], captured["infrastructure_layout"],
        captured["module_plan"], captured["operations"], captured["timeline"])
    db.commit(); db.refresh(scenario)
    return _scenario_summary(scenario)


@router.get("/scenarios/{scenario_id}")
async def get_scenario(scenario_id: int, request: Request, db: Session = Depends(get_db)):
    if not require_admin(request, db):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    scenario = db.get(Scenario, scenario_id)
    if not scenario:
        return JSONResponse({"error": "Scenario not found"}, status_code=404)
    return {
        **_scenario_summary(scenario),
        "quota": json.loads(scenario.quota) if scenario.quota else {},
        "infrastructure": json.loads(scenario.infrastructure) if scenario.infrastructure else None,
        "module_plan": json.loads(scenario.module_plan) if scenario.module_plan else None,
        "operations": json.loads(scenario.operations_json) if scenario.operations_json else [],
        "timeline": json.loads(scenario.timeline) if scenario.timeline else None,
        "content_fingerprint": scenario.content_fingerprint,
    }


@router.post("/scenarios/{scenario_id}/instantiate")
async def instantiate(scenario_id: int, request: Request, db: Session = Depends(get_db)):
    if not require_admin(request, db):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    scenario = db.get(Scenario, scenario_id)
    if not scenario:
        return JSONResponse({"error": "Scenario not found"}, status_code=404)
    body = await request.json()
    event_id, report = instantiate_scenario(db, scenario, name=body.get("name"))
    return {"event_id": event_id, "report": report}


@router.delete("/scenarios/{scenario_id}", status_code=204)
async def delete_scenario(scenario_id: int, request: Request, db: Session = Depends(get_db)):
    if not require_admin(request, db):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    scenario = db.get(Scenario, scenario_id)
    if not scenario:
        return JSONResponse({"error": "Scenario not found"}, status_code=404)
    if db.query(Event).filter(Event.scenario_id == scenario_id).first():
        return JSONResponse({"error": "scenario is referenced by an event"}, status_code=409)
    db.delete(scenario); db.commit()
    return Response(status_code=204)
```

- [ ] **Step 4: Wire the router in `api/main.py`**

In `api/main.py`, update the import at line 17 to include `scenarios` and add an include line after `app.include_router(admin.router)`:

```python
from api.routes import admin, ai_agent, ansible_export, auth, caldera_export, caldera_ops, caldera_setup, caldera_tree, event_dashboard, learner, scenarios, service_credentials, vm, vm_goals
```

```python
app.include_router(scenarios.router)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_scenarios_api.py -v`
Expected: PASS (all 4 tests).

- [ ] **Step 6: Commit**

```bash
git add api/routes/scenarios.py api/main.py tests/test_scenarios_api.py
git commit -m "feat: add scenarios CRUD + instantiate API"
```

---

### Task 5: Timeline + plan-health API endpoints

**Files:**
- Modify: `api/routes/scenarios.py` (add timeline + plan-health routes)
- Test: `tests/test_timeline_api.py`

**Interfaces:**
- Consumes: `builder.timeline` (`normalize_timeline`, `validate_timeline`), `builder.scenario.plan_health`, `api.routes.admin._utc_instant` (existing).
- Produces:
  - `GET /admin/api/events/{event_id}/timeline` — `{timeline, updated_at, read_only}`
  - `PUT /admin/api/events/{event_id}/timeline` — body `{timeline, expected_updated_at}` → optimistic save
  - `GET /admin/api/events/{event_id}/plan-health` — `plan_health(event, modules)`

- [ ] **Step 1: Write the failing test**

`tests/test_timeline_api.py`:
```python
import json
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.database import Base, get_db
from api.models import Event, User
from api.routes.scenarios import router


@pytest.fixture
def api_client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    db = sessions()
    event = Event(name="Timeline", quota="{}", status="draft", time_limit_minutes=120)
    db.add(event); db.commit(); db.refresh(event)
    event_id = event.id

    app = FastAPI()
    app.include_router(router)

    def override_db():
        session = sessions()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    with patch("api.routes.scenarios.require_admin", return_value=User(is_admin=True)):
        with TestClient(app) as client:
            yield client, sessions, event_id


TIMELINE = {"version": 1, "phases": [], "injects": [
    {"id": "i1", "name": "Beat", "offset_minutes": 30, "kind": "milestone", "payload": {}}
]}


def test_save_and_reload_timeline(api_client):
    client, _, event_id = api_client
    loaded = client.get(f"/admin/api/events/{event_id}/timeline").json()
    saved = client.put(f"/admin/api/events/{event_id}/timeline",
                       json={"timeline": TIMELINE, "expected_updated_at": loaded["updated_at"]})
    assert saved.status_code == 200
    assert client.get(f"/admin/api/events/{event_id}/timeline").json()["timeline"]["injects"][0]["name"] == "Beat"


def test_stale_timeline_save_is_rejected(api_client):
    client, _, event_id = api_client
    loaded = client.get(f"/admin/api/events/{event_id}/timeline").json()
    assert client.put(f"/admin/api/events/{event_id}/timeline",
                      json={"timeline": TIMELINE, "expected_updated_at": loaded["updated_at"]}).status_code == 200
    assert client.put(f"/admin/api/events/{event_id}/timeline",
                      json={"timeline": TIMELINE, "expected_updated_at": loaded["updated_at"]}).status_code == 409


def test_plan_health_returns_keys(api_client):
    client, _, event_id = api_client
    response = client.get(f"/admin/api/events/{event_id}/plan-health")
    assert response.status_code == 200
    body = response.json()
    assert {"module_issues", "timeline_issues", "operation_issues"} <= set(body.keys())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_timeline_api.py -v`
Expected: FAIL (routes return 404).

- [ ] **Step 3: Add the routes**

Append to `api/routes/scenarios.py` (the router already uses prefix `/admin/api`, so event-scoped routes fit alongside the `/scenarios` routes):

```python
@router.get("/events/{event_id}/timeline")
async def get_timeline(event_id: int, request: Request, db: Session = Depends(get_db)):
    if not require_admin(request, db):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        return JSONResponse({"error": "Event not found"}, status_code=404)
    from builder.timeline import empty_timeline, normalize_timeline
    timeline = normalize_timeline(json.loads(event.timeline) if event.timeline else empty_timeline())
    return {"timeline": timeline, "updated_at": event.updated_at.isoformat(),
            "read_only": event.status != "draft"}


@router.put("/events/{event_id}/timeline")
async def save_timeline(event_id: int, request: Request, db: Session = Depends(get_db)):
    if not require_admin(request, db):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        return JSONResponse({"error": "Event not found"}, status_code=404)
    if event.status != "draft":
        return JSONResponse({"error": "timeline is read only"}, status_code=409)
    body = await request.json()
    from api.routes.admin import _utc_instant
    from api.models import utcnow
    from builder.timeline import normalize_timeline
    try:
        if _utc_instant(body.get("expected_updated_at")) != _utc_instant(event.updated_at):
            return JSONResponse({"error": "event draft has changed",
                                 "current_updated_at": event.updated_at.isoformat()}, status_code=409)
        timeline = normalize_timeline(body.get("timeline"))
    except (TypeError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    event.timeline = json.dumps(timeline); event.updated_at = utcnow(); db.commit(); db.refresh(event)
    return {"status": "saved", "updated_at": event.updated_at.isoformat()}


@router.get("/events/{event_id}/plan-health")
async def plan_health(event_id: int, request: Request, db: Session = Depends(get_db)):
    if not require_admin(request, db):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        return JSONResponse({"error": "Event not found"}, status_code=404)
    from builder.module_loader import load_all_modules
    from builder.scenario import plan_health as _plan_health
    modules_by_id = {m.id: m for m in load_all_modules()}
    return _plan_health(event, modules_by_id)
```

Update `tests/test_scenarios_api.py` import path references if they hard-code the old `/admin/api/scenarios` prefix — the routes stay functionally identical, only the router prefix internals change, so the existing tests (which call `/admin/api/scenarios/...`) still pass as long as you prefix each route with `/scenarios`.

- [ ] **Step 4: Run both test files**

Run: `pytest tests/test_timeline_api.py tests/test_scenarios_api.py -v`
Expected: PASS (all tests in both files).

- [ ] **Step 5: Commit**

```bash
git add api/routes/scenarios.py tests/test_timeline_api.py
git commit -m "feat: add timeline save/load and plan-health endpoints"
```

---

### Task 6: Module schema additions (`phases`, `narrative`)

**Files:**
- Modify: `builder/module_loader.py` (add fields to `Module` dataclass + loader)
- Modify: `api/routes/admin.py` (expose in the module-plan listing payload)
- Test: `tests/test_module_loader.py` (append cases)

**Interfaces:**
- Produces: `Module.phases: list[str]`, `Module.narrative: str` (both default empty/`[]`); advisory only.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_module_loader.py`:
```python
def test_module_phases_and_narrative_load_from_yaml(tmp_path, monkeypatch):
    import builder.module_loader as ml
    (tmp_path / "m.yaml").write_text(
        "id: sample\nname: Sample\ndescription: d\ntype: vulnerability\n"
        "difficulty: easy\npoints: 10\ncategory: web\n"
        "phases: [recon, impact]\nnarrative: An attacker pivots through the web tier.\n"
    )
    monkeypatch.setattr(ml, "MODULES_DIR", tmp_path)
    modules = ml.load_all_modules()
    sample = next(m for m in modules if m.id == "sample")
    assert sample.phases == ["recon", "impact"]
    assert sample.narrative == "An attacker pivots through the web tier."


def test_module_phases_defaults_to_empty():
    from builder.module_loader import Module
    module = Module(id="x", name="X", description="", type="vulnerability",
                    difficulty="easy", points=0, category="test")
    assert module.phases == []
    assert module.narrative == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_module_loader.py::test_module_phases_and_narrative_load_from_yaml -v`
Expected: FAIL with `AttributeError: 'Module' object has no attribute 'phases'`.

- [ ] **Step 3: Write the implementation**

In `builder/module_loader.py`, add two fields to the `Module` dataclass (near `prerequisites`/`references`):
```python
    phases: list[str] = field(default_factory=list)
    narrative: str = ""
```
And in `load_all_modules()`'s `Module(...)` constructor call, add:
```python
            phases=data.get("phases", []),
            narrative=data.get("narrative", ""),
```

- [ ] **Step 4: Expose in admin module-plan listing**

In `api/routes/admin.py` `get_module_plan` (the `modules` list comprehension around line 730), add `"phases": m.phases, "narrative": m.narrative` to the dict.

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_module_loader.py -v`
Expected: PASS (existing + 2 new).

- [ ] **Step 6: Commit**

```bash
git add builder/module_loader.py api/routes/admin.py tests/test_module_loader.py
git commit -m "feat: add advisory phases/narrative module metadata"
```

---

### Task 7: Frontend (scenario library + timeline editor)

**Files:**
- Create: `frontend/templates/scenarios.html`, `frontend/static/scenarios.js`
- Create: `frontend/templates/event_timeline.html`, `frontend/static/event-timeline.js`
- Modify: `api/main.py` (page routes), and the admin navigation template(s) to link both pages.

**Interfaces:**
- Consumes: the API routes from Tasks 4–5; `GET /admin/api/events/{event_id}/operations` for operation bars; `GET /admin/api/events/{event_id}/module-plan` for module names on injects.

- [ ] **Step 1: Add page routes in `api/main.py`**

Mirror the existing `event_operation_page` pattern (around `api/main.py:644`). Add:

```python
@app.get("/admin/scenarios", response_class=HTMLResponse)
async def scenarios_page(request: Request, db: Session = Depends(get_db)):
    user = await get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "scenarios.html", {"user": user, "active_nav": "scenarios"})


@app.get("/admin/events/{event_id}/timeline", response_class=HTMLResponse)
async def event_timeline_page(event_id: int, request: Request, db: Session = Depends(get_db)):
    user = await get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse("/login", status_code=303)
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        return RedirectResponse("/admin/events", status_code=303)
    return templates.TemplateResponse(request, "event_timeline.html",
                                      {"user": user, "event_id": event.id,
                                       "event_name": event.name, "active_nav": "events"})
```

Confirm `templates`, `get_current_user`, and `HTMLResponse`/`RedirectResponse` are already imported in `api/main.py` (they are used by the existing event-operation pages).

- [ ] **Step 2: Build the scenario library page**

`frontend/templates/scenarios.html` (extend the existing admin base template — check `frontend/templates/` for the base name used by `event_operations.html` and extend it):
- A table/list of scenarios (`name`, `version`, `description`, `created_at`) with actions: **Instantiate** (prompt for event name → `POST /admin/api/scenarios/{id}/instantiate` → redirect to the new event), **Delete**, **View**.
- A "Save current event as scenario" form: event selector (from `GET /admin/api/events` or the existing event list) + name → `POST /admin/api/scenarios/from-event`.
- A "New empty scenario" form → `POST /admin/api/scenarios`.

`frontend/static/scenarios.js`: `fetch` wrappers for the endpoints above, rendering rows into the table, and `window.location` redirect on instantiate. Escape all server-provided text with an `esc()` helper (match the pattern used in the AI-agent templates).

- [ ] **Step 3: Build the timeline editor page**

`frontend/templates/event_timeline.html`: a single full-width canvas `div`, a header with the event name + "Plan health" toggle button, and a modal for creating/editing an inject.

`frontend/static/event-timeline.js`:
- On load: fetch `GET /admin/api/events/{id}/timeline` and `GET /admin/api/events/{id}/operations`; render a horizontal time axis (0 → `time_limit_minutes`, clamped to a sensible pixel-per-minute scale) using the same D3 v7 CDN approach as the topology page.
- Render phases as colored bands, operations as bars (offset = scheduled-trigger `offset_minutes` from the operation plan; width = `policy.time_limit_minutes`), injects as clickable markers.
- Drag-to-move injects (update `offset_minutes`), then `PUT /admin/api/events/{id}/timeline` with `expected_updated_at` from the last load; on 409, re-fetch and show a "timeline changed, reloaded" notice.
- Inject editor modal: fields for name, `offset_minutes`, `kind` (dropdown of the four kinds), and kind-specific payload fields (`module_id` + `target` for `apply_module`, `operation` for `start_operation`, `severity` + `message` for `notify`).
- Plan-health panel: fetch `GET /admin/api/events/{id}/plan-health`, list `module_issues`, `timeline_issues`, and `operation_issues`.

- [ ] **Step 4: Add navigation links**

Find the admin nav template (used by `event_operations.html` for "Operations" links) and add links to `/admin/scenarios` and, on the event pages, to `/admin/events/{event_id}/timeline`.

- [ ] **Step 5: Manual verification + frontend test**

Run: `docker compose --profile test run --rm tests` to confirm the full suite and migrations pass. For frontend, follow the existing `.test.mjs` pattern (e.g. `tests/event-operation-workspace.test.mjs`) and add one smoke test `tests/event-timeline.test.mjs` that loads `/admin/events/{id}/timeline` and asserts the inject marker renders.

- [ ] **Step 6: Commit**

```bash
git add frontend/templates/scenarios.html frontend/static/scenarios.js \
        frontend/templates/event_timeline.html frontend/static/event-timeline.js \
        api/main.py tests/event-timeline.test.mjs
git commit -m "feat: add scenario library and timeline editor pages"
```

---

## Self-Review Notes

- **Spec coverage:** Spec sections map to Tasks 1 (timeline), 2 (model/migration), 3 (scenario builder/instantiate/health), 4 (scenario API), 5 (timeline/health API), 6 (module `phases`/`narrative`), 7 (frontend). Spec's "provenance columns", "409 on delete", "advisory phase metadata", and "deterministic verbatim copy + validate" are all implemented.
- **Type consistency:** `scenario_fingerprint` signature is `(quota, infrastructure, infrastructure_layout, module_plan, operations, timeline)` everywhere; `instantiate_scenario(db, scenario, name)` returns `(event_id, report)`; `validate_timeline(timeline, infrastructure, operation_names, modules_by_id, event_minutes)` is stable across Tasks 1, 3, 5.
- **Router prefix:** `api/routes/scenarios.py` uses prefix `/admin/api` from Task 4 onward; scenario routes are `/scenarios/...` and event routes are `/events/{event_id}/timeline` and `/events/{event_id}/plan-health`. Tests call the full `/admin/api/scenarios/...` and `/admin/api/events/...` paths.
