# n8n-Inspired Operation Workspace

**Date:** 2026-08-16

## Purpose

Redesign `/admin/events/{event_id}/operation` as a canvas-dominant workflow editor inspired by n8n's efficient node creation and connection interactions. Preserve the product's existing dark planner theme, cyan accent, graph model, validation rules, persistence endpoints, and position as the third event-planning step.

The redesign must make selecting nodes, placing nodes, creating branches, and understanding transitions feel direct. It must not imitate n8n's light visual theme, introduce a frontend framework, or change the canonical operation-plan schema.

## Workspace Direction

The full-page workspace contains:

- the existing planner command header with navigation, save state, validation, preview, auto-arrange, and save actions;
- a canvas occupying the remaining page area;
- a compact canvas toolbar for adding nodes and changing interaction mode;
- cursor-centred zoom, pan, fit-to-workflow controls, and a minimap;
- a searchable node-picker overlay;
- a contextual toolbar above the selected node;
- a floating inspector for the selected node or transition;
- an optional accessible graph-outline drawer;
- a validation drawer and the existing preview dialog.

The permanent node-library and inspector columns are removed. Empty canvas space is the primary working surface. The current black and warm-black surfaces, cyan signal colour, flat borders, compact typography, and restrained motion remain the visual system.

## Node Discovery and Creation

The Add node control opens a searchable command panel over the canvas. Results are grouped into Targets, Abilities, Objectives, and Flow controls. Each result displays real catalogue information: its label, type, description when available, assigned-module provenance when applicable, and target compatibility when relevant.

Selecting a result from the toolbar inserts the node at the viewport centre. A result may also be dragged from the picker and dropped at an exact canvas position. Search supports node labels, descriptions, types, module names, site names, zone names, and target names already present in the operation catalogue.

The picker explicitly communicates empty results and unavailable items. Cancelling it never creates a partial graph mutation.

## Direct Connections

Nodes expose visible input and output ports. Inputs appear on the left and outputs on the right. Ports are semantic rather than generic:

- ability nodes expose Success and Failure outputs;
- simple linear nodes expose Continue;
- branching controls expose outputs appropriate to their configured behavior;
- port labels appear on hover and keyboard focus.

Dragging from an output displays a live curved connection. Compatible input ports highlight. Incompatible inputs remain legible but disabled and provide a concise reason when focused or hovered.

Dropping on a compatible input creates the transition immediately. Dropping on empty canvas opens the node picker at that graph position and filters it to nodes compatible with the originating port. Selecting a result inserts the node and creates the transition as one atomic action. Escape or picker cancellation leaves both the graph and history unchanged.

Local compatibility checks prevent self-links, duplicate transitions, invalid direction, and cycles that can be determined from the current draft. The backend remains authoritative for complete graph validation.

## Selection and Canvas Navigation

Selecting a node gives it a clear cyan focus ring, opens its floating inspector, and reveals contextual Duplicate, Disable or Enable, and Delete actions. Selecting a transition exposes its condition, optional label, and Delete action. Clicking empty canvas clears selection.

Connections have a wider invisible hit target than their visible stroke. Selected connections receive directional emphasis, while reduced-motion mode keeps them static. Success, Failure, and Always conditions use text and distinct line patterns so their meaning never relies on colour.

The canvas supports:

- pointer and keyboard node selection;
- a selection rectangle for multiple nodes;
- aligned movement of a multi-node selection;
- pan and cursor-centred zoom;
- fit to workflow and a minimap;
- undo and redo;
- copy, paste, duplicate, disable, and delete;
- automatic arrangement using the existing graph helper.

Dragging across many pointer events produces one undo entry. Inserting and connecting a node from an empty-canvas drop also produces one undo entry.

## Inspector, Policy, Outline, and Validation

The floating inspector edits the same node configuration, edge condition, and global operation policy values as the existing page. It does not cover the selected node when sufficient viewport space exists and remains reachable on narrow screens.

The non-canvas outline moves into an optional drawer. It preserves a linear representation of every node and transition for keyboard and screen-reader use. Choosing an outline item selects it and brings it into view on the canvas.

