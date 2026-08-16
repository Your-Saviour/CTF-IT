# Multiple Event Operations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single event-level operation graph with independently editable, named operations managed from an event overview.

**Architecture:** Persist operations as child rows of `Event`, retain the provider-neutral graph JSON contract, and scope graph APIs by both event and operation ID. Replace the existing operation route with an overview while moving the canvas designer to an item route.

**Tech Stack:** Python 3, FastAPI, SQLAlchemy 2, Alembic, SQLite/PostgreSQL, Jinja2, browser-native JavaScript/CSS, pytest, Node test runner, Docker Compose.

## Global Constraints

- Operations share event infrastructure and module assignments but have independent triggers, policies, graphs, validation, previews, and timestamps.
- Existing saved graphs migrate to one operation named `Operation 1`.
- Operations have required event-unique names and optional descriptions.
- Operations have no runtime dependency or execution ordering.
- Work inline in the current workspace and verify through the disposable Docker test service.

---

### Task 1: Persist independent event operations

**Files:**
- Modify: `api/models.py`
- Create: `migrations/versions/0013_multiple_event_operations.py`
- Modify: `api/migrations.py`
- Test: `tests/test_event_operations_model.py`

**Interfaces:**
- Produces: `EventOperation(event_id, name, description, position, operation_plan, created_at, updated_at)` and `Event.operations` ordered relationship.
- Consumes: the existing `Event.operation_plan` legacy JSON and `builder.operation_plan.empty_operation_plan()` contract.

- [ ] **Step 1: Write failing persistence and migration tests**

```python
def test_event_owns_ordered_operations(db_session):
    event = Event(name="Exercise", quota="{}")
    event.operations = [EventOperation(name="Second", position=1), EventOperation(name="First", position=0)]
    db_session.add(event); db_session.commit(); db_session.expire_all()
    assert [row.name for row in db_session.get(Event, event.id).operations] == ["First", "Second"]

def test_migration_converts_legacy_plan_once():
    source = Path("migrations/versions/0013_multiple_event_operations.py").read_text()
    assert 'name="Operation 1"' in source
    assert 'operation_plan IS NOT NULL' in source
```

- [ ] **Step 2: Run `docker compose --profile test run --rm tests pytest -q tests/test_event_operations_model.py` and confirm the missing model/migration failure.**
- [ ] **Step 3: Add the model, relationship, unique `(event_id, name)` and position indexes, and guarded Alembic migration that copies only legacy rows lacking child operations.**
- [ ] **Step 4: Run the focused Docker test and confirm it passes.**
- [ ] **Step 5: Commit with `git commit -m "feat: persist multiple event operations"`.**

### Task 2: Add operation collection CRUD and scoped graph APIs

**Files:**
- Modify: `api/routes/admin.py`
- Test: `tests/test_event_operations_api.py`
- Modify: `tests/test_event_operation_api.py`

**Interfaces:**
- Produces: `GET/POST /events/{event_id}/operations`, `PATCH/DELETE /events/{event_id}/operations/{operation_id}`, `POST .../{operation_id}/duplicate`, and graph endpoints below `.../{operation_id}/plan`.
- Consumes: `EventOperation`, `_operation_context(event)`, and existing normalization/validation/preview helpers.

- [ ] **Step 1: Write failing API contract tests for create/list/update/delete, whitespace rejection, duplicate-name 409, cross-event 404, copy naming/position, and per-operation stale-save 409.**

```python
def test_operation_routes_are_event_and_item_scoped():
    source = Path("api/routes/admin.py").read_text()
    assert '@router.post("/events/{event_id}/operations")' in source
    assert '@router.put("/events/{event_id}/operations/{operation_id}/plan")' in source
    assert 'EventOperation.event_id == event_id' in source
```

- [ ] **Step 2: Run the focused API tests in Docker and confirm route/behavior failures.**
- [ ] **Step 3: Implement shared event/item lookup, serialization with trigger/validity summary, collision-safe copy naming, atomic position insertion/compaction, metadata validation, and independent optimistic locking.**
- [ ] **Step 4: Move get/save/validate/preview behavior to scoped plan endpoints without changing builder contracts; remove `operation_plan` from the event detail response.**
- [ ] **Step 5: Run focused operation API and builder tests and confirm they pass.**
- [ ] **Step 6: Commit with `git commit -m "feat: add event operation APIs"`.**

### Task 3: Build the operations overview and retarget the designer

**Files:**
- Create: `frontend/templates/event_operations.html`
- Create: `frontend/static/event-operations.js`
- Create: `frontend/static/event-operations.css`
- Modify: `frontend/templates/event_operation.html`
- Modify: `frontend/static/event-operation.js`
- Modify: `api/main.py`
- Test: `tests/test_event_operations_template.py`
- Modify: `tests/test_event_operation_template.py`

**Interfaces:**
- Produces: overview route `/admin/events/{event_id}/operation` and designer route `/admin/events/{event_id}/operations/{operation_id}`.
- Consumes: Task 2 JSON APIs and existing canvas modules.

- [ ] **Step 1: Write failing template/route tests for the overview list, create/edit dialog, duplicate/delete actions, item route, operation dataset ID, and overview Back link.**

```python
def test_operation_overview_and_item_routes_are_wired():
    main = Path("api/main.py").read_text()
    assert '"event_operations.html"' in main
    assert '@app.get("/admin/events/{event_id}/operations/{operation_id}"' in main
```

- [ ] **Step 2: Run focused template tests in Docker and confirm failures.**
- [ ] **Step 3: Implement the overview with real event/operation fields, empty state, accessible dialogs, read-only state, and fetch-based actions with explicit error/status messaging.**
- [ ] **Step 4: Pass `operation_id` into the canvas page, change its API base to `/admin/api/events/${eventId}/operations/${operationId}/plan`, and return Back to the overview.**
- [ ] **Step 5: Run Python template tests plus `node --check` for both controllers and confirm they pass.**
- [ ] **Step 6: Commit with `git commit -m "feat: add event operations overview"`.**

### Task 4: Compatibility, documentation, and full verification

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`
- Modify: affected operation tests discovered by `rg -n "operation-plan|event_operation" tests`

**Interfaces:**
- Consumes: all prior tasks.
- Produces: documented routes/model and a verified release candidate.

- [ ] **Step 1: Update architecture documentation to describe the overview, child model, independent graphs, and legacy conversion.**
- [ ] **Step 2: Run `rg -n "operation-plan|event_operation" tests` and update old single-plan route assertions to the new scoped contract.**
- [ ] **Step 3: Run `docker compose --profile test run --rm --build tests` and resolve every regression.**
- [ ] **Step 4: Run `docker compose config`, `git diff --check`, and relevant `node --check` commands.**
- [ ] **Step 5: Commit with `git commit -m "docs: document multiple event operations"`.**
- [ ] **Step 6: Start the application on port 8091 and verify its health endpoint and operations overview response.**
