# Topology Tiered Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the D3 force-directed topology graph with a static tiered layout (WAN → Event → VMs flat) that removes per-team duplication and looks like a traditional network diagram.

**Architecture:** The force simulation is removed entirely. A `calculateLayout()` function assigns fixed `x`/`y` positions using `{ tier, group, index }` properties. A synthetic WAN node is injected client-side. The API endpoint is simplified to return event→vm links directly with no team nodes.

**Tech Stack:** Python/FastAPI (api/routes/vm.py), D3.js v7 (topology.html inline script), Jinja2 templates.

> **Note on TDD:** This feature is D3 SVG rendering code with no unit-testable logic. Tests are replaced by explicit manual verification steps at each task boundary.

---

## File Map

| File | Change |
|------|--------|
| `api/routes/vm.py` | Modify `topology_data()`: remove teams, add `team_count`, first-team VMs only, event→vm links |
| `frontend/templates/topology.html` | Replace force sim with `calculateLayout()`; add WAN node + tier labels; update link/event node rendering; remove team rendering |

---

### Task 1: Update the topology API endpoint

**Files:**
- Modify: `api/routes/vm.py:1528-1596`

- [ ] **Step 1: Replace `topology_data()` with the new implementation**

  In `api/routes/vm.py`, replace lines 1528–1596 (`@router.get("/topology-data")` through `return {"nodes": nodes, "links": links}`) with:

  ```python
  @router.get("/topology-data")
  async def topology_data(
      request: Request,
      event_id: int = None,
      db: Session = Depends(get_db),
  ):
      admin = require_admin(request, db)
      if not admin:
          return JSONResponse({"error": "forbidden"}, status_code=403)

      eq = db.query(Event)
      if event_id:
          eq = eq.filter(Event.id == event_id)
      else:
          eq = eq.filter(Event.status != "draft")
      events = eq.all()

      nodes = []
      links = []

      for event in events:
          event_node_id = f"event-{event.id}"
          teams = db.query(Team).filter(Team.event_id == event.id).all()
          team_count = len(teams)

          nodes.append({
              "id": event_node_id,
              "type": "event",
              "label": event.name,
              "status": event.status,
              "team_count": team_count,
          })

          # Show VMs from first team only (canonical setup — all teams identical)
          first_team = teams[0] if teams else None
          if first_team:
              vms = db.query(VM).filter(VM.team_id == first_team.id).all()
              for vm in vms:
                  total = len(vm.modules)
                  completed = sum(1 for m in vm.modules if m.completed)
                  vm_node_id = f"vm-{vm.id}"
                  nodes.append({
                      "id": vm_node_id,
                      "type": "vm",
                      "label": vm.hostname or f"vm-{vm.id}",
                      "hostname": vm.hostname,
                      "ip": vm.ip_address,
                      "status": vm.status,
                      "os": vm.os,
                      "event_id": event_node_id,
                      "modules_total": total,
                      "modules_completed": completed,
                  })
                  links.append({"source": event_node_id, "target": vm_node_id})

      return {"nodes": nodes, "links": links}
  ```

- [ ] **Step 2: Verify the endpoint manually**

  Start the API server (or use docker compose) and check the response:

  ```bash
  curl -s http://localhost:8080/admin/topology-data \
    -H "Cookie: <your admin session cookie>" | python3 -m json.tool
  ```

  Expected: Response has `nodes` with `type: "event"` and `type: "vm"` only (no `type: "team"`). Event nodes have `team_count`. Links connect `event-N` directly to `vm-N`. If there are no teams/VMs, nodes and links are empty arrays.

- [ ] **Step 3: Commit**

  ```bash
  git add api/routes/vm.py
  git commit -m "feat: simplify topology API - remove teams, add team_count, first-team VMs only"
  ```

---

### Task 2: Replace force simulation with `calculateLayout()`

**Files:**
- Modify: `frontend/templates/topology.html` (the `<script>` block, lines ~305–854)

