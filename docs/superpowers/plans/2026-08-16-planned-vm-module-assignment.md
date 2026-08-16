# Planned VM Module Assignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a full-page editor that pins and stably resolves modules for canonical planned VMs, identically across teams.

**Architecture:** Store a versioned `Event.module_plan` JSON document keyed by planner VM IDs. Put normalization, reconciliation, validation, deterministic consumption, random fill, and automatic repair in `builder/module_plan.py`; keep HTTP routes thin and the browser state/rendering split between a pure state module and a page controller. Preview consumes resolved IDs, while provider-specific provisioning remains unchanged.

**Tech Stack:** Python 3, FastAPI, SQLAlchemy, Alembic-style application migrations, Jinja2, browser-native ES modules, Node test runner, pytest, Docker Compose.

## Global Constraints

- Canonical assignments, including resolved random modules, repeat identically across every team.
- Pins override quota and are never silently removed.
- Blue endpoints use explicit per-VM random fill; red endpoints default to manual-only.
- Unresolved drafts may save, but Preview and Start Event remain blocked.
- Do not modify Vultr provisioning, cloud plan sizing, or Ansible launch integration.
- Keep gateway and firewall machines non-assignable.
- Preserve existing `Event.updated_at` optimistic concurrency behavior.

---

### Task 1: Module-plan domain model and migration

**Files:**
- Create: `builder/module_plan.py`
- Modify: `api/models.py`
- Modify: `api/migrations.py`
- Test: `tests/test_module_plan.py`

**Interfaces:**
- Produces: `empty_module_plan() -> dict`, `normalize_module_plan(value: object) -> dict`, `assignable_endpoints(infrastructure: dict) -> list[dict]`, and `reconcile_module_plan(plan: dict, infrastructure: dict) -> tuple[dict, list[dict]]`.
- Produces: nullable `Event.module_plan: str | None`.

- [ ] **Step 1: Write failing normalization and endpoint-discovery tests**

```python
def test_assignable_endpoints_include_blue_and_red_but_not_infrastructure():
    rows = assignable_endpoints(sample_infrastructure())
    assert [(row["id"], row["role"]) for row in rows] == [
        ("vm:head_office/corporate/analyst", "blue"),
        ("vm:head_office/red_team/operator", "red"),
    ]

def test_reconcile_preserves_existing_and_reports_deleted_assignment():
    plan, issues = reconcile_module_plan(saved_plan_with_deleted_vm(), sample_infrastructure())
    assert "vm:removed/zone/host" in plan["assignments"]
    assert issues[0]["code"] == "unknown_vm"
```

- [ ] **Step 2: Run the focused tests and confirm failure**

Run: `docker compose --profile test run --rm tests pytest -q tests/test_module_plan.py`
Expected: FAIL because `builder.module_plan` does not exist.

- [ ] **Step 3: Implement version-one normalization, stable endpoint derivation, reconciliation, and payload bounds**

```python
MODULE_PLAN_VERSION = 1
MAX_MODULE_PLAN_BYTES = 262_144

def empty_module_plan():
    return {"version": MODULE_PLAN_VERSION, "assignments": {}}

def assignable_endpoints(infrastructure):
    return [{
        "id": f"vm:{site['key']}/{zone['key']}/{endpoint['key']}",
        "name": endpoint["name"], "base_type": endpoint["base_type"],
        "role": zone["team"], "site": site["name"], "zone": zone["name"],
    } for site in infrastructure["sites"] for zone in site["zones"]
      for endpoint in zone["endpoints"]]
```

Validate version, assignment key shape, mode, string arrays, duplicate IDs, and maximum encoded size. Reconciliation reports unknown IDs without deleting them and inserts no implicit saved rows for new endpoints.

- [ ] **Step 4: Add the model column and idempotent SQLite/Postgres migration path**

Add `module_plan: Mapped[str] = mapped_column(Text, nullable=True)` beside `infrastructure_layout`, and follow the existing column-presence migration pattern in `api/migrations.py` to add `events.module_plan`.

- [ ] **Step 5: Run domain and migration tests**

