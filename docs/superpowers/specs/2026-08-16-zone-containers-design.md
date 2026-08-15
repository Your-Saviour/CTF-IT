# Event Planner Zone Containers Design

## Purpose

The event planner currently represents zones and their VMs as independent nodes connected by lines. Although the planner data model already records each VM under a zone, the canvas does not communicate that ownership strongly. Zones will become framed workspaces that visibly contain their VMs, matching the operator's mental model of a network zone as the holder for its machines.

This is a canvas and layout change only. It does not alter infrastructure JSON, provisioning, addressing, or explicit zone membership.

## Container Model

Every workload zone renders as a framed container with a dedicated header and bounded interior. The automatic, system-managed Firewall Zone uses the same container structure and visually contains its Primary Firewall VM.

The frame header is the zone's selection and group-drag surface. It displays only real planner data and actions:

- zone name;
- team role, or system-managed status for the Firewall Zone;
- current VM count;
- an `Arrange VMs` action.

The container keeps the planner's existing dark visual language and semantic zone colours. Selection, validation, and read-only states appear on the frame. Containers and structural links render behind machine nodes so machines remain readable and clickable.

Empty zones retain a useful minimum interior size. Non-empty zones expand automatically from the positions of their children plus fixed header and interior padding.

## VM Placement and Membership

VMs remain freely draggable within their assigned zone. Dragging a VM toward or beyond an interior edge expands the zone frame to continue containing it. VM dragging does not reassign membership, even if the pointer or rendered machine overlaps another zone. Moving a VM between zones remains an explicit data-editing operation outside canvas drag behavior.

VM dragging is constrained against crossing the zone's top header and left interior padding. The frame may expand to the right or bottom as needed. This keeps the zone's saved anchor stable and avoids shifting unrelated content merely because one child moved.

The Primary Firewall VM follows the same placement behavior inside the Firewall Zone. The Firewall Zone itself remains non-deletable and system-managed.

## Zone Movement

Dragging begins only from the zone header. Moving a zone translates its own saved coordinate and every child machine coordinate by the same pointer delta. The entire group therefore preserves its internal arrangement.

The completed drag emits one atomic layout update. Intermediate pointer movement updates the canvas continuously but does not produce partial persisted states or repeated dirty-state changes.

In read-only plans, zones and VMs remain selectable and inspectable, but group dragging, VM dragging, and arrangement actions are disabled.

## Automatic Grid Arrangement

`Arrange VMs` packs only the selected zone's current children into a deterministic grid within the zone interior. It does not lock the grid: every arranged VM remains freely draggable afterward.

The grid uses fixed machine-cell dimensions and gaps derived from the canvas machine geometry. Column count is deterministic from the child count, producing compact rows without depending on viewport size or zoom. A single VM occupies the first grid slot; additional machines fill rows in their existing topology order.

Arrangement updates all affected child coordinates and the zone coordinate as one layout change. For the Firewall Zone, the same action arranges the Primary Firewall VM and supports future additional firewall members without changing the interaction model.

## Layout and Persistence

The infrastructure document and stable node IDs remain unchanged. VM coordinates continue to use the existing global canvas coordinate system, preserving compatibility with saved layouts and current persistence APIs.

Zone width and height are derived presentation state and are not serialized. A container's rendered bounds come from:

1. its saved or calculated anchor coordinate;
2. its minimum width and height;
3. the bounding rectangles of its child machines;
4. fixed header and interior padding.

Existing plans need no data migration. When coordinates are missing or invalid, the current hierarchy-aware layout first supplies deterministic zone and VM positions; container bounds are calculated afterward and the completed coordinates follow the existing layout-change persistence path.

## Links and Drawing Order

Containment replaces direct zone-to-machine lines. The planner continues to draw structural links between the gateway, sites, Firewall Zones, and workload zones, but it does not draw a link from a zone container to a machine contained within it.

Structural links terminate at the relevant container boundary instead of its centre, preventing lines from crossing container interiors unnecessarily. Rendering order is:

1. structural links;
2. zone containers;
3. machine nodes;
4. interactive labels and controls that must remain above their owning frame.

## Component Boundaries

The canvas module owns presentation-only geometry and interactions:

- calculating container bounds from node positions;
- packing child machines into a grid;
- translating a container and its children;
- clipping or constraining child movement at fixed edges;
- routing links to container boundaries;
- emitting atomic layout changes.

The planner state module remains authoritative for topology membership, stable IDs, editing rules, validation, and layout pruning. Canvas overlap never mutates infrastructure membership.

The event planner controller maps the existing node index into the canvas graph and handles the explicit `Arrange VMs` callback. No backend endpoint or database migration is required.

## Accessibility

Zone containers remain focusable and selectable with the keyboard. The zone header exposes a clear accessible label containing the zone name and type. `Arrange VMs` is a real button with standard keyboard activation and is removed or disabled in read-only mode. Machine nodes retain their existing keyboard selection behavior.

Pointer hit targets distinguish the header's group-drag area from the interior machine-drag area. Activating the arrange button must not initiate a group drag.

## Error and Edge-Case Handling

- Empty zones use minimum bounds and remain selectable, movable, and ready to receive explicitly added VMs.
- A zone with invalid child coordinates receives hierarchy-aware fallback positions before its bounds are measured.
- A group move or arrangement either emits one complete layout update or leaves the saved layout unchanged.
- Selection and validation styling remains visible when a child and its container are both involved in an error.
- A machine visually overlapping another zone remains owned by its original zone and snaps or expands within that zone's containment rules on drag completion.
- Read-only rendering performs no layout mutation callbacks.

## Testing

Executable canvas tests will cover:

- minimum and child-derived container bounds;
- right/bottom expansion and top/left movement constraints;
- deterministic grid packing for empty, single-child, and multi-row zones;
- zone translation preserving child offsets;
- one atomic callback for group movement and arrangement;
- Firewall Zone containment of the Primary Firewall VM;
- absence of zone-to-machine links;
- structural links terminating at container boundaries;
- read-only interaction behavior;
- fallback placement for missing or invalid coordinates.

Planner controller and template contract tests will cover the container metadata, VM counts, arrange action, accessible controls, semantic team/system styling, and correct drawing order. Existing planner-state, icon, backend layout, preview, and provisioning tests must continue to pass unchanged.

## Acceptance Criteria

- Workload zones visibly contain all VMs assigned to them.
- The system-managed Firewall Zone visibly contains its Primary Firewall VM.
- Dragging a zone header moves the zone and every contained machine together.
- VMs remain freely draggable within their assigned zone.
- `Arrange VMs` packs a zone's machines into a deterministic grid without locking later movement.
- Canvas dragging never changes a VM's zone membership.
- Containers grow to include freely positioned machines and preserve a useful minimum size when empty.
- Zone-to-machine lines are removed; containment communicates ownership.
- Existing infrastructure JSON and saved global coordinates remain compatible.
- Editing, validation, selection, accessibility, and read-only behavior remain intact.
- Provisioning and backend resource behavior do not change.
