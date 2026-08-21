# Dedicated Planner Shell Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the event network planner a dedicated edge-to-edge application page without the global admin sidebar or topbar.

**Architecture:** The planner template will extend `base.html` directly and own its application header, account controls, dialogs, and workspace. Planner CSS will explicitly fill the viewport and allocate remaining height to the three-column workspace without changing planner state or API behavior.

**Tech Stack:** FastAPI/Jinja2 templates, HTML, CSS, pytest source-contract tests, Node syntax/state tests, Docker Compose.

## Global Constraints

- Preserve every existing planner action, DOM ID, state callback, API contract, and read-only rule.
- Keep the Industrial visual direction: black surfaces, JetBrains Mono, cyan signal color, flat one-pixel borders.
- Include Back to Events, event name/status, username, and Logout in the planner-owned toolbar.
- Do not render the global admin sidebar, topbar, breadcrumbs, page header, or constrained content container.
- Desktop must fit in `100dvh`; outline and inspector scroll independently.

---

### Task 1: Dedicated Jinja Page Shell

**Files:**
- Modify: `frontend/templates/event_plan.html`
- Modify: `tests/test_event_plan_template.py`

**Interfaces:**
- Consumes: route context values `user`, `event_id`, `event_name`, `event_status`, and `read_only`.
- Produces: existing planner DOM IDs plus `.planner-account`, a username label, and a POST form targeting `/auth/logout`.

- [ ] **Step 1: Write the failing template contract test**

```python
def test_plan_page_owns_a_dedicated_shell():
    source = TEMPLATE.read_text()
    assert '{% extends "base.html" %}' in source
    assert 'admin_base.html' not in source
    assert 'class="planner-account"' in source
    assert 'action="/auth/logout"' in source
    assert '{{ user.username }}' in source
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `docker compose --profile test run --rm --build tests pytest -q tests/test_event_plan_template.py::test_plan_page_owns_a_dedicated_shell`

Expected: FAIL because the template extends `admin_base.html` and lacks its own account controls.

- [ ] **Step 3: Implement the dedicated template boundary**

Change the template to extend `base.html`. Define `body_class` as `planner-page`, `container_class` as `planner-root`, suppress `site_nav`, render the planner inside the base `content` block, and retain the existing scripts. Add this toolbar account region:

```html
<div class="planner-account">
  <span>{{ user.username }}</span>
  <form method="post" action="/auth/logout">
    <button class="btn" type="submit">Logout</button>
  </form>
</div>
```

Render the actual lifecycle value beside the event name using `<span class="planner-event-status">{{ event_status }}</span>`.

- [ ] **Step 4: Run the focused template test and verify GREEN**

Run: `docker compose --profile test run --rm --build tests pytest -q tests/test_event_plan_template.py`

Expected: all template tests PASS.

- [ ] **Step 5: Commit the shell boundary**

```bash
git add frontend/templates/event_plan.html tests/test_event_plan_template.py
git commit -m "feat: give planner a dedicated page shell"
```

### Task 2: Edge-to-Edge Viewport Layout

**Files:**
- Modify: `frontend/static/event-planner.css`
- Modify: `tests/test_event_plan_template.py`

**Interfaces:**
- Consumes: `.planner-page`, `.planner-root`, `.planner`, `.planner-toolbar`, `.planner-workspace`, `.planner-rail`, `.planner-canvas-wrap`, and `.planner-inspector` from Task 1.
- Produces: a viewport-sized application with independently scrolling side panels and a flexible centre canvas.

- [ ] **Step 1: Write the failing CSS contract test**

```python
CSS = ROOT / "frontend" / "static" / "event-planner.css"

def test_plan_page_css_fills_the_viewport():
    source = CSS.read_text()
    assert '.planner-page' in source
    assert 'height:100dvh' in source
    assert '.planner-root' in source
    assert 'min-height:0' in source
    assert '.planner-account' in source
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `docker compose --profile test run --rm --build tests pytest -q tests/test_event_plan_template.py::test_plan_page_css_fills_the_viewport`

Expected: FAIL because the planner still subtracts admin-shell height and has no dedicated root styles.

- [ ] **Step 3: Implement the viewport layout**

Set `html`, `.planner-page`, and `.planner-root` to fill the viewport; make `.planner-root` marginless and max-width-free. Use `height:100dvh`, `padding:12px`, and `overflow:hidden` on `.planner`. Keep the toolbar compact and give `.planner-workspace` `min-height:0`. Apply `min-height:0; overflow:auto` to the rail and inspector, and make the SVG height `100%` without the desktop `min-height:520px`. Style `.planner-account` as a compact flex group separated from planner actions by a one-pixel border.

- [ ] **Step 4: Run focused frontend verification**

Run:

```bash
node --check frontend/static/event-planner.js
node --check frontend/static/event-planner-canvas.js
node --test tests/event-planner-state.test.mjs
docker compose --profile test run --rm --build tests pytest -q tests/test_event_plan_template.py
```

Expected: all commands exit 0.

- [ ] **Step 5: Commit the viewport layout**

```bash
git add frontend/static/event-planner.css tests/test_event_plan_template.py
git commit -m "style: make planner fill the viewport"
```

### Task 3: Regression and Local Browser Handoff

**Files:**
- Verify only; no production files expected.

**Interfaces:**
- Consumes: the completed dedicated planner shell.
- Produces: a verified Docker image running at `http://localhost:8091/`.

- [ ] **Step 1: Run the complete disposable test suite**

Run: `docker compose --profile test run --rm --build tests`

Expected: 0 failures.

- [ ] **Step 2: Run whitespace and repository checks**

Run: `git diff --check && git status --short`

Expected: no whitespace errors and only intentional changes, if any.

- [ ] **Step 3: Rebuild the local frontend**

Run: `API_PORT=8091 docker compose up --detach --build api`

Expected: the API container is Up with `0.0.0.0:8091->8000/tcp`.

- [ ] **Step 4: Verify the frontend responds**

Run: `curl --silent --show-error --fail --retry 8 --retry-all-errors --output /dev/null --write-out '%{http_code}\n' http://localhost:8091/`

Expected: `200`.
