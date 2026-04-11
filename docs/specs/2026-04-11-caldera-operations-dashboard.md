# Caldera Operations Dashboard & Red Team Status

## Context

The current Caldera integration generates plugins and creates operations, but provides no visibility after setup. Instructors cannot see which attacks succeeded, which VMs are vulnerable, or manage multiple operations. This design adds an operations management layer, a results dashboard, and a separate red team status view — giving instructors real-time visibility into the red team emulation without merging results into the CTF scoring system.

**Relationship to existing specs:** This builds on the "Red-Team VM Attack Model" spec (`2026-04-11-red-team-vm-attack-model.md`) which introduces per-team red-team VMs, remote abilities, and two-phase operations. This spec adds the management and visibility layer on top of that foundation.

**Design decisions:**
- All data fetched from Caldera's REST API on demand — no local duplication
- Red team status is tracked separately from CTF points (parallel system, not merged)
- Operations can be scoped per-event (all VMs) or per-VM (single target)
- Agent groups use `event-{id}` naming for event-level targeting; per-VM ops target by agent `paw`

---

## Step 1: Agent Group Targeting

**Files:** `api/routes/vm.py`

Modify `_run_deploy_agent()` to set agent group based on scope instead of hardcoded `"red"`:

- Change the Sandcat `-group` argument from `"red"` to `"event-{event_id}"` (derived from VM → Team → Event)
- This enables per-event operations to target only agents belonging to that event
- Per-VM operations use Caldera's agent `paw` identifier instead of group filtering

**Note:** If the Red-Team VM Attack Model spec is implemented first, the group assignment should incorporate the VM role: `"event-{event_id}"` for red-team VMs, `"target-{event_id}"` for target VMs. This spec assumes the simpler case where all agents in an event share a group.

---

## Step 2: Caldera API Service Layer

**File:** `api/services/caldera.py` (new)

Extract Caldera API interactions into a reusable service (similar pattern to `api/services/semaphore.py`):

```python
class CalderaClient:
    def __init__(self, base_url: str, api_key: str)

    # Operations
    async def list_operations(self) -> list[dict]
    async def get_operation(self, op_id: str, include_chain: bool = False) -> dict
    async def create_operation(self, name: str, adversary_id: str, planner_id: str, group: str, source_id: str | None = None) -> dict
    async def delete_operation(self, op_id: str) -> None

    # Agents
    async def list_agents(self, group: str | None = None) -> list[dict]
    async def get_agent_by_ip(self, ip: str) -> dict | None

    # Abilities & Adversaries
    async def list_abilities(self) -> list[dict]
    async def list_adversaries(self) -> list[dict]
    async def get_adversary_by_name(self, name: str) -> dict | None

    # Planners & Sources
    async def get_planner_by_name(self, name: str) -> dict
    async def ensure_source(self, source_id: str, name: str) -> None
```

Refactor `api/routes/caldera_setup.py` to use `CalderaClient` instead of inline `httpx` calls. Reuse `_get_caldera_api_key()` from `api/routes/vm.py` (move to the service).

---

## Step 3: Operation Management Endpoints

