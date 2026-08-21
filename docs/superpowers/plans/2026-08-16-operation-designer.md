# Full-Page Operation Designer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the third full-page event-planning step for authoring, validating, saving, and previewing a canonical per-team Caldera operation graph.

**Architecture:** Persist a provider-neutral versioned JSON graph on `Event`. A focused `builder.operation_plan` module owns normalization, catalogue projection, validation, fingerprints, and deterministic per-team preview manifests; thin API routes expose these operations. A planner-native HTML/CSS/JavaScript workspace owns graph editing and delegates all authoritative validation to the backend.

**Tech Stack:** FastAPI, SQLAlchemy/Alembic, Python 3, Jinja2, vanilla ES modules, SVG, Node test runner, pytest.

## Global Constraints

- The graph is a DAG and uses only assigned-module Caldera abilities and individual canonical planned VMs.
- Edges are `success`, `failure`, or `always`; retries and delays are node configuration.
- Invalid drafts save, but preview requires a valid graph.
- The canonical plan contains no runtime VM IDs or Caldera installation IDs.
- No subagents; execute inline as requested.

---

### Task 1: Operation-plan domain

**Files:**
- Create: `builder/operation_plan.py`
- Create: `tests/test_operation_plan.py`

**Interfaces:**
- Produces: `empty_operation_plan()`, `normalize_operation_plan(value)`, `operation_catalogue(infrastructure, module_plan, modules)`, `validate_operation_plan(plan, infrastructure, module_plan, modules, event_minutes=None)`, `operation_input_fingerprint(...)`, and `compile_team_preview(...)`.

- [ ] Write failing tests for normalization limits, duplicate IDs, invalid edge references/types, DAG detection, reachability, required objectives, ability provenance, exact planned-VM targeting, policy bounds, fingerprints, and deterministic manifests.
- [ ] Run `pytest tests/test_operation_plan.py -q` and confirm the tests fail because the module does not exist.
- [ ] Implement strict version-1 normalization, catalogue projection from resolved assignments and module Caldera metadata, structured issue generation, deterministic topological ordering, duration estimates, fingerprints, and preview manifests.
- [ ] Run `pytest tests/test_operation_plan.py -q` and confirm all tests pass.
- [ ] Commit `builder/operation_plan.py` and `tests/test_operation_plan.py`.

### Task 2: Persistence migration and API

**Files:**
- Modify: `api/models.py`
- Create: `migrations/versions/0012_event_operation_plan.py`
- Modify: `api/routes/admin.py`
- Modify: `tests/test_gamenet.py`

**Interfaces:**
- Produces: `GET/PUT /admin/api/events/{event_id}/operation-plan`, `POST .../validate`, and `POST .../preview`.
- Consumes: Task 1 domain functions.

- [ ] Write failing API tests for default retrieval, invalid-draft persistence, optimistic concurrency, read-only lifecycle behavior, validation response, preview rejection, and deterministic preview success.
- [ ] Run the focused pytest cases and confirm route/model failures.
- [ ] Add nullable `Event.operation_plan`, the guarded Alembic column migration, and thin authenticated routes returning catalogue, planned VMs, issues, fingerprint, and revision.
- [ ] Run the focused API tests and migration/model checks.
- [ ] Commit the persistence and API slice.

### Task 3: Client state helpers

**Files:**
- Create: `frontend/static/event-operation-state.js`
- Create: `tests/event-operation-state.test.mjs`

**Interfaces:**
- Produces pure helpers for default state, node/edge insertion, deletion, selection pruning, typed-edge checks, topological auto-arrangement, and payload construction.

- [ ] Write failing Node tests covering immutable edits, stable IDs, edge constraints, cascaded deletion, selection, and deterministic layout.
- [ ] Run `node --test tests/event-operation-state.test.mjs` and confirm import failure.
- [ ] Implement the minimal pure state module with no DOM dependencies.
- [ ] Run the Node tests and confirm they pass.
- [ ] Commit state helpers and tests.

### Task 4: Full-page operation workspace

**Files:**
- Create: `frontend/templates/event_operation.html`
- Create: `frontend/static/event-operation.css`
- Create: `frontend/static/event-operation.js`
- Modify: `api/main.py`
- Modify: `frontend/templates/event_modules.html`
- Create: `tests/test_event_operation_template.py`

**Interfaces:**
- Consumes: Task 2 endpoints and Task 3 helpers.
- Produces: `/admin/events/{event_id}/operation`.

- [ ] Write failing template-contract tests for the dedicated shell, three-column workspace, command actions, library filters, SVG canvas, inspector, outline, validation live region, dialogs, module-page navigation, and authenticated route context.
- [ ] Run the template tests and confirm missing-file failures.
- [ ] Add the route and semantic template with the shared planner toolbar and real event fields.
- [ ] Implement planner-native responsive CSS with labelled edge patterns, focus states, reduced motion, independent panel scrolling, and non-colour status cues.
- [ ] Implement loading, library search, click/keyboard node creation, SVG nodes/edges, inspector editing, typed connections, deletion confirmation, outline navigation, auto-arrange, validation, save concurrency, unsaved warning, and preview dialog.
- [ ] Run template tests plus `node --check frontend/static/event-operation.js`.
- [ ] Commit the complete workspace.

### Task 5: Planning-flow integration

**Files:**
- Modify: `frontend/templates/event_plan.html`
- Modify: `frontend/templates/event_modules.html`
- Modify: `frontend/static/event-modules.js`
- Modify: related template tests.

**Interfaces:**
- Produces the explicit workflow `Network plan → Module assignment → Operation design` and stale-operation messaging after assignment changes.

- [ ] Add failing assertions for forward/back navigation and operation-plan staleness visibility.
- [ ] Run focused template and client tests to confirm failure.
- [ ] Add operation-design navigation and ensure module saves cause the backend fingerprint to report stale inputs without mutating the operation draft.
- [ ] Run the focused suites and commit the workflow integration.

### Task 6: Verification and documentation

**Files:**
- Modify: `docs/superpowers/plans/2026-08-16-operation-designer.md` checkbox state only if useful.

- [ ] Run `pytest tests/test_operation_plan.py tests/test_event_operation_template.py tests/test_event_modules_template.py tests/test_event_plan_template.py -q`.
- [ ] Run `node --test tests/event-operation-state.test.mjs tests/event-modules-state.test.mjs tests/event-planner-state.test.mjs`.
- [ ] Run JavaScript syntax checks for every new or modified module.
- [ ] Run `git diff --check` and inspect `git status --short`.
- [ ] Run the repository's disposable Docker test suite if available in the current environment.
- [ ] Review the implementation against every acceptance criterion in the design spec and fix any gap before completion.
