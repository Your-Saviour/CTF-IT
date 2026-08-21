# Firewall Zone Topology Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Represent every site firewall as a Primary Firewall VM inside an automatic Firewall Zone, with workload-zone traffic links originating from that routing zone.

**Architecture:** Backend topology ID generation will expose the derived firewall-zone and collection-ready primary-firewall IDs without changing infrastructure JSON or provisioning. Pure client state functions will separate data ownership from visual parentage and migrate legacy layout IDs. The controller will consume those semantics for canvas edges, contextual actions, and system-managed inspector content.

**Tech Stack:** Python, FastAPI infrastructure helpers, browser ES modules, D3.js, Node test runner, pytest, Docker Compose.

## Global Constraints

- Do not change the canonical `site.firewall` or `site.zones` JSON shapes.
- Do not change provisioning counts, address allocation, cost estimation, or VM creation.
- Use `firewall-zone:<site-key>` and `firewall:<site-key>/primary` as stable planner IDs.
- Keep workload-zone data ownership with the site and visual linkage with the Firewall Zone.
- Keep Firewall Zone and Primary Firewall VM non-deletable.
- Preserve continuous drag movement and release-time layout persistence.

---

### Task 1: Backend Stable Topology IDs

**Files:**
- Modify: `builder/infrastructure_planner.py`
- Modify: `tests/test_gamenet.py`

**Interfaces:**
- Consumes: normalized infrastructure with `sites[].key`, `sites[].firewall`, and workload zones.
- Produces: `infrastructure_node_ids(infrastructure) -> set[str]` containing `firewall-zone:<site>` and `firewall:<site>/primary`.

- [ ] **Step 1: Write the failing backend ID test**

```python
def test_layout_ids_model_firewall_as_vm_inside_automatic_zone():
    ids = infrastructure_node_ids(normalize_infrastructure(INFRASTRUCTURE))
    assert "firewall-zone:head_office" in ids
    assert "firewall:head_office/primary" in ids
    assert "firewall:head_office" not in ids
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `docker compose --profile test run --rm --build tests pytest -q tests/test_gamenet.py::test_layout_ids_model_firewall_as_vm_inside_automatic_zone`

Expected: FAIL because the helper still emits `firewall:head_office`.

- [ ] **Step 3: Implement the new IDs**

In `infrastructure_node_ids`, replace the old firewall ID with:

```python
result.update({
    f"site:{site_key}",
    f"firewall-zone:{site_key}",
    f"firewall:{site_key}/primary",
})
```

- [ ] **Step 4: Run backend planner and provisioning regression tests**

Run: `docker compose --profile test run --rm --build tests pytest -q tests/test_gamenet.py -k 'layout or materialises'`

Expected: all selected tests PASS after updating existing layout fixtures to the new firewall ID where applicable; VM counts remain unchanged.

- [ ] **Step 5: Commit**

```bash
git add builder/infrastructure_planner.py tests/test_gamenet.py
git commit -m "feat: add firewall zone planner ids"
```

### Task 2: Client Topology and Layout Normalization

**Files:**
- Modify: `frontend/static/event-planner-state.js`
- Modify: `tests/event-planner-state.test.mjs`

**Interfaces:**
- Produces: `nodeIndex(infrastructure) -> Map<string, PlannerNode>` where `PlannerNode` includes `parent` for data ownership and `visualParent` for canvas edges.
- Produces: `normalizeClientLayout(layout, infrastructure) -> {version: 1, nodes: Record<string, Position>}`.
- Preserves: `renameStructuralKey`, `pruneLayout`, and `validateClientInfrastructure`.

- [ ] **Step 1: Write failing executable state tests**

Add tests that assert:

```javascript
const index = nodeIndex(infrastructure);
assert.equal(index.get('firewall-zone:head_office').type, 'firewall-zone');
assert.equal(index.get('firewall:head_office/primary').parent, 'firewall-zone:head_office');
assert.equal(index.get('zone:head_office/corporate').parent, 'site:head_office');
assert.equal(index.get('zone:head_office/corporate').visualParent, 'firewall-zone:head_office');
```

Add a layout test passing `firewall:head_office` and asserting its coordinates move to `firewall:head_office/primary`, the legacy key disappears, and unrelated coordinates remain.

Add a site rename test asserting coordinates for `firewall-zone:head_office`, `firewall:head_office/primary`, workload zones, and VMs all remap to the new site key.

- [ ] **Step 2: Run Node tests and verify RED**

Run: `node --test tests/event-planner-state.test.mjs`

Expected: FAIL because the derived nodes, visual parents, and layout migration do not exist.

- [ ] **Step 3: Implement derived nodes and layout migration**

For each site, add these records before workload zones:

```javascript
map.set(`firewall-zone:${site.key}`, {
  type: 'firewall-zone', value: site, parent: sid, visualParent: sid, path: `sites[${si}]`,
});
map.set(`firewall:${site.key}/primary`, {
  type: 'firewall', value: site.firewall, parent: `firewall-zone:${site.key}`,
  visualParent: `firewall-zone:${site.key}`, path: `sites[${si}].firewall`, site,
});
```

Workload-zone records keep `parent: sid` and add `visualParent: firewall-zone:<site>`. Workload VMs retain their workload zone for both properties.

Implement `normalizeClientLayout` as a clone that maps each legacy `firewall:<site>` key to `firewall:<site>/primary` unless the new key already exists. Extend site-key remapping prefixes to include `firewall-zone:` and the `/primary` firewall ID.

- [ ] **Step 4: Run executable state tests and JS syntax checks**

Run:

```bash
node --test tests/event-planner-state.test.mjs
node --check frontend/static/event-planner-state.js
```

Expected: all tests and syntax checks PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/static/event-planner-state.js tests/event-planner-state.test.mjs
git commit -m "feat: model automatic firewall zones"
```

