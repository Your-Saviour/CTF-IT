# Planner Icon Picker Design

## Goal

Replace the planner's native icon selects with dropdown pickers that show the actual SVG artwork for every Primary and Secondary icon choice.

## Interaction

The closed picker shows the resolved icon preview, selected label, and dropdown affordance. Opening it reveals a searchable, categorized list of all 50 icons. Each option displays its icon and label. Automatic is first and previews the currently resolved semantic or base-type default rather than a generic placeholder.

The picker supports pointer input plus Arrow Up/Down, Enter, Escape, and Tab. It exposes `aria-haspopup="listbox"`, expanded state, option selection state, and labelled search input. Selecting an option immediately updates the same `primary_icon` or `icon` property used today. Selecting Automatic removes only that override.

## Visual Direction

The component follows the planner's industrial theme: dark flat surfaces, one-pixel borders, cyan focus/selection, monospace labels, compact category headings, and no decorative shadows or rounded card treatment. The menu is constrained to the inspector and scrolls independently.

## Architecture

Pure markup helpers produce escaped icon SVGs, categorized options, and the picker shell. A binding function attaches open/close, search, keyboard, and selection behavior after the inspector rerenders. The existing icon registry, resolver, store, validation, and canvas projection remain the source of truth.

## Testing

Executable JavaScript tests cover the pure picker model: selected label/icon, Automatic resolution, category/search projection, and escaping-safe icon data. Controller contract tests cover both fields and accessibility hooks. The full Docker suite, independent review, and live port 8091 rebuild complete the change.
