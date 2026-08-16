# n8n-Inspired Operation Workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the operation designer's permanent three-column editor and connection dialog with a dark, canvas-dominant n8n-style workspace supporting direct ports, contextual node insertion, navigation, selection, history, overlays, and accessible fallbacks.

**Architecture:** Keep the existing SVG renderer and backend API. Extend the framework-independent graph state helpers, add a framework-independent viewport/history helper, and rebuild the page controller around transient viewport, picker, selection, and connection-gesture state. The Jinja template remains a thin shell and CSS owns the established dark visual language.

**Tech Stack:** Jinja2, vanilla ES modules, SVG, CSS, Node test runner, pytest template contracts.

## Global Constraints

- Preserve the existing dark planner theme and cyan signal colour.
- Do not add a frontend framework, graph library, or backend schema change.
- Keep invalid drafts saveable and backend validation authoritative.
- Every pointer interaction implemented here must expose a keyboard path and visible focus.
- Persist node coordinates only; keep viewport, selection, overlays, and history local.

---

### Task 1: Graph Editing Primitives

**Files:**
- Modify: `tests/event-operation-state.test.mjs`
- Modify: `frontend/static/event-operation-state.js`

**Interfaces:**
- Produces: `connectionError(state, source, target, condition): string | null`
- Produces: `insertConnectedNode(state, source, condition, template, position): {state, nodeId, edgeId}`
- Produces: `moveNodes(state, nodeIds, delta): object`
- Produces: `duplicateNodes(state, nodeIds, offset): {state, nodeIds}`

- [ ] **Step 1: Write failing state-helper tests**

Add tests asserting that cycle-producing connections are rejected, insert-and-connect returns both new IDs without mutating the input, grouped movement changes only selected nodes, and duplicated selections receive new IDs and internal copied edges.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `node --test tests/event-operation-state.test.mjs`

Expected: FAIL because the four new exports do not exist.

- [ ] **Step 3: Implement immutable graph helpers**

Use cloned state, depth-first reachability for cycle detection, the existing `nextId`, and a node-ID mapping for duplication. Make `addEdge` call `connectionError` so dialog and direct gestures share identical rules.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run: `node --test tests/event-operation-state.test.mjs`

Expected: all state-helper tests pass.

### Task 2: Viewport and History State

**Files:**
- Create: `tests/event-operation-workspace.test.mjs`
- Create: `frontend/static/event-operation-workspace.js`

**Interfaces:**
- Produces: `createViewport(width, height): {x, y, zoom, width, height}`
- Produces: `zoomAt(viewport, screenPoint, nextZoom): object`
- Produces: `graphPoint(viewport, screenPoint): {x, y}`
- Produces: `fitViewport(nodes, bounds, padding): object`
- Produces: `createHistory(initial, limit): object`
- Produces history methods `commit`, `undo`, `redo`, and `current`.

- [ ] **Step 1: Write failing viewport and history tests**

Cover cursor-stable zooming, graph/screen conversion, fitting node bounds, commit deduplication, redo clearing after a new commit, and bounded history.

- [ ] **Step 2: Run the new tests and verify RED**

Run: `node --test tests/event-operation-workspace.test.mjs`

Expected: FAIL because `event-operation-workspace.js` does not exist.

- [ ] **Step 3: Implement the pure workspace helpers**

Clamp zoom to `0.35..2`, use JSON structural snapshots for history, and keep every public helper free of DOM access.

- [ ] **Step 4: Run the new tests and verify GREEN**

Run: `node --test tests/event-operation-workspace.test.mjs`

Expected: all viewport and history tests pass.

### Task 3: Canvas-Dominant Template and Styling

**Files:**
- Modify: `tests/test_event_operation_template.py`
- Modify: `frontend/templates/event_operation.html`
- Modify: `frontend/static/event-operation.css`

**Interfaces:**
- Consumes IDs from the controller: `operation-canvas`, `operation-world`, `operation-edges`, `operation-nodes`, `node-picker`, `node-picker-search`, `node-picker-results`, `operation-inspector-panel`, `operation-outline-panel`, `operation-validation-panel`, `operation-minimap`, `operation-zoom-in`, `operation-zoom-out`, `operation-fit`, `operation-add-node`, `operation-undo`, `operation-redo`.

- [ ] **Step 1: Replace the old template assertions with failing workspace contracts**

Assert the permanent `operation-library` and `edge-dialog` are absent; assert the canvas toolbar, world group, picker, inspector, outline, validation drawer, minimap, live region, zoom controls, and undo/redo controls are present.

