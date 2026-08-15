# Event Planner Zone Containers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render workload and Firewall Zones as movable framed containers that hold freely draggable VMs and can arrange their children into a deterministic grid.

**Architecture:** Keep infrastructure membership and stable IDs in `event-planner-state.js`; add pure container geometry functions and D3 interaction/rendering in `event-planner-canvas.js`. The controller enriches graph rows with container metadata and persists the canvas's atomic layout callbacks through the existing store. Container dimensions remain derived presentation state, so backend schemas and provisioning stay unchanged.

**Tech Stack:** Browser ES modules, D3.js 7, SVG, CSS, Node.js `node:test`, pytest template-contract tests.

## Global Constraints

- Do not change the canonical infrastructure JSON, provisioning, addressing, stable node IDs, or backend APIs.
- VM positions remain global canvas coordinates; container width and height are never serialized.
- Canvas overlap never changes VM membership.
- Workload and Firewall Zones use the same framed-container interaction model.
- Read-only plans emit no layout mutation callbacks.
- Existing planner dark-theme tokens and real infrastructure labels remain authoritative.

## File Structure

- `frontend/static/event-planner-canvas.js` — pure geometry helpers, container/link rendering, free VM dragging, group movement, arrangement, and atomic layout callbacks.
- `frontend/static/event-planner.js` — graph projection metadata and persistence callback wiring.
- `frontend/static/event-planner.css` — framed-workspace, team/system, selected, invalid, and read-only presentation.
- `tests/event-planner-canvas.test.mjs` — executable geometry and layout behavior tests.
- `tests/test_event_plan_template.py` — source-contract coverage for controller, canvas accessibility, controls, and CSS.

---

### Task 1: Pure Zone Geometry and Grid Packing

**Files:**
- Modify: `frontend/static/event-planner-canvas.js`
- Test: `tests/event-planner-canvas.test.mjs`

**Interfaces:**
- Consumes: graph rows shaped as `{id, type, parent}` and layout `{version: 1, nodes: Record<string, {x: number, y: number}>}`.
- Produces: `ZONE_CONTAINER_GEOMETRY`, `zoneChildren(graph, zoneId)`, `calculateZoneBounds(zone, children)`, `arrangeZoneChildren(zone, children)`, and `translateZoneLayout(layout, zoneId, childIds, dx, dy)`.

- [ ] **Step 1: Write failing bounds and child-index tests**

Add tests asserting that only direct machine children belong to a container, empty containers use minimum dimensions, and child rectangles expand the right/bottom edges:

```js
test('zone bounds use minimum size and grow around direct machine children', () => {
  const zone = {id: 'zone:a/blue', x: 100, y: 200};
  assert.deepEqual(canvas.calculateZoneBounds(zone, []), {
    x: 100, y: 200, width: 280, height: 190,
  });
  assert.deepEqual(canvas.calculateZoneBounds(zone, [
    {id: 'vm:a/blue/web', type: 'vm', x: 430, y: 390},
  ]), {x: 100, y: 200, width: 390, height: 246});

  const graph = [
    {id: 'zone:a/blue', type: 'zone'},
    {id: 'vm:a/blue/web', type: 'vm', parent: 'zone:a/blue'},
    {id: 'zone:a/red', type: 'zone'},
    {id: 'vm:a/red/kali', type: 'vm', parent: 'zone:a/red'},
  ];
  assert.deepEqual(canvas.zoneChildren(graph, 'zone:a/blue').map(row => row.id), [
    'vm:a/blue/web',
  ]);
});
```

- [ ] **Step 2: Run the focused test and confirm the missing exports fail**

Run: `node --test --test-name-pattern="zone bounds" tests/event-planner-canvas.test.mjs`

Expected: FAIL because `calculateZoneBounds` and `zoneChildren` are not exported.

- [ ] **Step 3: Implement fixed geometry constants and bounds calculation**

Add immutable geometry constants for a 36 px header, 20 px padding, 80 × 72 px machine cells, 24 px gaps, and 280 × 190 minimum container size. Implement bounds from the zone's top-left anchor and the maximum child machine rectangle. Keep top and left fixed so dragging a child expands only right or bottom.