This task replaces the simulation infrastructure. The rendering functions (`renderNodes`, `renderLinks`, etc.) are untouched here — that comes in Task 3.

- [ ] **Step 1: Remove the `simulation` state variable and `NODE_RADIUS` constant**

  In the `// ── Constants ──` section (around line 306), remove:
  ```javascript
  const NODE_RADIUS = { event: 50, team: 35, vm: 22 };
  ```

  In the `// ── State ──` section (around line 321), remove:
  ```javascript
  let simulation = null;
  ```

- [ ] **Step 2: Add the `TIER_Y` constant and `calculateLayout()` function**

  Add this block immediately after the `STATUS_COLORS` constant (after line 317):

  ```javascript
  const TIER_Y = { wan: 90, event: 210, vm: 370 };
  const VM_H_SPACING = 110; // min px between VM node centers

  // Assigns x/y to each node using tier+group layout.
  // tier controls vertical band; group controls horizontal clustering within a tier.
  // Structure is designed to extend to firewalls/segments by adding new tiers/groups.
  function calculateLayout(nodes) {
      const width = svgEl.clientWidth || 800;

      const wanNodes  = nodes.filter(n => n.type === 'wan');
      const eventNodes = nodes.filter(n => n.type === 'event');
      const vmNodes   = nodes.filter(n => n.type === 'vm');

      // WAN — always centered
      wanNodes.forEach(n => {
          n.x = width / 2;
          n.y = TIER_Y.wan;
      });

      // Events — spread evenly across width
      const eCount = eventNodes.length;
      eventNodes.forEach((n, i) => {
          n.x = (i + 1) * (width / (eCount + 1));
          n.y = TIER_Y.event;
      });

      // VMs — per event group, centered under their event node
      eventNodes.forEach(evNode => {
          const vms = vmNodes.filter(v => v.event_id === evNode.id);
          const count = vms.length;
          if (count === 0) return;
          const totalW = (count - 1) * VM_H_SPACING;
          vms.forEach((v, i) => {
              v.x = evNode.x - totalW / 2 + i * VM_H_SPACING;
              v.y = TIER_Y.vm;
          });
      });
  }
  ```

- [ ] **Step 3: Replace `updateGraph()` to use `calculateLayout()` instead of the force simulation**

  Replace the entire `updateGraph()` function (lines ~391–448) with:

  ```javascript
  function updateGraph(data) {
      if (!data) return;

      // Inject synthetic WAN node (client-side only)
      const wanNode = { id: 'wan', type: 'wan', label: 'INTERNET' };
      const allNodes = [wanNode, ...data.nodes];

      // Add WAN→event links
      const eventIds = data.nodes.filter(n => n.type === 'event').map(n => n.id);
      const wanLinks = eventIds.map(eid => ({ source: 'wan', target: eid }));
      const allLinks = [...wanLinks, ...data.links];

      const oldIds = new Set(currentNodes.map(n => n.id));
      const structureChanged = allNodes.length !== currentNodes.length ||
          allNodes.some(n => !oldIds.has(n.id));

      if (structureChanged) {
          currentNodes = allNodes.map(n => ({ ...n }));
          currentLinks = allLinks.map(l => ({ ...l }));
          calculateLayout(currentNodes);
          renderLinks();
          renderNodes();
          applyPositions();
      } else {
          // Status-only update: merge new data into existing nodes
          data.nodes.forEach(newNode => {
              const existing = currentNodes.find(n => n.id === newNode.id);
              if (existing) {
                  const statusChanged = existing.status !== newNode.status;
                  Object.assign(existing, newNode, { x: existing.x, y: existing.y });
                  if (statusChanged) {
                      nodeGroup.selectAll('.node-group')
                          .filter(d => d.id === existing.id)
                          .classed('node-pulse', true)
                          .each(function() {
                              setTimeout(() => d3.select(this).classed('node-pulse', false), 4500);
                          });
                  }
              }
          });
          updateNodeVisuals();
      }
  }
  ```

