# Planner Zone and VM Colours Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add saved, editable zone colours with inheritance and per-VM colour overrides to the event planner.

**Architecture:** Store explicit colours in `infrastructure_layout.themes`, keyed by stable node ID, and keep provisioning infrastructure unchanged. State helpers normalize, validate, remap, prune, and resolve colours; the controller renders inspector controls and sends effective colours to the SVG canvas; the backend validates the optional theme map.

**Tech Stack:** Browser ES modules, D3/SVG, CSS, Node's built-in test runner, Python/FastAPI, pytest.

## Global Constraints

- Persist only normalized six-digit hexadecimal strings matching `#[0-9a-fA-F]{6}`.
- A machine inherits its parent zone's explicit colour unless it has its own explicit override.
- Missing colours preserve the planner's current semantic defaults.
- Theme data remains presentation-only in `infrastructure_layout`; provisioning JSON and database schema do not change.
- Selection, validation, system-managed, keyboard, and read-only behavior remain intact.

---

### Task 1: Client Theme State

**Files:**
- Modify: `frontend/static/event-planner-state.js`
- Test: `tests/event-planner-state.test.mjs`

**Interfaces:**
- Produces: `normalizeThemeColor(value): string | null`
- Produces: `setNodeThemeColor(layout, nodeId, color): layout`
- Produces: `effectiveNodeColor(index, layout, nodeId): {color: string | null, inherited: boolean}`
- Extends: normalized layout shape to `{version: 1, nodes: Record<string, Position>, themes: Record<string, {color: string}>}`

- [ ] **Step 1: Write failing normalization and inheritance tests**

Add tests proving valid colours normalize to lowercase, malformed theme entries disappear, a VM inherits its zone colour, an explicit VM colour wins, and reset removes only the explicit entry.

```js
test('normalizes theme colours and resolves VM inheritance', () => {
  const layout = normalizeClientLayout({version: 1, nodes: {}, themes: {
    'zone:head_office/corporate': {color: '#2563EB'},
    'vm:head_office/corporate/web_1': {color: 'invalid'},
  }}, infrastructure);
  const index = nodeIndex(infrastructure);
  assert.deepEqual(layout.themes, {
    'zone:head_office/corporate': {color: '#2563eb'},
  });
  assert.deepEqual(effectiveNodeColor(index, layout, 'vm:head_office/corporate/web_1'), {
    color: '#2563eb', inherited: true,
  });
});
```

- [ ] **Step 2: Run the focused state test and verify RED**

Run: `node --test tests/event-planner-state.test.mjs`

Expected: FAIL because the theme helpers and normalized `themes` map do not exist.

- [ ] **Step 3: Implement theme normalization, updates, inheritance, remapping, and pruning**

Add a strict colour regex, ensure layout normalization creates `themes`, remap themes in `renameStructuralKey`, prune themes in `pruneLayout`, and make coordinate-only operations preserve the theme map.

```js
export function setNodeThemeColor(layout, nodeId, value) {
  const next = clone(layout);
  next.themes ||= {};
  const color = normalizeThemeColor(value);
  if (color) next.themes[nodeId] = {color};
  else delete next.themes[nodeId];
  return next;
}
```

- [ ] **Step 4: Run the state tests and verify GREEN**

Run: `node --test tests/event-planner-state.test.mjs`

Expected: all tests pass, including rename/prune/theme-preservation cases.

- [ ] **Step 5: Commit client state support**

```bash
git add frontend/static/event-planner-state.js tests/event-planner-state.test.mjs
git commit -m "feat: add planner colour theme state"
```

### Task 2: Backend Layout Theme Validation

**Files:**
- Modify: `builder/infrastructure_planner.py`
- Modify: `tests/test_event_plan_template.py`

**Interfaces:**
- Consumes: layout `themes` mapping from Task 1.
- Extends: `validate_infrastructure_layout(layout, infrastructure) -> list[str]` with optional theme validation.

- [ ] **Step 1: Write failing backend validation tests**

Add cases for a valid theme, an unknown node ID, malformed entry, extra field, and invalid hex colour.

```python
def test_layout_theme_validation_rejects_malformed_colours(sample_infrastructure):
    layout = {
        "version": 1,
        "nodes": {},
        "themes": {"zone:head_office/corporate": {"color": "blue"}},
    }
    errors = validate_infrastructure_layout(layout, sample_infrastructure)
    assert any("color must be a six-digit hex colour" in error for error in errors)
```

- [ ] **Step 2: Run the focused pytest and verify RED**

Run: `pytest -q tests/test_event_plan_template.py -k layout`

Expected: FAIL because malformed theme values are currently ignored.

- [ ] **Step 3: Implement optional theme validation**

Validate `themes` only when present. Require an object keyed by a valid infrastructure node ID; require each value to be exactly `{"color": "#rrggbb"}`; reject non-object entries, unknown IDs, unsupported keys, and invalid colour strings with path-specific errors.

```python
themes = layout.get("themes", {})
if not isinstance(themes, dict):
    errors.append("infrastructure_layout.themes must be an object")
else:
    for node_id, theme in themes.items():
        # Validate stable ID, exact entry fields, and strict hex colour.
```

- [ ] **Step 4: Run backend tests and verify GREEN**

Run: `pytest -q tests/test_event_plan_template.py -k layout`

