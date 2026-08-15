# Compact Zone Containers

## Goal

Reduce unused space inside zone boxes on `/admin/events/{id}/plan` by sizing each box to its contained VM arrangement in both dimensions. Zones must retain enough header space for their title, metadata, and **Arrange VMs** control, and must continue to expand when a VM is moved beyond the compact footprint.

## Scope

This change affects only canvas geometry for workload zones and the system-managed Firewall Zone. It does not change planner data, persisted coordinates, styling, labels, selection, dragging, or provisioning behavior.

## Geometry

Replace the large fixed zone minimum (`280 × 190`) with content-driven minimum bounds.

- The minimum width is the greater of the header/control width floor and the width required by the arranged VM columns.
- The minimum height is the header (including an optional address rail), top padding, arranged VM rows, and bottom padding.
- An empty zone uses the header/control width floor and a small empty-body height so the container remains visible and selectable.
- A zone containing one VM uses one compact content cell. Additional VMs form the existing deterministic near-square grid.
- VM annotations continue to contribute their taller machine bounds, so address text is not clipped.
- The existing bounds calculation still considers actual child coordinates. Dragging a VM to the right or bottom therefore expands its zone, but unused fixed space is no longer added.
- The left and top edges remain fixed, preserving the current drag constraints and link-boundary behavior.

The header width floor will be expressed as named geometry values derived from the title inset, reserved title area, gap, arrange-control width, and right inset. This makes the non-overlap requirement explicit rather than hiding another arbitrary zone width constant.

## Arrangement

`arrangeZoneChildren` remains the single source of deterministic child positions. A shared grid-metrics helper will derive the number of columns and rows and the compact content footprint from the same machine dimensions, gaps, and padding used for arrangement. `calculateZoneBounds` will use those metrics for its minimum and then expand to include any children outside that footprint.

Saved layouts remain compatible. Opening an existing plan does not rewrite coordinates merely because zone bounds render more tightly. Choosing **Arrange VMs** continues to update only the direct VM/firewall children of the selected zone.

## Testing

Canvas unit tests will verify:

- one-VM zones compact in both dimensions while preserving the header/control floor;
- four-VM zones fit their two-by-two arranged grid without the old fixed excess;
- annotated zone headers and annotated VM labels add the required height;
- a manually moved child still expands the right or bottom edge;
- arranging and translating zone children retain their existing deterministic behavior.

The focused JavaScript canvas tests and the broader planner test suite will be run before completion.

## Success Criteria

- The Firewall Zone and Test Zone in the supplied example render with visibly less unused space in both dimensions.
- Headers remain legible and **Arrange VMs** never overlaps their text.
- VM icons and address labels remain inside their zone boxes.
- Zones still expand to contain manually positioned children.
- Existing persisted plans and planner interactions continue to work without migration.