Validation results appear in a bottom drawer. Each issue links to its affected node or transition when an identifier is available. Choosing an issue selects the graph item, moves it into view, and opens the relevant inspector. Invalid graph elements receive a visible marker in addition to the drawer message.

Invalid drafts remain saveable. Preview and compilation remain blocked by authoritative server validation.

## Architecture

`frontend/templates/event_operation.html` becomes a thin workspace shell containing the canvas, overlays, drawers, dialogs, controls, accessible labels, and live region.

`frontend/static/event-operation.css` retains the established operation-planner tokens while implementing the canvas-dominant layout, overlay surfaces, node ports, focus states, minimap, validation markers, and responsive behavior.

`frontend/static/event-operation.js` remains the page controller but delegates focused behavior to modules with the following responsibilities:

- viewport state, coordinate conversion, pan, zoom, and fit-to-workflow;
- node rendering, selection, multi-selection, movement, and contextual actions;
- connection gestures, compatibility feedback, live previews, and edge selection;
- node-picker state, catalogue search, grouping, filtering, and placement;
- inspector, policy editing, outline, validation drawer, save, and preview orchestration;
- history, clipboard, and keyboard commands.

`frontend/static/event-operation-state.js` remains framework-independent and gains immutable helpers for connection compatibility, cycle checks, multi-selection, compound insert-and-connect operations, clipboard duplication, and undoable graph mutations.

SVG remains the graph renderer. Accessible HTML overlays may be used for the picker, inspector, toolbar, drawers, and other controls. No frontend framework or graph dependency is introduced.

## Data and Persistence

The canonical operation-plan JSON, backend builder, API endpoints, optimistic concurrency behavior, validation rules, and preview compiler remain unchanged.

Node positions remain persisted in the graph as they are today. Viewport position, zoom, current selection, open overlays, and undo history are local presentation state and are not added to the operation-plan schema.

All graph mutations pass through one history boundary. Compound gestures commit atomically. Save requests serialize only the current canonical plan, never transient connection previews or selection state.

## Failure Handling and Accessibility

Known-invalid connections are rejected during the gesture with a message near the pointer and in the page live region. Validation failures returned by the server populate the validation drawer without discarding the local draft.

Failed saves preserve the complete local graph and undo history. Revision conflicts never overwrite local work and show a dedicated recovery message. Closing an overlay or cancelling a connection does not mark the draft dirty.

Every pointer interaction has a keyboard path: opening and searching the picker, selecting results, starting a connection from an output, selecting a destination, moving nodes, opening the inspector, duplicating, disabling, and deleting. Focus remains visible. Controls have accessible names. Reduced-motion mode disables connection animation and smooth viewport movement.

## Verification

Framework-independent state tests cover:

- compatible and incompatible connection decisions;
- self-link, duplicate, invalid-direction, and cycle prevention;
- atomic node insertion and connection;
- cancellation without mutation;
- multi-selection and grouped movement;
- clipboard duplication with new stable identifiers;
- history boundaries and undo or redo behavior.

Template tests verify the workspace controls, overlays, drawers, accessible labels, live region, and script loading. JavaScript syntax checks cover each frontend module. Existing operation-plan API, normalization, validation, persistence, and preview tests remain regression coverage, together with the network-planner and module-assignment suites.

Manual verification covers cursor-centred zoom, pan accuracy, connection hit targets, picker placement near viewport edges, compatible-target feedback, large graphs, narrow screens, keyboard-only graph editing, reduced motion, revision conflicts, and unsaved-navigation protection.

## Acceptance Criteria

- The operation page uses the existing dark planner theme in a full n8n-style canvas workspace.
- Administrators can find, insert, position, select, inspect, connect, branch, duplicate, disable, and delete nodes without using a source-and-target dialog.
- Dropping a connection on empty canvas opens a compatible-node picker and completes insertion plus connection atomically.
- Direct manipulation supports zoom, pan, fit, minimap, multi-selection, clipboard actions, and undo or redo.
- Transition meaning is explicit through port semantics, labels, and line patterns.
- Validation issues navigate to affected graph elements, while invalid drafts remain saveable.
- The graph remains operable without colour, pointer input, animation, or the spatial canvas outline.
- The operation-plan schema and backend API contract remain unchanged.