Expected: all selected tests pass.

- [ ] **Step 5: Commit backend validation**

```bash
git add builder/infrastructure_planner.py tests/test_event_plan_template.py
git commit -m "feat: validate planner colour themes"
```

### Task 3: Inspector Colour Controls and Canvas Rendering

**Files:**
- Modify: `frontend/static/event-planner.js`
- Modify: `frontend/static/event-planner-canvas.js`
- Modify: `frontend/static/event-planner.css`
- Modify: `tests/event-planner-canvas.test.mjs`
- Modify: `tests/test_event_plan_template.py`

**Interfaces:**
- Consumes: `setNodeThemeColor` and `effectiveNodeColor` from Task 1.
- Produces: inspector controls with `[data-theme-swatch]`, `input[name="theme_color"]`, and `[data-theme-reset]`.
- Extends: canvas graph nodes with `color: string | null` and `colorInherited: boolean`.

- [ ] **Step 1: Write failing controller and canvas contract tests**

Assert that editable zone/firewall-zone/VM/firewall inspectors expose palette, custom input, Reset, and accessible state; read-only controls are disabled. Assert canvas source applies `--node-theme-color` to zone containers and machine groups without removing semantic state classes.

```js
assert.match(source, /--node-theme-color/);
assert.match(source, /d\.color/);
```

- [ ] **Step 2: Run focused UI tests and verify RED**

Run: `node --test tests/event-planner-canvas.test.mjs`

Run: `pytest -q tests/test_event_plan_template.py -k 'colour or canvas'`

Expected: FAIL because no colour controls or SVG theme properties exist.

- [ ] **Step 3: Implement inspector controls and event handling**

Add a compact curated palette, render automatic/inherited status, bind swatches and custom input to `setNodeThemeColor`, and bind Reset to remove the explicit entry. Include controls for `zone`, `firewall-zone`, `vm`, and `firewall`; disable them in read-only mode.

```js
const THEME_SWATCHES = ['#06b6d4', '#2563eb', '#7c3aed', '#db2777', '#dc2626', '#ea580c', '#16a34a', '#64748b'];
```

- [ ] **Step 4: Pass effective colours into canvas graph nodes**

During `renderCanvas`, resolve the explicit/inherited colour for every node and include `color` and `colorInherited` fields. Keep default semantic classes when `color` is null.

- [ ] **Step 5: Apply scoped SVG colour variables and CSS styling**

Set `--node-theme-color` only for custom-coloured containers and machines. Use it for restrained body/header/ring/icon accents; keep `.selected`, `.invalid`, and `.system-managed` border/signifier rules dominant. Add keyboard-visible, labelled swatch styles and an active indicator that is not colour-only.

- [ ] **Step 6: Preserve themes through canvas reset and arrange operations**

Update canvas callbacks so coordinate updates merge `nodes` into the current layout while retaining `themes`, including initial automatic layout, `resetLayout`, group drag, individual drag, and `arrangedLayout`.

- [ ] **Step 7: Run focused UI tests and verify GREEN**

Run: `node --test tests/event-planner-state.test.mjs tests/event-planner-canvas.test.mjs tests/event-planner-icon-picker.test.mjs tests/event-planner-icons.test.mjs`

Run: `pytest -q tests/test_event_plan_template.py`

Expected: all tests pass.

- [ ] **Step 8: Commit UI and rendering support**

```bash
git add frontend/static/event-planner.js frontend/static/event-planner-canvas.js frontend/static/event-planner.css tests/event-planner-canvas.test.mjs tests/test_event_plan_template.py
git commit -m "feat: edit zone and VM colours in planner"
```

### Task 4: Full Verification

**Files:**
- Verify only; modify earlier files if verification exposes a regression.

**Interfaces:**
- Consumes the complete feature from Tasks 1-3.
- Produces verified syntax, focused behavior, backend compatibility, and clean diffs.

- [ ] **Step 1: Run JavaScript syntax checks**

Run: `node --check frontend/static/event-planner-state.js`

Run: `node --check frontend/static/event-planner-canvas.js`

Run: `node --check frontend/static/event-planner.js`

Expected: each command exits 0 with no output.

- [ ] **Step 2: Run all planner JavaScript tests**

Run: `node --test tests/event-planner-state.test.mjs tests/event-planner-canvas.test.mjs tests/event-planner-icon-picker.test.mjs tests/event-planner-icons.test.mjs`

Expected: all tests pass.

- [ ] **Step 3: Run the focused backend suite**

Run: `pytest -q tests/test_event_plan_template.py`

Expected: all tests pass.

- [ ] **Step 4: Run project verification**

Run: `docker compose --profile test run --rm tests`

Expected: the complete container test suite passes.

- [ ] **Step 5: Inspect the final diff**

Run: `git diff --check`

Run: `git status --short`

Expected: no whitespace errors and only intentional feature/plan changes remain.

- [ ] **Step 6: Commit any verification fixes**

```bash
git add frontend/static/event-planner-state.js frontend/static/event-planner-canvas.js frontend/static/event-planner.js frontend/static/event-planner.css builder/infrastructure_planner.py tests/event-planner-state.test.mjs tests/event-planner-canvas.test.mjs tests/test_event_plan_template.py
git commit -m "test: verify planner colour customization"
```
