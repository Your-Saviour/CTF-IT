# Operation Trigger Nodes

**Date:** 2026-08-16

## Purpose

Replace the operation planner's generic Start node and global launch-mode fields with n8n-style typed trigger nodes. An administrator can choose a manual trigger, an event-start trigger, or a one-shot scheduled trigger. This change defines and previews when a future operation runtime should begin; it does not add graph execution or scheduling.

## Trigger Semantics

Every operation has exactly one enabled trigger:

- **Manual Trigger** starts only after an explicit instructor action in a future runtime. It has no timing configuration.
- **Event Start Trigger** starts once, immediately after the event transitions from draft to open.
- **Scheduled Trigger** starts once at a non-negative whole-minute offset from the event's actual start. An offset of zero is valid. Because the schedule is event-relative, it can never fire before the event starts.

Trigger delivery is one-shot. A future runtime must persist whether it has handled the trigger so page reloads, process restarts, and repeated lifecycle messages cannot launch a second operation. Runtime delivery and persistence are outside this implementation, but the compiled contract must retain these semantics.

## Graph Model

The supported trigger node types are `manual_trigger`, `event_start_trigger`, and `scheduled_trigger`. They replace `start` in newly normalized plans and in the node catalogue. New plans contain a Manual Trigger and Finish node.

A trigger is the sole root of an enabled graph:

- exactly one enabled trigger is required;
- a trigger cannot have an incoming edge;
- all other enabled nodes must be reachable from the trigger;
- trigger nodes expose one `always` output;
- trigger nodes can be moved and selected like other nodes, but cannot be duplicated or disabled if that would leave the graph without exactly one enabled trigger;
- replacing the trigger is an atomic graph mutation that preserves its outgoing transitions and position.

The picker groups the three choices under **Triggers**. If an enabled trigger already exists, selecting another trigger replaces it rather than adding a second root. Delete and duplicate controls are unavailable for the active trigger. Backend validation remains authoritative if malformed JSON contains zero or multiple enabled triggers.

## Configuration and Inspector

The inspector identifies the selected trigger by its user-facing name and description. Only Scheduled Trigger displays a field named **Start after event begins (minutes)**. The value is stored as `config.offset_minutes`, must be an integer of at least zero, and defaults to zero.

Launch mode and start offset are removed from the operation-policy editor. The policy continues to own time limit, concurrency, default timeouts, retries, retry delay, missing-agent behavior, unreachable-objective behavior, and instructor approval.

The page's accessible outline, node labels, ports, live announcements, and keyboard connection flow treat triggers as ordinary graph nodes while describing their trigger-specific behavior. Trigger meaning does not depend on colour or iconography.

## Compatibility and Normalization

The canonical operation-plan version remains version 1. `normalize_operation_plan` accepts legacy version-1 plans and returns the new canonical representation:

- `policy.launch_mode == "manual"` converts the Start node to Manual Trigger;
- `policy.launch_mode == "scheduled"` converts it to Scheduled Trigger and moves `policy.start_offset_minutes` to `config.offset_minutes`;
- `policy.launch_mode == "scheduled_hold"` converts it to Scheduled Trigger, moves the offset, and sets `policy.instructor_approval` to true so the hold is not silently discarded;
- a missing launch mode uses Manual Trigger;
- legacy Start node identifiers, coordinates, labels, outgoing edges, and disabled state are preserved during conversion;
- `launch_mode` and `start_offset_minutes` are omitted from normalized policy output.

Typed trigger nodes are left unchanged on every normalization pass. Any legacy Start node is converted independently, even if a malformed plan also contains a typed trigger; the resulting plan then fails normal trigger-count validation rather than silently dropping graph data.

This read-time migration means existing stored plans remain loadable without a database migration. The next successful save persists the canonical trigger-node form.

## Validation and Preview Contract

Validation replaces the old Start-node count rule with trigger-specific issues for a missing trigger, multiple enabled triggers, incoming trigger edges, and invalid scheduled offsets. Existing cycle, reachability, Finish-node, catalogue compatibility, and timing validation continue to apply.

When event duration is known, `scheduled_trigger.config.offset_minutes + policy.time_limit_minutes` must not exceed it. Manual and Event Start triggers have no offset for this calculation.

`compile_team_preview` returns a top-level provider-neutral `trigger` object:

```json
{"type": "scheduled", "offset_minutes": 15, "once": true}
```

Manual and event-start previews use `{ "type": "manual", "once": true }` and `{ "type": "event_start", "once": true }`. The trigger remains present in `order` and `manifest` so the complete graph is inspectable. Preview text displays the trigger name and, for a schedule, its event-relative offset. It no longer reads `policy.launch_mode`.

## Components

- `builder/operation_plan.py` owns trigger constants, legacy normalization, structural validation, event-duration timing checks, catalogue controls, and preview compilation.
- `frontend/static/event-operation-state.js` owns atomic trigger replacement and client-side connection rules.
- `frontend/static/event-operation.js` renders trigger nodes, picker entries, inspector configuration, port behavior, and preview text, and removes launch controls from the policy panel.
- `frontend/templates/event_operation.html` and operation styles provide any trigger labels or presentation hooks required by the existing canvas without changing the page layout.

No database migration, scheduler, background task, operation executor, or event-lifecycle callback is included.

## Failure Handling

Invalid drafts remain saveable under the existing behavior, but validation and preview report precise trigger issues. Invalid scheduled values are preserved locally until corrected. Save conflicts and network failures retain the local graph and history.

Legacy migration is deterministic and idempotent. It never changes non-trigger nodes or rewires edges beyond retaining the converted Start node's existing identifier.

## Verification

Backend tests cover new-plan defaults, all three legacy conversions, idempotent normalization, trigger counts, incoming-edge rejection, reachability, scheduled offset validation, event-duration bounds, and all three compiled trigger contracts.

Frontend state tests cover atomic trigger replacement, preservation of outgoing edges and position, rejection of incoming trigger connections, and prevention of duplicate or delete actions on the active trigger. Template and JavaScript tests cover picker labels, policy-field removal, scheduled inspector input, preview text, syntax, and existing keyboard behavior.

The full disposable Docker test service remains the authoritative regression run, as required by the repository instructions.

## Acceptance Criteria

- New operation plans begin with one Manual Trigger rather than Start.
- Administrators can replace it with Event Start Trigger or Scheduled Trigger from the graph picker.
- Scheduled Trigger accepts a whole-minute offset from the actual event start and represents a one-shot launch.
- Exactly one enabled trigger roots every valid graph, with no incoming edges and all enabled nodes reachable from it.
- Launch mode and offset no longer appear in global policy configuration.
- Existing version-1 plans load deterministically and retain their graph layout and outgoing transitions.
- Preview exposes a normalized provider-neutral trigger contract without executing or scheduling the operation.
- No runtime trigger delivery is introduced in this change.
