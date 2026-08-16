# Planner Command Header Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the network-plan and module-assignment pages a readable two-row command header with the existing CTF Platform brand.

**Architecture:** Both Jinja templates will use the same semantic class structure, while `event-planner.css` owns the shared header layout and responsive behaviour. Existing control IDs and JavaScript contracts stay unchanged; `event-modules.css` will stop overriding obsolete one-row toolbar behaviour.

**Tech Stack:** Jinja2 templates, CSS, pytest source-contract tests, Docker Compose.

## Global Constraints

- Preserve the Industrial dark surface, mono typography, flat one-pixel borders, and cyan signal colour.
- Keep all existing control IDs, links, forms, labels, disabled states, and JavaScript behaviour unchanged.
- Apply the same shared header structure to network planning and module assignment.
- Use only real event, account, status, and action content already available to the templates.

---

### Task 1: Shared two-row header structure

**Files:**
- Modify: `tests/test_event_plan_template.py`
- Modify: `tests/test_event_modules_template.py`
- Modify: `frontend/templates/event_plan.html`
- Modify: `frontend/templates/event_modules.html`

**Interfaces:**
- Consumes: existing Jinja values `event_id`, `event_name`, `event_status`, `user`, and `read_only`
- Produces: shared `.planner-context-row`, `.planner-brand`, `.planner-command-row`, `.planner-command-start`, and `.planner-actions` markup

- [ ] **Step 1: Write failing template tests**

Add assertions to both test modules requiring `class="planner-context-row"`, `class="planner-brand"`, `>_ CTF Platform`, `class="planner-command-row"`, and `class="planner-command-start"`. Require the brand link to target `/admin` and retain each page's existing controls.

- [ ] **Step 2: Verify the tests fail**

Run: `PYTHONPATH=. pytest -q tests/test_event_plan_template.py tests/test_event_modules_template.py -k planner_command_header`

Expected: both tests fail because the new two-row classes are absent.

- [ ] **Step 3: Implement the shared markup**

In both templates, make `.planner-toolbar` contain:

```html
<div class="planner-context-row">
  <a class="planner-brand" href="/admin">&gt;_ CTF Platform</a>
  <div class="planner-identity">...</div>
  <div class="planner-account">...</div>
</div>
<div class="planner-command-row">
  <div class="planner-command-start">...back link and live save state...</div>
  <div class="planner-actions">...existing page actions...</div>
</div>
```

Keep every existing ID, endpoint, Jinja expression, and action label unchanged.

- [ ] **Step 4: Verify the template tests pass**

Run: `PYTHONPATH=. pytest -q tests/test_event_plan_template.py tests/test_event_modules_template.py -k planner_command_header`

Expected: two passing tests.

### Task 2: Readable Industrial header styling

**Files:**
- Modify: `tests/test_event_plan_template.py`
- Modify: `frontend/static/event-planner.css`
- Modify: `frontend/static/event-modules.css`

**Interfaces:**
- Consumes: Task 1 shared header classes
- Produces: two-row desktop layout with grouped responsive wrapping below 1100px

- [ ] **Step 1: Write a failing style contract test**

Add a test requiring the shared stylesheet to define `.planner-toolbar` as a grid, `.planner-context-row` and `.planner-command-row`, the Share Tech Mono brand treatment, increased muted-copy contrast via `var(--text-secondary)`, grouped separators, and the existing `@media (max-width: 1100px)` breakpoint.

- [ ] **Step 2: Verify the style test fails**

Run: `PYTHONPATH=. pytest -q tests/test_event_plan_template.py -k planner_command_header_styles`

Expected: fail because the two-row style rules do not exist.

- [ ] **Step 3: Implement the shared CSS**

Replace the one-row flex toolbar rules with a two-row grid. Style `.planner-brand` using Share Tech Mono and cyan, give the context row a bottom border, align account controls to the far edge, keep command groups intact, add one-pixel action-group separators, improve descriptive copy to `var(--text-secondary)`, and preserve readable disabled states. Remove the obsolete module-specific toolbar wrapping overrides.

- [ ] **Step 4: Verify focused tests and syntax**

Run: `PYTHONPATH=. pytest -q tests/test_event_plan_template.py tests/test_event_modules_template.py`

Expected: all focused tests pass.

### Task 3: Integrated verification

**Files:**
- Verify only

**Interfaces:**
- Consumes: completed templates and CSS
- Produces: verified frontend image and clean working tree diff

- [ ] **Step 1: Run the complete Docker test suite**

Run: `docker compose --profile test run --rm --build tests`

Expected: all collected tests pass, with only documented skips.

- [ ] **Step 2: Rebuild the local service on port 8091**

Run: `API_PORT=8091 docker compose up --detach --build api`

Expected: the API container is running and port `8091` maps to container port `8000`.

- [ ] **Step 3: Inspect the rendered pages and diff**

Confirm the two-row hierarchy, brand visibility, control readability, responsive wrapping, unchanged labels, and no unrelated file changes. Run `git diff --check`.
