# Planner Address Annotations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add free-form, display-only address ranges to workload zones and addresses to planned VMs in the event network planner.

**Architecture:** Persist `address_range` and `address` as optional strings in the existing canonical infrastructure JSON. The planner controller edits those fields and passes them as presentation data to the canvas; the canvas renders them without interpreting them, while provisioning continues to ignore them.

**Tech Stack:** Python 3, FastAPI/SQLAlchemy infrastructure JSON, vanilla JavaScript ES modules, D3/SVG, CSS, Node test runner, pytest in Docker Compose.

## Global Constraints

- Address values are free-form and values such as `x.x.{{team_id}}.x` must be preserved without IP, CIDR, placeholder, or template syntax validation.
- Address annotations are display-only and must not affect provisioning, generated VM addresses, firewall configuration, preview sizing, or runtime behavior.
- Only workload zones receive `address_range`; only individual VM endpoint records receive `address`.
- Gateway, site, Firewall Zone, and primary firewall address controls are out of scope.
- Existing plans without annotations and legacy count-based endpoint groups must remain supported.
- Read-only plans display annotations but do not allow editing.

---

### Task 1: Accept and Preserve Address Annotation Fields

**Files:**
- Modify: `tests/test_event_plan_template.py`
- Modify: `tests/event-planner-state.test.mjs`
- Modify: `builder/infrastructure_validation.py`
- Modify: `frontend/static/event-planner-state.js`

**Interfaces:**
- Consumes: existing `validate_infrastructure(infrastructure, valid_base_ids)` and `validateClientInfrastructure(value, catalogues)` validation paths.
- Produces: optional `zone.address_range: string` and `endpoint.address: string` fields accepted unchanged; non-string values produce field-specific errors.

- [ ] **Step 1: Write failing backend validation tests**

Add tests that build `default_infrastructure()`, assign `"x.x.{{team_id}}.0/24"` to `sites[0].zones[0].address_range` and `"x.x.{{team_id}}.10"` to its first endpoint's `address`, and assert `validate_infrastructure(...) == []`. Add a second test assigning objects/numbers and assert errors name `sites[0].zones[0].address_range` and `sites[0].zones[0].endpoints[0].address` as optional strings.

- [ ] **Step 2: Run backend tests to verify they fail**

Run: `docker compose --profile test run --rm tests pytest -q tests/test_event_plan_template.py`

Expected: FAIL because annotation types are not yet validated.

- [ ] **Step 3: Write failing client state tests**

Add a test passing arbitrary template strings through `normalizeClientInfrastructure` and asserting both fields remain unchanged. Add validation assertions that strings produce no address-specific errors while non-string values produce errors at the exact zone and endpoint paths.

- [ ] **Step 4: Run client state tests to verify they fail**

Run: `node --test tests/event-planner-state.test.mjs`

Expected: FAIL because `validateClientInfrastructure` does not yet report invalid annotation types.

- [ ] **Step 5: Implement minimal backend and client validation**

In `validate_infrastructure`, add only type checks:

```python
address_range = zone.get("address_range")
if address_range is not None and not isinstance(address_range, str):
    errors.append(f"{zpath}.address_range must be a string")
```

For each endpoint, add the equivalent check for `address`. Do not use `ipaddress`, regexes, trimming, or format constraints.

In `validateClientInfrastructure`, add a helper or direct checks that append `Address range must be text` and `Address must be text` only when a present value is not a string. Do not normalize valid strings.

- [ ] **Step 6: Run focused state and backend tests**

Run: `node --test tests/event-planner-state.test.mjs`

Run: `docker compose --profile test run --rm tests pytest -q tests/test_event_plan_template.py`

Expected: PASS.

- [ ] **Step 7: Commit Task 1**

```bash
git add builder/infrastructure_validation.py frontend/static/event-planner-state.js tests/event-planner-state.test.mjs tests/test_event_plan_template.py
git commit -m "feat: accept planner address annotations"
```

### Task 2: Add Inspector Editing and Canvas Presentation Data

**Files:**
- Modify: `tests/test_event_plan_template.py`
- Modify: `frontend/static/event-planner.js`

**Interfaces:**
- Consumes: `zone.address_range`, `endpoint.address`, the existing `field(name, label, value, options)` helper, and `createPlannerCanvas.render(graph, layout)`.
- Produces: inspector inputs named `address_range` and `address`; graph nodes with `annotation: string | null`.

- [ ] **Step 1: Write failing controller/template assertions**

Extend the planner controller source test to assert that `field('address_range','Address range',value.address_range)` appears only in the zone inspector branch, `field('address','Address',value.address)` appears only in the VM branch, and rendered graph objects include `annotation:node.type==='zone'?node.value.address_range:node.type==='vm'?node.value.address:null`.

- [ ] **Step 2: Run the focused backend source test to verify it fails**

Run: `docker compose --profile test run --rm tests pytest -q tests/test_event_plan_template.py`

Expected: FAIL because the inspector inputs and presentation property are absent.

- [ ] **Step 3: Implement inspector fields and graph metadata**

Update the workload-zone inspector branch to append:

```javascript
field('address_range','Address range',value.address_range)
```

Update only the VM inspector branch to append:

```javascript
field('address','Address',value.address)
```

Use `value.address_range ?? ''` and `value.address ?? ''` if needed to prevent the literal text `undefined`. Existing input binding supplies disabled read-only behavior and writes arbitrary strings through `updateSelected`.

In `renderCanvas`, pass each zone's range and each VM's address as `annotation`, with `null` for all other node types.

- [ ] **Step 4: Run controller tests and syntax check**

Run: `docker compose --profile test run --rm tests pytest -q tests/test_event_plan_template.py`