Run: `docker compose --profile test run --rm tests pytest -q tests/test_module_plan.py tests/test_gamenet.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add builder/module_plan.py api/models.py api/migrations.py tests/test_module_plan.py
git commit -m "feat: add planned VM module plan model"
```

### Task 2: Resolution engine and validation

**Files:**
- Modify: `builder/module_plan.py`
- Test: `tests/test_module_plan.py`

**Interfaces:**
- Consumes: module definitions from `builder.module_loader.Module` and quota selection rules from `builder.selector`.
- Produces: `resolve_assignment(endpoint: dict, assignment: dict, quota: dict, library: list[Module], *, refill: bool) -> dict`.
- Produces: `validate_assignment(...) -> list[dict]`, `resolution_fingerprint(...) -> str`, and `resolved_module_ids(plan, stable_vm_id) -> list[str]`.

- [ ] **Step 1: Write failing tests for pins, quota deficits, stability, red defaults, dependencies, and conflicts**

```python
def test_pins_override_quota_and_random_only_fills_deficit(module_library):
    result = resolve_assignment(blue_endpoint(), assignment(pins=["easy_a", "easy_b"]),
                                {"vulnerability": {"easy": 1}}, module_library, refill=True)
    assert result["resolved_module_ids"][:2] == ["easy_a", "easy_b"]
    assert result["quota_overrides"][0]["excess"] == 1

def test_pin_conflict_is_not_removed_by_auto_resolution(module_library):
    result = resolve_assignment(blue_endpoint(), assignment(pins=["left", "right"]),
                                {}, module_library, refill=True)
    assert result["pinned_module_ids"] == ["left", "right"]
    assert any(issue["code"] == "pinned_conflict" for issue in result["issues"])
```

- [ ] **Step 2: Run tests and confirm the new cases fail**

Run: `docker compose --profile test run --rm tests pytest -q tests/test_module_plan.py`
Expected: FAIL because resolution interfaces are absent.

- [ ] **Step 3: Implement pin-first quota accounting and candidate selection**

Reuse selector compatibility semantics, but start `selected` with ordered pins plus transitive requirements. Count these against type/difficulty, category, and tag quotas; select random candidates only for positive deficits. Never call random selection when returning saved resolved IDs.

- [ ] **Step 4: Implement automatic repair and fingerprinting**

```python
def resolution_fingerprint(quota, endpoint, pinned_ids, library):
    payload = {"quota": quota, "base_type": endpoint["base_type"],
               "role": endpoint["role"], "pins": pinned_ids,
               "catalogue": relevant_catalogue_signature(pinned_ids, library)}
    return "sha256:" + hashlib.sha256(canonical_json(payload).encode()).hexdigest()
```

Replace conflicting random selections, add transitive requirements before consumers, preserve pin-to-pin conflicts, and return structured issue paths.

- [ ] **Step 5: Run selector and module-plan tests**

Run: `docker compose --profile test run --rm tests pytest -q tests/test_module_plan.py tests/test_selector.py`
Expected: PASS with existing selector behavior unchanged.

- [ ] **Step 6: Commit**

```bash
git add builder/module_plan.py tests/test_module_plan.py
git commit -m "feat: resolve pinned and random planned modules"
```

### Task 3: Module-plan API and concurrency

**Files:**
- Modify: `api/routes/admin.py`
- Modify: `api/schemas.py`
- Test: `tests/test_module_plan_api.py`

**Interfaces:**
- Consumes: Task 1 and 2 domain functions.
- Produces: GET/PUT `/admin/api/events/{id}/module-plan`, POST `/module-plan/generate`, and POST `/module-plan/resolve` with stable VM ID in JSON bodies.

- [ ] **Step 1: Write failing API tests**

Cover admin authentication, normalized GET payload, unresolved draft save, non-draft rejection, unknown VM, blue per-VM generation, red generation rejection, automatic resolution, payload validation, and stale `expected_updated_at` returning `409`.

```python
response = client.put(f"/admin/api/events/{event.id}/module-plan", json={
    "module_plan": unresolved_plan, "expected_updated_at": revision,
})
assert response.status_code == 200
assert json.loads(db.get(Event, event.id).module_plan) == unresolved_plan
```

