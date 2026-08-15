# Icon-Only Arrange Control

## Goal

Replace the wide **Arrange VMs** canvas control with a compact icon-only button so zone headers—and therefore zone boxes—can use less horizontal space.

## Design

Each workload zone and system-managed Firewall Zone keeps one arrange control in the top-right of its header. The control becomes a `32 × 28` dark rectangular button containing a cyan four-tile grid SVG. The grid is a literal visual representation of arranging items and follows the planner's existing geometric icon language; no Unicode symbol, external asset, or new dependency is introduced.

The existing retro-futuristic palette, border, hover/focus treatment, corner radius, and header placement remain unchanged. Removing the visible label allows the reserved control width in zone geometry to fall from `96` to `32`, reducing the compact header-width floor by `64` canvas units.

## Accessibility and Interaction

The control remains an SVG group with `role="button"`, keyboard focus, Enter/Space activation, click handling, and read-only hiding. Its existing contextual accessible name, `Arrange VMs in <zone name>`, remains unchanged. A `<title>` child with the visible-equivalent text `Arrange VMs` provides a native pointer tooltip and an SVG text alternative without adding persistent header copy.

## Testing

Canvas tests will verify that the control markup uses a path-based icon and title while retaining its contextual accessible label. Geometry tests will update the compact header-width expectations and continue covering VM-grid sizing, annotated rows, manual expansion, keyboard behavior, and read-only behavior. The focused canvas tests, all planner JavaScript tests, and the disposable Docker test suite will run before completion.

## Success Criteria

- Zone headers show a small grid icon instead of the **Arrange VMs** text button.
- Pointer and keyboard users can discover and activate the same action.
- Screen readers receive the zone-specific accessible name.
- Compact zone boxes become visibly narrower without clipping titles, metadata, VM icons, or address labels.
- Existing saved layouts and planner data remain unchanged.
