# Planned VM Module Assignment

**Date:** 2026-08-16

## Summary

Add a dedicated full-page module-assignment workspace at `/admin/events/{event_id}/modules`. Administrators assign modules to canonical VMs from the network plan before an event starts. A canonical assignment is repeated identically for every team, including the resolved random portion.

Manual pins override the event quota. For blue-team VMs, random generation fills only quota deficits after pins. Red-team VMs are manual-only by default so administrators can install tools and supporting applications without receiving an ordinary blue-team challenge quota.

This feature is provider-agnostic. It owns authoring, validation, stable resolution, persistence, and preview behavior. It does not modify the current Vultr-specific provisioning implementation because provisioning is being replaced by an AWS-oriented branch. Instead, it provides a small backend reader that returns the exact resolved module IDs for a canonical planned VM.

## Full-Page Experience

### Entry and shell

- The network planner toolbar gains **Assign modules**.
- The assignment route uses the same dedicated full-viewport shell and Industrial visual direction as the planner: warm-black surfaces, mono typography, cyan primary signal, flat one-pixel borders, and no global admin sidebar.
- Its toolbar contains Back to network plan, event identity and status, save state, Preview, Save Draft, account identity, and Logout.
- Draft events are editable. Non-draft events render the saved assignment read-only.

### Three-panel workspace

The selected Option A layout contains:

1. A left rail listing canonical planned VMs grouped by site and zone. Blue and red VMs are included; gateways and firewalls are excluded. Each VM shows its role and one assignment state: valid, unresolved, not generated, or manual-only.
2. A central catalogue with search and filters for module type, difficulty, stage, category, tags, and compatibility. Each result names real module metadata and exposes Pin or Unpin. Base-incompatible modules are crossed out and explain why they are unavailable. Modules involved in current conflicts or missing dependency chains are highlighted with the exact relationship.
3. A right inspector showing pinned modules, resolved random modules, quota coverage and overruns, resource requirements, validation issues, Generate random fill where applicable, and Resolve automatically.

Selection is preserved while editing. If a selected planned VM is removed from the network plan, returning to this page selects the next available VM.

## Assignment Rules

### Canonical repetition

Every planned endpoint has one assignment keyed by the same stable VM identifier used by the planner:

`vm:<site_key>/<zone_key>/<endpoint_key>`

The saved resolved module list is repeated exactly for each event team. Random selection is never rerun per team during preview or downstream provisioning.

### Pins and quota fill

- Pins are administrator intent and are never removed silently.
- Pinned modules count toward matching type/difficulty, category, and tag quota requirements.
- Random generation fills only remaining deficits.
- Pins that exceed a quota bucket or fall outside all quota buckets remain assigned and appear as quota overrides.
- Dependencies added explicitly or by automatic resolution also count toward quota coverage when they match a bucket.
- Disabled or unknown modules cannot be newly pinned. A previously saved assignment that references one becomes invalid but remains visible until corrected.

### Blue and red defaults

- Blue-team endpoints default to `random_fill`. They begin unresolved until the administrator chooses **Generate random fill** for that VM.
- Generation is explicit and per-VM; there is no Generate all action.
- A generated result remains stable until that VM is deliberately regenerated or inputs invalidate it.
- Red-team endpoints default to `manual_only`. They accept pins and dependencies but do not show random-fill controls in the normal flow.
- Changing a zone role updates the default only for endpoints without saved assignment intent. Existing pins are preserved, while the assignment is marked for review.

### Dependencies and conflicts

By default, the UI reports rather than silently repairs relationships.

**Resolve automatically**:

- adds every missing transitive dependency in dependency order;
- replaces conflicting random modules and refills the resulting quota deficit;
- never removes a pin;
- leaves pin-to-pin conflicts unresolved for an administrator to decide;
- fails without changing the last valid resolved result when no compatible replacement exists.

Base-incompatible modules cannot be assigned automatically. A pinned module that becomes base-incompatible remains visible as a blocking error until removed or the VM base is changed.

## Persistence and API

### Event document

Add nullable `Event.module_plan` text through an Alembic migration. The parsed document is versioned:

```json
{
  "version": 1,
  "assignments": {
    "vm:head_office/dmz/web_server": {
      "mode": "random_fill",
      "pinned_module_ids": ["docker_service_lab"],
      "resolved_module_ids": [
        "docker_service_lab",
        "docker_socket_exposure",
        "journal_retention"
      ],
      "resolution_fingerprint": "sha256:..."
    }
  }
}
```

Array order is execution order: dependencies precede their consumers. `resolved_module_ids` contains the complete effective assignment, including pins, dependencies, and random selections. `resolution_fingerprint` hashes normalized quota, endpoint base type and role, pins, and the relevant catalogue compatibility metadata. It detects stale resolution; it is not used as a random seed.

### Endpoints

