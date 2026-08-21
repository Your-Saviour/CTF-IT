# Operation Ability Details Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show administrators what each operation ability does in a compact inspector and an expandable n8n-inspired dialog.

**Architecture:** Enrich the existing event-scoped operation catalogue directly from module YAML, then introduce a side-effect-free frontend renderer that both the inspector and dialog consume. Keep selection, tabs, dialog state, focus, and clipboard behavior in the existing operation controller.

**Tech Stack:** Python, pytest, vanilla JavaScript ES modules, Node test runner, Jinja2 templates, CSS.

## Global Constraints

- No database migration or public API is added.
- Module-sourced content is escaped before HTML rendering.
- The compact command is collapsed; the expanded dialog command starts open.
- Non-ability inspector behavior and read-only mutation rules remain unchanged.
- Preserve the existing Industrial visual system.

---

### Task 1: Enrich the operation ability catalogue

**Files:**
- Modify: `builder/operation_plan.py`
- Modify: `tests/test_operation_plan.py`

**Interfaces:**
- Consumes: module `caldera.recon`, `caldera.exploit`, `caldera.tactic`, `caldera.technique`, and `supported_bases`.
- Produces: ability catalogue rows with `command: str`, `technique: {attack_id: str, name: str} | null`, plus existing fields.

- [ ] **Step 1: Write a failing catalogue metadata test**

Extend the existing catalogue test to assert the recon/exploit command, phase description, tactic, technique object, and supported bases from the fixture modules.

- [ ] **Step 2: Run the focused test and verify the new metadata assertion fails**

Run: `docker compose --profile test run --rm tests pytest -q tests/test_operation_plan.py`

- [ ] **Step 3: Add the minimal catalogue fields**

Add `command: row["command"]` and `technique: caldera.get("technique")` to each ability row while retaining description fallbacks and existing fields.

- [ ] **Step 4: Re-run the focused test and verify it passes**

Run: `docker compose --profile test run --rm tests pytest -q tests/test_operation_plan.py`

---

### Task 2: Build the shared ability dossier renderer

**Files:**
- Create: `frontend/static/event-operation-ability-details.js`
- Create: `tests/event-operation-ability-details.test.mjs`

**Interfaces:**
- Produces: `findAbilityDetails(node, catalogue)`, `renderAbilityDetails(node, catalogue, {expanded})`, and `abilityCommand(node, catalogue)`.
- Returns escaped dossier markup, the original command string for clipboard use, or an unavailable state when no catalogue entry matches.

- [ ] **Step 1: Write failing Node tests**

Cover catalogue lookup by `(module_id, ability)`, escaped names/descriptions/commands, optional technique/base metadata, missing command copy behavior, unavailable entries, collapsed compact details, and open expanded details.

- [ ] **Step 2: Run the new test and verify module-not-found failure**

Run: `node --test tests/event-operation-ability-details.test.mjs`

- [ ] **Step 3: Implement the pure renderer**

Export the three planned functions. Use semantic `dl`, `details`, `pre`, and `code` markup; omit absent fields; show `No command metadata available` when needed; render `Copy command` only for available commands.

- [ ] **Step 4: Re-run the Node test and verify it passes**

Run: `node --test tests/event-operation-ability-details.test.mjs`

---

### Task 3: Integrate inspector tabs and expanded dialog

**Files:**
- Modify: `frontend/templates/event_operation.html`
- Modify: `frontend/static/event-operation.js`
- Modify: `frontend/static/event-operation.css`
- Modify: `tests/test_event_operation_template.py`

**Interfaces:**
- Consumes: `renderAbilityDetails()` and `abilityCommand()` from Task 2.
- Produces: Details/Settings tabs for a sole ability selection, `#ability-details-dialog`, Expand/Close/Copy behavior, focus restoration, and selection-synchronized dialog lifecycle.

- [ ] **Step 1: Add failing template/controller assertions**

Assert the dialog, tab/action hooks, renderer import, clipboard flow, focus restoration, and cache-busted asset versions are present while the existing inspector controls remain.

- [ ] **Step 2: Run the focused Python test and verify missing hooks fail**

Run: `docker compose --profile test run --rm tests pytest -q tests/test_event_operation_template.py`

- [ ] **Step 3: Add the dialog and controller behavior**

Track `inspectorTab`, `inspectorNodeId`, and `expandedAbilityNodeId`. Default a newly selected ability to Details, preserve the tab for the same node, render Settings with the existing fields, synchronize or close the dialog on selection changes, restore focus on close, and announce clipboard success/failure.

- [ ] **Step 4: Add Industrial dossier styling**

Use existing warm-black/cyan tokens, square one-pixel borders, compact definition-list rows, a black command surface, viewport-bounded dialog scrolling, responsive inspector width, focus-visible cyan outlines, and reduced-motion compatibility.

- [ ] **Step 5: Run focused tests and syntax checks**

Run: `node --test tests/event-operation-ability-details.test.mjs tests/event-operation-workspace.test.mjs`

Run: `node --check frontend/static/event-operation-ability-details.js`

Run: `node --check frontend/static/event-operation.js`

Run: `docker compose --profile test run --rm tests pytest -q tests/test_operation_plan.py tests/test_event_operation_template.py`

- [ ] **Step 6: Run repository verification**

Run: `docker compose --profile test run --rm tests`

Run: `git diff --check`

- [ ] **Step 7: Launch the local application on port 8091**

Run: `API_PORT=8091 docker compose up --detach --build`

Verify: `curl --silent --show-error --fail http://localhost:8091/healthz`
