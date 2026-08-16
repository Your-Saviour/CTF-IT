# Planner Command Header Design

## Goal

Improve the readability of the full-page network planner and module-assignment headers while preserving their dense, dark industrial interface. Both pages will use the same header structure and include the existing `>_ CTF Platform` brand used throughout the admin interface.

## Visual direction

Retain the existing Industrial system: dark flat surfaces, JetBrains Mono and Share Tech Mono typography, one-pixel borders, and cyan as the primary signal colour. The visible differentiator is a cyan brand block anchoring the upper-left corner, followed by a clear two-row command layout instead of one long strip of equally weighted controls.

## Structure

The toolbar becomes two rows:

1. The context row contains the CTF Platform brand, event title and description, event status, and account controls. The brand links to `/admin`, matching the existing admin navigation. The title remains the strongest event-specific label; the description uses improved muted-text contrast.
2. The command row contains back navigation, save/loading status, and page-specific actions. Actions are grouped by purpose using spacing and one-pixel separators: navigation, view/edit tools, and workflow actions. Primary actions retain cyan emphasis.

The network planner keeps Advanced JSON, Fit view, Reset layout, Preview per team, Assign modules, Save draft, and Start event. The module-assignment page keeps Preview and Save draft. Existing IDs, links, forms, disabled states, and JavaScript behaviour remain unchanged.

## Responsive behaviour

At wide widths, both rows remain horizontal. At narrower desktop widths, the command groups wrap as units so buttons do not collide or shrink into illegibility. At mobile widths, the context and command rows stack, account controls remain reachable, and the existing single-column workspace behaviour remains intact.

## Accessibility and readability

- Preserve semantic `header`, link, button, status, and form elements.
- Keep visible keyboard focus styles from the shared button system.
- Increase muted-copy contrast without competing with primary labels.
- Keep disabled controls readable through border, text, and opacity treatment.
- Do not replace standard action labels or introduce decorative filler text.

## Scope and testing

Changes are limited to `event_plan.html`, `event_modules.html`, and their shared planner CSS, plus focused template/style regression tests. Tests will verify that both pages expose the shared brand and two-row structure while retaining required controls and status regions. Existing planner and module tests must continue to pass.