```js
export const ZONE_CONTAINER_GEOMETRY = Object.freeze({
  headerHeight: 36, padding: 20, machineWidth: 80, machineHeight: 72,
  columnGap: 24, rowGap: 24, minWidth: 280, minHeight: 190,
});

export function zoneChildren(graph, zoneId) {
  return graph.filter(node => node.parent === zoneId && ['vm', 'firewall'].includes(node.type));
}
```

- [ ] **Step 4: Write failing deterministic arrangement and translation tests**

```js
test('zone arrangement packs children deterministically and translation is atomic', () => {
  const zone = {id: 'zone:a/blue', x: 100, y: 200};
  const children = ['one', 'two', 'three', 'four', 'five'].map(id => ({id, type: 'vm'}));
  const arranged = canvas.arrangeZoneChildren(zone, children);
  assert.deepEqual(arranged.one, {x: 160, y: 292});
  assert.deepEqual(arranged.two, {x: 264, y: 292});
  assert.deepEqual(arranged.four, {x: 160, y: 388});

  const moved = canvas.translateZoneLayout(
    {version: 1, nodes: {'zone:a/blue': {x: 100, y: 200}, one: arranged.one}},
    'zone:a/blue', ['one'], 25, -10,
  );
  assert.deepEqual(moved.nodes['zone:a/blue'], {x: 125, y: 190});
  assert.deepEqual(moved.nodes.one, {x: 185, y: 282});
});
```

- [ ] **Step 5: Run the focused test and confirm the missing exports fail**

Run: `node --test --test-name-pattern="zone arrangement" tests/event-planner-canvas.test.mjs`

Expected: FAIL because `arrangeZoneChildren` and `translateZoneLayout` are not exported.

- [ ] **Step 6: Implement deterministic packing and immutable translation**

Use `Math.ceil(Math.sqrt(children.length))` columns, existing graph order, and cell centres inside the header/padding area. Return an ID-to-coordinate object from arrangement. Deep-copy `layout.nodes` before translating the zone and every listed child so callers receive one complete layout object.

- [ ] **Step 7: Run all canvas unit tests**

Run: `node --test tests/event-planner-canvas.test.mjs`

Expected: PASS.

- [ ] **Step 8: Commit the geometry unit**

```bash
git add frontend/static/event-planner-canvas.js tests/event-planner-canvas.test.mjs
git commit -m "feat: add planner zone container geometry"
```

---

### Task 2: Render Framed Containers and Boundary-Routed Links

**Files:**
- Modify: `frontend/static/event-planner-canvas.js`
- Modify: `frontend/static/event-planner.css`
- Test: `tests/event-planner-canvas.test.mjs`
- Test: `tests/test_event_plan_template.py`

**Interfaces:**
- Consumes: Task 1's `zoneChildren()` and `calculateZoneBounds()` plus graph rows with `team`, `systemManaged`, `childCount`, `selected`, and `invalid`.
- Produces: `topologyLinks(nodes)`, `linkEndpoints(link, containerBounds)`, SVG groups `.zone-container`, `.zone-container-header`, `.zone-arrange`, and machine-only child containment.

- [ ] **Step 1: Write failing executable link tests**

```js
test('topology links omit contained machines and target container boundaries', () => {
  const nodes = [
    {id: 'site:a', type: 'site', x: 0, y: 0},
    {id: 'zone:a/blue', type: 'zone', parent: 'site:a', x: 100, y: 100},
    {id: 'vm:a/blue/web', type: 'vm', parent: 'zone:a/blue', x: 160, y: 200},
  ];
  const links = canvas.topologyLinks(nodes);
  assert.deepEqual(links.map(link => [link.source.id, link.target.id]), [
    ['site:a', 'zone:a/blue'],
  ]);
  const points = canvas.linkEndpoints(links[0], new Map([
    ['zone:a/blue', {x: 100, y: 100, width: 280, height: 190}],
  ]));
  assert.equal(points.x2, 100);
  assert.equal(points.y2 >= 100 && points.y2 <= 290, true);
});
```

