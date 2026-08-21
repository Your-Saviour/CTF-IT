# Dedicated Full-Page Network Planner Shell

## Purpose

The event network planner must feel like a dedicated application workspace, not a page embedded inside the administration interface. Opening `/admin/events/{event_id}/plan` removes the global admin sidebar, breadcrumb bar, and constrained admin content area. The topology canvas and its editing tools use the complete browser viewport.

## Page Shell

`event_plan.html` will extend the shared base page directly instead of `admin_base.html`. This keeps the platform document structure, fonts, global tokens, favicon, and session behavior without rendering the admin navigation shell.

The planner owns a compact top toolbar containing:

- a Back to Events link;
- the real event name and lifecycle status;
- save state and all existing planner actions;
- the signed-in username;
- a standard Logout action posting to `/auth/logout`.

The toolbar is the only persistent chrome. There is no global sidebar, admin topbar, breadcrumb row, page header, centred container, or unused outer margin.

## Workspace Layout

The page uses the entire viewport height and width. Beneath the toolbar, the validation region consumes space only when it has content. The remaining space is a three-column workspace:

- a fixed-width structure rail for contextual add actions and the topology outline;
- a flexible canvas that receives all remaining width;
- a fixed-width inspector for the selected gateway, site, firewall, zone, or VM.

The canvas remains the visual centre of gravity. Its network grid reaches the panel boundaries and its SVG fills the available height without a fixed minimum overriding the viewport. The outline and inspector scroll independently when their content exceeds the viewport.

At narrow breakpoints, the existing responsive stacked layout remains available so controls do not become inaccessible. Desktop behavior is the acceptance target for the dedicated application layout.

## Visual Direction

The planner retains the established Industrial anchor: warm-black and black surfaces, JetBrains Mono typography, cyan as the single primary signal color, flat one-pixel borders, and tabular status information. The differentiating visual move is the uninterrupted network-grid canvas occupying the centre of the entire browser window.

All labels continue to name real event or planner information. Standard actions retain standard wording.

## Behavior and Data

This change is presentation-only. Planner state, validation, layout persistence, preview, save concurrency, provisioning, read-only behavior, and API contracts remain unchanged. Existing dialogs remain siblings of the planner application and continue to use native modal behavior.

The dedicated template must still receive the authenticated `user`, event name, event status, event ID, and read-only flag from the existing route.

## Accessibility

- The toolbar is a page header with labelled navigation and account actions.
- Existing status live regions remain intact.
- Keyboard access to the canvas, outline, inspector fields, dialogs, and buttons remains unchanged.
- Independent scrolling must not trap keyboard focus.
- Reduced-motion behavior remains supported.

## Verification

Automated template coverage will assert that the planner extends the shared base template, does not extend or render the admin shell, and includes its own account/logout controls. CSS contract tests will cover the viewport-sized root and workspace.

The implementation will also be verified with JavaScript syntax checks, the existing planner state test, the full disposable Docker test suite, and a rebuilt local container on port 8091 for browser review.

## Acceptance Criteria

- No admin sidebar, admin topbar, breadcrumbs, or constrained admin container appears on the planner route.
- The planner fills the complete browser viewport.
- The planner toolbar includes Back to Events, event identity, planner actions, username, and Logout.
- Validation and the three-column workspace fit beneath the toolbar without causing unnecessary page-level scrolling on desktop.
- Outline and inspector panels scroll independently.
- Existing planner behavior and read-only rules remain unchanged.
