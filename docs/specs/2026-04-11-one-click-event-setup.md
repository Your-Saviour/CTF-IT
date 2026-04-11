# One-Click Event Setup (Bulk Operations)

**Date:** 2026-04-11  
**Status:** Proposed  
**Priority:** Medium — major admin time-saver for multi-VM events

---

## Context

Setting up an event after teams and VMs are registered requires: assign modules to each VM, provision each VM, run Caldera setup, deploy agents to each VM. For an event with 20 VMs this is 20 module assignments + 20 provisions + 1 Caldera setup + 20 agent deployments — all sequential clicks with waiting between each. A "Setup Event" action automates everything after manual VM registration.

---

## Design

### 1. What Gets Automated (Sequence)

**Phase 1 — Bulk Module Assignment:**  
For every VM in the event with no modules assigned, run `select_modules()` with the event's quota. Reuses the existing `assign_modules` logic. Also generates `verify_token` per VM (Feature 1 prerequisite).

**Phase 2 — Bulk VM Provisioning:**  
For every VM with modules assigned and status = `registered`, start provisioning. Run provisions concurrently using `asyncio.create_task` (same pattern as single-VM provisioning). Poll until all complete.

**Phase 3 — Caldera Setup:**  
Run the existing `caldera_setup` logic once for the event (generate plugin, copy to mount, restart Caldera, create operation). Runs after provisioning so the module set is final.

**Phase 4 — Bulk Agent Deployment:**  
For every VM that reached status = `active`, deploy the Caldera agent concurrently.

### 2. Backend

New file `api/routes/event_setup.py`.

`POST /admin/events/{event_id}/bulk-setup` (admin-only):
- Validates: event exists, has teams, teams have VMs with IP addresses
- Returns 409 if a setup is already running for this event (check `Event.setup_state` for active phase)
- Returns immediately with `{"status": "started"}`
- Spawns background task `_run_bulk_setup(event_id)`

`GET /admin/events/{event_id}/setup-status` (admin-only):
- Returns current `Event.setup_state` JSON

**Progress state** stored on `Event` as new column `setup_state` (JSON text):

```json
{
  "phase": "provisioning",
  "phases": {
    "assigning_modules": {"status": "done", "count": 10},
    "provisioning": {"status": "running", "done": 3, "total": 10, "failed_vms": [5]},
    "caldera_setup": {"status": "pending"},
    "deploying_agents": {"status": "pending"}
  }
}
```

**Error handling:** If a VM's provision fails, it is recorded in `failed_vms` and bulk setup continues with the remaining VMs. Phase 4 only targets VMs that reached `active` status. Final state is `completed_with_errors` if any VMs failed. Admin investigates and re-provisions failed VMs individually from the VM detail page.

**Double-run prevention:** Endpoint returns 409 if `setup_state.phase` is not `null`, `completed`, `completed_with_errors`, or `failed`.

### 3. UI

**Button:** "Setup Event" added to each event row's actions in `admin.html`. Only shown for events with teams that have registered VMs. Clicking opens a confirmation modal showing: event name, VM count, checklist of 4 phases.

**Progress display:** After starting, the admin page shows an inline progress panel for the event (same card pattern as `vm_detail.html` provisioning steps). 4-phase vertical stepper:

```
✓ Assign Modules     10/10
⟳ Provision VMs       3/10  [VM-5: failed]
○ Caldera Setup
○ Deploy Agents
```

Failed VMs highlighted in red with their hostname. Progress updates via 5-second polling of `/admin/events/{event_id}/setup-status`.

### 4. Reuse of Existing Logic

`_run_provision(vm_id)` in `vm.py` is already a self-contained background function — the bulk setup calls it for each VM in parallel. Same for `_run_deploy_agent(vm_id)`. Caldera setup logic from `api/routes/caldera_setup.py` is extracted into a callable function (currently it is inlined in the route handler).

---

## Files to Create / Modify

| Action | Path |
|--------|------|
| Create | `api/routes/event_setup.py` |
| Modify | `api/models.py` — add `setup_state` column to `Event` |
| Modify | `api/main.py` — register router, add migration for new column |
| Modify | `api/routes/caldera_setup.py` — extract setup logic into a callable function |
| Modify | `frontend/templates/admin.html` — "Setup Event" button, confirmation modal, progress panel |

---

## Verification / Testing

- **Unit:** mock Semaphore and Caldera, create event with teams/VMs, run bulk setup, verify all phases execute in order
- **Error test:** make one VM's provision fail (mock), verify setup continues and reports partial failure in `setup_state`
- **Double-run:** verify second POST to bulk-setup returns 409 while setup is running
- **Integration:** manual end-to-end with real Semaphore/Caldera (documented in TEST_PLAN.md)

---

## Dependencies

- **Feature 1 (VM Verification)** — token generation should be integrated into Phase 1 (bulk module assignment). Not strictly blocking, but Phase 1 should generate `verify_token` per VM if Feature 1 is implemented.
- Caldera setup must work before Phase 4 — no dependency on other features beyond this.
