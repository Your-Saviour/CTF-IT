# Topology Tiered Layout Redesign

**Date:** 2026-04-12

## Context

The current network topology page uses a D3 force-directed simulation to display an event→team→VM hierarchy. The force-directed layout groups teams around the event node via link forces — which is organic but not particularly readable. The key problems driving this redesign:

1. All teams share an identical VM setup, so displaying every team as a separate node cluster adds visual noise without adding information.
2. The force simulation layout doesn't look like a traditional network diagram — it floats and settles rather than presenting a clean, readable structure.
3. The platform will eventually support firewalls and network segments; the layout approach should be designed to accommodate that without a rewrite.

**Goal:** Replace the force-directed graph with a static tiered layout (WAN → Event → VMs) that looks like a traditional Cisco/Visio network diagram, removes per-team duplication, and uses a layout algorithm structured for future extension.

---

## Design

### Layout Engine

Replace the D3 force simulation entirely with a `calculateLayout()` function that assigns fixed `x`/`y` positions to nodes. D3 is still used for SVG rendering, zoom/pan, and drag, but there is no physics.

Each node is assigned three layout properties before rendering:

- `tier` — integer, controls vertical position (0 = top)
- `group` — string key, controls horizontal clustering within a tier
- `index` — integer, position within the group

`calculateLayout()` maps these to pixel coordinates:

```
x = groupCenterX + (index - groupSize/2) * NODE_SPACING
y = TIER_Y[tier]
```

This means adding network segments later is a matter of assigning nodes to groups — the math handles placement automatically. For now, all VMs share a single group (`"default"`).

**Tier Y positions (approximate, tuned to viewport):**

| Tier | Label | Y |
|------|-------|---|
| 0 | INTERNET / WAN | 90 |
| 1 | EVENT | 210 |
| 2 | VMs | 370 |

### Node Changes

**WAN node** — new synthetic node, created client-side (not from the API). Rendered as a dashed circle with a globe/⊕ icon and "INTERNET" label. Grey color (`#555`). Always present; links down to each event node.

**Event node** — unchanged visually, but gains a `×N teams` pill badge rendered below the event name. `N` comes from a new `team_count` field on the event node returned by the API.

**VM nodes** — no visual changes. Same rounded rect, server rack icon, status colors, OS badge, labels.

**Team nodes** — removed entirely from both the API response and the renderer.

### Link Changes

- Remove the dashed stroke style (`stroke-dasharray: 4 4`) — all links are solid lines
- WAN→Event: `stroke-width: 2`, color `#2a2a2a`
- Event→VM: `stroke-width: 1.5`, color `#1e1e1e`
- Remove team-colored link strokes (links no longer inherit team color)

### Tier Labels

Faint labels on the left edge of the canvas, one per tier row:

```
INTERNET / WAN    ─────────────────────
EVENT             ─────────────────────
VMs               ─────────────────────
```

Labels are static text elements, not data-driven. Color: `#2a2a2a` (very subtle). A 1px `#111` horizontal rule runs from after the label to the right edge.

---

## API Changes (`/admin/topology-data`)

**`api/routes/vm.py` — `get_topology_data()`**

1. **Remove team nodes** from the `nodes` list.
2. **Add `team_count`** field to each event node (count of teams in that event).
3. **Return only first-team VMs** — query VMs for the first team in the event only. If there are no teams/VMs, return no VM nodes (no crash).
4. **Change links** — emit `event→vm` links directly. Remove `event→team` and `team→vm` links.

No schema changes. The response shape is the same (`{nodes, links}`); team nodes are simply absent.

---

## What Stays the Same

- Zoom/pan (D3 zoom, scale 0.2–4, zoom badge in toolbar)
- VM tooltips (hostname, IP, OS, status, module progress bar)
- Context menus on VM nodes (View Details, Provision, Assign Modules, Export Playbook, Destroy)
- Event filter dropdown (populates from `/admin/events`)
- Live polling every 5 seconds — on data change, `calculateLayout()` reruns and node positions update; status-only changes still trigger CSS pulse animation
- Double-click to navigate to VM detail page

---

## Future Extension: Firewalls & Segments

When network segments are added:

1. Add a `segment` field to the VM model (e.g., `"dmz"`, `"internal"`).
2. Map segment → `group` in the topology data endpoint.
3. Add firewall nodes between tiers (new node type, new tier row).
4. `calculateLayout()` automatically handles multi-group tiers — VMs in the same segment cluster together, different segments space apart.

No changes to the layout algorithm itself — only the node data and tier count changes.

---

## Files to Change

| File | Change |
|------|--------|
| `api/routes/vm.py` | `get_topology_data()` — remove teams, add `team_count`, return first-team VMs only, simplify links |
| `frontend/templates/topology.html` | Remove force simulation; add `calculateLayout()`; add WAN node; add tier labels; update link rendering; add `×N teams` badge on event node |

---

## Verification

1. Start the stack: `docker compose up -d`
2. Create an event with 2+ teams, each with VMs provisioned
3. Navigate to `/admin/topology`
4. Confirm: WAN node at top, event node with `×N teams` badge in middle, VMs in a flat row at bottom — no team nodes visible
5. Confirm: tier labels visible on left edge
6. Confirm: hovering a VM shows tooltip with correct hostname/IP/status/modules
7. Confirm: right-click context menu works on VM nodes
8. Confirm: event filter dropdown scopes the graph correctly
9. Confirm: live polling — change a VM's status in DB and verify the graph updates within 5 seconds without a page reload
10. Confirm: zoom/pan still works