- [ ] **Step 2: Run focused tests and confirm they fail**

Run: `node --test --test-name-pattern="topology links" tests/event-planner-canvas.test.mjs`

Expected: FAIL because `topologyLinks` and `linkEndpoints` are not exported.

- [ ] **Step 3: Implement container-aware links and rendering order**

Build links only when the target is not a contained `vm` or `firewall`. Calculate the intersection between the source-to-target line and the target container rectangle. In `render()`, create layers in this exact order:

```js
const linkLayer = scene.append('g').attr('class', 'topology-links');
const containerLayer = scene.append('g').attr('class', 'topology-containers');
const machineLayer = scene.append('g').attr('class', 'topology-machines');
```

Render each `zone` and `firewall-zone` as a `.zone-container` group anchored at its saved coordinate, with body rectangle, 36 px header rectangle, real name/status/count text, and a `foreignObject`-free SVG button group for `Arrange VMs`. Continue rendering gateway/site structural cards separately and render firewall/VM nodes in the machine layer.

- [ ] **Step 4: Add failing template/CSS contract assertions**

Extend `test_machine_nodes_render_as_standalone_icons_with_labels_below` or add a focused test asserting:

```python
assert "zone-container-header" in canvas
assert "zone-arrange" in canvas
assert "topology-containers" in canvas
assert "topology-machines" in canvas
assert ".zone-container.team-red" in css
assert ".zone-container.system-managed" in css
assert ".zone-container.selected" in css
assert ".zone-container.invalid" in css
```

- [ ] **Step 5: Run the contract test and confirm it fails**

Run: `pytest -q tests/test_event_plan_template.py -k zone_container`

Expected: FAIL because the new SVG classes and CSS rules are absent.

- [ ] **Step 6: Implement framed-workspace CSS**

Use existing `--bg-*`, `--border`, `--cyan*`, `--red*`, `--amber`, and text tokens. Give the container a solid frame and darker header; use cyan/blue treatment for blue zones, red treatment for red zones, and amber/cyan system styling for the Firewall Zone. Add visible selected and invalid outlines, a minimum 44 px arrange-button hit target, and pointer-event rules so the body does not steal VM drags.

- [ ] **Step 7: Run canvas and template tests**

Run: `node --test tests/event-planner-canvas.test.mjs && pytest -q tests/test_event_plan_template.py`

Expected: PASS.

- [ ] **Step 8: Commit container rendering**

```bash
git add frontend/static/event-planner-canvas.js frontend/static/event-planner.css tests/event-planner-canvas.test.mjs tests/test_event_plan_template.py
git commit -m "feat: render planner zones as containers"
```

---

### Task 3: Wire Container Metadata and Automatic Arrangement

**Files:**
- Modify: `frontend/static/event-planner.js`
- Modify: `frontend/static/event-planner-canvas.js`
- Test: `tests/event-planner-canvas.test.mjs`
- Test: `tests/test_event_plan_template.py`

**Interfaces:**
- Consumes: `createPlannerCanvas(svgElement, {onSelect, onLayoutChange, onArrangeZone, readOnly})` and Task 1 arrangement helpers.
- Produces: enriched canvas graph rows and an `onArrangeZone(zoneId)` callback that saves one complete layout through the existing planner store.

- [ ] **Step 1: Write failing controller contract tests**

Add assertions that `renderCanvas()` supplies exact real-data metadata and that arrangement persists via the existing state store:

```python
assert "systemManaged:node.type==='firewall-zone'" in compact
assert "team:node.type==='zone'?node.value.team:null" in compact
assert "childCount" in source
assert "onArrangeZone" in source
assert "state.layout=nextLayout" in compact
```

- [ ] **Step 2: Run the controller contract test and confirm it fails**

Run: `pytest -q tests/test_event_plan_template.py -k planner_renders_system_firewall_zone`

