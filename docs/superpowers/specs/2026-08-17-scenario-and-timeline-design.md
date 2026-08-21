# Scenario Layer & Timeline / Inject Authoring

## Goal

Give administrators a reusable, versioned way to plan large Locked Shields-style training exercises as a coherent *story*, instead of assembling infrastructure, module assignments, and operation graphs by hand for every event.

Two capabilities:

1. **Scenario layer** — a versioned template that bundles the four planning artifacts (infrastructure, module plan, operations, timeline) and can be instantiated into a fresh draft event.
2. **Timeline & inject authoring** — an event-level timeline (phases + injects) that renders the whole exercise on a time axis and lets instructors plan timed story events that fire mid-exercise.

## Background

The current planning stack is bottom-up and static:

- **Infrastructure planner** (`builder/infrastructure_planner.py`) — sites → zones → endpoints, per-team repeat, firewall/VPN.
- **Module assignment** (`builder/module_plan.py`) — per-VM `random_fill` + pins, conflicts, `requires`, `supported_bases`, deterministic `resolution_fingerprint`.
- **Operation planner** (`builder/operation_plan.py`) — graph designer with `manual/event_start/scheduled_trigger`, `ability` (with `target_vm_id`), `objective`, `gate`, `delay`, `finish` nodes; per-team preview; policy.
- **Module catalogue** (`builder/module_loader.py`) — 75 YAML modules with rich `caldera` recon/exploit metadata.

Nothing ties these into a reusable whole, and there is no way to plan time-based story events that change the scenario mid-exercise. The `EventOperation` graph plan is a design/planning artifact only (no runtime execution) — this spec stays consistent with that: it is planning-only.

## Design decisions (summary)

- **Template + instantiate.** A `Scenario` is a reusable template; instantiation produces a concrete draft `Event`.
- **Round-trip editing.** Scenarios are authored via *"Save event as scenario"* and edited via *instantiate → edit → save-as-new-version*. No dedicated in-place scenario editor in v1.
- **Identical for all teams.** No per-team variation in the scenario template (existing `random_fill` + fingerprint still permit deterministic variation if the author wants it).
- **Timeline stored as JSON on `Event`**, normalized/validated by a new pure module (`builder/timeline.py`), mirroring the existing `module_plan.py` / `operation_plan.py` pattern. No new tables for phases/injects.
- **Injects can trigger anything** among a fixed kind set (`apply_module`, `start_operation`, `notify`, `milestone`).
- **Module `phase`/`phases` metadata is advisory only** — never enforced, never gates selection/scoring/validation.

## Scenario layer

### Data model

New `Scenario` table:

| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | |
| `name` | String(128) | unique |
| `description` | Text | nullable |
| `version` | Integer | bumped on each save |
| `quota` | Text (JSON) | module selection quota |
| `infrastructure` | Text (JSON) | normalized infrastructure |
| `infrastructure_layout` | Text (JSON) | presentation layout, nullable |
| `module_plan` | Text (JSON) | normalized module plan |
| `operations_json` | Text (JSON) | array of `{name, description, position, operation_plan}` |
| `timeline` | Text (JSON) | phases + injects (see below) |
| `content_fingerprint` | String(64) | deterministic digest of the planning artifacts |
| `created_at` / `updated_at` | DateTime | |

`Event` gains three nullable provenance columns: `scenario_id` (FK), `scenario_version` (Integer), `scenario_fingerprint` (String(64)). These record where an instantiated event came from; they have no effect on event behaviour.

### Editing model

- **Save as scenario**: from any event, capture its `infrastructure`, `module_plan`, operations (all `EventOperation` rows), and `timeline` into a `Scenario`. Saving an existing scenario name creates a new version (bumps `version`). Content per version is immutable; history/rollback is out of scope.
- **Instantiate**: "New event from scenario" creates a draft `Event` and populates it (below). Scenario content is never mutated by instantiation.

### Instantiation

1. Copy `quota`, `infrastructure`, `infrastructure_layout` (if present), `module_plan`, and `timeline` verbatim. The instantiated event is a deterministic reproduction of the scenario's planning state.
2. Validate the copied module plan against the *current* module catalogue: every `pinned_module_id` and `resolved_module_id` must exist, be enabled, and be compatible with its endpoint's `base_type`; missing `requires` are flagged. Resolved ids are **not** recomputed — random re-selection would diverge from what the author saw.
3. Copy each scenario operation into an `EventOperation` row (preserving name, description, position, graph).
4. Return an **instantiation report** (see Plan health).