- [ ] **Step 4: Add `applyPositions()` — replaces the simulation `ticked()` function**

  Replace the `ticked()` function (lines ~604–613) with:

  ```javascript
  function applyPositions() {
      // Resolve link source/target from node id strings to node objects
      currentLinks.forEach(l => {
          if (typeof l.source === 'string') l.source = currentNodes.find(n => n.id === l.source) || l.source;
          if (typeof l.target === 'string') l.target = currentNodes.find(n => n.id === l.target) || l.target;
      });

      linkGroup.selectAll('line')
          .attr('x1', d => (typeof d.source === 'object' ? d.source : currentNodes.find(n => n.id === d.source))?.x ?? 0)
          .attr('y1', d => (typeof d.source === 'object' ? d.source : currentNodes.find(n => n.id === d.source))?.y ?? 0)
          .attr('x2', d => (typeof d.target === 'object' ? d.target : currentNodes.find(n => n.id === d.target))?.x ?? 0)
          .attr('y2', d => (typeof d.target === 'object' ? d.target : currentNodes.find(n => n.id === d.target))?.y ?? 0);

      nodeGroup.selectAll('.node-group')
          .attr('transform', d => `translate(${d.x ?? 0},${d.y ?? 0})`);
  }
  ```

- [ ] **Step 5: Update the drag handlers to remove simulation references**

  Replace the three drag handler functions (lines ~616–630) with:

  ```javascript
  function dragStarted(event, d) {
      hideContextMenu();
      d._dragging = true;
  }
  function dragged(event, d) {
      d.x = event.x;
      d.y = event.y;
      applyPositions();
  }
  function dragEnded(event, d) {
      d._dragging = false;
  }
  ```

- [ ] **Step 6: Update the event filter reset to remove `simulation.stop()`**

  Replace the `eventFilter.addEventListener('change', ...)` block (lines ~827–836) with:

  ```javascript
  eventFilter.addEventListener('change', () => {
      currentEventFilter = eventFilter.value;
      currentNodes = [];
      currentLinks = [];
      linkGroup.selectAll('*').remove();
      nodeGroup.selectAll('*').remove();
      refreshData();
  });
  ```

- [ ] **Step 7: Verify layout renders without errors**

  Open `/admin/topology` in a browser. Check the browser console for JS errors. The graph may look rough (WAN node not yet rendered, links still dashed) — that's fine. Confirm no exceptions.

- [ ] **Step 8: Commit**

  ```bash
  git add frontend/templates/topology.html
  git commit -m "feat: replace force simulation with calculateLayout() static tiered layout"
  ```

---

### Task 3: Add WAN node, tier labels, and update link rendering

**Files:**
- Modify: `frontend/templates/topology.html`

- [ ] **Step 1: Update the CSS — remove dashed links, add tier label style**

  In the `/* Links */` CSS section (around line 114), replace:

  ```css
  .topo-link {
      stroke-dasharray: 4 4;
      fill: none;
      opacity: 0.3;
  }
  ```

  with:

  ```css
  .topo-link {
      fill: none;
      opacity: 0.6;
  }
  .topo-tier-label {
      font-family: 'JetBrains Mono', monospace;
      font-size: 9px;
      fill: #2a2a2a;
      text-transform: uppercase;
      letter-spacing: 2px;
      pointer-events: none;
      user-select: none;
  }
  ```

- [ ] **Step 2: Add a `tierGroup` layer to the SVG container**

  In the section where `linkGroup` and `nodeGroup` are created (around lines 336–338), add a tier label group **before** `linkGroup`:

  ```javascript
  const tierGroup = container.append('g').attr('class', 'tiers');
  const linkGroup = container.append('g').attr('class', 'links');
  const nodeGroup = container.append('g').attr('class', 'nodes');
  ```

