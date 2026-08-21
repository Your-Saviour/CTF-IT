# Operation Ability Details

## Goal

Help administrators understand exactly what an ability node will do without leaving the event operation graph. The experience should follow n8n's progressive-detail pattern: a compact inspector for routine planning and an expanded view for deeper inspection.

## Interaction model

Selecting an ability node opens the existing right-side inspector. Ability nodes receive two tabs:

- **Details** presents the ability's execution summary and is selected when the node first opens.
- **Settings** contains the existing editable node fields, including label, planned VM, timeout, retries, enabled state, and node actions.

The Details tab shows, when available:

- ability name and plain-language description;
- selected target VM;
- ability phase (`recon` or `exploit`);
- MITRE ATT&CK tactic;
- MITRE ATT&CK technique ID and name;
- supported base systems; and
- the exact shell command.

The command is collapsed behind a `Show command` disclosure in the compact inspector. An `Expand` button opens the same ability details in a larger modal dialog, where the command disclosure starts open and includes a `Copy command` control. The dialog is a larger presentation of the same content, not a separate source of truth.

Closing the dialog restores focus to the Expand button. If the selected node changes while the dialog is open, the dialog updates to the newly selected ability; selecting a non-ability node or clearing the selection closes it. Escape and the visible Close control dismiss it.

Non-ability nodes retain the current inspector behavior and do not gain empty Details/Settings tabs.

## Data contract

No database change is required. Ability information already lives in each assigned module's YAML definition.

`operation_catalogue()` will enrich each ability entry with:

- `command` from the selected Caldera phase;
- `description` from the phase, falling back to the module description;
- `tactic` from the module's Caldera definition;
- `technique`, represented as an object containing `attack_id` and `name`; and
- `supported_bases` from the module definition.

The existing `module_id`, `ability`, `name`, and stable catalogue ID remain unchanged. The authenticated, event-scoped admin plan endpoint continues to return this catalogue; no public route exposes command contents.

The selected ability node resolves its catalogue entry by the pair `(node.config.module_id, node.config.ability)`. Its current `target_vm_id` resolves against the catalogue's targets, so the details always reflect the node's present settings.

## Frontend structure

A focused, side-effect-free presentation module will own ability-detail lookup and markup generation. It will accept a node and catalogue and produce the shared dossier content used by both the compact inspector and expanded dialog. The operation controller remains responsible for selection, tab state, dialog lifecycle, focus restoration, clipboard actions, and rebinding settings controls after renders.

The compact inspector defaults to Details only when a different ability node becomes the sole selection. Changing a field or re-rendering the same node must preserve the active tab. This prevents Settings from unexpectedly switching back to Details during editing.

Commands and all module-sourced strings are HTML-escaped before insertion. Copy uses the original command string rather than text reconstructed from rendered markup. A successful copy announces `Command copied` through the existing live region; a clipboard failure announces `Could not copy command` without closing the dialog or losing selection.

## Missing and read-only data

Optional metadata is omitted rather than rendered as an empty field. If an ability has no command, the command section displays `No command metadata available` and no Copy control. An unresolved catalogue entry displays a concise `Ability details are unavailable` message while leaving Settings usable, allowing an administrator to repair or remove a stale node.

Read-only operations expose the same details, expansion, disclosure, and copy behavior. Existing mutation controls remain disabled or hidden under the current read-only rules.

## Visual direction

The feature extends the operation workspace's Industrial design: warm-black surfaces, JetBrains Mono, cyan as the interaction signal, flat one-pixel borders, tabular technical metadata, and square controls. It does not introduce a second visual language.

The distinguishing treatment is a shared technical dossier. The plain-language description leads; target and ATT&CK metadata form a compact definition list; and the command sits on a black code surface separated from explanatory content. Expanding the dossier changes available space and command disclosure state, not its information hierarchy.

On narrow viewports, the inspector uses nearly the full canvas width. The dialog is constrained to the viewport, scrolls internally, and keeps its header and close action reachable. Focus indicators use the existing cyan treatment, and reduced-motion preferences continue to disable nonessential transitions.

## Testing

Backend tests verify that recon and exploit catalogue entries include the correct command, phase description, tactic, technique, and supported bases, including modules with partial Caldera metadata.

Frontend unit tests cover ability lookup, escaped rendering, optional metadata, and the unavailable-details state. Controller/template tests cover:

- presence of Details and Settings tabs for ability selections;
- preservation of the active tab while editing the same node;
- default Details tab when selecting a different ability;
- collapsed command in the inspector and open command in the dialog;
- dialog open, close, selection-change, and focus-restoration behavior;
- copying the original command and announcing success or failure;
- unchanged non-ability inspector behavior; and
- availability of details and copy actions in read-only mode while mutations remain blocked.

The focused backend and JavaScript tests run first, followed by the repository's authoritative Docker test suite and syntax checks used by the operation workspace.

## Scope boundaries

This change does not edit module definitions, add live Caldera lookups, execute or simulate commands, add external MITRE links, redesign the node picker, or change operation-plan persistence. Ability details describe the assigned catalogue data already used to build the operation.