### Task 3: Controller, Inspector, and Canvas Edges

**Files:**
- Modify: `frontend/static/event-planner.js`
- Modify: `frontend/static/event-planner.css`
- Modify: `tests/test_event_plan_template.py`

**Interfaces:**
- Consumes: `normalizeClientLayout`, `PlannerNode.parent`, and `PlannerNode.visualParent` from Task 2.
- Produces: graph rows whose `parent` field is the visual edge source expected by `createPlannerCanvas`.

- [ ] **Step 1: Write failing controller contract tests**

Assert the controller imports and calls `normalizeClientLayout`, passes `node.visualParent||node.parent` to the canvas, renders a `firewall-zone` inspector branch with `System managed` and `Automatically allocated`, and includes `firewall-zone`/`firewall` in Add Zone site-context handling without enabling Add VM for those types.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `docker compose --profile test run --rm --build tests pytest -q tests/test_event_plan_template.py`

Expected: FAIL on the new controller contracts.

- [ ] **Step 3: Implement controller behavior**

Normalize event layout before creating the store:

```javascript
const infrastructure = normalizeClientInfrastructure(event.infrastructure);
const layout = normalizeClientLayout(event.infrastructure_layout, infrastructure);
store = createPlannerStore({infrastructure, layout});
```

Use `node.visualParent || node.parent` when building canvas graph rows. Add the automatic zone inspector showing its region, system-managed status, automatic infrastructure-subnet allocation, and routing explanation. Keep firewall fields on the Primary Firewall VM. Derive Add Zone site context from the selected node's site record; keep Add VM limited to `zone` and `vm`.

Add a distinct `.topo-node.firewall-zone rect` or equivalent class styling using existing infrastructure colors and no new palette.

- [ ] **Step 4: Run focused frontend tests**

Run:

```bash
node --check frontend/static/event-planner.js
node --check frontend/static/event-planner-canvas.js
node --test tests/event-planner-state.test.mjs
docker compose --profile test run --rm --build tests pytest -q tests/test_event_plan_template.py
```

Expected: all commands exit 0.

- [ ] **Step 5: Commit**

```bash
git add frontend/static/event-planner.js frontend/static/event-planner.css tests/test_event_plan_template.py
git commit -m "feat: render firewall routing zones"
```

### Task 4: Full Verification and Local Deployment

**Files:**
- Verify only.

**Interfaces:**
- Produces: a clean, reviewed branch and rebuilt local planner at `http://localhost:8091/`.

- [ ] **Step 1: Run the full disposable suite**

Run: `docker compose --profile test run --rm --build tests`

Expected: 0 failures and unchanged provisioning-count tests passing.

- [ ] **Step 2: Run final frontend checks**

Run: `node --test tests/event-planner-state.test.mjs && node --check frontend/static/event-planner-state.js && node --check frontend/static/event-planner.js && node --check frontend/static/event-planner-canvas.js && git diff --check`

Expected: all commands exit 0.

- [ ] **Step 3: Request focused code review**

Review the implementation against `docs/superpowers/specs/2026-08-15-firewall-zone-topology-design.md`; fix all Critical and Important findings and rerun verification.

- [ ] **Step 4: Rebuild localhost**

Run: `API_PORT=8091 docker compose up --detach --build api` followed by a retrying curl request to `http://localhost:8091/`.

Expected: HTTP `200` and the API container Up on port 8091.