- [ ] **Step 3: Add `renderTierLabels()` function**

  Add this function immediately after the `applyPositions()` function:

  ```javascript
  function renderTierLabels() {
      tierGroup.selectAll('*').remove();
      const labels = [
          { y: TIER_Y.wan,   text: 'INTERNET / WAN' },
          { y: TIER_Y.event, text: 'EVENT' },
          { y: TIER_Y.vm,    text: 'VMs' },
      ];
      const width = svgEl.clientWidth || 800;
      labels.forEach(({ y, text }) => {
          const labelY = y - 30;
          // Horizontal rule
          tierGroup.append('line')
              .attr('x1', 0).attr('y1', labelY + 4)
              .attr('x2', width).attr('y2', labelY + 4)
              .attr('stroke', '#111').attr('stroke-width', 1);
          // Label text
          tierGroup.append('text')
              .attr('class', 'topo-tier-label')
              .attr('x', 14).attr('y', labelY)
              .text(text);
      });
  }
  ```

- [ ] **Step 4: Update `renderLinks()` to use solid lines with tier-appropriate stroke widths**

  Replace the `renderLinks()` function (lines ~451–463) with:

  ```javascript
  function renderLinks() {
      linkGroup.selectAll('line').remove();
      linkGroup.selectAll('line')
          .data(currentLinks)
          .enter()
          .append('line')
          .attr('class', 'topo-link')
          .attr('stroke', '#2a2a2a')
          .attr('stroke-width', d => {
              const src = typeof d.source === 'object' ? d.source : currentNodes.find(n => n.id === d.source);
              return src && src.type === 'wan' ? 2 : 1.5;
          });
  }
  ```

- [ ] **Step 5: Add WAN node rendering in `renderNodes()`**

  In `renderNodes()`, add the WAN node renderer immediately before the existing event node renderer (before the `// Event nodes — large circle` comment, around line 489):

  ```javascript
  // WAN node — dashed circle with globe icon
  groups.filter(d => d.type === 'wan').each(function(d) {
      const g = d3.select(this);
      g.append('circle')
          .attr('r', 28)
          .attr('fill', 'rgba(80,80,80,0.06)')
          .attr('stroke', '#444')
          .attr('stroke-width', 1.5)
          .attr('stroke-dasharray', '5 3');
      g.append('text')
          .attr('y', -4)
          .attr('text-anchor', 'middle')
          .attr('font-size', '14px')
          .attr('fill', '#555')
          .text('⊕');
      g.append('text').attr('class', 'node-label')
          .attr('y', 14).attr('font-size', '9px')
          .attr('fill', '#555')
          .text('INTERNET');
  });
  ```

- [ ] **Step 6: Call `renderTierLabels()` inside `updateGraph()` on structural change**

  In `updateGraph()`, in the `if (structureChanged)` branch, add the call after `renderNodes()`:

  ```javascript
  if (structureChanged) {
      currentNodes = allNodes.map(n => ({ ...n }));
      currentLinks = allLinks.map(l => ({ ...l }));
      calculateLayout(currentNodes);
      renderTierLabels();   // ← add this line
      renderLinks();
      renderNodes();
      applyPositions();
  }
  ```

- [ ] **Step 7: Verify visually**

  Open `/admin/topology`. Confirm:
  - Three tier labels on the left edge: "INTERNET / WAN", "EVENT", "VMs"
  - WAN node (dashed circle, ⊕ icon) at top center
  - Event node in the middle tier
  - VM nodes in a flat row at the bottom
  - Solid lines connecting WAN→Event and Event→VMs

- [ ] **Step 8: Commit**

  ```bash
  git add frontend/templates/topology.html
  git commit -m "feat: add WAN node, tier labels, solid link rendering to topology"
  ```

---

### Task 4: Update event node rendering and tooltips

**Files:**
- Modify: `frontend/templates/topology.html`

