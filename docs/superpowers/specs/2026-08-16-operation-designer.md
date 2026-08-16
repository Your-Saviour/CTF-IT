# Full-Page Operation Designer

**Date:** 2026-08-16

## Purpose

Add the third event-planning workspace after network creation and module assignment. Administrators design one canonical, platform-native red-team operation template before the event; the template is resolved independently for every team and later compiled into Caldera artifacts. The saved plan must not contain runtime VM IDs or Caldera installation IDs.

## Workspace

The route `/admin/events/{event_id}/operation` uses the same dedicated full-page planner shell and visual language as the network and module workspaces. Its command header provides Back to module assignment, Validate graph, Auto-arrange, Preview per team, Save draft, launch mode and timing controls, and Finish planning.

The workspace uses Option A:

- a left node library containing searchable module-derived abilities, individual planned-VM targets, assigned objectives, and control nodes;
- a central free-form directed acyclic graph canvas;
- a right inspector for the selected node or edge.

The page continuously answers who is targeted, what is attempted, what follows success or failure, when the operation runs, and what counts as completion.

## Graph Model

The event owns a versioned operation-plan JSON document. Nodes have stable IDs, types, labels, positions, and type-specific configuration. Supported node types are `start`, `finish`, `target`, `ability`, `objective`, `delay`, and `gate`. Gates support `all`, `any`, and `first` joins. Only abilities exposed by modules assigned in the event module plan may be used. Additional Caldera behavior must first become a module.

Every executable ability targets exactly one canonical planned VM. Objectives are required or optional. The graph is acyclic. Edges are explicitly typed `success`, `failure`, or `always`; retries and retry delay belong to ability nodes rather than graph loops.

The operation policy stores launch mode (`manual`, `scheduled`, or `scheduled_hold`), event-relative start offset, overall time limit, maximum per-team concurrency, default timeout, retries and retry delay, unreachable-required-objective behavior, missing-agent behavior, and instructor-approval requirement. Node settings may override timing defaults.

## Validation

Invalid drafts remain saveable, but preview, compilation, scheduled launch, and event readiness require a valid graph. Validation checks:

- known node and edge types, unique stable IDs, and valid references;
- acyclicity;
- reachability from Start and a route to Finish or an objective;
- reachable required objectives;
- exactly one planned-VM target for every ability;
- ability provenance from an assigned module and compatibility with the target base type;
- non-contradictory typed edges;
- valid retries, delays, timeouts, joins, and global policy values;
- worst-case duration within the operation and event limits;
- scheduled timing within the event duration;
- staleness after network or module-plan inputs change.

Messages appear in the page summary and on affected graph elements. Conditions are communicated by text and line pattern, not colour alone.

## Preview and Compilation Boundary

Preview compiles the current valid draft in memory for one selected team without creating Caldera operations. Canonical planned-VM IDs resolve deterministically to that team's runtime targets when available. The result includes paths, execution ordering, timing bounds, required and optional objectives, target mappings, and a manifest that maps generated identifiers to event, team, graph node, module, ability, planned VM, runtime VM, and objective.

Compilation is deterministic, revision-aware, and idempotent. Network, module, or operation changes mark old artifacts stale. Started operations retain their original snapshot. One team's failure cannot affect another team. Unsupported Caldera translations fail explicitly and are never silently flattened.

This implementation establishes the saved plan, validation, preview, and deterministic manifest boundary. Runtime launch scheduling, recovery, and live result handling remain responsibilities of the existing operations layer and consume the compiled manifest.

## Failure Handling and Accessibility

Failed saves preserve local edits. Revision conflicts never overwrite silently. Compilation diagnostics are reported per team and graph node. The editor supports keyboard-accessible creation, selection, connection, movement and deletion; a non-canvas outline; labelled controls; live announcements; visible focus; reduced motion; unsaved-navigation protection; and confirmation for bulk deletion.

## Verification

Automated coverage includes normalization, DAG and reachability validation, typed edges, objectives, ability provenance, VM targeting, policy and timing bounds, stale-input fingerprints, deterministic team preview and manifests, persistence endpoints, template contracts, and client-side state helpers. Existing planner and module-assignment tests must remain green.

## Acceptance Criteria

- Operation design is the third dedicated full-page planning step.
- Administrators can build and persist a manual DAG using only assigned-module abilities and individual planned VMs.
- Required and optional objectives, typed transitions, retries, timeouts, scheduling policy, and global limits are explicit.
- Invalid drafts save but cannot preview or compile.
- Preview demonstrates independent per-team resolution without runtime mutation.
- The page uses the established planner shell and remains understandable and operable without colour or pointer input.
