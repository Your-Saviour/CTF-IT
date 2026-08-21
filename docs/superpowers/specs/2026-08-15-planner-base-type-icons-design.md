# Planner Base-Type Icons Design

## Goal

Show each machine node's selected base-type icon in the event planner, reusing the icon metadata and visual vocabulary from the original infrastructure topology.

## Data Flow

The existing `/admin/api/base-types` catalogue already returns each base type's `icon`. The planner controller resolves a machine node's `base_type` against that catalogue and includes the icon definition in the graph row sent to the canvas.

This applies to the VPN gateway, primary firewall VM, and workload VMs. Site and zone nodes remain text-only. Changing a Base type rerenders the graph and updates the icon immediately.

Each machine inspector provides two independent selectors:

- `Primary icon` represents function or form factor. Automatic resolves gateway to Router, firewall VM to Firewall, and workload VM to Server. An explicit choice is stored in `primary_icon`.
- `Secondary icon` represents platform or product. Automatic follows base-type catalogue metadata. An explicit choice remains stored in the existing `icon` field for backwards compatibility.

Both selectors expose the full icon library, allowing any useful combination. Choosing Automatic removes only that selector's override. Provisioning ignores both presentation-only fields.

## Icon Contract

The canvas supports the original topology contract:

- built-in keywords: `server`, `desktop`, `laptop`, `ubuntu`, `linux`, `debian`, `kali`, `windows`, `router`, `firewall`, `attacker`, `database`, `web`, `dns`, `mail`, `directory`, `cloud`, `container`, `kubernetes`, `storage`, and `monitoring`;
- custom objects containing `svg_path` and an optional `viewbox`;
- unknown, absent, or malformed definitions fall back to `server`.

Custom paths render inside a nested SVG viewport so their declared view box is respected. Icon metadata is treated as SVG attributes, not injected markup.

## Presentation

Keep the established industrial planner styling, but do not render card backgrounds around machines. The selected primary icon renders large, with the selected secondary icon in a small circular badge at the lower-right. The machine name is centred below the glyph. Sites and zones retain their structural cards.

Machine nodes retain an invisible interaction target for reliable clicking and dragging. Selected or invalid machines gain a circular cyan or red state ring around the icon; the ring is absent in the normal state. The firewall icon remains amber. Collision bounds, link anchors, and persisted coordinates do not change.

## Verification

Executable JavaScript tests cover every built-in keyword, custom path/view-box handling, and fallback behavior. Planner controller coverage verifies that machine nodes receive catalogue icons. Existing placement, state, backend, and provisioning suites continue to pass.