- [ ] **Step 1: Add `×N teams` badge to the event node renderer**

  In `renderNodes()`, find the event node renderer block (the `groups.filter(d => d.type === 'event').each(...)` block, around lines 490–502). Replace it with:

  ```javascript
  // Event nodes — rounded rect with ×N teams badge
  groups.filter(d => d.type === 'event').each(function(d) {
      const g = d3.select(this);
      const w = 130, h = 42, rx = 8;
      g.append('rect')
          .attr('x', -w / 2).attr('y', -h / 2)
          .attr('width', w).attr('height', h)
          .attr('rx', rx)
          .attr('fill', 'rgba(0, 229, 255, 0.05)')
          .attr('stroke', statusColor(d.status))
          .attr('stroke-width', 1.5)
          .style('filter', 'drop-shadow(0 0 10px rgba(0, 229, 255, 0.25))');
      g.append('text').attr('class', 'node-label')
          .attr('y', -6).attr('font-size', '11px')
          .attr('fill', '#00e5ff')
          .text(d.label);
      // ×N teams badge
      if (d.team_count && d.team_count > 0) {
          g.append('rect')
              .attr('x', -32).attr('y', 6)
              .attr('width', 64).attr('height', 14)
              .attr('rx', 7)
              .attr('fill', 'rgba(255,180,0,0.12)')
              .attr('stroke', '#ffb400')
              .attr('stroke-width', 0.8);
          g.append('text')
              .attr('y', 17)
              .attr('text-anchor', 'middle')
              .attr('font-size', '8px')
              .attr('fill', '#ffb400')
              .text(`\u00d7 ${d.team_count} teams`);
      }
  });
  ```

- [ ] **Step 2: Update the event tooltip to use `team_count` instead of counting team nodes**

  In `showTooltip()`, replace the `else if (d.type === 'event')` branch (lines ~657–665) with:

  ```javascript
  } else if (d.type === 'event') {
      const eventVMs = currentNodes.filter(n => n.type === 'vm' && n.event_id === d.id);
      html = `
          <div class="topo-tooltip-row"><span class="topo-tooltip-label">Event</span><span class="topo-tooltip-value">${d.label}</span></div>
          <div class="topo-tooltip-row"><span class="topo-tooltip-label">Status</span><span class="topo-tooltip-value" style="color:${statusColor(d.status)}">${d.status}</span></div>
          <div class="topo-tooltip-row"><span class="topo-tooltip-label">Teams</span><span class="topo-tooltip-value">${d.team_count ?? 0}</span></div>
          <div class="topo-tooltip-row"><span class="topo-tooltip-label">VMs (canonical)</span><span class="topo-tooltip-value">${eventVMs.length}</span></div>
      `;
  ```

- [ ] **Step 3: Verify event node renders correctly**

  Open `/admin/topology`. Confirm:
  - Event node is a rounded rectangle (not a circle)
  - Event name shown in cyan
  - `×N teams` gold pill badge visible below the name if `team_count > 0`
  - Hovering the event node shows tooltip with Teams count and VM count

- [ ] **Step 4: Commit**

  ```bash
  git add frontend/templates/topology.html
  git commit -m "feat: event node rounded rect with x-teams badge, updated tooltip"
  ```

---

### Task 5: Remove team rendering code and clean up

**Files:**
- Modify: `frontend/templates/topology.html`

- [ ] **Step 1: Guard `showTooltip()` and `showContextMenu()` against the WAN node**

  The WAN node is a new type that has no tooltip or context menu. Without guards, hovering shows an empty tooltip and right-clicking shows an empty menu.

  At the top of `showTooltip()`, add an early return for non-interactive node types:

  ```javascript
  function showTooltip(event, d) {
      if (d.type === 'wan') return; // no tooltip for WAN node
      let html = '';
      // ... rest of function unchanged
  ```

  At the top of `showContextMenu()`, add an early return:

  ```javascript
  function showContextMenu(event, d) {
      event.preventDefault();
      event.stopPropagation();
      hideTooltip();
      if (d.type === 'wan') return; // no context menu for WAN node
      // ... rest of function unchanged
  ```

