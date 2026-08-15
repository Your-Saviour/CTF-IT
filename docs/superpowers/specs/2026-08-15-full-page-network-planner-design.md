# Full-Page Event Network Planner

**Date:** 2026-08-15

## Summary

Replace the draft event's raw GameNet JSON editor and read-only topology preview with a full-page, diagram-first network planner at `/admin/events/{event_id}/plan`. The canvas is the authoritative authoring view for the event's canonical team network. Admins create sites, zones, firewalls, and individual VMs visually, edit their properties in a contextual inspector, and receive immediate validation feedback.

The canonical plan is repeated for every event team, matching the current GameNet provisioning model. After provisioning begins, the same route becomes read-only and shows the saved topology and deployment-oriented preview information.

## Product Experience

### Entry and page structure

- The existing **Plan** action on each event row opens the full-page planner.
- The compact event drawer continues to edit event metadata and module quota. GameNet infrastructure editing moves out of the drawer; it provides a link to the planner for an existing draft.
- Creating an event initializes the current Head Office starter topology through a shared backend default, so the drawer and planner do not maintain separate infrastructure constants.
- The planner toolbar contains Back to Events, Save Draft, Validate, Preview per Team, Reset Layout, and Start Event. Start Event is shown only when the existing lifecycle prerequisites allow it and remains disabled while the plan is invalid or unsaved.
- The page uses three persistent regions: an add/outline rail, a zoomable topology canvas, and a contextual inspector.

### Diagram authoring

- The canvas is the source of truth. The outline mirrors it for navigation and quick selection.
- Admins can add sites, zones, individual VMs, and site firewalls. Add actions are context-sensitive: a zone requires a selected site, and a VM requires a selected zone.
- A new site creates its required firewall automatically. A site's firewall cannot be removed while the site exists.
- Supported relationships are rendered automatically as team VPN gateway → site firewall → zones → VMs. V1 does not allow arbitrary links, routing intent, firewall ports, or firewall policy editing.
- Dragging nodes changes presentation only. Canvas coordinates are saved and restored; Reset Layout discards manual coordinates and runs the deterministic layout again.
- The canvas supports pan, zoom, fit-to-view, selection highlighting, keyboard-accessible selection/actions, and clear site/zone containment.

### Inspector and destructive actions

- VPN gateway: base type, cloud plan, region, listen port, and optional UST prompt.
- Site: display name, generated/editable key, and region.
- Firewall: base type, cloud plan, and optional UST prompt.
- Zone: display name, generated/editable key, and blue/red team role. Subnet allocation is displayed as automatic and is not directly editable.
- VM: display name, generated/editable key, base type, cloud plan, and optional UST prompt.
- Deleting a VM removes only that VM. Deleting a zone requires confirmation and removes its VMs. Deleting a site requires a stronger confirmation and removes its firewall, zones, and VMs.
- Keys use the existing slug rules and must be unique within their current scope. Changing a display name does not silently change an existing key.

## Data and API Design

### Infrastructure document

The event continues to persist infrastructure as JSON and use the existing event create/read/update APIs. The endpoint array changes from count-based groups to individual VM records:

```json
{
  "vpn_gateway": {
    "base_type": "ubuntu_24_server",
    "default_plan": "vc2-1c-1gb",
    "region": "ewr",
    "listen_port": 51820
  },
  "sites": [{
    "key": "head_office",
    "name": "Head Office",
    "region": "ewr",
    "firewall": {
      "base_type": "opnsense",
      "default_plan": "vc2-2c-4gb"
    },
    "zones": [{
      "key": "corporate",
      "name": "Corporate",
      "team": "blue",
      "endpoints": [{
        "key": "analyst_1",
        "name": "Analyst Workstation 1",
        "base_type": "ubuntu_24_server",
        "default_plan": "vc2-1c-1gb"
      }]
    }]
  }]
}
```

- Each endpoint record represents exactly one VM per team; the new form omits `count`.
- Provisioning, sizing, cost preview, hostnames, and summaries iterate individual endpoint records. Generated hostnames continue to use the endpoint key and remain deterministic.
- The validator accepts the new endpoint shape, requires a non-empty display name, and retains existing base type, plan, key, capacity, role, region, and VPC-limit rules.

### Legacy compatibility

- Existing infrastructure containing endpoint `count` values remains readable.
- When a draft is opened, each legacy group is expanded into individual records in memory. A count of two for `workstation` becomes deterministic unique keys `workstation_1` and `workstation_2`, with display names derived from the key. If a generated key collides, the next free numeric suffix is used.
- The conversion is not persisted merely by viewing the page. The next successful draft save writes the individual-record form.
- Read-only non-draft events retain their stored legacy document and are expanded only for display. Existing provisioning records and historical event views are not migrated.
- During the compatibility period, backend preview and provisioning code accepts both shapes so an untouched legacy draft can still be validated and started.

### Canvas layout

