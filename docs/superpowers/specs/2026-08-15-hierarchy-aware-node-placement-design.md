# Hierarchy-Aware Node Placement Design

## Goal

Place newly created topology nodes predictably beside their siblings and beneath their visual parent instead of assigning positions from the node's global array index.

## Placement Model

The canvas uses the graph's visual parent relationship as the source of truth. Every node with a parent is placed on a child row beneath that parent. Siblings occupy evenly spaced horizontal slots on that row.

The first child is centred under its parent. Additional siblings extend alternately to the left and right using this slot order: centre, right one, left one, right two, left two. This keeps a growing group visually balanced around its parent.

Root nodes without a parent use a deterministic top row. In the current graph this keeps the VPN Gateway as the root and sites as its child row.

## Existing Layout and Collisions

Saved coordinates are authoritative. Rendering a graph must never reposition nodes that already have valid saved coordinates.

For a node without saved coordinates, the placement algorithm starts at its preferred sibling slot. If that rectangle would overlap an existing or newly calculated node rectangle, it searches outward along the same child row until it finds the nearest free slot. The search is deterministic, so the same graph and saved layout produce the same result.

Node dimensions and padding are included in collision checks. Placement does not use viewport size, zoom state, or pointer location.

## Persistence

When a newly added graph node receives a calculated coordinate, the canvas reports the completed layout through the existing layout-change callback. The planner stores that coordinate immediately, making it stable across rerenders and draft saves.

This callback occurs only when the calculated layout introduces missing coordinates. Normal selection and rerendering with a complete layout do not mark the planner dirty.

## Reset Layout

Reset Layout discards saved coordinates and recalculates every node using the same hierarchy-aware algorithm. The resulting complete layout is persisted through the existing callback and then fitted into view.

## Scope

The change applies to the VPN Gateway, sites, automatic Firewall Zones, firewall VMs, workload zones, and workload VMs. It changes only client-side coordinates. Topology structure, editing rules, backend layout format, provisioning, and manual drag behavior remain unchanged.

## Verification

- Executable JavaScript tests cover sibling slot order, parent-relative rows, collision avoidance, preservation of saved coordinates, and deterministic output.
- A canvas test covers reporting newly calculated coordinates through the existing callback.
- Existing planner state, drag, backend layout, and provisioning tests continue to pass.
- The rebuilt planner is manually checked by adding multiple sites, zones, and VMs and by using Reset Layout.