Expected: FAIL because graph rows lack container metadata and arrangement wiring.

- [ ] **Step 3: Enrich graph rows and wire one atomic arrangement callback**

In `renderCanvas()`, calculate direct machine child counts from the node index and include:

```js
{
  id,
  parent: node.visualParent || node.parent,
  type: node.type,
  label: nodeLabel(node),
  team: node.type === 'zone' ? node.value.team : null,
  systemManaged: node.type === 'firewall-zone',
  childCount,
  // existing icons/selected/invalid fields
}
```

Construct the canvas with `onArrangeZone: zoneId => store.update(state => ({...state, layout: canvas.arrangedLayout(zoneId)}))`. Have `arrangedLayout(zoneId)` return the current complete layout with Task 1's packed child coordinates, without emitting a second callback. Hide the SVG arrange control when `readOnly` is true.

- [ ] **Step 4: Write failing tests for the public arrangement calculation**

Export `arrangedZoneLayout(graph, layout, zoneId)` as a pure function used by the canvas method. Test it directly with workload-zone, Firewall Zone, and unrelated machine rows. Assert that the workload zone changes only its direct VMs, the Firewall Zone changes its Primary Firewall, and an unknown/non-zone ID returns a structurally equal cloned layout.

- [ ] **Step 5: Run the focused arrangement tests and confirm they fail**

Run: `node --test --test-name-pattern="arranged layout" tests/event-planner-canvas.test.mjs`

Expected: FAIL until the public arrangement method exists.

- [ ] **Step 6: Implement the public arrangement method and accessible action**

Expose `arrangedLayout(zoneId)` from the canvas object returned by `createPlannerCanvas()`. Give each arrange control `role="button"`, `tabindex="0"`, and an `aria-label` of `Arrange VMs in ${zone.label}`. On click, Enter, or Space, stop propagation and call `callbacks.onArrangeZone(zone.id)`; never start zone drag from the control.

- [ ] **Step 7: Run controller, canvas, and syntax checks**

Run: `node --check frontend/static/event-planner.js && node --check frontend/static/event-planner-canvas.js && node --test tests/event-planner-canvas.test.mjs && pytest -q tests/test_event_plan_template.py`

Expected: PASS.

- [ ] **Step 8: Commit arrangement wiring**

```bash
git add frontend/static/event-planner.js frontend/static/event-planner-canvas.js tests/event-planner-canvas.test.mjs tests/test_event_plan_template.py
git commit -m "feat: arrange VMs within planner zones"
```

---

### Task 4: Implement Free Child Dragging and Atomic Group Movement

**Files:**
- Modify: `frontend/static/event-planner-canvas.js`
- Test: `tests/event-planner-canvas.test.mjs`
- Test: `tests/test_event_plan_template.py`

**Interfaces:**
- Consumes: Task 1's geometry/translation helpers and current `currentLayout` inside `createPlannerCanvas()`.
- Produces: `constrainMachinePosition(position, zoneBounds)`, continuous container/link redraw during drag, and one persisted layout callback per completed machine or group drag.

- [ ] **Step 1: Write failing movement-constraint tests**

```js
test('machine movement keeps fixed zone edges and permits right-bottom expansion', () => {
  const bounds = {x: 100, y: 200, width: 280, height: 190};
  assert.deepEqual(canvas.constrainMachinePosition({x: 80, y: 210}, bounds), {x: 160, y: 292});
  assert.deepEqual(canvas.constrainMachinePosition({x: 500, y: 500}, bounds), {x: 500, y: 500});
});
```

- [ ] **Step 2: Run the focused test and confirm it fails**

Run: `node --test --test-name-pattern="machine movement" tests/event-planner-canvas.test.mjs`

Expected: FAIL because `constrainMachinePosition` is not exported.

- [ ] **Step 3: Implement machine constraints and continuous container updates**

Clamp machine centres to the zone's left padding and header-plus-padding minima. Allow arbitrary right/bottom positions so `calculateZoneBounds()` grows the frame. During machine drag, update the machine transform, recalculate its owning container rectangle/header/button positions, and reroute structural links. On drag end, update only the machine coordinate and call `onLayoutChange` exactly once.

