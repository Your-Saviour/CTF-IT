# VM Network Topology Visualization

## Context

The current admin UI displays VMs in a flat table — hostname, IP, team, status. For events with many teams and VMs, this makes it hard to understand the relationships and overall health at a glance. This feature adds a dedicated network topology page (`/admin/topology`) using D3.js force-directed layout that visualizes the event → team → VM hierarchy as an interactive graph with live status updates and inline actions.

D3.js is chosen deliberately over higher-level graph libraries because it will be reused elsewhere in the project.

## Design

### Page: `/admin/topology`

A full-page SVG canvas with a toolbar at the top. Accessible from a new link on the admin page.

### Node Hierarchy

| Node Type | Shape | Size | Visual |
|-----------|-------|------|--------|
| Event | Circle | r=50 | Cyan border, radial glow, event name label |
| Team | Circle | r=35 | Unique color per team, team name label |
| VM | Rounded square (44x44) | — | Server rack SVG icon + OS badge in corner, status-colored border/glow |

### VM Node Icons (Hybrid Style)

Each VM node is a rounded-square containing a monoline server rack icon (3 stacked rectangles with LED dots). A small circular badge in the bottom-right corner shows the OS:
- Linux: penguin icon
- Windows: grid/window icon
- Unknown/Other: generic terminal icon

The entire node's stroke color and glow reflect status:
- Green (`#00ff64`): active
- Amber (`#ffc800`): creating/provisioning
- Red (`#ff3c3c`): failed
- Grey (`#888`): stopped/registered

### Links

Dashed lines connecting event → team → VM. Colored to match the team's assigned color. Opacity 0.3 to avoid visual noise.

### Toolbar

- **Title**: "NETWORK TOPOLOGY" in monospace
- **Event filter**: Dropdown to scope to a single event or "All Events"
- **Zoom indicator**: Shows current zoom level
- **Live indicator**: Pulsing dot showing polling is active

### Interactions

| Action | Target | Result |
|--------|--------|--------|
| Drag | Any node | Repositions node, simulation re-heats |
| Scroll | Canvas | Zoom in/out |
| Drag background | Canvas | Pan |
| Hover | VM node | Tooltip: hostname, IP, OS, module progress (X/Y completed) |
| Hover | Team node | Tooltip: team name, VM count, overall progress |
| Right-click | VM node | Context menu: View Details, Re-provision, Assign Modules, Export Playbook, Destroy |
| Right-click | Team node | Context menu: View Team, Add VM, Delete Team |
| Double-click | Any node | Navigate to detail page |

### Live Polling

- Fetch `/api/topology-data?event_id=X` every 5 seconds
- Diff response against current graph state
- Add new nodes with fade-in animation from center
- Remove deleted nodes with fade-out
- Status changes: smooth color transition (300ms) + pulse animation
- If event filter changes, full re-render with new data

## API

### `GET /api/topology-data`

Query params:
- `event_id` (optional): filter to specific event. Omit for all events.

Response:
```json
{
  "nodes": [
    {"id": "event-1", "type": "event", "label": "Spring CTF", "status": "open"},
    {"id": "team-3", "type": "team", "label": "Alpha", "event_id": "event-1"},
    {"id": "vm-7", "type": "vm", "label": "vm-alpha-01", "hostname": "vm-alpha-01",
     "ip": "45.76.12.34", "status": "active", "os": "Ubuntu 22.04",
     "team_id": "team-3", "event_id": "event-1",
     "modules_total": 8, "modules_completed": 5}
  ],
  "links": [
    {"source": "event-1", "target": "team-3"},
    {"source": "team-3", "target": "vm-7"}
  ]
}
```

Auth: admin-only (same auth as other admin routes).

## Files

### New Files

| Path | Purpose |
|------|---------|
| `frontend/templates/topology.html` | Page template extending base.html, loads D3 + topology.js |
| `frontend/static/js/topology.js` | D3 force graph, node rendering, context menu, polling logic |
| `frontend/static/css/topology.css` | Topology-specific styles (context menu, tooltips, animations) |

### Modified Files

| Path | Change |
|------|--------|
| `api/routes/admin.py` | Add `GET /admin/topology` page route |
| `api/routes/vm.py` | Add `GET /api/topology-data` endpoint |
| `frontend/templates/admin.html` | Add "Network Topology" link/button in the Teams & VMs card |

## D3 Force Configuration

```javascript
const simulation = d3.forceSimulation(nodes)
  .force("link", d3.forceLink(links).id(d => d.id)
    .distance(d => {
      if (d.source.type === "event") return 180;
      return 100;
    }))
  .force("charge", d3.forceManyBody().strength(-300))
  .force("center", d3.forceCenter(width / 2, height / 2))
  .force("collision", d3.forceCollide()
    .radius(d => nodeRadius(d) + 10));
```

## D3 Dependency

Load D3 v7 from CDN (`d3.min.js`) in the topology template. Only the topology page loads it — no impact on other pages. This sets up D3 for reuse in future features.

## Verification

1. Start dev server with `docker compose up -d`
2. Create an event with teams and VMs (or use existing test data)
3. Navigate to `/admin/topology`
4. Verify: nodes render in correct hierarchy, dragging works, zoom/pan works
5. Right-click a VM node — context menu appears with correct actions
6. Click "View Details" — navigates to VM detail page
7. Change a VM's status via the admin UI — topology updates within 5s with color transition
8. Filter by event — only that event's nodes shown
9. Hover a VM — tooltip shows IP, OS, module progress
