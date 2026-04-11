# Event Plan Preview — Design Spec

## Context

Before starting an event, admins need to inspect exactly what will be deployed: which VMs will be created per team, which modules each VM will receive, how the network topology will look, and what the Caldera attack paths will be. Currently there is no way to preview this without actually deploying. This page provides a full dry-run — no VMs created, no Vultr calls, nothing deployed — so admins can verify the event configuration is correct before committing.

---

## Page

**Route:** `GET /admin/events/{id}/plan`
**Template:** `frontend/templates/event_plan.html` (extends `base.html`)
**Access:** Admin-only (same guard as other admin routes)
**Nav entry:** "Plan Preview" button on each event row in the admin events table

---

## Backend Endpoint

### `POST /admin/events/{id}/plan-preview`

Accepts an optional JSON body with quota overrides for sandbox mode:

```json
{ "quota": {...}, "vm_quota": {...} }
```

Falls back to the event's saved `quota` and `vm_quota` if body fields are omitted. Returns a dry-run plan — no DB writes, no Vultr calls.

**Logic:**

1. Load event. Validate `vm_quota` is set and teams exist.
2. Fetch Vultr plans (for plan sizing and cost estimates).
3. For each VM type × team × count: call `select_modules(quota, library)` once per VM to get that VM's module draw. Call `plan_for_vm(modules, default_plan, vultr_plans)` to get the sized plan. Call `build_attack_tree(modules)` + `serialize_tree(tree)` for target VMs.
4. Build projected topology nodes/links using the same shape as `topology_data()` but with `"status": "projected"` on all VM nodes.
5. Return:

```json
{
  "summary": {
    "total_vms": 12,
    "teams": 3,
    "estimated_monthly_cost": 62.00,
    "total_modules": 108,
    "total_attack_paths": 36
  },
  "teams": ["alpha", "bravo", "charlie"],
  "vm_types": [
    {
      "type_key": "ubuntu_target",
      "role": "target",
      "os": "Ubuntu 24.04 LTS x64",
      "default_plan": "vc2-2c-4gb",
      "region": "ewr",
      "count_per_team": 3,
      "total_count": 9,
      "vms": [
        {
          "hostname": "alpha-ubuntu_target-1",
          "team": "alpha",
          "plan": "vc2-2c-4gb",
          "modules": [ { "id": "...", "name": "...", "type": "...", "difficulty": "...", "points": 150 } ],
          "attack_tree": { "nodes": [...], "edges": [...], "paths": [...] }
        }
      ]
    },
    {
      "type_key": "red_team",
      "role": "attacker",
      "vms": [
        { "hostname": "alpha-red_team-1", "team": "alpha", "plan": "vc2-2c-4gb", "modules": [], "attack_tree": null }
      ]
    }
  ],
  "topology": { "nodes": [...], "links": [...] }
}
```

**Reused functions (no changes needed):**
- `builder/selector.py` → `select_modules()`
- `builder/plan_sizing.py` → `plan_for_vm()`
- `builder/attack_tree.py` → `build_attack_tree()`, `serialize_tree()`
- `builder/module_loader.py` → `load_all_modules()`

---

## Frontend

### Page Header

- Event name + "Plan Preview" title
- Amber "Preview mode — no resources provisioned" badge (pulsing dot)
- Buttons: **Re-roll All** · **Edit Quota** (expands quota drawer) · **Start Event** (links to existing start flow)

### Stats Bar

Five cards: Total VMs · Teams · Est. Cost/mo · Total Modules · Total Attack Paths

### Quota Drawer (collapsible)

Two side-by-side JSON textareas — module quota and vm_quota — pre-populated from saved event values. "Generate Preview" button POSTs overrides to the endpoint and re-renders the page without navigating away.

### Tabs

#### VM Plan

Toggle: **By VM Type** | **By Team**

**By VM Type view:** One collapsible card per VM type. Header shows type name, OS, plan, role badge, total count. Body lists all projected VMs as rows: hostname · plan · module count · module names (truncated). Each row has a 🎲 per-VM re-roll button.

**By Team view:** Grouped by team. Each team section lists its VMs as rows with the same columns. Team color dot matches the topology color.

Both views show a "projected" amber badge on each VM instead of a live status.

#### Network Topology

Reuses the full interactive D3 force graph (drag, zoom, pan, hover tooltips). Differences from live topology:
- All VM nodes rendered with dashed borders + amber stroke (status `"projected"`)
- No IPs shown (not yet assigned)
- Context menu actions that require a real VM (Provision, Destroy, Export Playbook) are hidden; "View Details" is disabled
- Faint "PREVIEW" watermark over the canvas
- No live polling (static, re-rendered only on Re-roll or Generate Preview)

#### Attack Paths

One collapsible section per VM type. Within each section, one `renderAttackTree()` panel per projected VM. Each tree is fed the VM's sampled module draw with all node statuses set to `"pending"`.

- Full interactivity: hover tooltips, click detail panel (read-only)
- Dashed node borders throughout (nothing has run)
- Individual 🎲 per-VM re-roll button POSTs to the same `plan-preview` endpoint with the current quota but a `reroll_vm` hint, or simply re-POSTs the full preview and replaces only that VM's panel client-side. The simpler implementation is a full re-POST that replaces only the affected VM's tree DOM element.
- Attacker VMs show a "No modules — attacker role" placeholder

**Reused JS (no changes needed):**
- `renderAttackTree()` from `attack_tree_partial.html` — accepts `{ nodes, edges, paths }` and renders; projected nodes just all have `status: null` which already renders as pending/gray
- D3 topology code from `topology.html` — adapted for the plan template

---

## Projected Topology Node Shape

Same as live topology but with two differences:

```json
{
  "id": "vm-projected-ubuntu_target-alpha-1",
  "type": "vm",
  "label": "alpha-ubuntu_target-1",
  "hostname": "alpha-ubuntu_target-1",
  "ip": null,
  "status": "projected",
  "os": "Ubuntu 24.04 LTS x64",
  "team_id": "team-1",
  "event_id": "event-5",
  "modules_total": 9,
  "modules_completed": 0
}
```

Frontend maps `"projected"` status → amber dashed border (new `STATUS_COLORS` entry).

---

## Files to Create

- `frontend/templates/event_plan.html` — new page template
- Route handler in `api/routes/admin.py` — `plan_preview()` endpoint
- Page route in `api/main.py` — `GET /admin/events/{id}/plan`

## Files to Modify

- `frontend/templates/admin.html` — add "Plan Preview" button to each event row's action buttons
- `api/routes/admin.py` — add `POST /admin/events/{id}/plan-preview` handler
- `api/main.py` — add `GET /admin/events/{id}/plan` page route

---

## Verification

1. Create a test event with `vm_quota` and at least one team.
2. Navigate to `/admin/events/{id}/plan` — page loads with stats and VM cards.
3. Confirm no VMs appear in the DB (`GET /admin/vms` unchanged).
4. Click Re-roll All — module assignments change, attack trees re-render.
5. Edit quota in the drawer and click Generate Preview — plan updates without navigating away.
6. Topology tab: nodes are dashed amber, draggable, zoomable; context menu shows no Provision/Destroy actions.
7. Attack Paths tab: one tree per VM in each target type section; attacker VMs show placeholder.
8. Per-VM 🎲 button re-rolls one VM without affecting others.
9. Click Start Event — redirects to existing start flow.
