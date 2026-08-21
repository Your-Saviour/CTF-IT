# Planner Address Annotations Design

## Purpose

Allow event planners to assign display-only address information in the event network planner. Each workload zone and system-managed Firewall Zone may have an address range, and each planned VM and primary firewall may have an address. The annotations help communicate the intended network plan but do not affect provisioning, generated VM addresses, firewall configuration, or any other runtime behavior.

Address values are intentionally free-form. Values such as `x.x.{{team_id}}.x` are valid and must be preserved without IP or CIDR syntax validation.

## Data Model

Address annotations live in the canonical infrastructure document:

```json
{
  "sites": [{
    "key": "head_office",
    "firewall_zone_address_range": "10.20.{{team_id}}.0/24",
    "firewall": {
      "address": "10.20.{{team_id}}.1"
    },
    "zones": [{
      "key": "corporate",
      "address_range": "10.20.{{team_id}}.0/24",
      "endpoints": [{
        "key": "workstation_1",
        "address": "10.20.{{team_id}}.10"
      }]
    }]
  }]
}
```

`address_range` is an optional string on workload-zone records. `firewall_zone_address_range` is an optional string on site records and belongs to that site's derived Firewall Zone. `address` is an optional string on individual endpoint records and on the site's existing primary `firewall` record. Missing or empty values mean that no annotation is displayed. The client preserves arbitrary entered text. Validation only permits the optional fields to be strings; it does not parse, normalize, or otherwise restrict their syntax.

The fields are not added to the VPN gateway or site node itself. The site-level `firewall_zone_address_range` is exposed only through its system-derived Firewall Zone node. Legacy count-based endpoint groups remain supported by normalization; any display address carried by such a group follows the existing expansion behavior until the plan is edited into individual VM records.

## Planner Inspector

The inspector for a workload zone and the Firewall Zone includes an **Address range** text input. The inspector for a VM and the primary firewall includes an **Address** text input. These controls use the planner's existing field styling and update the canonical infrastructure state through the existing selected-node update path.

The Firewall Zone's system details label the runtime value as **Provisioned subnet: Automatically allocated**. This distinguishes the operator's display-only planned range from the subnet that provisioning continues to calculate.

The controls do not use browser IP-specific input types or patterns. In read-only plans, saved values remain visible while the inputs are disabled consistently with other inspector controls.

## Canvas Rendering

Each workload-zone and Firewall Zone container displays its address range in a full-width subnet rail directly below the compact title header when a non-empty value exists. The rail reads `Range · <value>` and uses a darker zone-tinted surface with restrained cyan text. Zones without a range omit the rail and retain the compact header. Missing values do not create placeholder text or consume unnecessary visual emphasis.

Each VM and primary firewall node displays its address as a secondary line beneath its name when a non-empty value exists. The address uses the same text colour as the machine name, including inherited selected and invalid states; it does not use the machine or zone accent colour. The machine node and interaction bounds grow enough to contain both lines without colliding with neighbouring content.

Canvas rendering receives address annotations as explicit node presentation data from the planner controller. The canvas does not read or mutate the infrastructure document directly. Address text is rendered as text content, not injected markup, and is included in the accessible label for the corresponding zone or VM.

Long values are visually constrained to the available node or container space using the existing typography and safe truncation where necessary. The full value remains available in the inspector.

Zone geometry derives its content start from the actual header stack: the compact title header plus the subnet rail when present. Container bounds, automatic VM arrangement, drag constraints, link boundaries, and persisted layouts use that shared geometry rather than independent annotation coordinates. Annotation positions must remain inside their calculated zone or machine bounds.

## Persistence and Provisioning Isolation

The existing event update endpoint saves the annotated infrastructure document with the plan. No database migration or new endpoint is needed.

Infrastructure validation recognizes `address_range` on workload zones, `firewall_zone_address_range` on sites, and `address` on endpoints and primary firewalls as optional strings without applying IP, CIDR, placeholder, or template validation. Existing plans without these fields remain valid.

Provisioning services continue to allocate their current deterministic subnets and VM addresses. They do not read either annotation field. Preview cost and VM-count calculations likewise remain unchanged. The feature does not substitute templates or copy display annotations into provisioned VM records.

## Component Boundaries

- `frontend/static/event-planner-state.js` preserves the optional fields and accepts arbitrary string contents without IP-format validation.
- `frontend/static/event-planner.js` adds inspector controls and passes the relevant annotation to each rendered zone and VM node.
- `frontend/static/event-planner-canvas.js` renders the already-supplied annotation and accessible label.
- `frontend/static/event-planner.css` styles secondary address text while preserving readable node and zone layouts.
- `builder/infrastructure_validation.py` accepts the two optional string fields without interpreting their contents.
- Provisioning code remains unchanged unless a regression test needs an explicit assertion that annotations are ignored.

## Error Handling

Arbitrary strings, including unresolved templates, are accepted. Non-string values submitted through Advanced JSON produce a precise validation error for the affected field. An absent field or empty string is valid and renders no canvas annotation.

Existing save-conflict, read-only, catalogue-loading, and general infrastructure validation behavior remains unchanged.

## Testing

Planner state and controller tests cover:

- preservation of `address_range` and `address` through normalization and updates;
- preservation of `firewall_zone_address_range` and primary-firewall `address` values;
- acceptance of values containing `{{team_id}}` and other non-IP text;
- zone and VM inspector fields, including disabled read-only behavior;
- passing address annotations to the canvas;
- omission of annotations for nodes without values.

Canvas tests cover:

- zone range text in the full-width subnet rail;
- VM address text beneath the VM name;
- subnet rails that are included in zone layout, arrangement, and drag geometry only when present;
- annotation coordinates contained by their calculated zone or machine bounds;
- VM address colour matching the VM name across normal, selected, and invalid states;
- accessible labels that include present annotations;
- safe text rendering and constrained long-value presentation.

Backend tests cover:

- acceptance of arbitrary string annotations;
- rejection of non-string annotation values;
- unchanged infrastructure summaries and provisioning address allocation.

Planner-focused JavaScript tests, syntax checks, relevant backend tests, and the full disposable Docker test suite must continue to pass.

## Acceptance Criteria

- A planner can enter a free-form address range for each workload zone.
- A planner can enter a free-form address range for each Firewall Zone.
- A planner can enter a free-form address for each VM.
- A planner can enter a free-form address for each primary firewall.
- Values such as `x.x.{{team_id}}.x` save without IP or CIDR validation.
- Saved annotations appear both on topology nodes and in their inspectors after reload.
- Zone ranges appear in a full-width subnet rail below the title header without crossing a divider or overlapping controls.
- VM addresses appear inside the VM node beneath the name and use the same text colour as the name.
- Read-only plans show annotations but do not allow changes.
- Empty annotations do not add placeholder text to the canvas.
- VPN gateway and site nodes do not gain address controls in this iteration.
- Address annotations do not change provisioned subnets, VM addresses, preview sizing, or any other runtime behavior.
