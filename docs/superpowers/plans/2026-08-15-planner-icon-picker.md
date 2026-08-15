# Planner Icon Picker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide searchable, categorized dropdown pickers with live SVG previews for both planner icon fields.

**Architecture:** Add pure picker-model helpers beside the icon registry and render the custom dropdown from the existing inspector controller. Bind accessible pointer, search, and keyboard interactions after every inspector render; preserve current state mutation and validation APIs.

**Tech Stack:** JavaScript ES modules, inline SVG, HTML listbox semantics, CSS, Node test runner, pytest, Docker Compose.

## Global Constraints

- Both Primary and Secondary icon fields use the picker.
- Automatic is first and previews the resolved machine/base default.
- All 50 icons remain categorized and searchable.
- Escape closes; arrows move; Enter selects; Tab follows normal focus order.
- Read-only mode cannot open or change a picker.
- No persistence, validation, canvas, layout, or provisioning changes.

---

### Task 1: Picker Model

**Files:**
- Modify: `frontend/static/event-planner-icons.js`
- Test: `tests/event-planner-icons.test.mjs`

**Interfaces:**
- Produces: `machineAutomaticIcon(type, machine, field, baseTypes)`
- Produces: `filterPlannerIconGroups(query)`
- Preserves: `PLANNER_ICON_GROUPS`, `machineIconPair`, `setMachineIconOverride`

- [ ] Add failing tests for field-specific Automatic resolution and case-insensitive category/label filtering.
- [ ] Run the tests and confirm missing exports fail.
- [ ] Implement the two pure helpers without mutating machine input.
- [ ] Run focused tests and commit.

### Task 2: Accessible Preview Dropdown

**Files:**
- Modify: `frontend/static/event-planner.js`
- Modify: `frontend/static/event-planner.css`
- Test: `tests/test_event_plan_template.py`

**Interfaces:**
- Consumes: `machineAutomaticIcon`, `filterPlannerIconGroups`, `resolvePlannerIcon`
- Adds: icon picker shell with `data-icon-picker`, trigger, search, listbox, categorized option buttons

- [ ] Add a failing controller contract for SVG previews, search, listbox semantics, both field names, and keyboard bindings.
- [ ] Implement escaped SVG/option markup and replace only the two icon fields.
- [ ] Bind open/close, filtering, option selection, Arrow Up/Down, Enter, Escape, outside-click, and read-only behavior.
- [ ] Add industrial picker styling with constrained independent menu scrolling.
- [ ] Run syntax/focused/full tests, request review, fix blockers, commit, rebuild port 8091, and verify HTTP 200.
