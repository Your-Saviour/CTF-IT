# Planner Base-Type Icons Design

## Goal

Show each machine node's selected base-type icon in the event planner, reusing the icon metadata and visual vocabulary from the original infrastructure topology.

## Data Flow

The existing `/admin/api/base-types` catalogue already returns each base type's `icon`. The planner controller resolves a machine node's `base_type` against that catalogue and includes the icon definition in the graph row sent to the canvas.

This applies to the VPN gateway, primary firewall VM, and workload VMs. Site and zone nodes remain text-only. Changing a Base type rerenders the graph and updates the icon immediately.

## Icon Contract

The canvas supports the original topology contract:

- built-in keywords: `server`, `ubuntu`, `linux`, `debian`, `kali`, `windows`, `attacker`, and `router`;
- custom objects containing `svg_path` and an optional `viewbox`;
- unknown, absent, or malformed definitions fall back to `server`.

Custom paths render inside a nested SVG viewport so their declared view box is respected. Icon metadata is treated as SVG attributes, not injected markup.

## Presentation

Keep the established industrial planner styling. Machine icons sit at the left edge inside the existing node card, use the node's semantic accent colour, and shift the label slightly right. Node dimensions, collision bounds, link anchors, selection, invalid states, and drag behavior do not change.

## Verification

Executable JavaScript tests cover every built-in keyword, custom path/view-box handling, and fallback behavior. Planner controller coverage verifies that machine nodes receive catalogue icons. Existing placement, state, backend, and provisioning suites continue to pass.
