# Module Assignment Usability Redesign

**Date:** 2026-08-16

## Purpose

Replace the thin module-assignment catalogue with an information-rich admin workspace that makes each module understandable before assignment and makes its current assignment state unmistakable.

## Workspace

Retain the full-page three-panel layout. The left rail lists planned VMs vertically with role, location, assigned count, and validation state. The centre presents substantial module cards. The right inspector switches between **Assignment summary** and **Module details** without losing the selected VM.

## Catalogue Cards

Every card shows the real module name, plain-language description, type, difficulty, points, category, stage, tags, base compatibility, requirements, conflicts, and one explicit selected-VM state:

- **Manually assigned** — directly chosen by the administrator.
- **Randomly assigned** — selected while filling quota.
- **Required dependency** — included because another effective module requires it.
- **Not assigned** — absent from the selected VM.

Cards never use “pinned.” Clicking selects a module for inspection and does not mutate assignment. Search includes name, ID, description, category, and tags. Filters cover type, difficulty, category, assignment state, and compatibility.

## Inspector

The **Assignment summary** tab groups the selected VM's effective assignment by manual, random, and dependency origin and retains Generate random fill and Resolve automatically.

The **Module details** tab shows description, status, type, difficulty, points, category, stage, tags, estimated time, learning objectives, prerequisites, requirements, conflicts, verification summary, compatibility, and a link to the existing complete module-detail page. It also lists every planned VM using the selected module and the provenance on each VM.

Primary actions are **Assign module** and **Remove assignment**. A dependency-only module names the parent assignment that requires it and cannot be removed directly. Disabled or incompatible modules explain why assignment is unavailable.

## Data and Boundaries

Extend the existing module-plan GET response with metadata already present in module definitions: learning objectives, estimated minutes, prerequisites, and a concise verification type. No new persistence shape is required. Client state derives provenance by comparing `pinned_module_ids`, ordered effective IDs, dependency relationships, and quota-generated remainder. Plan-wide usage is derived from every assignment in the returned module plan.

Backend assignment semantics, canonical repetition across teams, random-fill behavior, red manual-only behavior, concurrency, and provider boundaries remain unchanged.

## Error and Empty States

Loading, no planned VMs, no catalogue matches, no assignment, incompatible base, disabled module, unresolved dependency/conflict, failed request, read-only event, and stale-save states use visible text and live-region announcements. State never depends on colour alone.

## Testing

Pure JavaScript tests cover provenance classification, plan-wide usage, search/filter results, dependency parents, and terminology. Template/CSS tests cover the two inspector tabs, rich card regions, explicit actions, module-detail links, live regions, and vertical VM navigation. Existing backend and module-plan tests protect assignment semantics.

## Acceptance Criteria

- An admin can understand what a module does without leaving the assignment page.
- Every module visibly states whether and how it is assigned to the selected VM.
- The detail inspector shows where the module is used across the full plan.
- No assignment UI uses “pinned” or “pin.”
- Admins can distinguish manual, random, and dependency assignments without relying on colour.
- Existing generation, resolution, save, and read-only behavior remains available.