Teams, users, and runtime state are never copied — they are created on the instantiated event as today.

## Timeline & inject authoring

### Storage

One new JSON column `Event.timeline`, normalized/validated by `builder/timeline.py`. Saves use `Event.updated_at` optimistic concurrency (same as infrastructure saves).

### Schema

```json
{
  "version": 1,
  "phases": [
    {"id": "phase:recon", "name": "Recon", "start_offset_minutes": 0,
     "end_offset_minutes": 60, "color": "#ff5555", "description": "..."}
  ],
  "injects": [
    {"id": "inject:1", "name": "Deploy Log4Shell", "offset_minutes": 45,
     "kind": "apply_module",
     "payload": {"module_id": "log4shell_app",
                 "target": "vm:head_office/corporate/workstation_1"},
     "description": "Story beat text"}
  ]
}
```

### Inject kinds

All timed by `offset_minutes` relative to event start:

- `apply_module` — apply a module to a target VM mid-exercise (net-new capability; current planning is build-time only).
- `start_operation` — fire an operation at this offset (references an operation by name/position).
- `notify` — white-cell instructor notification (`{severity, message}`).
- `milestone` — narrative marker only.

### Timeline view

`/admin/events/{id}/timeline` (planning-only). Horizontal axis T+0 → event end; phases as colored bands; operations as bars (scheduled-trigger offset + `time_limit_minutes`); injects as markers. Click a bar/marker to jump to the relevant editor; drag injects to re-time; drag/resize phase spans.

### Validation (`builder/timeline.py`)

- Inject `offset_minutes` non-negative and ≤ event duration.
- `apply_module` references an existing module compatible with the target's `base_type`.
- `start_operation` references an existing operation.
- Phases are non-overlapping and in-bounds.
- Warnings surface in the instantiation report and the plan-health panel.

### Relationship to operations

`scheduled_trigger` nodes remain the source of truth for an operation's own timing. The timeline is an aggregate view plus the inject layer; an inject that starts an operation acts at event level without touching the operation's internal graph.

## Module schema additions (advisory)

Two optional YAML fields, consumed only by the planner — no effect on selection, scoring, or validation:

- `phases` (list of strings) — advisory story-phase labels (`recon`, `escalation`, `impact`, ...). Used for default grouping on the timeline and in the catalogue. A module may list multiple phases or none; it is never gated by phase.
- `narrative` (string) — one-line story text surfaced on the timeline and inject editor.

## Plan health / readiness

An event-level "plan health" summary, derived from existing validators plus new timeline validation:

- Per-operation validation status (already computed by `validate_operation_plan`).
- Module coverage per VM/zone (gaps where an endpoint has no modules assigned).
- Timeline dead zones (long spans with no injects/operations).
- Orphan injects (dangling module/operation/Vm references).
- Phase overlap / out-of-bounds.

This is surfaced as a panel on the timeline page and as the instantiation report payload.

## API surface

- `GET/POST /admin/scenarios` — list, create.
- `POST /admin/scenarios/from-event` — save an event as a scenario (`{event_id, name}`), bumping version.
- `GET /admin/scenarios/{id}` — detail (content + version + provenance).
- `POST /admin/scenarios/{id}/instantiate` — create a draft event from a scenario; returns the new event id + instantiation report.
- `DELETE /admin/scenarios/{id}` — delete; returns `409` if any event references the scenario (provenance must remain intact).
- `GET/PUT /admin/events/{id}/timeline` — get/save timeline JSON (optimistic concurrency).
- `GET /admin/events/{id}/plan-health` — readiness summary.

## Frontend

- Scenario library page (`/admin/scenarios`): list, create, save-from-event, instantiate, delete.
- Timeline editor page (`/admin/events/{id}/timeline`): phase bands, operation bars, inject markers; inject editor modal; plan-health panel.
- Reuse existing planner shell and JS conventions.

## Testing

Follow the disposable Docker test-service pattern. Cover: scenario round-trip (save → instantiate → identical plans), version bumping, instantiation report (missing/disabled modules, unknown references), timeline normalize/validate (bounds, overlap, orphan injects), provenance columns, and CRUD/scoping.

## Non-goals

- No runtime execution of injects/operations (planning only).
- No version-history table or rollback.
- No dedicated in-place scenario editor (round-trip editing only).
- No per-team variation in v1.