- `GET /admin/api/events/{id}/module-plan` returns the normalized plan, canonical assignable VMs, catalogue metadata, validation issues, and current `updated_at` concurrency token.
- `PUT /admin/api/events/{id}/module-plan` saves a draft module plan with the expected `updated_at`. Unresolved drafts are accepted. Stale writes return `409` without overwriting newer data.
- `POST /admin/api/events/{id}/module-plan/{stable_vm_id}/generate` resolves one blue VM from submitted current pins and event quota. It returns a candidate assignment without persisting it; Save Draft remains the only persistence action.
- `POST /admin/api/events/{id}/module-plan/{stable_vm_id}/resolve` resolves dependencies and replaceable random conflicts for one VM, also without persisting.
- `POST /admin/api/events/{id}/plan-preview` consumes the submitted or saved module plan and uses exact resolved IDs. It does not perform new random selection when a module plan exists.

Stable VM IDs are encoded as path-safe request data or sent in the JSON body rather than interpolated unescaped into URLs.

### Reconciliation

On read and save, the backend compares assignments with canonical endpoints from infrastructure:

- stale assignments for deleted endpoints are reported and removed only by the next successful save;
- new endpoints appear as unconfigured;
- endpoint key changes are identity changes and do not guess at assignment migration;
- name-only and presentation-layout changes preserve assignments;
- base-type, role, quota, pins, or catalogue compatibility changes that alter the fingerprint mark the resolution stale;
- stale resolved IDs remain visible for review but block Preview and Start Event.

## Backend Boundaries

Module-plan behavior lives in a focused builder/service module rather than route handlers. Its public operations are:

- normalize and validate a module-plan document;
- derive assignable canonical endpoints from infrastructure;
- generate one stable resolved assignment from pins plus quota;
- resolve dependencies and replaceable conflicts;
- reconcile a saved plan with current infrastructure and catalogue;
- return ordered resolved module IDs for a stable VM ID.

The final reader is the provider-neutral integration seam for the AWS provisioning work. This branch will not wire module assignments into current Vultr VM creation, cloud plan sizing, or Ansible launch code.

## Validation and Lifecycle

Saving a draft is allowed with warnings or blocking assignment errors. Preview and Start Event require:

- every blue endpoint to have a current, valid resolved assignment;
- every red endpoint assignment to be valid, including an empty manual-only assignment;
- all referenced modules to exist and be enabled;
- every module to support the endpoint base type;
- all dependencies to be present in execution order;
- no conflicts in the effective assignment;
- every saved assignment to reference an existing assignable endpoint;
- every resolution fingerprint to match current inputs.

Empty manual-only red assignments are valid. Infrastructure validation remains independent and continues to report its own errors.

Non-draft events cannot mutate module plans through either the UI or API.

## Error Handling

- Catalogue load failure leaves planned VM navigation and saved assignments visible, disables catalogue mutation/generation, and provides Retry.
- Generate or Resolve failures preserve pins and the previous resolved result and identify the unsatisfied quota, dependency, conflict, or compatibility cause.
- Save failures preserve all local edits. A concurrency conflict prompts reload and never merges automatically.
- Invalid or unknown saved module IDs remain visible as blocking rows; they are not silently discarded.
- Navigation warns when local module-plan edits are unsaved.

## Testing

### Unit tests

- Pins satisfy quota deficits and remain when exceeding or outside quota.
- Random fill is compatible, conflict-free, dependency-complete, and ordered.
- Repeated reads and previews use the saved resolved IDs without rerandomizing.
- Automatic resolution adds transitive dependencies and replaces conflicting random choices without removing pins.
- Pin-to-pin conflicts remain unresolved.
- Red endpoints default to manual-only and accept empty or pinned assignments.
- Fingerprints become stale for relevant quota, base, role, pin, disabled-state, compatibility, dependency, and conflict changes, but not display-name or layout changes.
- Reconciliation handles new, deleted, and re-keyed endpoints deterministically.

### API and template tests

- Admin authentication, draft-only mutation, payload limits, stable-ID handling, and `409` concurrency behavior.
- Unresolved drafts save successfully while invalid previews are rejected with stable issue paths.
- Preview uses exact resolved module IDs across all teams.
- The full-page route has no admin shell and includes planner navigation, account controls, and read-only state.

### Frontend tests

- VM navigation, catalogue search/filtering, pin/unpin, per-VM generation, automatic resolution, and state badges.
- Crossed-out incompatibilities and highlighted conflicts include textual explanations.
- Pin and resolved/random provenance remains visually and programmatically distinguishable.
- Dirty/saving/saved/failed states, unsaved navigation warning, failed-operation preservation, keyboard access, focus visibility, and live-region announcements.

## Acceptance Criteria

- An administrator can open a dedicated module-assignment page from the network planner and curate one canonical assignment per planned blue or red VM.
- A blue VM can combine quota-overriding pins with an explicitly generated random fill.
- A red VM can host manually pinned tool/application modules without receiving random quota fill.
- Conflicts and missing dependencies are visible before repair; automatic resolution never removes pins.
- Saving and reopening preserves pins and the exact resolved module order.
- Preview shows the same modules for a canonical VM across every team.
- Unresolved drafts can be saved, but Preview and Start Event are blocked with actionable issues.
- Provider-specific provisioning is unchanged, and the resolved-assignment reader is available for the AWS branch.

## Explicit Boundaries

- No current Vultr provisioning, cloud plan sizing, or Ansible launch integration.
- No per-team assignment overrides.
- No bulk Generate all action.
- No arbitrary module ordering; dependency order is authoritative.
- No silent conflict resolution or automatic removal of pins.
- No module catalogue authoring or editing from this workspace.
