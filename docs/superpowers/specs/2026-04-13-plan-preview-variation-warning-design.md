# Plan Preview: Low Module Variation Warning

## Context

When the module library is small relative to the event quota, `select_modules` produces identical (or near-identical) module assignments across VMs. This makes every VM's attack tree graph look the same in the plan preview, which is confusing — it appears as though each VM is showing the full event graph rather than a per-VM selection.

The module selection logic is correct (per-VM random selection from the quota), but users need visibility into when the module pool is too small for meaningful variation.

## Design

### Backend: Variation Detection

**File**: `api/routes/admin.py` — `plan_preview` endpoint (line 488)

After the VM generation loop (around line 635), before the return statement:

1. For each VM type with `role == "target"`, collect the module ID sets (as frozensets) for every generated VM.
2. Compute `unique_count = len(set(frozensets))` and `total_count = len(frozensets)`.
3. If `unique_count / total_count < 0.5` (fewer than half the VMs have unique module sets), generate a warning.
4. To provide actionable detail, check each quota tier against the available module library: for each `(type, difficulty)` pair, compare `requested_count` vs `available_count`. If `requested / available >= 0.8`, include that tier in the warning as "at capacity".

Add a `warnings: list[str]` field to the response JSON.

**Warning message format**:
```
Low module variation: {identical_count} of {total_count} target VMs have identical module assignments. Tiers at capacity: {tier_list}. Add more modules or reduce quota counts for greater diversity.
```

Where `tier_list` is a comma-separated list like `medium vulnerability (7/7), easy application_external (2/2)`.

### Frontend: Amber Warning Banner

**File**: `frontend/templates/event_plan.html`

1. Add a new `.plan-warnings` styled element below the stats bar (after `plan-stats`, before `quota-drawer`). Styled like the existing `.plan-error` but using amber colors (`var(--amber)`, `var(--amber-glow)`, `var(--amber-dim)`).
2. In `loadPreview()`, after receiving the response, check `data.warnings`. If present and non-empty, render each warning as a list item in the banner and show it. Otherwise hide it.
3. The banner is persistent (does not auto-dismiss) since it reflects a structural limitation, not a transient error. It clears on re-roll.

### CSS Addition

```css
.plan-warnings {
    background: var(--amber-glow);
    border: 1px solid var(--amber-dim);
    border-radius: 6px;
    padding: 12px 16px;
    color: var(--amber);
    font-size: 12px;
    margin-bottom: 16px;
    display: none;
}
.plan-warnings ul {
    margin: 4px 0 0 16px;
    padding: 0;
}
.plan-warnings li {
    margin-bottom: 2px;
}
```

## Files to Modify

| File | Change |
|------|--------|
| `api/routes/admin.py` | Add variation detection after VM generation loop, add `warnings` to response |
| `frontend/templates/event_plan.html` | Add amber warning banner HTML + CSS, render warnings in `loadPreview()` |

## Verification

1. Run `docker compose up -d` and create an event with a quota that requests nearly all available modules (e.g., `{"vulnerability": {"easy": 4, "medium": 7}}`)
2. Create teams and set up vm_quota
3. Navigate to the plan preview page
4. Verify the amber warning banner appears with the correct message identifying saturated tiers
5. Modify the quota to request fewer modules (e.g., `{"vulnerability": {"easy": 2, "medium": 3}}`) and re-roll
6. Verify the warning disappears when sufficient variation exists