- Add nullable event-level `infrastructure_layout` JSON text and non-null `updated_at` timestamp columns through Alembic. Layout data is deliberately separate from the provisioning document; `updated_at` supplies the planner's concurrency token and is refreshed on every event update.
- Its wire shape is `{ "version": 1, "nodes": { "<stable-node-id>": { "x": number, "y": number } } }`.
- Stable node IDs derive from structural keys (`gateway`, `site:<site_key>`, `firewall:<site_key>`, `zone:<site_key>/<zone_key>`, and `vm:<site_key>/<zone_key>/<endpoint_key>`).
- Layout validation rejects unsupported versions, non-finite coordinates, unknown IDs, duplicate structural IDs, and excessive payload size. Missing positions are auto-laid out; stale positions are ignored and removed on the next save.
- `GET /admin/api/events/{id}` returns `infrastructure_layout`; draft `PUT /admin/api/events/{id}` accepts infrastructure and layout atomically. Both fields remain immutable after provisioning begins.

### Save, advanced JSON, and concurrency

- Edits are held locally until Save Draft. The page shows clean, dirty, saving, saved, and failed states and warns before navigation with unsaved changes.
- The Advanced panel exposes the infrastructure JSON as an escape hatch. Opening it serializes current canvas state; applying edited JSON parses, normalizes, validates, and rebuilds the diagram. Invalid JSON never replaces the last valid canvas state.
- Immediate client-side validation provides fast node-level feedback, but the server remains authoritative on save, preview, and start. Server errors include a stable field path that the UI maps to a node and inspector field.
- The API includes the event's `updated_at` value in reads and requires it as an optimistic concurrency token on planner saves. A stale save returns `409` without overwriting the newer draft and prompts the admin to reload.

## Preview and Lifecycle

- Preview per Team uses the saved or current valid draft to show the canonical topology expanded for a selected team, deterministic illustrative address allocation, total VM counts, estimated monthly cost, module assignments, and attack paths.
- Preview does not persist infrastructure or layout. If the local draft differs from the saved version, the preview request carries the current valid infrastructure explicitly.
- Validation appears continuously in a summary and on affected nodes. Incomplete edits are allowed locally, but Save Draft, preview, and start are blocked until the relevant operation's server validation succeeds.
- Once provisioning begins, all authoring controls and Advanced JSON editing are disabled. The canvas retains pan, zoom, fit, selection, and inspector viewing.

## Error Handling

- Failed base-type or provider-plan catalogue loads leave the saved diagram viewable, disable affected creation/edit controls, and provide a retry action.
- Save and preview failures preserve all local edits and expose the server error without resetting the canvas.
- Removing a selected node moves selection to its parent. Removing the final site is allowed locally but is invalid for save/start under the existing non-empty-site rule.
- Unknown legacy fields are surfaced in Advanced JSON and rejected by server validation rather than silently discarded.

## Testing and Acceptance Criteria

### Backend

- Validate individual endpoint records, duplicate keys, missing names, invalid bases/plans, zone address exhaustion, and VPC region limits.
- Confirm legacy count groups normalize deterministically, collision-safe, and without persistence on read.
- Confirm provisioning, preview totals, cost calculation, address planning, hostnames, and module assignment produce one VM per individual endpoint and preserve legacy behavior.
- Test layout schema validation, stale-node cleanup, maximum payload size, and draft-only mutation.
- Test atomic infrastructure/layout updates and stale `updated_at` saves returning `409` without partial writes.

### Frontend

- Add/select/edit/reorder/delete sites, zones, firewalls, and individual VMs; verify outline, canvas, inspector, and JSON stay synchronized.
- Verify context-sensitive add actions, mandatory-firewall behavior, destructive confirmations, and selection fallback.
- Verify immediate field/node validation, server error mapping, unsaved-change warning, save-state transitions, and preservation after failed requests.
- Verify drag persistence, reset layout, deterministic placement of new/unpositioned nodes, pan/zoom/fit, and read-only mode.
- Verify keyboard navigation, focus visibility, labelled controls, and non-color-only validation/status cues.
- Verify a legacy draft expands into individual nodes and converts only after Save Draft.

### End-to-end acceptance

- From an existing draft event's Plan action, an admin can build a valid two-site network with red and blue zones and individually configured VMs without editing JSON.
- Saving and reopening restores the complete structure and node positions.
- Preview totals and per-team expansion match the diagram; starting the event provisions the same individual machines for every team.
- Invalid plans cannot be saved, previewed, or started, and each issue identifies the affected node and field.
- A provisioned event opens the same planner route read-only and cannot mutate infrastructure through either UI or API.

## Explicit Boundaries

- No arbitrary network links, custom routes, firewall rules, port policies, or per-team topology overrides in this version.
- No changes to module quota authoring beyond using the existing quota in preview and provisioning.
- No forced migration of historical or already provisioned events.
- The existing standalone live topology page remains a runtime/deployment view; this planner is the event design view.
