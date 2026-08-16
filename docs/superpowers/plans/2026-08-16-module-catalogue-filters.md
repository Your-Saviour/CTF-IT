# Module Catalogue Filters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add compact, combinable module catalogue filters including both parent-module meanings, compatibility, and a clear action with result count.

**Architecture:** Extend the pure `filterModules` state helper with relationship and complete compatibility predicates. Keep DOM state and derived option population in the existing page controller, with semantic controls in the template and responsive layout in the page stylesheet.

**Tech Stack:** Browser ES modules, HTML/Jinja2, CSS custom properties, Node's built-in test runner, pytest in Docker Compose.

## Global Constraints

- All active filters combine with AND logic.
- “Required by other modules” means a module ID appears in another catalogue module's `requires` list.
- “Requires no modules” means the module's own `requires` list is empty.
- Relationship classification always uses the complete module catalogue.
- Clearing filters does not clear the selected VM, selected module, or relationship-focus view.
- Styling remains consistent with the existing dark, monospace module assignment page.

---

### Task 1: Pure relationship and compatibility filtering

**Files:**
- Modify: `tests/event-modules-state.test.mjs`
- Modify: `frontend/static/event-modules-state.js`

**Interfaces:**
- Consumes: existing `filterModules(modules, options)` calls.
- Produces: `filterModules` options `relationship: 'required_by_others' | 'no_requirements' | ''` and `compatibility: 'compatible' | 'incompatible' | ''`.

- [ ] **Step 1: Write failing state tests**

Add fixtures with `supported_bases` and assertions proving that `required_by_others` returns IDs referenced by another module, `no_requirements` returns modules with empty requirements, a module can satisfy both, relationship combines with difficulty/category, and compatibility has both branches.

- [ ] **Step 2: Run the state tests and verify RED**

Run: `node --test tests/event-modules-state.test.mjs`

Expected: failures show relationship is ignored and incompatible-only filtering is unsupported.

- [ ] **Step 3: Implement the minimal predicates**

Build a set from every module's `requires`, then add explicit relationship and compatibility checks inside `filterModules`. Treat an empty or missing `supported_bases` list as compatible with every base.

- [ ] **Step 4: Run the state tests and verify GREEN**

Run: `node --test tests/event-modules-state.test.mjs`

Expected: all state tests pass.

### Task 2: Filter controls and page behavior

**Files:**
- Modify: `tests/test_event_modules_template.py`
- Modify: `frontend/templates/event_modules.html`
- Modify: `frontend/static/event-modules.js`

**Interfaces:**
- Consumes: the Task 1 `filterModules` options.
- Produces: controls `#module-difficulty`, `#module-category`, `#module-compatibility`, `#module-relationship`, `#clear-catalogue-filters`, and status `#catalogue-result-count`.

- [ ] **Step 1: Write failing template contract tests**

Require all new semantic control IDs, both relationship option values, the result-count live status, and the clear button. Update cache-version assertions for the changed JS and CSS assets.

- [ ] **Step 2: Run the template tests and verify RED**

Run: `docker compose --profile test run --rm tests pytest tests/test_event_modules_template.py -q`

Expected: the new control contract fails because the template does not contain it.

- [ ] **Step 3: Add the controls and controller wiring**

Add labelled select controls with all-state defaults. Derive sorted difficulty and category options from `data.modules`; pass all control values into `filterModules`; display “N of M modules”; bind all selects to render; and reset every catalogue filter from the clear button without changing selection or dependency focus.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `docker compose --profile test run --rm tests pytest tests/test_event_modules_template.py -q`

Expected: all template tests pass.

### Task 3: Responsive styling and final verification

**Files:**
- Modify: `frontend/static/event-modules.css`
- Modify: `frontend/templates/event_modules.html`

**Interfaces:**
- Consumes: Task 2 filter markup.
- Produces: a compact wrapping filter grid consistent with the current module page.

- [ ] **Step 1: Style the expanded filter bar**

Use the existing background, border, cyan focus, radius, and JetBrains Mono tokens. Give search a wider grid span, add a compact metadata row for count and clear, and collapse cleanly to one column at the existing responsive breakpoint.

- [ ] **Step 2: Run syntax and focused test verification**

Run: `node --check frontend/static/event-modules.js`

Run: `node --check frontend/static/event-modules-state.js`

Run: `node --test tests/event-modules-state.test.mjs`

Run: `docker compose --profile test run --rm tests pytest tests/test_event_modules_template.py -q`

Expected: every command exits zero.

- [ ] **Step 3: Run repository verification**

Run: `docker compose --profile test run --rm tests`

Run: `git diff --check`

Expected: the full disposable test service and whitespace validation exit zero.

- [ ] **Step 4: Review the diff against the design**

Confirm both relationship meanings, all requested secondary filters, AND semantics, filter reset behavior, result count, dependency-focus preservation, responsive styling, and asset cache bumps are present with no unrelated changes.
