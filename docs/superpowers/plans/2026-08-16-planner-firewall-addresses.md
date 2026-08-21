# Planner Firewall Addresses Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend display-only planner addresses to the system-managed Firewall Zone and its primary firewall.

**Architecture:** Persist the Firewall Zone range as `site.firewall_zone_address_range` and the firewall address as `site.firewall.address`. Extend the existing address helper so inspector and canvas consumers receive the same field/annotation model already used by workload zones and VMs; extend shared canvas geometry to treat annotated Firewall Zones and firewalls identically to their workload counterparts.

**Tech Stack:** Python infrastructure validation, vanilla JavaScript ES modules, D3/SVG, Node test runner, Docker Compose pytest suite.

## Global Constraints

- Firewall Zone uses a free-form address range; primary firewall uses a free-form individual address.
- Values such as `x.x.{{team_id}}.x` remain unchanged and receive no IP, CIDR, placeholder, or template syntax validation.
- Values are display-only and do not affect provisioned subnets, firewall addresses, preview sizing, or runtime behavior.
- Firewall Zone uses the existing full-width subnet rail and geometry-aware spacing.
- Primary firewall address sits beneath its name, inside its machine bounds, and uses the same text colour as its name.
- Existing plans without these fields remain valid.

---

### Task 1: Persist and Expose Firewall Address Fields

**Files:**
- Modify: `tests/test_event_plan_template.py`
- Modify: `tests/event-planner-state.test.mjs`
- Modify: `tests/event-planner-addresses.test.mjs`
- Modify: `builder/infrastructure_validation.py`
- Modify: `frontend/static/event-planner-state.js`
- Modify: `frontend/static/event-planner-addresses.js`
- Modify: `frontend/static/event-planner.js`

**Interfaces:**
- Consumes: node shapes from `nodeIndex`: Firewall Zone `{type:'firewall-zone', value:site}` and primary firewall `{type:'firewall', value:site.firewall}`.
- Produces: `addressFieldForNode` results for `firewall_zone_address_range` and `address`; `addressAnnotationForNode` values used by inspector and canvas.

- [ ] **Step 1: Write failing backend validation tests**

Extend the infrastructure annotation tests with `site["firewall_zone_address_range"] = "10.0.{{team_id}}.0/24"` and `site["firewall"]["address"] = "10.0.{{team_id}}.1"`; assert arbitrary strings pass. Add non-string values and assert exact errors for `sites[0].firewall_zone_address_range` and `sites[0].firewall.address`.

- [ ] **Step 2: Run backend test and verify RED**

Run: `docker compose --profile test run --rm --build tests pytest -q tests/test_event_plan_template.py`

Expected: FAIL because firewall annotation types are not validated.

- [ ] **Step 3: Write failing client helper/state tests**

Assert `addressFieldForNode({type:'firewall-zone', value:site})` returns `{name:'firewall_zone_address_range', label:'Address range', value:<range>}` and the firewall node returns `{name:'address', label:'Address', value:<address>}`. Assert both annotations are returned for canvas rendering. Add client validation tests for non-string values at the matching infrastructure paths.

- [ ] **Step 4: Run client tests and verify RED**

Run: `node --test tests/event-planner-addresses.test.mjs tests/event-planner-state.test.mjs`

Expected: FAIL because firewall nodes currently return no address field or annotation and client validation ignores the fields.

- [ ] **Step 5: Implement minimal validation and helper support**

In backend/client validation, accept missing values and arbitrary strings, rejecting only present non-strings. Extend `addressFieldForNode`:

```javascript
if (node?.type === 'firewall-zone') {
  return {name: 'firewall_zone_address_range', label: 'Address range', value: node.value.firewall_zone_address_range ?? ''};
}
if (node?.type === 'firewall') {
  return {name: 'address', label: 'Address', value: node.value.address ?? ''};
}
```

