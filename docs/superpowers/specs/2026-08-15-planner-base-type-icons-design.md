# Planner Base-Type Icons Design

## Goal

Show each machine node's selected base-type icon in the event planner, reusing the icon metadata and visual vocabulary from the original infrastructure topology.

## Data Flow

The existing `/admin/api/base-types` catalogue already returns each base type's `icon`. The planner controller resolves a machine node's `base_type` against that catalogue and includes the icon definition in the graph row sent to the canvas.

This applies to the VPN gateway, primary firewall VM, and workload VMs. Site and zone nodes remain text-only. Changing a Base type rerenders the graph and updates the icon immediately.

Each machine inspector also provides an Icon selector. `Automatic (Base type)` removes the override and follows catalogue metadata. Choosing a library icon stores its keyword in the machine's optional `icon` field. Provisioning ignores this presentation-only field.

## Icon Contract

The canvas supports the original topology contract:

- built-in keywords: `server`, `desktop`, `laptop`, `ubuntu`, `linux`, `debian`, `kali`, `windows`, `router`, `firewall`, `attacker`, `database`, `web`, `dns`, `mail`, `directory`, `cloud`, `container`, `kubernetes`, `storage`, and `monitoring`;
- custom objects containing `svg_path` and an optional `viewbox`;
- unknown, absent, or malformed definitions fall back to `server`.

Custom paths render inside a nested SVG viewport so their declared view box is respected. Icon metadata is treated as SVG attributes, not injected markup.

## Presentation

Keep the established industrial planner styling, but do not render card backgrounds around machines. The VPN gateway, firewall VM, and workload VMs render as a large standalone base-type icon with the machine name centred below it, following the original topology's icon-first hierarchy. Sites and zones retain their structural cards.

Machine nodes retain an invisible interaction target for reliable clicking and dragging. Selected or invalid machines gain a circular cyan or red state ring around the icon; the ring is absent in the normal state. The firewall icon remains amber. Collision bounds, link anchors, and persisted coordinates do not change.

## Verification

Executable JavaScript tests cover every built-in keyword, custom path/view-box handling, and fallback behavior. Planner controller coverage verifies that machine nodes receive catalogue icons. Existing placement, state, backend, and provisioning suites continue to pass.