Run: `node --check frontend/static/event-planner.js`

Expected: PASS.

- [ ] **Step 5: Commit Task 2**

```bash
git add frontend/static/event-planner.js tests/test_event_plan_template.py
git commit -m "feat: edit planner address annotations"
```

### Task 3: Render Address Annotations on Topology Nodes

**Files:**
- Modify: `tests/event-planner-canvas.test.mjs`
- Modify: `tests/test_event_plan_template.py`
- Modify: `frontend/static/event-planner-canvas.js`
- Modify: `frontend/static/event-planner.css`

**Interfaces:**
- Consumes: graph-node `annotation: string | null` from Task 2.
- Produces: exported `topologyAccessibleLabel(node): string` and `truncatedAnnotation(value, maxLength): string`; SVG `.zone-container-address` and `.topo-node-address` text elements.

- [ ] **Step 1: Write failing pure canvas tests**

Add tests asserting:

```javascript
assert.equal(canvas.topologyAccessibleLabel({type:'zone',label:'Corporate',annotation:'x.x.{{team_id}}.0/24'}), 'zone: Corporate, address x.x.{{team_id}}.0/24');
assert.equal(canvas.topologyAccessibleLabel({type:'vm',label:'Web',annotation:null}), 'vm: Web');
assert.equal(canvas.truncatedAnnotation('1234567890', 8), '12345…');
assert.equal(canvas.truncatedAnnotation('short', 8), 'short');
```

- [ ] **Step 2: Run canvas tests to verify they fail**

Run: `node --test tests/event-planner-canvas.test.mjs`

Expected: FAIL because the helpers do not exist.

- [ ] **Step 3: Implement pure presentation helpers**

Export helpers that coerce annotation values safely, omit empty strings, append `, address <value>` to accessible labels, and truncate only visual copies. Keep the full value in the SVG group's accessible label.

- [ ] **Step 4: Write failing SVG/CSS source assertions**

Extend tests to require `.zone-container-address` and `.topo-node-address` elements/classes and calls to `topologyAccessibleLabel`. Assert CSS contains both selectors and subdued secondary-text styling.

- [ ] **Step 5: Run source tests to verify they fail**

Run: `docker compose --profile test run --rm tests pytest -q tests/test_event_plan_template.py`

Expected: FAIL because annotation SVG elements and styles are absent.

- [ ] **Step 6: Render annotations in SVG and style them**

Use `topologyAccessibleLabel(d)` for zone and machine group `aria-label` values. Add a zone annotation text element in the header/meta area and a machine annotation text element below the existing machine label. Set text with D3 `.text(...)`, never `.html(...)`. Return an empty string for missing annotations so no placeholder is displayed. Adjust header/meta or node text positions only as needed to avoid overlap, and apply a subdued monospace style with pointer events disabled.

- [ ] **Step 7: Run focused tests and syntax checks**

Run: `node --test tests/event-planner-canvas.test.mjs`

Run: `node --check frontend/static/event-planner-canvas.js`

Run: `docker compose --profile test run --rm tests pytest -q tests/test_event_plan_template.py`

Expected: PASS.

- [ ] **Step 8: Commit Task 3**

```bash
git add frontend/static/event-planner-canvas.js frontend/static/event-planner.css tests/event-planner-canvas.test.mjs tests/test_event_plan_template.py
git commit -m "feat: show addresses on planner topology"
```

### Task 4: Verify Provisioning Isolation and Complete Regression Checks

**Files:**
- Modify: `tests/test_provisioning.py`
- Modify: `tests/test_event_plan_template.py`

**Interfaces:**
- Consumes: annotated infrastructure accepted by Tasks 1–3.
- Produces: regression evidence that preview summaries and provisioning continue using existing calculated addresses.

- [ ] **Step 1: Add a provisioning-isolation regression test if existing coverage is indirect**

Build an infrastructure fixture with `address_range: "display-only/{{team_id}}"` and endpoint `address: "not-an-ip"`. Exercise the existing allocation or placeholder path and assert the persisted VM private address still comes from the zone's allocated subnet, not either annotation. Keep provider calls mocked using the existing provisioning test fixtures.

- [ ] **Step 2: Run the isolation test**

Run: `docker compose --profile test run --rm tests pytest -q tests/test_provisioning.py -k address`

Expected: PASS without production provisioning changes. If it fails because production reads either annotation, remove that coupling rather than interpreting the display value.

- [ ] **Step 3: Run all planner JavaScript tests**

Run: `node --test tests/event-planner-state.test.mjs tests/event-planner-canvas.test.mjs tests/event-planner-colours.test.mjs tests/event-planner-icons.test.mjs tests/event-planner-icon-picker.test.mjs`

Expected: PASS.

- [ ] **Step 4: Run JavaScript syntax checks**

Run: `node --check frontend/static/event-planner-state.js`

Run: `node --check frontend/static/event-planner.js`

Run: `node --check frontend/static/event-planner-canvas.js`

Expected: PASS.

- [ ] **Step 5: Run relevant backend tests in the disposable test service**

Run: `docker compose --profile test run --rm tests pytest -q tests/test_event_plan_template.py tests/test_provisioning.py`

Expected: PASS.

- [ ] **Step 6: Run the full project verification suite**

Run: `docker compose --profile test run --rm tests`

Expected: PASS.

- [ ] **Step 7: Check the final diff**

Run: `git diff --check`

Run: `git status --short`

Expected: no whitespace errors and only intended feature/test/plan changes.

- [ ] **Step 8: Commit final regression coverage**

```bash
git add tests/test_provisioning.py tests/test_event_plan_template.py
git commit -m "test: verify planner addresses stay display only"
```