- [ ] **Step 2: Run focused API tests and confirm failure**

Run: `docker compose --profile test run --rm tests pytest -q tests/test_module_plan_api.py`
Expected: FAIL with missing routes.

- [ ] **Step 3: Add request schemas and thin handlers**

Handlers load event infrastructure/quota/catalogue, call domain functions, serialize issue objects, and never persist generate/resolve responses. PUT is the only mutation and updates `Event.updated_at` through the established concurrency contract.

- [ ] **Step 4: Run API, lifecycle, and GameNet tests**

Run: `docker compose --profile test run --rm tests pytest -q tests/test_module_plan_api.py tests/test_event_lifecycle.py tests/test_gamenet.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/routes/admin.py api/schemas.py tests/test_module_plan_api.py
git commit -m "feat: expose planned module assignment API"
```

### Task 4: Preview and lifecycle validation

**Files:**
- Modify: `api/routes/admin.py`
- Modify: `builder/module_plan.py`
- Test: `tests/test_gamenet.py`
- Test: `tests/test_event_lifecycle.py`

**Interfaces:**
- Consumes: `resolved_module_ids()` and assignment validation.
- Produces: preview module lists sourced from exact saved/submitted resolution, plus lifecycle blocking issues.

- [ ] **Step 1: Write failing preview tests**

```python
def test_preview_repeats_exact_resolved_modules_for_every_team(...):
    payload = preview_with_plan(["journal_retention", "persistence_triage"])
    sets = [tuple(m["id"] for m in vm["modules"]) for vm in payload["vm_types"][0]["vms"]]
    assert sets == [("journal_retention", "persistence_triage")] * len(sets)
```

Also assert unresolved blue plans and stale fingerprints reject preview/start, empty manual-only red assignments pass, and events without `module_plan` retain legacy preview selection.

- [ ] **Step 2: Run focused tests and confirm failure**

Run: `docker compose --profile test run --rm tests pytest -q tests/test_gamenet.py tests/test_event_lifecycle.py`
Expected: new assertions FAIL because preview rerandomizes.

- [ ] **Step 3: Replace preview-time selection only when a module plan exists**

For canonical endpoints, load definitions in saved order and build sizing/attack-tree preview from that exact list. Keep the legacy `select_modules()` path only when `Event.module_plan is None`.

- [ ] **Step 4: Add provider-neutral start validation without touching provisioning code**

Before the existing start transition, reject invalid saved module plans using structured issue messages. Do not change VM creation, provider calls, sizing, or Ansible functions.

- [ ] **Step 5: Run focused tests**

Run: `docker compose --profile test run --rm tests pytest -q tests/test_gamenet.py tests/test_event_lifecycle.py tests/test_attack_tree.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add api/routes/admin.py builder/module_plan.py tests/test_gamenet.py tests/test_event_lifecycle.py
git commit -m "feat: preview stable planned module assignments"
```

### Task 5: Pure browser state and rendering helpers

**Files:**
- Create: `frontend/static/event-modules-state.js`
- Create: `tests/event-modules-state.test.mjs`

**Interfaces:**
- Produces: `createModulePlanStore`, `vmAssignmentState`, `catalogueRows`, `togglePin`, `applyResolvedAssignment`, `pruneAssignmentsForSave`, and `validateClientModulePlan`.

- [ ] **Step 1: Write failing Node tests**

```javascript
test('togglePin preserves resolved rows but marks generation stale', () => {
  const next = togglePin(plan, VM_ID, 'journal_retention');
  assert.deepEqual(next.assignments[VM_ID].pinned_module_ids, ['journal_retention']);
  assert.equal(vmAssignmentState(next, VM_ID).state, 'unresolved');
});
```

Cover blue/red defaults, pin/unpin, provenance, catalogue filtering, crossed-out compatibility, conflict highlighting, stale/deleted reconciliation, and issue counts.

- [ ] **Step 2: Run Node tests and confirm failure**

Run: `node --test tests/event-modules-state.test.mjs`
Expected: FAIL because the module does not exist.

- [ ] **Step 3: Implement immutable state helpers**

