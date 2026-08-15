# Compact Zone Containers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Size planner zone boxes to their VM grid in both dimensions while preserving header controls and expansion around manually moved VMs.

**Architecture:** Keep all behavior in the canvas geometry module. Add a pure grid-metrics helper shared by arrangement and bounds calculation, replace the fixed zone minimum with an explicit header-width floor plus content dimensions, and retain the existing actual-child extent calculation for dragged positions.

**Tech Stack:** Browser ES modules, D3 SVG rendering, Node.js built-in test runner and strict assertions.

## Global Constraints

- Affect only canvas geometry for workload zones and the system-managed Firewall Zone.
- Do not change planner data, persisted coordinates, styling, labels, selection, dragging, or provisioning behavior.
- Keep left and top zone edges fixed and continue expanding right and down for manually positioned children.
- Keep annotated VM labels and optional zone address rails inside their containers.

---

### Task 1: Content-driven zone geometry

**Files:**
- Modify: `frontend/static/event-planner-canvas.js:50-140`
- Test: `tests/event-planner-canvas.test.mjs:181-270`

**Interfaces:**
- Consumes: machine nodes shaped as `{id: string, type: 'vm' | 'firewall', annotation?: string}` and zone nodes shaped as `{id: string, type: 'zone' | 'firewall-zone', x: number, y: number, annotation?: string}`.
- Produces: `zoneGridMetrics(children)` returning `{columns: number, rows: Array<Array<object>>, width: number, contentHeight: number}`; existing `calculateZoneBounds(zone, children)` continues returning `{x, y, width, height, headerHeight}`.

- [ ] **Step 1: Add failing compact-geometry tests**

Extend the canvas tests with exact expectations for an arranged one-VM zone, a two-by-two four-VM zone, annotations, and manual expansion:

```js
test('zone bounds compact to their arranged VM grid in both dimensions', () => {
  const plainZone = {id: 'zone:a/blue', type: 'zone', x: 100, y: 200};
  const one = [{id: 'one', type: 'vm'}];
  const four = ['one', 'two', 'three', 'four'].map(id => ({id, type: 'vm'}));

  const arrangedOne = canvas.arrangeZoneChildren(plainZone, one);
  const arrangedFour = canvas.arrangeZoneChildren(plainZone, four);
  assert.deepEqual(canvas.calculateZoneBounds(plainZone, one.map(node => ({...node, ...arrangedOne[node.id]}))), {
    x: 100, y: 200, width: 228, height: 154, headerHeight: 36,
  });
  assert.deepEqual(canvas.calculateZoneBounds(plainZone, four.map(node => ({...node, ...arrangedFour[node.id]}))), {
    x: 100, y: 200, width: 228, height: 250, headerHeight: 36,
  });
});

test('compact zone bounds include address rails, VM labels, and manual expansion', () => {
  const zone = {id: 'zone:a/blue', type: 'zone', annotation: '10.2.0.0/24', x: 100, y: 200};
  const child = {id: 'one', type: 'vm', annotation: '10.2.0.1'};
  const arranged = {...child, ...canvas.arrangeZoneChildren(zone, [child]).one};
  assert.deepEqual(canvas.calculateZoneBounds(zone, [arranged]), {
    x: 100, y: 200, width: 228, height: 190, headerHeight: 60,
  });
  assert.deepEqual(canvas.calculateZoneBounds(zone, [{...child, x: 420, y: 460}]), {
    x: 100, y: 200, width: 380, height: 334, headerHeight: 60,
  });
});
```

Update the existing annotated empty-zone expectation from `width: 280, height: 214` to `width: 228, height: 100`; its header remains `60` high and its empty body retains `20` units of padding above and below.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `node --test tests/event-planner-canvas.test.mjs`

Expected: FAIL because `calculateZoneBounds` still applies the `280 × 190` fixed minimum.

- [ ] **Step 3: Implement shared grid metrics and compact bounds**

In `ZONE_CONTAINER_GEOMETRY`, replace `minWidth` and `minHeight` with named header values whose sum is `228`:

```js
titleInset: 12,
headerTextWidth: 104,
headerControlGap: 8,
arrangeControlWidth: 96,
headerRightInset: 8,
```

Export `zoneGridMetrics(children)` so it determines near-square rows exactly once and derives the content width and height from `machineBounds`, `padding`, `columnGap`, `rowGap`, `machineAnchorOffset`, and `machineTop`. Empty children return zero columns, no rows, the two horizontal paddings as width, and two vertical paddings as content height.

Update `arrangeZoneChildren` to consume the helper's `columns` and `rows`. Update `calculateZoneBounds` to use:

```js
const headerWidth = geometry.titleInset + geometry.headerTextWidth
  + geometry.headerControlGap + geometry.arrangeControlWidth + geometry.headerRightInset;
const minimumWidth = Math.max(headerWidth, metrics.width);
const minimumHeight = headerHeight + metrics.contentHeight;
```

Retain the existing reductions over actual child bounds so dragged children can increase `requiredWidth` and `requiredHeight` beyond those minimums.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `node --test tests/event-planner-canvas.test.mjs`

Expected: all canvas tests PASS, including existing deterministic arrangement, translation, constraints, links, and annotations.

- [ ] **Step 5: Run planner regression tests**

Run: `node --test tests/event-planner-*.test.mjs`

Expected: all JavaScript planner tests PASS with no warnings or errors.

- [ ] **Step 6: Check the patch**

Run: `git diff --check && git diff -- frontend/static/event-planner-canvas.js tests/event-planner-canvas.test.mjs`

Expected: no whitespace errors; diff contains only compact geometry and its tests.

- [ ] **Step 7: Commit the implementation**

```bash
git add frontend/static/event-planner-canvas.js tests/event-planner-canvas.test.mjs docs/superpowers/plans/2026-08-16-compact-zone-containers.md
git commit -m "feat: compact planner zone containers"
```
