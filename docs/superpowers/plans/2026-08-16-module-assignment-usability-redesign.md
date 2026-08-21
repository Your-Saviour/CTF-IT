# Module Assignment Usability Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make planned-module assignment understandable, explicit, and useful without leaving the workspace.

**Architecture:** Extend the existing API metadata only; derive assignment provenance and plan-wide usage in a pure browser state module. Render rich catalogue cards and a two-tab inspector from those tested derivations.

**Tech Stack:** FastAPI, Jinja2, browser-native ES modules, CSS, Node test runner, pytest, Docker Compose.

## Global Constraints

- Never display “pin” or “pinned” in assignment UI copy.
- Preserve current persistence and assignment semantics.
- Keep provider provisioning unchanged.
- Use explicit text in addition to colour for every state.

---

### Task 1: Assignment presentation state

**Files:** Create `frontend/static/event-modules-state.js`; create `tests/event-modules-state.test.mjs`.

**Interfaces:** Produces `moduleProvenance(plan, vmId, moduleId, modules)`, `moduleUsage(plan, moduleId, vms, modules)`, `dependencyParents(...)`, and `filterModules(...)`.

- [ ] Write tests with literal expected states for manual, random, dependency, absent, cross-plan usage, dependency parents, and description search.
- [ ] Run `node --test tests/event-modules-state.test.mjs`; expect missing-module failure.
- [ ] Implement the pure functions without DOM access.
- [ ] Re-run the Node tests; expect all passing.

### Task 2: Rich API metadata and template contracts

**Files:** Modify `api/routes/admin.py`; modify `frontend/templates/event_modules.html`; modify `tests/test_event_modules_template.py`.

**Interfaces:** GET module-plan rows add `learning_objectives`, `estimated_minutes`, `prerequisites`, and `verification_type`.

- [ ] Add failing tests for inspector tabs, rich catalogue region, live status, and explicit action regions.
- [ ] Run the focused Docker test; expect failure.
- [ ] Add semantic template containers and API metadata.
- [ ] Re-run focused backend/template tests; expect passing.

### Task 3: Rich cards and inspector controller

**Files:** Modify `frontend/static/event-modules.js`; rewrite `frontend/static/event-modules.css`.

**Interfaces:** Consumes Task 1 derivations and Task 2 DOM regions.

- [ ] Add terminology and controller contract tests to the Node suite; confirm failure.
- [ ] Render descriptive cards with explicit provenance, metadata, compatibility, requirements, and conflicts.
- [ ] Render Assignment summary and Module details tabs, plan-wide usage, actions, generation/resolution, empty/error/read-only states, and complete-detail links.
- [ ] Run Node syntax/tests and focused Docker tests; expect passing.

### Task 4: Live verification

**Files:** Modify `CLAUDE.md` only if architecture documentation requires terminology alignment.

- [ ] Run `node --check frontend/static/event-modules.js frontend/static/event-modules-state.js` and both Node suites.
- [ ] Run focused module-plan and template tests in Docker.
- [ ] Rebuild the API on port 8091 and verify HTTP 200.
- [ ] Run `git diff --check` and inspect the final diff for forbidden terminology.