Use `structuredClone`, stable IDs, and plain objects. Do not duplicate backend resolution logic; client validation provides immediate display state while server responses remain authoritative.

- [ ] **Step 4: Run Node tests and syntax check**

Run: `node --test tests/event-modules-state.test.mjs && node --check frontend/static/event-modules-state.js`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/static/event-modules-state.js tests/event-modules-state.test.mjs
git commit -m "feat: add planned module editor state"
```

### Task 6: Full-page assignment UI

**Files:**
- Create: `frontend/templates/event_modules.html`
- Create: `frontend/static/event-modules.js`
- Create: `frontend/static/event-modules.css`
- Modify: `frontend/templates/event_plan.html`
- Modify: `api/main.py`
- Modify: `tests/test_event_plan_template.py`
- Create: `tests/test_event_modules_template.py`

**Interfaces:**
- Consumes: Task 3 APIs and Task 5 state helpers.
- Produces: `/admin/events/{event_id}/modules` full-page route and planner toolbar navigation.

- [ ] **Step 1: Write failing route/template contract tests**

Assert admin-only access, shared-base rather than admin-shell inheritance, full-page root data attributes, Back to network plan, real event identity, save/preview controls, account/logout controls, three labelled panels, live regions, and planner Assign modules link.

- [ ] **Step 2: Run template tests and confirm failure**

Run: `docker compose --profile test run --rm tests pytest -q tests/test_event_modules_template.py tests/test_event_plan_template.py`
Expected: FAIL because the route/template do not exist.

- [ ] **Step 3: Build the template and Industrial stylesheet**

Use warm-black/black surfaces, JetBrains Mono, cyan as the sole primary signal, one-pixel borders, no shadows, no rounded cards, and tabular counts. The desktop grid is `240px minmax(420px, 1fr) 340px`; each panel scrolls independently below the toolbar.

- [ ] **Step 4: Implement the controller**

Boot GET data, render grouped VMs, searchable/filterable real catalogue rows, pinned/resolved provenance, quota status, issues, and read-only state. Wire per-VM Generate, Resolve, Pin/Unpin, Save Draft, Preview, Retry, `409` handling, keyboard focus, live announcements, and `beforeunload`.

- [ ] **Step 5: Link the network planner and run frontend checks**

Run: `node --check frontend/static/event-modules.js && node --test tests/event-modules-state.test.mjs`
Expected: PASS.

- [ ] **Step 6: Run template tests**

Run: `docker compose --profile test run --rm tests pytest -q tests/test_event_modules_template.py tests/test_event_plan_template.py`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/templates/event_modules.html frontend/static/event-modules.js frontend/static/event-modules.css frontend/templates/event_plan.html api/main.py tests/test_event_modules_template.py tests/test_event_plan_template.py
git commit -m "feat: add full-page module assignment workspace"
```

### Task 7: End-to-end verification and documentation alignment

**Files:**
- Modify: `CLAUDE.md`
- Modify: `README.md`
- Test: all affected suites

**Interfaces:**
- Consumes: completed feature.
- Produces: documented provider-neutral module-plan contract and verified release candidate.

- [ ] **Step 1: Update architecture documentation**

Document `Event.module_plan`, canonical stable VM IDs, blue pin-plus-random behavior, red manual-only behavior, preview stability, and the `resolved_module_ids()` AWS integration seam. State explicitly that current provider provisioning does not consume it.

- [ ] **Step 2: Run all targeted checks**

Run: `node --check frontend/static/event-modules.js && node --check frontend/static/event-modules-state.js && node --test tests/event-modules-state.test.mjs tests/event-planner-state.test.mjs`
Expected: PASS.

- [ ] **Step 3: Run the disposable Docker suite**

Run: `docker compose --profile test run --rm tests`
Expected: PASS.

- [ ] **Step 4: Run repository hygiene checks**

Run: `git diff --check && git status --short`
Expected: no whitespace errors; only intended changes are present.

- [ ] **Step 5: Commit documentation and any verification fixes**

```bash
git add CLAUDE.md README.md
git commit -m "docs: describe planned module assignments"
```
