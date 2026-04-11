# VM Quota System & Base Configurations Design

## Context

CTF-IT recently added the ability to create Vultr VMs for events, but VM creation is entirely manual — admins pick the plan, OS, and region per-VM, then manually assign modules and trigger provisioning. For events with multiple teams each needing several VMs, this is tedious and error-prone.

This design introduces a **VM quota system** (analogous to the existing module quota) where admins define a spec of VM types and counts per event. When the event starts, the platform automatically provisions VMs for every team, assigns modules, sizes plans based on resource requirements, and deploys everything — zero manual steps after the initial configuration.

## VM Quota Schema

A new `vm_quota` JSON field on the `Event` model. Structure:

```json
{
  "ubuntu_target": {
    "os": "Ubuntu 24.04 LTS x64",
    "default_plan": "vc2-1c-2gb",
    "count": 3,
    "role": "target",
    "region": "ewr"
  },
  "red_team": {
    "os": "Ubuntu 24.04 LTS x64",
    "default_plan": "vc2-2c-4gb",
    "count": 1,
    "role": "attacker"
  }
}
```

### Fields per VM type entry

| Field | Required | Description |
|---|---|---|
| `os` | Yes | Vultr OS name (e.g., "Ubuntu 24.04 LTS x64") |
| `default_plan` | Yes | Vultr plan ID (e.g., "vc2-1c-2gb"). Used unless modules require more resources. |
| `count` | Yes | Number of VMs of this type per team |
| `role` | Yes | `"target"` (gets modules assigned + provisioned) or `"attacker"` (bare OS, no modules) |
| `region` | No | Vultr region override. Falls back to `VULTR_DEFAULT_REGION` env var. |

### Validation rules

- Top level must be a dict with at least one entry.
- Each key is a slug identifier (alphanumeric + underscores).
- Each value must contain `os`, `default_plan`, `count` (positive int), and `role` (`"target"` or `"attacker"`).
- `region` is optional string.

Validation function: `builder/vm_quota_validation.py` → `validate_vm_quota(vm_quota: dict) -> list[str]`, returning error strings (empty = valid). Mirrors the pattern in `builder/quota_validation.py`.

## Data Model Changes

### Event model (`api/models.py`)

Add one field:

```python
vm_quota: Mapped[str] = mapped_column(Text, nullable=True)  # JSON string
```

### VM model (`api/models.py`)

Add one field:

```python
vm_type: Mapped[str] = mapped_column(String(64), nullable=True)  # key from vm_quota
```

This traces which VM quota entry created the VM.

### Module YAML — optional resource fields

Add optional fields to module YAML definitions:

```yaml
min_ram_mb: 2048    # optional, default 0
min_vcpu: 2         # optional, default 0
```

These are read by the module loader and available on the `Module` dataclass. Only relevant for plan sizing — modules without these fields contribute 0 to resource requirements.

## Plan Sizing Logic

New function in `builder/plan_sizing.py`:

```
plan_for_vm(modules: list[Module], default_plan: str, available_plans: list[dict]) -> str
```

1. Look up `default_plan` in `available_plans` to get its RAM and vCPU.
2. Sum `min_ram_mb` and `min_vcpu` across all assigned modules.
3. Required RAM = max(default_plan RAM, module total RAM). Same for vCPU.
4. Pick the cheapest Vultr plan from `available_plans` that meets both requirements.
5. If no plan fits, return the largest available plan and log a warning.

The `available_plans` list is fetched once from the Vultr API at the start of the provisioning batch and cached in memory for the duration.

## Event Start Orchestration

### Modified endpoint: `POST /admin/events/{event_id}/start`

Currently at `api/routes/admin.py:373`. The endpoint currently just flips `status` to `"open"`. New behavior:

1. Validate event has `vm_quota` defined (if not, start as before — backward compatible for Docker-only events).
2. Validate teams exist for the event.
3. Set `event.status = "open"`.
4. Kick off `_provision_event_vms(event_id)` as a background task.
5. Return `{"status": "started", "provisioning": true, "vm_count": total_vms}`.

### Background task: `_provision_event_vms(event_id)`

New function in `api/routes/vm.py` (or a new `api/services/vm_orchestrator.py`):

```
For each team in event.teams:
    For each (vm_type_key, vm_spec) in vm_quota:
        For i in range(vm_spec["count"]):
            1. Create VM record:
               - hostname = "{team.name}-{vm_type_key}-{i+1}"
               - os = vm_spec["os"]
               - status = "creating"
               - vm_type = vm_type_key
               - vultr_plan = vm_spec["default_plan"]
               - vultr_region = vm_spec.get("region", VULTR_DEFAULT_REGION)
               - team_id, event_id

            2. If role == "target":
               - Run select_modules(event.quota, library)
               - Create VMModule records
               - Compute actual plan via plan_for_vm()
               - Update vm.vultr_plan if upgraded

            3. Kick off _run_vultr_create(vm.id) as background thread
```

