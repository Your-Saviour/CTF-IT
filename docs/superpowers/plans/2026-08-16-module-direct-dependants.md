# Module Direct-Dependant Highlighting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Highlight modules that can be applied directly to the module an administrator selects.

**Architecture:** Add a pure direct-dependant selector to the existing module state helper. Keep temporary focus state in the catalogue controller and render its notice, card classes, and relationship badges through the existing catalogue pass. Add focused styling in the module colour layer without changing assignment persistence.

**Tech Stack:** JavaScript ES modules, CSS custom properties, Node test runner, pytest template contracts.

## Global Constraints

- Match only immediate `requires` relationships.
- Do not mutate or persist assignment data.
- Invalid red overrides relationship violet.
- Keep explicit text labels alongside colour.
- Start focus inactive. Once established, preserve it across ordinary catalogue selection and clear it only on VM change or `Clear relationship view`.

---

### Task 1: Direct-dependant state helper

**Files:**
- Modify: `frontend/static/event-modules-state.js`
- Modify: `tests/event-modules-state.test.mjs`

**Interfaces:**
- Produces: `directDependants(modules, moduleId): Module[]`.

- [ ] Add tests proving direct matches are returned and transitive descendants are excluded.
- [ ] Run the focused Node test and confirm it fails because the export is absent.
- [ ] Implement the pure helper using each module's `requires` array.
- [ ] Run the focused Node test and confirm it passes.

### Task 2: Catalogue relationship focus

**Files:**
- Modify: `frontend/templates/event_modules.html`
- Modify: `frontend/static/event-modules.js`
- Modify: `frontend/static/event-modules-colours.css`
- Modify: `tests/test_event_modules_template.py`

**Interfaces:**
- Consumes: `directDependants(modules, moduleId): Module[]`.
- Produces: `#dependency-focus`, `.direct-dependant`, `.relationship-muted`, and `.direct-badge` UI states.

- [ ] Add failing template/controller contracts for the focus notice, clear control, direct-dependant class, and invalid precedence.
- [ ] Render an initially hidden live focus notice above the catalogue.
- [ ] Track focus separately from the selected details module; activate it on the first card click, preserve it across later card selection, and clear it on VM change or button click.
- [ ] Render the parent and valid direct matches in a stable group above the remaining catalogue, with explicit relationship text.
- [ ] Add `Use as parent` to module details as the only way to replace an existing focus parent.
- [ ] Add flat violet relationship styling and preserve red invalid precedence.
- [ ] Bump JavaScript and colour stylesheet cache versions.
- [ ] Run focused Node, pytest, and JavaScript syntax checks.
- [ ] Run the rebuilt full Docker suite, rebuild the API on port 8091, verify live assets, and commit.
