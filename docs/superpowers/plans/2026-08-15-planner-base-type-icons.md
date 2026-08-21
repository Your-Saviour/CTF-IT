# Planner Base-Type Icons Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render catalogue-driven icons on every planner machine node and allow a persisted per-machine override from a substantial built-in icon library.

**Architecture:** A focused icon module owns the keyword catalogue, labels, and safe resolution of built-in/custom icon definitions. The controller chooses override → base-type icon → server fallback and the D3 canvas renders the resolved path inside a nested SVG viewport.

**Tech Stack:** JavaScript ES modules, D3.js, SVG, Node test runner, pytest, Docker Compose.

## Global Constraints

- Cover VPN gateway, primary firewall VM, and workload VMs; keep sites and zones text-only.
- `Automatic (Base type)` removes the persisted override.
- Preserve custom base-type `{svg_path, viewbox}` support without injecting SVG markup.
- Preserve node dimensions, placement, collision bounds, links, drag, selection, and provisioning.
- Match the current industrial navy/cyan/amber visual system.

---

### Task 1: Icon Library and Resolution

**Files:**
- Create: `frontend/static/event-planner-icons.js`
- Create: `tests/event-planner-icons.test.mjs`

**Interfaces:**
- Produces: `PLANNER_ICONS`, `PLANNER_ICON_OPTIONS`, and `resolvePlannerIcon(value)` returning `{path, viewBox}`.

- [ ] Write failing executable tests for all 21 keywords, labels/options, custom path and view-box preservation, and malformed/unknown fallback.
- [ ] Run `node --test tests/event-planner-icons.test.mjs` and confirm failure because the module does not exist.
- [ ] Implement filled 24×24 SVG paths for the approved library and safe resolver logic.
- [ ] Run the icon tests and confirm all pass.
- [ ] Commit with `git commit -m "feat: add planner machine icon library"`.

### Task 2: Planner Projection, Override, and Rendering

**Files:**
- Modify: `frontend/static/event-planner.js`
- Modify: `frontend/static/event-planner-canvas.js`
- Modify: `frontend/static/event-planner.css`
- Modify: `frontend/static/event-planner-state.js`
- Modify: `tests/event-planner-canvas.test.mjs`
- Modify: `tests/event-planner-state.test.mjs`
- Modify: `tests/test_event_plan_template.py`

**Interfaces:**
- Consumes: icon module exports from Task 1 and base catalogue rows shaped as `{id, icon}`.
- Produces: optional machine `icon` override and graph rows carrying resolved `{path, viewBox}` icons.

- [ ] Write failing tests for optional override validation, automatic override removal, catalogue fallback projection, custom view-box rendering hooks, and machine-only icon presentation.
- [ ] Run focused Node and pytest tests and confirm the expected failures.
- [ ] Add the Icon selector to gateway, firewall, and VM inspectors; delete `value.icon` when Automatic is selected.
- [ ] Resolve icon priority as machine override, then selected base catalogue icon, then `server`.
- [ ] Render a nested SVG and path for graph rows with icons; offset only machine labels and apply semantic node accent colours in CSS.
- [ ] Run focused Node tests, JavaScript syntax checks, planner pytest tests, and `git diff --check`.
- [ ] Run `docker compose --profile test run --rm --build tests` and confirm the full suite passes.
- [ ] Commit with `git commit -m "feat: render configurable planner VM icons"`.
- [ ] Request focused review, fix Critical/Important findings, rebuild port 8091, and verify HTTP 200.
