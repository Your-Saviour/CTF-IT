# Planner Node Theming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give each topology role a restrained, theme-native visual treatment and highlight connections adjacent to the selected node.

**Architecture:** Keep graph data and persistence unchanged. The D3 canvas will add semantic link classes and a VM edge-marker element; CSS will own all role-specific fills, strokes, selection, error, and link emphasis.

**Tech Stack:** JavaScript ES modules, D3.js, CSS, pytest source-contract tests, Docker Compose.

## Global Constraints

- Preserve the existing dark industrial theme and monospace typography.
- Do not change topology structure, editing behavior, layout persistence, or provisioning.
- Do not rely on color alone: retain dashed automatic-zone borders and add a VM shape marker.
- Selected and invalid states must remain visually distinguishable.

---

### Task 1: Semantic Canvas Styling

**Files:**
- Modify: `tests/test_event_plan_template.py`
- Modify: `frontend/static/event-planner-canvas.js`
- Modify: `frontend/static/event-planner.css`

**Interfaces:**
- Consumes: graph rows shaped as `{id, parent, type, label, selected, invalid}`.
- Produces: `.topo-link.selected-adjacent`, role classes on `.topo-node`, and `.vm-edge` markers within VM nodes.

- [ ] **Step 1: Write the failing styling contract test**

Add a test asserting that the canvas marks selected-adjacent links, appends `.vm-edge`, and the stylesheet defines distinct `site`, `firewall-zone`, `firewall`, `zone`, `vm`, `selected`, `invalid`, and selected-adjacent rules.

- [ ] **Step 2: Run the focused test and confirm RED**

Run: `docker compose --profile test run --rm tests pytest tests/test_event_plan_template.py -q`

Expected: FAIL because selected-adjacent link classes and VM edge markers do not exist.

- [ ] **Step 3: Add semantic canvas hooks**

Set each link class from whether its source or target is selected. Append a narrow rectangle with class `vm-edge` only to VM groups. Preserve the existing drag and link-update behavior.

- [ ] **Step 4: Implement the role styling**

Define flat, role-specific fills and borders using the current navy/cyan variables, an amber firewall treatment, a dashed Firewall Zone boundary, a cyan VM edge marker, bright selected treatment, red invalid treatment, and brighter selected-adjacent links.

- [ ] **Step 5: Run focused verification and confirm GREEN**

Run:

```bash
docker compose --profile test run --rm tests pytest tests/test_event_plan_template.py -q
node --check frontend/static/event-planner-canvas.js
git diff --check
```

Expected: all commands pass.

- [ ] **Step 6: Run the full suite**

Run: `docker compose --profile test run --rm --build tests`

Expected: all tests pass, excluding existing skips.

- [ ] **Step 7: Commit**

```bash
git add tests/test_event_plan_template.py frontend/static/event-planner-canvas.js frontend/static/event-planner.css
git commit -m "style: theme planner topology nodes"
```

- [ ] **Step 8: Rebuild the local frontend and inspect**

Run: `API_PORT=8091 docker compose up --detach --build api`

Verify `http://localhost:8091/` returns HTTP 200 and inspect the planner at desktop and narrow widths.
