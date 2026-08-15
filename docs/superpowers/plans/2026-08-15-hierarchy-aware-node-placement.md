# Hierarchy-Aware Node Placement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Place new planner nodes deterministically beside siblings and beneath their visual parent while preserving every saved coordinate.

**Architecture:** Add a pure exported layout function to the canvas module so placement is independently testable. Canvas rendering will use the completed layout, asynchronously persist newly calculated coordinates through the existing callback, and reuse the same function for Reset Layout.

**Tech Stack:** JavaScript ES modules, D3.js, Node's built-in test runner, Docker Compose, pytest.

## Global Constraints

- Saved coordinates are authoritative and must never be moved automatically.
- Child slots use centre, right, left, farther right, farther left order beneath the visual parent.
- Collision checks include node dimensions and padding and are deterministic.
- Topology structure, backend layout format, provisioning, selection, zoom, and drag behavior remain unchanged.
- Read-only rendering may calculate transient coordinates but must not persist them or mark state dirty.

---

### Task 1: Pure Hierarchical Placement

**Files:**
- Modify: `tests/event-planner-canvas.test.mjs`
- Modify: `frontend/static/event-planner-canvas.js`

**Interfaces:**
- Produces: `calculateHierarchicalLayout(graph, savedLayout?) -> {version: 1, nodes: Record<string, {x: number, y: number}>, added: boolean}`.
- Consumes: graph rows with `id` and `parent`; an optional version-one layout.

- [ ] **Step 1: Write failing executable tests**

Add literal assertions proving that a gateway is placed at `(120, 70)`, its children use `(120, 180)`, `(310, 180)`, and `(-70, 180)`, deeper children use their parent's coordinate plus 110 pixels vertically, saved coordinates remain byte-for-byte equal, a saved occupied slot is skipped, and repeated calls return equal results.

- [ ] **Step 2: Run the tests and confirm RED**

Run: `node --test tests/event-planner-canvas.test.mjs`

Expected: FAIL because `calculateHierarchicalLayout` is not exported.

- [ ] **Step 3: Implement the pure placement function**

Use constants `ROOT_X=120`, `ROOT_Y=70`, `HORIZONTAL_GAP=190`, `VERTICAL_GAP=110`, `NODE_WIDTH=140`, `NODE_HEIGHT=48`, and `COLLISION_PADDING=24`. Copy saved positions first, recursively ensure a parent is placed before its child, enumerate siblings in graph order, and search deterministic balanced slots until the candidate rectangle does not overlap an occupied rectangle.

- [ ] **Step 4: Run the tests and confirm GREEN**

Run: `node --test tests/event-planner-canvas.test.mjs`

Expected: all canvas tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/event-planner-canvas.test.mjs frontend/static/event-planner-canvas.js
git commit -m "feat: calculate hierarchy-aware planner layouts"
```

### Task 2: Canvas Persistence and Reset Integration

**Files:**
- Modify: `tests/event-planner-canvas.test.mjs`
- Modify: `tests/test_event_plan_template.py`
- Modify: `frontend/static/event-planner-canvas.js`

**Interfaces:**
- Consumes: `calculateHierarchicalLayout(graph, layout)` from Task 1.
- Preserves: `createPlannerCanvas(svgElement, callbacks)` public API and `onLayoutChange(layout)` callback shape.

- [ ] **Step 1: Write failing integration contracts**

Add assertions that `render()` uses `calculateHierarchicalLayout`, reports a completed layout only when `added` is true and the canvas is editable, and `resetLayout()` calculates from an empty version-one layout instead of using the removed global-index grid.

- [ ] **Step 2: Run the focused tests and confirm RED**

Run:

```bash
node --test tests/event-planner-canvas.test.mjs
docker compose --profile test run --rm tests pytest tests/test_event_plan_template.py -q
```

Expected: FAIL because render and reset still use index-based defaults.

- [ ] **Step 3: Integrate placement into render**

Replace index fallback coordinates with the completed calculated layout. After the SVG render finishes, schedule one layout-change callback when missing coordinates were added, unless `callbacks.readOnly` is true. Prevent duplicate pending notifications for the same completed layout.

- [ ] **Step 4: Integrate placement into Reset Layout**

Calculate from `{version: 1, nodes: {}}`, notify the existing callback, render with the complete result, and fit the scene. Do not retain the old `defaults()` grid helper.

- [ ] **Step 5: Run focused verification and confirm GREEN**

Run:

```bash
node --test tests/event-planner-canvas.test.mjs
node --test tests/event-planner-state.test.mjs
node --check frontend/static/event-planner-canvas.js
docker compose --profile test run --rm tests pytest tests/test_event_plan_template.py -q
git diff --check
```

Expected: all commands pass.

- [ ] **Step 6: Run full verification**

Run: `docker compose --profile test run --rm --build tests`

Expected: all tests pass, excluding existing skips.

- [ ] **Step 7: Commit**

```bash
git add tests/event-planner-canvas.test.mjs tests/test_event_plan_template.py frontend/static/event-planner-canvas.js
git commit -m "feat: place new planner nodes beside siblings"
```

- [ ] **Step 8: Review and rebuild locally**

Request a focused review of placement determinism, collision behavior, render callback reentrancy, read-only behavior, reset behavior, and drag preservation. Then rebuild with `API_PORT=8091 docker compose up --detach --build api` and verify an HTTP 200 response.