**File:** `api/routes/caldera_ops.py` (new)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/admin/caldera/operations` | GET | List all operations with summary stats |
| `/admin/caldera/operations` | POST | Create operation scoped to event or VM |
| `/admin/caldera/operations/{op_id}` | GET | Operation detail with per-agent results |
| `/admin/caldera/operations/{op_id}` | DELETE | Remove an operation |

### POST `/admin/caldera/operations`

Request body:
```json
{
  "event_id": 1,           // event-wide operation (required if no vm_id)
  "vm_id": 5,              // VM-specific operation (required if no event_id)
  "adversary_name": "CTF Full Exploit Chain"  // optional, defaults to full chain
}
```

Logic:
- **Per-event**: sets `group = "event-{event_id}"`, uses the event's quota to find the matching adversary
- **Per-VM**: looks up the VM's agent `paw` from Caldera's agent list (matched by IP), generates a VM-specific adversary from the VM's `VMModule` assignments, creates operation targeting that agent's group or uses Caldera's agent-specific operation features

### GET `/admin/caldera/operations/{op_id}`

Returns operation details plus chain (the list of executed links/abilities). Each link includes:
- Ability name, tactic, technique
- Agent paw (mapped back to VM hostname via IP lookup)
- Status (success/fail/timeout)
- Command output (truncated)
- Timestamp

The ability UUID → module mapping uses the deterministic `_ability_uuid(module_id, phase)` from `builder/caldera.py` to reverse-lookup which CTF module each result belongs to.

---

## Step 4: Red Team Admin Page

**File:** `frontend/templates/caldera_dashboard.html` (new)

A new admin page at `/admin/caldera` with three sections:

### 4a. Operations Table
- Columns: Name, State (queued/running/finished/cleanup), Agent Count, Abilities Run, Start Time, Actions
- "Create Operation" button opens a modal with event/VM selector
- Each row links to operation detail view
- Auto-refreshes every 10s while any operation is running

### 4b. Operation Detail View
Route: `/admin/caldera/operation/{op_id}`

- Operation metadata (name, state, adversary, planner, start/finish times)
- Per-agent results table:
  - Agent paw → VM hostname (resolved via IP match to VM records)
  - Abilities executed: name, status (color-coded), output preview (expandable)
  - Summary: X succeeded, Y failed, Z pending
- Timeline view showing ability execution order

### 4c. Event Red Team Summary
Accessible from the event detail or as a tab on the operations page:
- Per-VM row: hostname, team, total attacks, exploits succeeded, exploits failed, last run
- Color-coded status: green (all attacks failed = well-defended), red (exploits succeeded = vulnerable), gray (not yet attacked)
- Drill-down shows which specific modules were exploited vs. defended per VM

---

## Step 5: VM Detail Page Integration

**File:** `frontend/templates/vm_detail.html`

Add a "Red Team Status" card below the existing module progress card:

- Shows results from the most recent operation targeting this VM
- Per-module breakdown: module name, attack phase (recon/exploit), result (success/fail), timestamp
- "Run Attack" button to create a per-VM operation directly from this page
- Links to full operation detail page

---

## Step 6: Navigation & Registration

**File:** `api/main.py`, `api/routes/admin.py`, `frontend/templates/admin.html`

- Register `caldera_ops.router` in `main.py`
- Add "Red Team" link to admin navigation/sidebar
- Add Caldera dashboard link to admin page service links section
- Add page route for `/admin/caldera` and `/admin/caldera/operation/{op_id}` in admin routes

---

## Implementation Order

1. **Caldera service layer** (Step 2) — foundation, refactors existing code
2. **Agent group targeting** (Step 1) — depends on service layer
3. **Operation management endpoints** (Step 3) — depends on service layer
4. **Red Team admin page** (Step 4) — depends on Step 3 endpoints
5. **VM detail integration** (Step 5) — depends on Steps 3-4
6. **Navigation & registration** (Step 6) — depends on Steps 4-5

Steps 2+1 are sequential. Steps 3+4 can partially overlap (endpoints first, then UI).

---

## Key Files to Modify

| File | Changes |
|------|---------|
| `api/services/caldera.py` | New — reusable Caldera API client |
| `api/routes/vm.py` | Agent group from `"red"` → `"event-{event_id}"` |
| `api/routes/caldera_setup.py` | Refactor to use `CalderaClient` |
| `api/routes/caldera_ops.py` | New — operation CRUD + results endpoints |
| `api/routes/admin.py` | Page routes for Caldera dashboard |
| `api/main.py` | Register `caldera_ops.router` |
| `frontend/templates/caldera_dashboard.html` | New — operations list + detail pages |
| `frontend/templates/vm_detail.html` | Red team status card |
| `frontend/templates/admin.html` | Navigation link |
| `builder/caldera.py` | Expose `_ability_uuid` for reverse lookups (or add a mapping function) |

---

## Existing Code to Reuse

- `builder/caldera.py:_ability_uuid()` — deterministic UUID for module→ability mapping (line ~20)
- `builder/caldera.py:generate_caldera_export()` — plugin generation for per-VM adversaries
- `api/routes/caldera_setup.py:_get_caldera_api_key()` → move to service
- `api/routes/caldera_setup.py:_create_operation()` → absorb into `CalderaClient`
- `api/routes/vm.py:_get_caldera_api_key()` → deduplicate, use service
- `api/services/semaphore.py` — pattern reference for service client structure

---

## Verification

1. **Service layer**: Refactored `caldera-setup` endpoint works identically to before
2. **Agent groups**: Deploy agent to a VM, verify it registers with `event-{N}` group in Caldera
3. **Create per-event operation**: `POST /admin/caldera/operations` with `event_id`, verify operation targets correct agent group
4. **Create per-VM operation**: `POST /admin/caldera/operations` with `vm_id`, verify operation targets the specific VM's agent
5. **List operations**: `GET /admin/caldera/operations` returns all operations with correct metadata
6. **Operation results**: Run an operation, then `GET /admin/caldera/operations/{id}` — verify per-agent ability results are returned with module name mapping
7. **Dashboard UI**: Navigate to `/admin/caldera`, verify operations table renders, auto-refreshes during running operations
8. **Operation detail UI**: Click into an operation, verify per-agent results with color-coded status
9. **VM detail red team card**: View a VM that has been attacked, verify module-level attack results show
10. **Backward compatibility**: Existing `POST /admin/caldera-setup` still works unchanged
