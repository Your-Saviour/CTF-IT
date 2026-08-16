# Operation Ability Zone and VM Filter

**Date:** 2026-08-17

## Purpose

Add applicability filters to the Add node picker on `/admin/events/{event_id}/operations/{operation_id}` so administrators can see which Caldera abilities apply to a planned zone or VM before adding an ability node.

The feature applies only to the operation designer's Add node picker. It does not hide, dim, or otherwise filter nodes already placed on the operation canvas.

## Interaction Design

The node picker retains its existing text search and grouped results. Directly beneath Search nodes, it adds two compact filters:

- **Zone**, defaulting to **All zones**;
- **VM**, defaulting to **All VMs**.

Opening the picker resets both filters to their defaults. Choosing a zone narrows the VM options to planned VMs in that zone and shows abilities assigned to at least one VM in that zone. Choosing a VM narrows the ability results to abilities applicable to that VM.

When a VM is selected, choosing an ability creates the node with that VM's planned target ID in `config.target_vm_id`. Clearing the VM selection clears this implicit target. A user may choose a zone, leave VM set to All VMs, and add an ability node without a target. The node remains an intentionally incomplete draft that can be configured later in the inspector and is reported by the existing graph validation.

The zone and VM filters affect only the Abilities result section. Trigger, Target, Objective, and Flow control results continue to follow the existing search and connection-compatibility behavior. Text search and applicability filtering combine: an ability must match both the search query and the selected zone or VM.

Each ability result communicates its current applicability using real catalogue data. With a VM selected, it names the selected target. With only a zone selected, it reports the number of matching VMs in that zone. With no applicability filter, it reports its total applicable VM count. When no abilities match, the picker distinguishes an applicability-filter empty state from a text-search empty state.

## Zone Identity and Cascading Behavior

Zone choices use a stable identity composed from the target's site and zone values. Their visible labels include both site and zone so equal zone names in different sites remain distinct.

Changing Zone resets VM to All VMs because the previous VM may not belong to the new zone. The VM list is then rebuilt from targets in the selected zone. Selecting All zones restores all planned VM choices.

Selecting a VM does not mutate the operation plan. It only affects the picker result set and the configuration of a subsequently added ability node. Closing or cancelling the picker discards filter state without creating a history entry or marking the draft dirty.

## Authoritative Applicability Data

`builder.operation_plan.operation_catalogue()` remains the authority for ability applicability. Each ability catalogue row gains `applicable_target_ids`, a deterministic list of planned target IDs to which that ability's module is effectively assigned.

Effective assignment includes:

- modules in an endpoint assignment's `pinned_module_ids`;
- modules in its `resolved_module_ids`;
- recursively required modules reached from either set.

An ability is applicable to a target only when its module is effectively assigned to that target. Existing base compatibility remains authoritative validation: if an assigned ability declares `supported_bases`, the target must also use one of those bases. The applicability list excludes targets that fail that compatibility rule so the picker does not offer a known-invalid VM and ability pairing.

Ability rows remain globally unique by module and Caldera phase. The backend adds target IDs to those rows rather than duplicating an ability once per VM. Catalogue ordering stays deterministic.

## Frontend Architecture

A focused framework-independent module owns picker applicability behavior. It provides pure functions for:

- producing distinct site-aware zone options from catalogue targets;
- producing VM options for the selected zone;
- filtering ability rows by selected zone, selected VM, and search text;
- deriving the `target_vm_id` for a newly selected ability;
- generating concise applicability descriptions and empty-state reasons.

`frontend/static/event-operation.js` remains the page controller. It owns the transient selected-zone and selected-VM values, resets them when opening the picker, renders the controls, and passes the chosen target into the existing node insertion path. No applicability filter state is persisted in the operation plan, browser storage, or API.

`frontend/templates/event_operation.html` provides labelled Zone and VM controls in the picker. `frontend/static/event-operation.css` extends the current Industrial visual system: warm-black surfaces, monospaced typography, cyan signal colour, flat one-pixel borders, square controls, and no decorative shadows or rounded filter chrome. The filters remain usable on narrow screens and retain visible keyboard focus.

## Validation and Failure Handling

The operation-plan schema and save API do not change. An ability added without a VM receives an empty `target_vm_id`; the existing `unknown_target` validation issue blocks preview or compilation while preserving the saveable draft.

If there are no planned zones or VMs, the filters remain legible and disabled where appropriate. If a selected zone contains no VMs, or a selected VM has no applicable abilities, the Abilities section explains the condition rather than presenting a blank panel. Search remains available for the unaffected node sections.

Malformed or stale applicability IDs are ignored by frontend filtering. Backend validation remains authoritative if catalogue inputs change between editing and saving.

## Verification

Backend tests cover:

- per-target applicability for pinned and resolved assignments;
- recursively required modules appearing only for targets whose assignment reaches them;
- exclusion of base-incompatible targets;
- deterministic `applicable_target_ids` ordering;
- preservation of the existing catalogue fields and controls.

Framework-independent JavaScript tests cover:

- distinct site-aware zones when zone names repeat across sites;
- VM options cascading from Zone;
- ability filtering for All zones, one zone, and one VM;
- applicability filters combined with text search;
- VM selection becoming a new ability node's target;
- All VMs producing an empty target;
- applicability descriptions and empty states;
- picker filter reset behavior.

Template tests verify labelled Zone and VM controls, accessible associations, and loading of the picker helper module. Existing operation state, catalogue, validation, API, preview, and JavaScript syntax checks remain regression coverage.

Manual verification covers keyboard-only filtering, repeated zone names across sites, selecting and clearing a VM, adding a targeted and untargeted ability, picker reopening with reset filters, connection-originated picker use, narrow layouts, and empty states.

## Acceptance Criteria

- The Add node picker on `/admin/events/{event_id}/operations/{operation_id}` exposes Zone and VM filters.
- Selecting a zone shows abilities assigned to at least one VM in that site-aware zone and limits the VM choices accordingly.
- Selecting a VM shows only abilities effectively assigned and compatible with that VM.
- A newly added ability automatically targets the explicitly selected VM.
- A user can add an ability with a zone selected and no VM selected; the resulting draft node has no target and existing validation identifies it.
- Search combines with applicability filters without changing non-ability result behavior.
- Picker filters reset whenever the picker opens and never become persisted graph state.
- Empty and unavailable states are explicit and accessible.
- Existing operation-plan persistence, canvas nodes, validation authority, and visual language remain intact.
