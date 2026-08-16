# Module Assignment Planner Alignment

**Date:** 2026-08-16

## Design

Restyle the full-page module-assignment workspace to read as the next stage of the network planner. This is presentation-only; module metadata, provenance, usage, generation, resolution, and persistence remain unchanged.

The page uses the planner's viewport shell, 12px inset and gaps, three-region toolbar, identity treatment, lifecycle badge, standard buttons, validation region, surface panels, shared theme variables, typography, radii, and responsive breakpoint. The left rail is 240px, the catalogue remains flexible, and the detail-heavy inspector is 360px.

VM rows follow planner outline controls. Catalogue cards use `var(--bg-elevated)`, `var(--border)`, planner radii, cyan selection glow, shared muted/secondary text, amber warnings, and standard focus rings. Filters use planner form-control styling. Inspector tabs and actions use the same button and panel language rather than a separate hard-coded palette.

## Acceptance

- Both pages share the same outer spacing, toolbar structure, panel surfaces, borders, radii, tokens, and responsive behavior.
- Module assignment retains its richer cards and inspector.
- The page contains no independent hard-coded black/cyan design system.
- State remains understandable without colour.
