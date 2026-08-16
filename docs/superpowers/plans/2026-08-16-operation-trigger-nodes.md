# Operation Trigger Nodes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the generic operation Start node and launch policy fields with manual, event-start, and event-relative scheduled trigger nodes and compile their one-shot preview contract.

**Architecture:** Keep operation-plan version 1 and migrate legacy Start nodes during normalization. The backend remains authoritative for trigger validation and preview compilation; framework-independent frontend state helpers enforce safe trigger replacement and connections, while the existing canvas controller renders and edits the new nodes.

**Tech Stack:** Python 3, FastAPI, pytest, browser-native ES modules, Node's built-in test runner, SVG/Jinja2.

## Global Constraints

- Exactly one enabled typed trigger roots a valid graph and has no incoming edge.
- Scheduled offsets are non-negative whole minutes relative to actual event start and run once.
- Runtime scheduling or graph execution is out of scope.
- Existing version-1 plans migrate without a database migration.
- Use the disposable Docker test service for the authoritative full regression run.

---

### Task 1: Backend trigger schema, migration, validation, and preview

**Files:**
- Modify: `tests/test_operation_plan.py`
- Modify: `builder/operation_plan.py`

**Interfaces:**
- Produces: `TRIGGER_TYPES`, normalized typed trigger nodes, `validate_operation_plan(...)` trigger issues, and `compile_team_preview(...)["trigger"]`.
- Preserves: `normalize_operation_plan(value)`, `operation_catalogue(...)`, and version-1 API compatibility.

- [ ] **Step 1: Write failing backend tests** for the Manual Trigger default, legacy manual/scheduled/scheduled-hold conversions, idempotence, zero/multiple/incoming trigger validation, offset bounds, event-duration bounds, catalogue trigger controls, and literal preview contracts.
- [ ] **Step 2: Run `docker compose --profile test run --rm tests pytest tests/test_operation_plan.py -q`** and confirm failures are caused by missing typed-trigger behavior.
- [ ] **Step 3: Implement minimal backend behavior** by replacing Start constants/defaults, migrating each legacy Start while stripping legacy policy keys, validating typed roots and scheduled offsets, applying event-duration checks only to scheduled triggers, and compiling `{type, once, offset_minutes?}`.
- [ ] **Step 4: Re-run the focused backend tests** and confirm they pass.
- [ ] **Step 5: Commit** `builder/operation_plan.py` and `tests/test_operation_plan.py` with message `feat: add operation trigger plan contract`.

### Task 2: Framework-independent frontend trigger mutations

**Files:**
- Modify: `tests/event-operation-state.test.mjs`
- Modify: `frontend/static/event-operation-state.js`

**Interfaces:**
- Produces: `isTriggerType(type)` and `replaceTrigger(state, template)`.
- Updates: `connectionError`, `deleteSelection`, and `duplicateNodes` to protect trigger invariants.

- [ ] **Step 1: Write failing Node tests** proving trigger replacement preserves identifier, position, disabled state, and outgoing edges; incoming trigger edges are rejected; trigger deletion and duplication are no-ops; and ordinary nodes retain existing behavior.
- [ ] **Step 2: Run `node --test tests/event-operation-state.test.mjs`** and confirm failures name missing trigger helpers or invariant enforcement.
- [ ] **Step 3: Implement minimal immutable state helpers** and integrate them with connection, deletion, and duplication paths.
- [ ] **Step 4: Re-run the focused Node tests** and confirm they pass.
- [ ] **Step 5: Commit** the state module and tests with message `feat: enforce operation trigger graph rules`.

### Task 3: Canvas picker, inspector, policy, and preview UI

**Files:**
- Modify: `tests/test_event_operation_template.py`
- Modify: `frontend/static/event-operation.js`
- Modify: `frontend/templates/event_operation.html`
- Modify: `frontend/static/event-operation.css` only if a small trigger presentation hook is required.

**Interfaces:**
- Consumes: `isTriggerType` and `replaceTrigger` from Task 2 and backend preview `trigger` from Task 1.
- Produces: trigger picker entries, scheduled offset editing, trigger-safe controls, and readable preview copy.

- [ ] **Step 1: Write failing UI contract tests** that execute or inspect consumer-visible controller behavior for Triggers grouping, all three labels, scheduled offset input, removal of launch policy fields, trigger replacement import/use, and preview trigger rendering.
- [ ] **Step 2: Run `pytest tests/test_event_operation_template.py -q` and `node --check frontend/static/event-operation.js`** and confirm the contract test fails for missing UI behavior while syntax remains valid.
- [ ] **Step 3: Implement the canvas behavior**: add the three picker templates; use atomic replacement when a trigger is chosen; omit trigger inputs; label the trigger output; show only scheduled offset configuration; hide duplicate/toggle/delete for triggers; remove launch fields from policy; render the compiled trigger preview; update search placeholder and asset cachebusters.
- [ ] **Step 4: Run template tests, Node state tests, Python operation tests, and JavaScript syntax checks** and confirm they pass.
- [ ] **Step 5: Commit** frontend and contract tests with message `feat: add operation trigger nodes to canvas`.

### Task 4: Full verification and local frontend

**Files:**
- Verify only unless a regression requires a test-first correction.

**Interfaces:**
- Consumes: all prior tasks.
- Produces: verified repository state and a running local stack on port 8091.

- [ ] **Step 1: Run `git diff --check`, JavaScript syntax checks, focused Node tests, and focused Python tests.**
- [ ] **Step 2: Run the authoritative disposable test service** with `docker compose --profile test run --rm --build tests` and record its exit status and test count.
- [ ] **Step 3: Review the implementation against every acceptance criterion** in `docs/superpowers/specs/2026-08-16-operation-trigger-nodes-design.md`.
- [ ] **Step 4: Start the local stack on port 8091** using the repository's Docker Compose configuration and verify its health endpoint/page responds.
- [ ] **Step 5: Report the exact local URL, verification evidence, commits, and any residual limitations.**
