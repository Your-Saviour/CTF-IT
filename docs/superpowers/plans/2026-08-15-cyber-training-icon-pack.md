# Cyber Training Planner Icon Pack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a coherent, categorized icon library covering common cyber-training topology scenarios while preserving every existing saved icon key.

**Architecture:** Extend the existing icon registry entries with category metadata and expose a grouped projection for inspector rendering. Keep the current resolver and persistence schema, update the mirrored Python allowlist, and render grouped native selects without adding dependencies.

**Tech Stack:** JavaScript ES modules, SVG paths, Python validation, Node test runner, pytest, Docker Compose.

## Global Constraints

- All SVGs use a 24×24 view box and remain legible at 36px and 24px.
- Preserve all existing keywords and `primary_icon`/`icon` persistence semantics.
- Generic icons represent function; recognizable simplified marks represent products.
- Both selectors expose the complete library grouped by category.
- No provisioning, topology, layout, or drag behavior changes.

---

### Task 1: Expand and Categorize the Registry

**Files:**
- Modify: `frontend/static/event-planner-icons.js`
- Modify: `builder/infrastructure_validation.py`
- Test: `tests/event-planner-icons.test.mjs`
- Test: `tests/test_gamenet.py`

**Interfaces:**
- Produces: `PLANNER_ICONS: Record<string, {label:string, category:string, path:string}>`
- Produces: `PLANNER_ICON_GROUPS: Array<{label:string, options:Array<{value:string,label:string}>}>`
- Preserves: `resolvePlannerIcon`, `machineIconPair`, `setMachineIconOverride`

- [ ] Add failing tests asserting the complete literal keyword set, category coverage, valid paths, preserved legacy keys, and client/server allowlist parity.
- [ ] Run the focused Node and Python tests and confirm failures identify missing entries/categories.
- [ ] Replace placeholder paths, add the approved categories and keywords, export grouped options, and mirror keywords in Python.
- [ ] Run focused tests and confirm they pass.
- [ ] Commit the registry and validation change.

### Task 2: Render Grouped Selectors and Verify

**Files:**
- Modify: `frontend/static/event-planner.js`
- Test: `tests/test_event_plan_template.py`

**Interfaces:**
- Consumes: `PLANNER_ICON_GROUPS`
- Preserves: `field(name, label, value, options)` for ordinary flat selects
- Adds: grouped select rendering for both icon fields with Automatic first

- [ ] Add a failing UI contract test proving both selectors consume categorized groups and retain their distinct Automatic labels.
- [ ] Run the focused test and confirm it fails on the current flat options.
- [ ] Extend select rendering to support category groups and use it for Primary and Secondary icons.
- [ ] Run JavaScript syntax checks, focused Node tests, and the full rebuilt Docker suite.
- [ ] Request independent review, fix all Critical/Important findings, commit, rebuild port 8091, and verify HTTP 200.
