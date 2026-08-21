# Planner Node Theming Design

## Goal

Make the topology nodes feel native to the planner's existing dark industrial interface while making the network hierarchy easier to scan. This is a visual-only change: topology structure, editing behavior, layout persistence, and provisioning remain unchanged.

## Visual Direction

Use the existing industrial theme: dark navy surfaces, monospace typography, flat one-pixel borders, cyan as the interaction signal, and amber as the firewall/security signal. Avoid decorative gradients, rounded cards, and heavy shadows.

Each topology role receives a restrained, recognizable treatment:

- Sites use a strong solid blue-grey frame and a slightly raised navy fill.
- The automatic Firewall Zone keeps its dashed cyan boundary and subtle cyan fill.
- The Primary Firewall uses an amber border and muted amber fill to communicate its routing and security role.
- Workload zones use a quieter blue outline and dark blue fill.
- VMs use compact dark cards with a narrow cyan edge marker.
- Selected nodes override their role treatment with a bright cyan border and restrained cyan glow.
- Invalid nodes retain the existing error treatment and remain distinguishable from selected nodes.

## Connections

Connections remain subdued so nodes carry the hierarchy. Links attached to the selected node become brighter, helping users trace the selected object's immediate network relationships without adding persistent visual noise.

## Implementation Boundary

The canvas will expose enough semantic classes or attributes to style node roles and selection-adjacent links. Styling stays in `event-planner.css`; topology data and persisted layout formats do not change.

## Accessibility and Interaction

The design must not depend on color alone: dashed borders distinguish the automatic zone, the firewall remains labelled, and VM edge markers provide a shape cue. Dragging, clicking, selection, and read-only behavior must remain unchanged.

## Verification

- Add an executable canvas or DOM-level test for semantic node/link classes where practical.
- Retain existing state and controller tests.
- Run JavaScript syntax checks, the planner-focused tests, and the full container test suite.
- Manually inspect the rebuilt planner at desktop and narrow widths.
