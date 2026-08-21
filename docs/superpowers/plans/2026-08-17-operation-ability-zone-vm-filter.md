# Operation Ability Zone and VM Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Filter Add node ability results by site-aware zone or planned VM and automatically target abilities added with an explicit VM selection.

**Architecture:** Enrich the backend operation catalogue with deterministic per-ability target IDs, then consume that metadata through a small pure JavaScript filter module. The existing page controller owns transient picker state and the existing graph validation continues to handle deliberately untargeted draft nodes.

**Tech Stack:** Python 3, pytest, browser-native ES modules, Node.js test runner, Jinja2, CSS.

## Global Constraints

- Scope is only `/admin/events/{event_id}/operations/{operation_id}` and only its Add node picker.
- Zone identity is the stable combination of site and zone; labels include both values.
- Zone narrows ability and VM options; only explicit VM selection supplies `target_vm_id`.
- Ability applicability includes pinned, resolved, and recursively required modules and excludes incompatible base types.
- Filters reset each time the picker opens and are never persisted.
- Non-ability picker sections retain their existing search and connection behavior.
- Preserve the existing Industrial visual system and operation-plan schema.

---

### Task 1: Authoritative ability applicability metadata

**Files:**
- Modify: `builder/operation_plan.py`
- Test: `tests/test_operation_plan.py`

**Interfaces:**
- Consumes: `assignable_endpoints(infrastructure)` and normalized per-target `assignments`.
- Produces: every `operation_catalogue(...)["abilities"]` row includes `applicable_target_ids: list[str]`.

- [ ] **Step 1: Write failing catalogue tests**

Add fixtures with two targets in different zones and assignments where one target receives a module directly and the other receives a module with a recursive dependency. Assert literal target-ID lists for direct, dependency-derived, and base-incompatible cases, including stable ordering.

```python
assert weak_ssh["applicable_target_ids"] == ["vm:hq/blue/web"]
assert foundation["applicable_target_ids"] == ["vm:hq/red/jump"]
assert windows_only["applicable_target_ids"] == []
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `pytest -q tests/test_operation_plan.py -k catalogue`

Expected: FAIL because ability rows do not contain `applicable_target_ids`.

- [ ] **Step 3: Implement target-scoped effective assignments**

Refactor catalogue assignment traversal into a helper that expands each target's pinned and resolved modules through `requires`. Build an ability's sorted target-ID list from those per-target sets and the existing `supported_bases` rule.

```python
effective_by_target = {
    target_id: _effective_module_ids(assignment, by_id)
    for target_id, assignment in assignments.items()
}
```

- [ ] **Step 4: Run focused and regression tests**

Run: `pytest -q tests/test_operation_plan.py`

Expected: PASS.

- [ ] **Step 5: Commit backend metadata**

```bash
git add builder/operation_plan.py tests/test_operation_plan.py
git commit -m "feat: expose ability target applicability"
```

### Task 2: Pure picker filter behavior

**Files:**
- Create: `frontend/static/event-operation-picker-filter.js`
- Create: `tests/event-operation-picker-filter.test.mjs`

**Interfaces:**
- Consumes: catalogue targets `{id, name, site, zone}` and abilities `{applicable_target_ids, ...}`.
- Produces: `zoneKey(target)`, `zoneOptions(targets)`, `vmOptions(targets, selectedZone)`, `filterAbilities(abilities, targets, filters)`, `abilityTargetId(selectedVm)`, and `abilityApplicabilityText(ability, targets, filters)`.

- [ ] **Step 1: Write failing pure-function tests**

Use complete literal targets including repeated `Blue` zone names at two sites. Assert site-aware zone options, cascading VM rows, combined search/applicability filtering, selected VM target derivation, empty target derivation, and applicability copy.

```javascript
assert.deepEqual(zoneOptions(targets), [
  {value:'hq\u001fBlue',label:'HQ · Blue'},
  {value:'remote\u001fBlue',label:'Remote · Blue'},
]);
assert.equal(abilityTargetId('vm:hq/blue/web'),'vm:hq/blue/web');
assert.equal(abilityTargetId(''),'');
```

- [ ] **Step 2: Run the new test and verify RED**

Run: `node --test tests/event-operation-picker-filter.test.mjs`

Expected: FAIL because the helper module does not exist.

- [ ] **Step 3: Implement minimal pure functions**

Implement deterministic, null-safe transforms without DOM access. Ignore applicability IDs absent from the target catalogue. Search compares normalized ability name, description, module ID, and phase.

- [ ] **Step 4: Run the helper test and verify GREEN**

Run: `node --test tests/event-operation-picker-filter.test.mjs`

Expected: PASS.

- [ ] **Step 5: Commit filter helpers**

```bash
git add frontend/static/event-operation-picker-filter.js tests/event-operation-picker-filter.test.mjs
git commit -m "feat: add operation picker filters"
```

### Task 3: Wire zone and VM filters into the Add node picker

**Files:**
- Modify: `frontend/templates/event_operation.html`
- Modify: `frontend/static/event-operation.css`
- Modify: `frontend/static/event-operation.js`
- Modify: `tests/test_event_operation_template.py`

**Interfaces:**
- Consumes: Task 2 helper exports and Task 1 `applicable_target_ids` metadata.
- Produces: labelled `node-picker-zone` and `node-picker-vm` selects; targeted or untargeted ability templates passed to existing `addNode` and `insertConnectedNode` paths.

- [ ] **Step 1: Write failing template integration assertions**

Assert the template exposes labelled Zone and VM selects, the controller imports the helper module, resets filter values in `openPicker`, listens for filter changes, and uses the selected VM rather than `catalogue.targets[0]` for ability templates.

```python
assert 'id="node-picker-zone"' in html
assert 'id="node-picker-vm"' in html
assert "from './event-operation-picker-filter.js?v=1'" in source
assert "target_vm_id:abilityTargetId(selectedPickerVm)" in source
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `pytest -q tests/test_event_operation_template.py`

