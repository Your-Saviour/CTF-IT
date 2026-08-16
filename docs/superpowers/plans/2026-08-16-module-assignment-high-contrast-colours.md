# Module Assignment High-Contrast Colours Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make semantic module-assignment states immediately distinguishable in the catalogue and inspector.

**Architecture:** Strengthen the existing CSS-only provenance layer in `event-modules-colours.css`. Keep state classes and rendering logic unchanged, using the shared semantic colour custom property to drive borders, surfaces, badges, and rows.

**Tech Stack:** CSS custom properties, Jinja template asset loading, pytest source-contract tests.

## Global Constraints

- Keep manual cyan, random green, dependency amber, invalid red, and absent neutral.
- Preserve explicit state labels so meaning does not rely on colour alone.
- Preserve the cyan outer selection outline.
- Keep module names and descriptions near-white, secondary metadata light blue-grey, and incompatible cards at full opacity.
- Do not change data, assignment behaviour, or workspace geometry.

---

### Task 1: Strengthen semantic state presentation

**Files:**
- Modify: `tests/test_event_modules_template.py`
- Modify: `frontend/static/event-modules-colours.css`

**Interfaces:**
- Consumes: Existing `provenance-*`, `invalid-state`, `assignment-state`, and `usage-row` classes.
- Produces: High-contrast CSS presentation for those existing classes.

- [ ] **Step 1: Write the failing test**

Add a source-contract test requiring a six-pixel rail, full semantic border, stronger tint, solid badge fill with dark text, and tinted bordered usage rows.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_event_modules_template.py -q`

Expected: FAIL because the current styling uses a four-pixel rail, low-opacity badge fill, and unboxed usage rows.

- [ ] **Step 3: Write minimal implementation**

Update `event-modules-colours.css` so assigned and invalid cards use a six-pixel rail, semantic outer border, and one flat low-luminance semantic surface without gradients or glow. Give badges a solid semantic background and dark text. Give usage rows semantic border, flat tint, padding, and radius while leaving absent rows neutral.

- [ ] **Step 4: Run focused verification**

Run: `python -m pytest tests/test_event_modules_template.py -q`

Expected: all tests pass.

- [ ] **Step 5: Run complete verification and rebuild**

Run: `docker compose --profile test run --rm tests`

Run: `API_PORT=8091 docker compose up --detach --build api`

Verify the module route redirects unauthenticated requests and the new stylesheet returns HTTP 200.

- [ ] **Step 6: Commit**

```bash
git add docs/superpowers/specs/2026-08-16-module-assignment-high-contrast-colours-design.md docs/superpowers/plans/2026-08-16-module-assignment-high-contrast-colours.md tests/test_event_modules_template.py frontend/static/event-modules-colours.css
git commit -m "style: strengthen module assignment colours"
```
