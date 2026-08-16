# Module Catalogue Filters Design

## Goal

Make the module catalogue at `/admin/events/{id}/modules` easier to narrow, including distinct filters for modules that are required by other modules and modules that require no dependencies.

## Scope

Extend the existing sticky catalogue filter bar. Keep the current search, type, and assignment-state controls, then add difficulty, category, compatibility, and relationship filters plus a clear action and visible result count.

The relationship filter provides these choices:

- All relationships
- Required by other modules: the module ID appears in at least one other catalogue module's `requires` list.
- Requires no modules: the module's own `requires` list is empty.

A module may satisfy both relationship definitions. Each option is independently selectable; the UI does not attempt to impose a single hierarchy on the catalogue.

## Interaction and Data Flow

All active filters combine with AND logic. Search continues to match module ID, name, description, category, and tags. Select options are derived from the complete module catalogue and sorted for predictable scanning.

Filtering remains client-side and uses the complete catalogue when determining relationship status, regardless of other active filters. Compatibility is evaluated for the currently selected VM and changes naturally when the administrator selects a different VM.

The existing dependency-focus view remains intact. Catalogue filters continue to narrow both its highlighted relationship group and the remaining module list. Clearing filters resets search and every select without clearing the selected VM, selected module, or dependency-focus view.

The header reports the number of matching modules out of the total catalogue. The existing empty-state message remains when no modules match.

## Visual Design

Preserve the page's existing compact industrial admin styling: dark surfaces, JetBrains Mono controls, cyan focus treatment, one-pixel borders, and the sticky catalogue header. Controls wrap responsively rather than forcing the catalogue wider. The search field receives the most horizontal space; selects remain compact, and the clear action reads as a secondary control.

No new palette, typography, iconography, modal, or advanced-filter drawer is introduced.

## Implementation Boundaries

The pure filtering behavior belongs in `frontend/static/event-modules-state.js`. The page controller in `frontend/static/event-modules.js` derives select options, reads and clears filter state, updates the count, and renders the filtered catalogue. The template owns the semantic controls, while `frontend/static/event-modules.css` owns their responsive arrangement and existing-page styling.

## Testing

Node tests cover:

- modules required by at least one other module;
- modules with no requirements;
- a module satisfying both definitions;
- relationship filters combined with existing filters;
- compatibility behavior against the selected VM base type.

Template contract tests cover the added controls, result count, and clear action. The relevant Node and Python test suites must pass, followed by the repository's prescribed Docker test command if proportionate to the final change.
