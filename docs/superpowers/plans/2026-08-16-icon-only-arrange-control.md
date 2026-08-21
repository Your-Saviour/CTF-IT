# Icon-Only Arrange Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the planner zone's wide text arrange button with an accessible 32 × 28 grid-icon control and reduce the compact zone header width accordingly.

**Architecture:** Define the icon control as pure presentation data in the existing canvas module, consume that data when rendering the SVG control, and derive the zone header floor from the same control width. Keep D3 event handling and accessibility semantics unchanged.

**Tech Stack:** Browser ES modules, D3 SVG, CSS, Node.js built-in test runner.

## Global Constraints

- Use a `32 × 28` button with a cyan four-tile SVG icon.
- Add no Unicode symbol, external asset, or dependency.
- Preserve `role="button"`, keyboard focus, Enter/Space and click activation, read-only hiding, and `Arrange VMs in <zone name>` accessible names.
- Provide a native tooltip/title of `Arrange VMs`.
- Do not change persisted planner data or saved layouts.

---

### Task 1: Accessible compact arrange control

**Files:**
- Modify: `frontend/static/event-planner-canvas.js:50-150,390-445`
- Modify: `frontend/static/event-planner.css:134-138`
- Test: `tests/event-planner-canvas.test.mjs:1-240`

**Interfaces:**
- Produces: `arrangeControlPresentation()` returning `{width: 32, height: 28, viewBox: '0 0 24 24', path: string, title: 'Arrange VMs'}`.
- Consumes: `arrangeControlPresentation()` in `createPlannerCanvas()` and the existing `ZONE_CONTAINER_GEOMETRY.arrangeControlWidth` in zone bounds.

- [ ] **Step 1: Add failing presentation and geometry tests**

```js
test('arrange control uses a compact labelled grid icon', () => {
  assert.deepEqual(canvas.arrangeControlPresentation(), {
    width: 32,
    height: 28,
    viewBox: '0 0 24 24',
    path: 'M3 3h7v7H3V3zm11 0h7v7h-7V3zM3 14h7v7H3v-7zm11 0h7v7h-7v-7z',
    title: 'Arrange VMs',
  });
});
```

Update literal compact-width expectations: empty and single-VM zones become `164` wide, while a four-VM grid remains content-driven at `224` wide. Manually expanded width remains `380`.

- [ ] **Step 2: Verify RED**

Run: `node --test tests/event-planner-canvas.test.mjs`

Expected: FAIL because `arrangeControlPresentation` does not exist and geometry still reserves a 96-unit control.

- [ ] **Step 3: Implement the compact SVG control**

Add `arrangeControlPresentation()`, set `ZONE_CONTAINER_GEOMETRY.arrangeControlWidth` to `32`, and update D3 rendering to append:

```js
arrangeControls.append('title').text(control.title);
arrangeControls.append('rect')
  .attr('width', control.width)
  .attr('height', control.height)
  .attr('rx', 5);
arrangeControls.append('path')
  .attr('d', control.path)
  .attr('transform', 'translate(8 6) scale(.6667)')
  .attr('aria-hidden', 'true');
```

Keep the existing contextual `aria-label`, handlers, and right-edge transform. In CSS, replace the obsolete `.zone-arrange text` color rule with `.zone-arrange path { fill: var(--cyan); pointer-events: none; }`.

- [ ] **Step 4: Verify GREEN and regressions**

Run: `node --test tests/event-planner-canvas.test.mjs && node --test tests/event-planner-*.test.mjs`

Expected: 21 or more canvas tests and all planner JavaScript tests PASS.

- [ ] **Step 5: Rebuild and verify the local app**

Run: `API_PORT=8091 docker compose up --detach --build api`

Then run: `curl --silent --show-error --output /dev/null --write-out '%{http_code}\n' http://localhost:8091/`

Expected: container starts and the frontend returns HTTP `200`.

- [ ] **Step 6: Run the repository verification suite**

Run: `docker compose --profile test run --rm tests`

Expected: all non-optional tests PASS.

- [ ] **Step 7: Check and commit**

Run: `git diff --check && git status --short`

```bash
git add frontend/static/event-planner-canvas.js frontend/static/event-planner.css tests/event-planner-canvas.test.mjs docs/superpowers/plans/2026-08-16-icon-only-arrange-control.md
git commit -m "feat: use icon-only zone arrange control"
```