Expected: FAIL because the filter controls and controller wiring are absent.

- [ ] **Step 3: Add accessible filter markup and Industrial styling**

Add a two-column `.picker-filters` fieldset beneath search, with visible labels, All zones/All VMs defaults, square black controls, one-pixel borders, cyan focus, and a single-column narrow-screen rule.

- [ ] **Step 4: Wire transient filter state and rendering**

Import Task 2 helpers. Reset zone and VM in `openPicker`; rebuild VM options when Zone changes; filter only ability rows; set `target_vm_id` from the explicit VM; render applicability detail and a specific no-abilities explanation while preserving other sections.

- [ ] **Step 5: Run focused frontend tests and syntax checks**

Run: `pytest -q tests/test_event_operation_template.py`

Run: `node --test tests/event-operation-picker-filter.test.mjs tests/event-operation-ability-details.test.mjs tests/event-operation-state.test.mjs`

Run: `node --check frontend/static/event-operation-picker-filter.js`

Run: `node --check frontend/static/event-operation.js`

Expected: all commands PASS.

- [ ] **Step 6: Run operation regressions**

Run: `pytest -q tests/test_operation_plan.py tests/test_event_operation_template.py tests/test_event_operation_api.py tests/test_event_operations_api.py tests/test_event_operations_model.py`

Expected: PASS.

- [ ] **Step 7: Commit picker integration**

```bash
git add frontend/templates/event_operation.html frontend/static/event-operation.css frontend/static/event-operation.js tests/test_event_operation_template.py
git commit -m "feat: filter operation abilities by zone and VM"
```

### Task 4: Final verification and local frontend

**Files:**
- Verify all changed files and repository state.

**Interfaces:**
- Consumes: completed Tasks 1–3.
- Produces: verified feature and a running local stack exposing the frontend on port 8091.

- [ ] **Step 1: Review the diff against every acceptance criterion**

Run: `git diff HEAD~3 --check`

Run: `git status --short`

Inspect changed code for stale default-first-target behavior, persisted filter state, and non-ability filtering regressions.

- [ ] **Step 2: Run the repository's full automated test command**

Run: `docker compose --profile test run --rm --build tests`

Expected: exit 0 with no failed tests.

- [ ] **Step 3: Start the local frontend on port 8091**

Run: `EXPO_PORT=8091 docker compose up --detach --build`

Expected: the web service is running and publishes port 8091.

- [ ] **Step 4: Verify service state and HTTP response**

Run: `EXPO_PORT=8091 docker compose ps`

Run: `curl --silent --show-error --fail http://localhost:8091/`

Expected: running services and an HTTP-successful frontend response.