The existing inspector update path writes these fields into the correct node values. Rename the Firewall Zone system-detail label from `Infrastructure subnet` to `Provisioned subnet`; keep `Automatically allocated` unchanged.

- [ ] **Step 6: Run focused Task 1 tests and syntax checks**

Run: `node --test tests/event-planner-addresses.test.mjs tests/event-planner-state.test.mjs`

Run: `node --check frontend/static/event-planner-addresses.js`

Run: `node --check frontend/static/event-planner-state.js`

Run: `node --check frontend/static/event-planner.js`

Run: `docker compose --profile test run --rm --build tests pytest -q tests/test_event_plan_template.py`

Expected: PASS.

- [ ] **Step 7: Commit Task 1**

```bash
git add builder/infrastructure_validation.py frontend/static/event-planner-state.js frontend/static/event-planner-addresses.js frontend/static/event-planner.js tests/event-planner-addresses.test.mjs tests/event-planner-state.test.mjs tests/test_event_plan_template.py
git commit -m "feat: assign planner firewall addresses"
```

### Task 2: Reuse Address Geometry and Prove Provisioning Isolation

**Files:**
- Modify: `tests/event-planner-canvas.test.mjs`
- Modify: `tests/test_gamenet.py`
- Modify: `frontend/static/event-planner-canvas.js`

**Interfaces:**
- Consumes: Firewall Zone and firewall graph nodes carrying `annotation` through `addressAnnotationForNode`.
- Produces: Firewall Zone rail height of 60px when annotated; primary firewall bounds of 84px when annotated.

- [ ] **Step 1: Write failing Firewall Zone and firewall geometry tests**

Assert `zoneHeaderHeight({type:'firewall-zone', annotation:'10.0.0.0/24'}) === 60`, an annotated primary firewall receives `machineBounds(...).height === 84`, and `arrangeZoneChildren` places that firewall below the rail. Retain compact 36px/72px geometry when annotations are absent.

- [ ] **Step 2: Run canvas tests and verify RED**

Run: `node --test tests/event-planner-canvas.test.mjs`

Expected: FAIL because geometry currently expands only `zone` and `vm` node types.

- [ ] **Step 3: Generalize existing annotation-aware geometry**

Make `zoneHeaderHeight` consider both `zone` and `firewall-zone`. Make `machineBounds` consider both `vm` and `firewall`. Do not duplicate rail rendering or machine-label rendering; the existing generic D3 paths must render both node types.

- [ ] **Step 4: Add provisioning-isolation coverage**

Extend the existing display-only allocation regression fixture with `firewall_zone_address_range` and `firewall.address` values that are deliberately not valid runtime addresses. Assert allocated site subnets and created firewall/runtime VM addresses continue to come from deterministic allocation rather than these annotations.

- [ ] **Step 5: Run all focused planner and GameNet tests**

Run: `node --test tests/event-planner-state.test.mjs tests/event-planner-addresses.test.mjs tests/event-planner-canvas.test.mjs tests/event-planner-colours.test.mjs tests/event-planner-icons.test.mjs tests/event-planner-icon-picker.test.mjs`

Run: `docker compose --profile test run --rm --build tests pytest -q tests/test_event_plan_template.py tests/test_gamenet.py`

Expected: PASS.

- [ ] **Step 6: Rebuild and verify the live frontend**

Run: `API_PORT=8091 docker compose up --detach --build`

Confirm the live CSS/JavaScript assets serve the Firewall Zone rail and annotated firewall geometry, and the frontend root returns HTTP 200.

- [ ] **Step 7: Run final regression verification**

Run: `docker compose --profile test run --rm --build tests`

Run: `git diff --check`

Expected: all tests pass and no whitespace errors.

- [ ] **Step 8: Commit Task 2**

```bash
git add frontend/static/event-planner-canvas.js tests/event-planner-canvas.test.mjs tests/test_gamenet.py
git commit -m "test: keep firewall annotations display only"
```