- [ ] **Step 2: Remove the team node renderer from `renderNodes()`**

  Delete the entire `// Team nodes — medium circle` block (lines ~505–519):

  ```javascript
  // Team nodes — medium circle
  groups.filter(d => d.type === 'team').each(function(d) {
      const g = d3.select(this);
      const color = d.color || '#ffb400';
      g.append('circle')
          .attr('r', NODE_RADIUS.team)
          .attr('fill', color.replace(')', ', 0.1)').replace('rgb', 'rgba').replace('#', ''))
          .attr('fill', `${color}18`)
          .attr('stroke', color)
          .attr('stroke-width', 2)
          .style('filter', `drop-shadow(0 0 10px ${color}40)`);
      g.append('text').attr('class', 'node-label-type')
          .attr('y', -6).attr('fill', color).text('TEAM');
      g.append('text').attr('class', 'node-label')
          .attr('y', 8).attr('font-size', '10px').text(d.label);
  });
  ```

- [ ] **Step 3: Remove team branch from `showTooltip()`**

  Delete the `else if (d.type === 'team')` branch (lines ~645–656):

  ```javascript
  } else if (d.type === 'team') {
      const teamVMs = currentNodes.filter(n => n.type === 'vm' && n.team_id === d.id);
      // ... (entire block through closing brace)
  }
  ```

- [ ] **Step 4: Remove team branch from `showContextMenu()`**

  Delete the `else if (d.type === 'team')` branch (lines ~696–703):

  ```javascript
  } else if (d.type === 'team') {
      const teamId = d.id.replace('team-', '');
      items = `
          <div class="topo-context-header">${d.label}</div>
          <div class="topo-context-item" onclick="location.href='/admin'">&#9654; View Team</div>
          <div class="topo-context-item danger" data-action="delete-team" data-id="${teamId}">&#128465; Delete Team</div>
      `;
  }
  ```

- [ ] **Step 5: Remove the `delete-team` case from `handleContextAction()`**

  Delete the `case 'delete-team':` block (lines ~750–755):

  ```javascript
  case 'delete-team':
      if (!confirm('Delete this team?')) return;
      url = `/admin/teams/${id}`;
      method = 'DELETE';
      msg = 'Team deleted';
      break;
  ```

- [ ] **Step 6: Remove `_TEAM_COLORS` from the API if unused elsewhere**

  In `api/routes/vm.py`, search for `_TEAM_COLORS`. If it's only referenced in the old `topology_data()` function and nowhere else, delete the constant definition too.

  ```bash
  grep -n "_TEAM_COLORS" api/routes/vm.py
  ```

  If only found in the deleted section, delete the constant (look for it near the top of the file or near the old endpoint).

- [ ] **Step 7: Final end-to-end verification**

  With `docker compose up -d`, open `/admin/topology` and verify all spec requirements:

  1. Three tiers visible with labels: INTERNET / WAN, EVENT, VMs
  2. WAN node (dashed circle, ⊕, "INTERNET") at top center
  3. Event node (rounded rect, cyan, `×N teams` gold badge) in middle
  4. VM nodes in a flat row at the bottom — no team nodes anywhere
  5. Solid lines: WAN→Event (slightly thicker), Event→VMs
  6. Hover a VM → tooltip shows hostname, IP, OS, status, module progress bar
  7. Hover event node → tooltip shows Teams count and canonical VM count
  8. Right-click a VM → context menu with View Details / Provision / Assign Modules / Export Playbook / Destroy
  9. Double-click a VM → navigates to `/admin/vm/{id}`
  10. Event filter dropdown scopes the graph to one event
  11. Zoom/pan works (scroll to zoom, drag background to pan)
  12. Change a VM status in the DB and wait 5 seconds → status color updates with pulse animation, no page reload

- [ ] **Step 8: Commit**

  ```bash
  git add frontend/templates/topology.html api/routes/vm.py
  git commit -m "feat: remove team nodes from topology - clean up renderer, tooltip, context menu"
  ```
