# Icon-First Machine Nodes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace machine node cards with standalone base-type icons and labels beneath them.

**Architecture:** Split canvas rendering into structural nodes without icons and machine nodes with icons. Structural nodes retain cards; machine nodes receive an invisible interaction target, an icon, a below-icon label, and a state ring visible only for selection or validation errors.

**Tech Stack:** JavaScript ES modules, D3.js, SVG, CSS, Node test runner, pytest, Docker Compose.

## Global Constraints

- Apply to gateway, firewall VM, and workload VMs.
- Keep site and zone cards unchanged.
- Preserve click, keyboard, drag, links, coordinates, collision logic, and icon overrides.
- Normal machine nodes show no visible box or ring.

### Task 1: Icon-First Canvas Rendering

**Files:** `frontend/static/event-planner-canvas.js`, `frontend/static/event-planner.css`, `tests/event-planner-canvas.test.mjs`, `tests/test_event_plan_template.py`

- [ ] Add failing tests distinguishing machine and structural presentation.
- [ ] Render `.node-body` only for structural nodes.
- [ ] Render machine `.node-hit-target`, `.node-state-ring`, 36px `.node-icon`, and below-icon label.
- [ ] Style normal rings as hidden, selected rings cyan, invalid rings red, and firewall icons amber.
- [ ] Run focused tests, full suite, review, commit, rebuild port 8091, and verify HTTP 200.
