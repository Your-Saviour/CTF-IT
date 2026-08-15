# Planner Address Annotations Design

## Purpose

Allow event planners to assign display-only address information in the event network planner. Each workload zone may have an address range, and each planned VM may have an address. The annotations help communicate the intended network plan but do not affect provisioning, generated VM addresses, firewall configuration, or any other runtime behavior.

Address values are intentionally free-form. Values such as `x.x.{{team_id}}.x` are valid and must be preserved without IP or CIDR syntax validation.

## Data Model

Address annotations live in the canonical infrastructure document:

```json
{
  "sites": [{
    "key": "head_office",
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

`address_range` is an optional string on workload-zone records. `address` is an optional string on individual endpoint records. Missing or empty values mean that no annotation is displayed. The client preserves arbitrary entered text. Validation only permits the optional fields to be strings; it does not parse, normalize, or otherwise restrict their syntax.

The fields are not added to the VPN gateway, sites, the system-managed Firewall Zone, or the primary firewall. Legacy count-based endpoint groups remain supported by normalization; any display address carried by such a group follows the existing expansion behavior until the plan is edited into individual VM records.

## Planner Inspector

The inspector for a workload zone includes an **Address range** text input. The inspector for a VM includes an **Address** text input. These controls use the planner's existing field styling and update the canonical infrastructure state through the existing selected-node update path.

The controls do not use browser IP-specific input types or patterns. In read-only plans, saved values remain visible while the inputs are disabled consistently with other inspector controls.

## Canvas Rendering

Each workload-zone container displays its address range in the header/meta area when a non-empty value exists. Each VM node displays its address as a secondary line beneath its name when a non-empty value exists. Missing values do not create placeholder text or consume unnecessary visual emphasis.

Canvas rendering receives address annotations as explicit node presentation data from the planner controller. The canvas does not read or mutate the infrastructure document directly. Address text is rendered as text content, not injected markup, and is included in the accessible label for the corresponding zone or VM.

Long values are visually constrained to the available node or container space using the existing typography and safe truncation where necessary. The full value remains available in the inspector.

## Persistence and Provisioning Isolation

The existing event update endpoint saves the annotated infrastructure document with the plan. No database migration or new endpoint is needed.

Infrastructure validation recognizes `address_range` on workload zones and `address` on endpoints as optional strings without applying IP, CIDR, placeholder, or template validation. Existing plans without either field remain valid.

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
- acceptance of values containing `{{team_id}}` and other non-IP text;
- zone and VM inspector fields, including disabled read-only behavior;
- passing address annotations to the canvas;
- omission of annotations for nodes without values.

Canvas tests cover:

- zone range text in the container header/meta area;
- VM address text beneath the VM name;
- accessible labels that include present annotations;
- safe text rendering and constrained long-value presentation.

Backend tests cover:

- acceptance of arbitrary string annotations;
- rejection of non-string annotation values;
- unchanged infrastructure summaries and provisioning address allocation.

Planner-focused JavaScript tests, syntax checks, relevant backend tests, and the full disposable Docker test suite must continue to pass.

## Acceptance Criteria

- A planner can enter a free-form address range for each workload zone.
- A planner can enter a free-form address for each VM.
- Values such as `x.x.{{team_id}}.x` save without IP or CIDR validation.
- Saved annotations appear both on topology nodes and in their inspectors after reload.
- Read-only plans show annotations but do not allow changes.
- Empty annotations do not add placeholder text to the canvas.
- Gateway, firewall, and site nodes do not gain address controls in this iteration.
- Address annotations do not change provisioned subnets, VM addresses, preview sizing, or any other runtime behavior.
