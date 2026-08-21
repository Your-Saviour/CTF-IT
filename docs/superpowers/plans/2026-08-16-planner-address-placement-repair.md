# Planner Address Placement Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace overlapping planner address labels with a geometry-aware zone subnet rail and VM address text contained within the machine node.

**Architecture:** Centralize variable zone header height and machine text bounds in pure canvas geometry helpers. D3 rendering consumes those helpers, so layout, arranging, dragging, accessibility, and styling agree on the same dimensions.

**Tech Stack:** Vanilla JavaScript ES modules, D3/SVG, CSS, Node test runner, Docker Compose pytest suite.

## Global Constraints

- Workload zones with an address range show a full-width `Range · <value>` rail below the compact title header.
- Zones without an address range retain the compact header and do not reserve rail space.
- VM addresses remain beneath VM names and inside machine layout and interaction bounds.
- VM addresses use the same text colour as VM names, including invalid-state inheritance; they do not use zone or VM accent colours.
- Full values remain in accessible labels and inspectors; visual labels may truncate safely.
- Free-form values such as `x.x.{{team_id}}.x` remain unchanged and provisioning remains unaffected.

---

### Task 1: Make Address Placement Part of Canvas Geometry

**Files:**
- Modify: `tests/event-planner-canvas.test.mjs`
- Modify: `frontend/static/event-planner-canvas.js`

**Interfaces:**
- Consumes: graph nodes with `type`, `annotation`, `x`, and `y`; `ZONE_CONTAINER_GEOMETRY`; `MACHINE_ICON_GEOMETRY`.
- Produces: `zoneHeaderHeight(zone): number`, `machineBounds(node): {x, y, width, height}`, and geometry results that contain annotation presentation coordinates.

- [ ] **Step 1: Write failing variable-header geometry tests**

Add literal assertions that `zoneHeaderHeight({type:'zone', annotation:'10.0.0.0/24'})` returns `60`, while a zone without an annotation and a Firewall Zone return `36`. Assert arranged VM Y positions shift down by 24px only for an annotated workload zone, and drag constraints use the same content start.

- [ ] **Step 2: Run the canvas tests and verify RED**

Run: `node --test tests/event-planner-canvas.test.mjs`

Expected: FAIL because `zoneHeaderHeight` does not exist and arrangement still uses the fixed 36px header.

- [ ] **Step 3: Implement shared variable zone geometry**

Add constants for `baseHeaderHeight: 36` and `addressRailHeight: 24`. Implement:

```javascript
export function zoneHeaderHeight(zone) {
  return ZONE_CONTAINER_GEOMETRY.baseHeaderHeight
    + (zone?.type === 'zone' && typeof zone.annotation === 'string' && zone.annotation !== ''
      ? ZONE_CONTAINER_GEOMETRY.addressRailHeight : 0);
}
```

Use the helper in zone arrangement. Include the effective header height in calculated container bounds and use that value in machine constraints so dragging cannot place machines under the rail. Preserve compact Firewall Zone geometry.

- [ ] **Step 4: Write failing containment tests for zone and VM annotations**

Assert the subnet rail presentation starts at `y=36`, has height `24`, and ends at the effective header boundary. Assert an annotated VM's address baseline lies within `machineBounds(node)`, while an unannotated VM retains the compact machine height.

- [ ] **Step 5: Run the containment tests and verify RED**

Run: `node --test tests/event-planner-canvas.test.mjs`

Expected: FAIL because the current zone annotation is a loose text coordinate and the machine address lies outside its 72px bounds.

- [ ] **Step 6: Implement annotation-aware machine and rail geometry**

Change `topologyAnnotationPresentation` so zone results describe a rail (`className`, `text`, `y`, `height`) and VM results describe a contained baseline. Add `machineBounds(node)` that expands only annotated VMs enough to contain the second line. Use those bounds for hit targets, arrangement dimensions, zone bounds, and drag constraints.

- [ ] **Step 7: Run canvas tests and syntax checks**

Run: `node --test tests/event-planner-canvas.test.mjs`

Run: `node --check frontend/static/event-planner-canvas.js`

Expected: PASS.

- [ ] **Step 8: Commit Task 1**

```bash
git add frontend/static/event-planner-canvas.js tests/event-planner-canvas.test.mjs
git commit -m "fix: contain planner address geometry"
```

### Task 2: Render the Subnet Rail and Match VM Text Colour

**Files:**
- Modify: `tests/event-planner-canvas.test.mjs`
- Modify: `frontend/static/event-planner-canvas.js`
- Modify: `frontend/static/event-planner.css`

**Interfaces:**
- Consumes: `zoneHeaderHeight`, `machineBounds`, and `topologyAnnotationPresentation` from Task 1.
- Produces: SVG `.zone-address-rail`, `.zone-address-label`, `.zone-address-value`, and `.topo-node-address` elements contained by calculated geometry.

- [ ] **Step 1: Write failing rendering-model tests**

Assert the zone annotation presentation exposes the exact prefix `Range · ` separately from the truncated value and that VM presentation uses `className: 'topo-node-address'`. Add a style-contract helper or equivalent observable presentation value showing VM address text uses the same text role as `.machine-label`, not a muted or accent role.

- [ ] **Step 2: Run canvas tests and verify RED**

Run: `node --test tests/event-planner-canvas.test.mjs`

Expected: FAIL because the existing presentation is a single loose zone text label and VM address CSS is muted.

- [ ] **Step 3: Implement SVG rail rendering**

Render an address rail rectangle below the title header for annotated workload zones. Render the prefix and value with SVG text/tspans using `.text(...)`, never `.html(...)`. Size the rail to the live container width during `updateContainers`, and place the body divider at `zoneHeaderHeight(d)`.

- [ ] **Step 4: Implement VM name-colour inheritance**

Style `.topo-node-address` with the same `fill` as the general `.topo-node text`/`.machine-label` rule. Remove the muted fill override. Keep `.topo-node.invalid text` authoritative so both name and address turn red together.

- [ ] **Step 5: Run focused planner verification**

Run: `node --test tests/event-planner-state.test.mjs tests/event-planner-addresses.test.mjs tests/event-planner-canvas.test.mjs tests/event-planner-colours.test.mjs tests/event-planner-icons.test.mjs tests/event-planner-icon-picker.test.mjs`

Run: `node --check frontend/static/event-planner-state.js`

Run: `node --check frontend/static/event-planner-addresses.js`

Run: `node --check frontend/static/event-planner.js`

Run: `node --check frontend/static/event-planner-canvas.js`

Expected: PASS.

- [ ] **Step 6: Rebuild the local frontend and inspect the original scenario**

Run: `API_PORT=8091 docker compose up --detach --build`

Verify the red-team zone with `10.1.{{team_id}}.0/24` has a contained full-width rail, its VM address is visible inside the node, and both VM text lines share a colour.

- [ ] **Step 7: Run full regression verification**

Run: `docker compose --profile test run --rm --build tests`

Run: `git diff --check`

Expected: all tests pass and no whitespace errors.

- [ ] **Step 8: Commit Task 2**

```bash
git add frontend/static/event-planner-canvas.js frontend/static/event-planner.css tests/event-planner-canvas.test.mjs
git commit -m "fix: restyle planner address labels"
```