- [ ] **Step 2: Run the template test and verify RED**

Run: `docker compose --profile test run --rm tests pytest tests/test_event_operation_template.py -q`

Expected: FAIL on missing full-workspace IDs and the still-present edge dialog.

- [ ] **Step 3: Rebuild the template shell**

Keep existing command-header actions and preview dialog. Add labelled overlay panels, toolbar buttons using text or existing icon assets, and one polite live region. Remove the permanent columns and source/target connection dialog.

- [ ] **Step 4: Implement the dark canvas visual system**

Make the graph surface fill all remaining height, render a transformable dotted grid, style floating controls with flat borders, give nodes visible semantic ports and selected/invalid states, and provide bottom/right drawers that collapse on narrow viewports.

- [ ] **Step 5: Run the template test and verify GREEN**

Run: `docker compose --profile test run --rm tests pytest tests/test_event_operation_template.py -q`

Expected: all template contracts pass.

### Task 4: Direct Manipulation Controller

**Files:**
- Modify: `frontend/static/event-operation.js`
- Modify: `tests/test_event_operation_template.py`

**Interfaces:**
- Consumes graph primitives from Task 1 and viewport/history helpers from Task 2.
- Produces direct port connection gestures, empty-canvas filtered insertion, canvas navigation, minimap, multi-selection, contextual actions, overlays, keyboard commands, and persistence orchestration.

- [ ] **Step 1: Add failing source-contract assertions**

Assert the controller imports `connectionError`, `insertConnectedNode`, `moveNodes`, `duplicateNodes`, `createViewport`, `zoomAt`, `fitViewport`, and `createHistory`; assert it uses wheel zoom, pointer capture, keyboard shortcuts, clipboard APIs, and the node-picker IDs.

- [ ] **Step 2: Run the contract test and verify RED**

Run: `docker compose --profile test run --rm tests pytest tests/test_event_operation_template.py -q`

Expected: FAIL because the old controller has none of the new imports or behaviors.

- [ ] **Step 3: Implement rendering and viewport navigation**

Render nodes with type badges, metadata, left input ports, and semantic right output ports inside `operation-world`. Apply one world transform for pan and zoom. Implement cursor-centred wheel zoom, background pan, fit-to-workflow, zoom buttons, and minimap viewport indication.

- [ ] **Step 4: Implement selection, history, and clipboard actions**

Support single and additive node selection, background clearing, grouped dragging with one history commit, undo/redo, duplicate, delete, disable/enable, copy, and paste. Keep Start and Finish protected by existing deletion rules.

- [ ] **Step 5: Implement connection gestures and contextual node creation**

Start gestures from semantic output ports, draw a live preview path, highlight compatible inputs, announce incompatible drops, and create typed transitions on valid inputs. On empty-canvas drop, open the filtered picker at the graph point and use `insertConnectedNode` for one atomic action.

- [ ] **Step 6: Implement overlays and existing workflows**

Render searchable grouped catalogue results, the selected-item inspector, the policy inspector, the outline drawer, linked validation issues, and preview results. Preserve save-state messages, optimistic concurrency, read-only behavior, and unsaved-navigation protection.

- [ ] **Step 7: Run syntax and focused frontend tests**

Run: `node --check frontend/static/event-operation.js`

Run: `node --check frontend/static/event-operation-workspace.js`

Run: `node --test tests/event-operation-state.test.mjs tests/event-operation-workspace.test.mjs`

Expected: syntax checks exit zero and all focused tests pass.

### Task 5: Regression Verification

**Files:**
- Modify only if a regression is discovered, with a failing test first.

**Interfaces:**
- Verifies the complete feature against operation, module, and planner contracts.

- [ ] **Step 1: Run frontend and template regression suites**

Run: `node --test tests/event-operation-state.test.mjs tests/event-operation-workspace.test.mjs tests/event-modules-state.test.mjs tests/event-planner-state.test.mjs`

Run: `docker compose --profile test run --rm tests pytest tests/test_operation_plan.py tests/test_event_operation_template.py tests/test_event_modules_template.py tests/test_event_plan_template.py -q`

- [ ] **Step 2: Run static verification**

Run: `node --check frontend/static/event-operation.js`

Run: `node --check frontend/static/event-operation-workspace.js`

Run: `git diff --check`

- [ ] **Step 3: Inspect the final diff against the acceptance criteria**

Confirm the old connection dialog and permanent side columns are gone; direct semantic ports, contextual insertion, history, navigation, minimap, overlays, validation navigation, keyboard support, and unchanged API/schema are present.