All Vultr creation tasks run concurrently (each in its own thread via `asyncio.to_thread`).

### Auto-provision after Vultr creation

Modify `_run_vultr_create()` to chain into module provisioning for target VMs:

After the VM gets an IP (status transitions to `"registered"`):
- If `vm.vm_type` is set and the VM has VMModules assigned → automatically call `_run_provision(vm.id)`
- This eliminates the manual "Provision" button click for auto-created VMs

Attacker VMs transition directly to `status = "active"` after Vultr creation (no modules to provision).

### Error handling

- Each VM provisions independently. One failure doesn't block others.
- Failed VMs get `status = "failed"` with `provision_error` populated.
- Admin can retry failed VMs individually via existing provision/create-vultr endpoints.

## Event Provisioning Status

### New endpoint: `GET /admin/events/{event_id}/provision-status`

Returns aggregate provisioning progress:

```json
{
  "total": 12,
  "creating": 3,
  "registered": 2,
  "provisioning": 4,
  "active": 2,
  "failed": 1,
  "vms": [
    {
      "id": 1,
      "hostname": "alpha-ubuntu_target-1",
      "team": "Alpha",
      "vm_type": "ubuntu_target",
      "status": "provisioning",
      "provision_step": "running_playbook",
      "ip_address": "45.76.1.2"
    }
  ]
}
```

The admin UI polls this endpoint to show real-time progress.

## Admin UI Changes

### Event form — VM Quota editor

On the event create/edit page (`frontend/templates/admin.html`), add a **VM Quota** section below the existing module quota editor.

- Dynamic form with an "Add VM Type" button.
- Each row: type name (text), OS (dropdown from Vultr API), default plan (dropdown from Vultr API), count (number), role (dropdown: Target/Attacker), region (optional dropdown).
- Rows are removable with an "X" button.
- Form serializes to `vm_quota` JSON on save.
- OS and plan dropdowns are populated by fetching `/admin/vultr/os` and `/admin/vultr/plans` on page load (same endpoints used by the existing Vultr creation form).

### Event start — confirmation modal

When admin clicks "Start Event" on an event that has `vm_quota`:
- Modal shows: "This will create X VMs across Y teams. Estimated cost: $Z/mo. Proceed?"
- Cost calculated from plan monthly costs times VM count times team count.
- On confirm, calls `POST /admin/events/{event_id}/start`.

### Provisioning dashboard

After event start, redirect to `/admin/events/{event_id}/provision-status` page (or an inline section on the event detail view):
- Table of all VMs grouped by team.
- Per-VM: hostname, type, status badge (with pulse animation for in-progress states), provision step, IP (when available).
- Overall progress bar: "8/12 VMs active".
- Polls `GET /admin/events/{event_id}/provision-status` every 5 seconds.
- "Retry Failed" button that re-triggers provisioning for all failed VMs.

### VM detail page

- Show `vm_type` in the connection info section.
- Existing functionality unchanged.

## File Changes Summary

| File | Change |
|---|---|
| `api/models.py` | Add `vm_quota` to Event, `vm_type` to VM |
| `builder/vm_quota_validation.py` | New file — validate vm_quota JSON |
| `builder/plan_sizing.py` | New file — plan_for_vm() function |
| `builder/module_loader.py` (or equivalent) | Read `min_ram_mb`/`min_vcpu` from module YAML |
| `api/routes/admin.py` | Modify start_event to trigger VM provisioning; add vm_quota to event CRUD; add provision-status endpoint |
| `api/routes/vm.py` | Add `_provision_event_vms()` orchestrator; modify `_run_vultr_create()` to auto-chain provisioning for target VMs |
| `frontend/templates/admin.html` | VM quota editor on event form; start confirmation modal; provisioning dashboard |
| `frontend/templates/vm_detail.html` | Show vm_type field |

## Verification

1. Create an event with both a module quota and a vm_quota (e.g., 2 ubuntu targets + 1 attacker per team).
2. Create 2 teams for the event.
3. Start the event.
4. Verify: 6 VMs created (2 teams x 3 VMs each).
5. Verify: target VMs have modules assigned, attacker VMs do not.
6. Verify: target VM plans are upgraded if modules require more resources than the default plan.
7. Verify: all VMs progress through creating → registered → provisioning → active (targets) or creating → active (attackers).
8. Verify: provisioning dashboard shows real-time progress.
9. Verify: a failed VM can be retried individually.
10. Verify: events without vm_quota start as before (backward compatible).