- [ ] **Step 4: Add group-drag source-contract tests**

Assert the canvas source contains distinct header and machine drag handlers, uses Task 1's `translateZoneLayout`, updates child transforms during the pointer drag, and calls `onLayoutChange` only in the end handler. Also assert arrange-control events stop propagation.

- [ ] **Step 5: Run the group-drag contract test and confirm it fails**

Run: `pytest -q tests/test_event_plan_template.py -k zone_drag`

Expected: FAIL until group movement is wired.

- [ ] **Step 6: Implement zone-header group dragging**

Capture the starting layout and child IDs on drag start. During drag, derive the full translated layout from the original positions and current pointer delta, update the zone frame and every child transform, and reroute links. On end, assign the translated result to `currentLayout` and emit one cloned layout through `onLayoutChange`. Do not attach this D3 drag behavior in read-only mode.

- [ ] **Step 7: Run all planner frontend tests and syntax checks**

Run: `node --check frontend/static/event-planner-canvas.js && node --check frontend/static/event-planner.js && node --test tests/event-planner-state.test.mjs tests/event-planner-canvas.test.mjs tests/event-planner-icons.test.mjs tests/event-planner-icon-picker.test.mjs && pytest -q tests/test_event_plan_template.py`

Expected: PASS.

- [ ] **Step 8: Commit drag behavior**

```bash
git add frontend/static/event-planner-canvas.js tests/event-planner-canvas.test.mjs tests/test_event_plan_template.py
git commit -m "feat: move planner zones with contained VMs"
```

---

### Task 5: Integration Regression and Manual Acceptance

**Files:**
- Modify only if verification exposes a defect: `frontend/static/event-planner-canvas.js`, `frontend/static/event-planner.js`, `frontend/static/event-planner.css`, or their focused tests.

**Interfaces:**
- Consumes: completed zone-container feature.
- Produces: verified planner behavior with no backend/provisioning regression.

- [ ] **Step 1: Run whitespace and complete focused frontend verification**

Run: `git diff --check && node --check frontend/static/event-planner-state.js && node --check frontend/static/event-planner-canvas.js && node --check frontend/static/event-planner.js && node --test tests/event-planner-state.test.mjs tests/event-planner-canvas.test.mjs tests/event-planner-icons.test.mjs tests/event-planner-icon-picker.test.mjs && pytest -q tests/test_event_plan_template.py`

Expected: all checks PASS.

- [ ] **Step 2: Run backend planner regression tests**

Run: `pytest -q tests/test_event_plan_template.py tests/test_event_lifecycle.py tests/test_provisioning.py`

Expected: PASS, demonstrating unchanged planner routes, event lifecycle, and provisioning behavior.

- [ ] **Step 3: Start the existing local application stack**

Run from the repository root: `docker compose up -d --build`

Expected: the development API starts successfully. Open a draft event's `/admin/events/{id}/plan` route and confirm the page loads without browser console errors.

- [ ] **Step 4: Perform the approved interaction checks**

On a draft plan containing a workload zone with at least five VMs and a Firewall Zone:

1. Confirm both zones render as framed containers and the Primary Firewall is inside the Firewall Zone.
2. Freely drag a VM to the right/bottom and confirm its container expands.
3. Attempt to drag a VM across another zone and confirm membership does not change.
4. Drag a zone header and confirm every child preserves its relative offset.
5. Activate `Arrange VMs` by pointer and keyboard and confirm deterministic grid placement followed by free dragging.
6. Reload after saving and confirm positions persist.
7. Open a read-only plan and confirm selection works while dragging and arrangement do not.

- [ ] **Step 5: Inspect the final diff and commit any verification-only correction**

Run: `git status --short && git diff --check && git diff --stat`

Expected: only the planned frontend/test files are changed. If Step 1–4 required a correction, stage only those focused files and commit with `fix: correct planner zone container interaction`. If no correction was required, do not create an empty commit.
