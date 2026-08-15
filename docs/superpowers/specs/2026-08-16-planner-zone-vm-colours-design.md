# Planner Zone and VM Colours Design

## Purpose

Allow event planners to choose colours for workload zones, the system-managed Firewall Zone, and the machines inside them. Colours are presentation-only: they help operators distinguish topology groups without changing infrastructure, provisioning, networking, or saved node coordinates.

## Colour Model

Each zone may have one explicit colour. That colour tints the zone frame and header and becomes the inherited accent for every machine contained by the zone. Each VM may optionally define its own colour, which takes precedence over its zone's colour. Resetting a VM colour removes its override and immediately restores zone inheritance.

The Firewall Zone and Primary Firewall follow the same model. Their system-managed and security identities remain visible through labels, border patterns, icons, and other existing non-colour cues.

When no custom colour exists, the planner retains its current semantic defaults for blue-team zones, red-team zones, the Firewall Zone, and machines. Changing a zone colour does not create explicit colour entries for its children; inheritance is resolved while rendering.

## Inspector Controls

The inspector for zones, the Firewall Zone, VMs, and the Primary Firewall gains a Colour section containing:

- a curated set of swatches that fit the planner's dark industrial palette;
- a native custom colour input for arbitrary colours;
- a Reset action that restores the semantic default for zones or inherited zone colour for machines.

The active choice is clearly identified without relying on colour alone. Controls use accessible labels, keyboard-operable buttons, and visible focus states. In read-only plans, the selected and inherited colours remain visible, while colour controls are disabled or omitted consistently with the existing inspector.

## Persistence

Theme data is stored in the existing `infrastructure_layout` document under a presentation-only `themes` object keyed by stable topology node ID:

```json
{
  "version": 1,
  "nodes": {
    "zone:head_office/corporate": {"x": 280, "y": 160}
  },
  "themes": {
    "zone:head_office/corporate": {"color": "#2563eb"},
    "vm:head_office/corporate/web_1": {"color": "#a855f7"}
  }
}
```

Theme entries contain only a normalized six-digit hexadecimal `color` value. Missing entries mean automatic/inherited colour. The client and server accept existing layouts without `themes`, normalize missing theme maps to an empty object, and preserve the layout format version.

Renaming a site, zone, or VM remaps matching theme IDs using the same stable-ID rules as coordinates. Removing topology nodes prunes their theme entries. Resetting canvas positions changes only `nodes` and preserves `themes`.

## Rendering

The planner controller resolves each rendered node's effective colour from its explicit theme, its parent zone theme for machines, or its existing semantic default. It passes the effective colour and whether it is inherited to the canvas as presentation metadata.

The canvas applies colours through scoped SVG attributes or CSS custom properties rather than generating arbitrary class names. Zone colour affects the frame, header tint, and restrained accent details. Machine colour affects its state ring, icon/accent treatment, and related highlight while preserving readable labels and the existing icon shapes.

Selection and validation states remain visually dominant and distinguishable. Existing selected cyan and invalid/error treatments may override border emphasis while leaving enough of the custom fill or accent visible to retain group recognition. Links remain subdued and do not inherit custom colours.

## Validation and Error Handling

Only strings matching `#[0-9a-fA-F]{6}` are accepted. Client normalization converts accepted values to lowercase. Invalid or malformed persisted values are discarded during client normalization and rejected by server layout validation when submitted.

The server continues enforcing the existing layout size limit and valid node IDs for theme keys. A theme key that references an unknown topology node, a non-object theme entry, an unsupported field, or an invalid colour produces a precise layout validation error. Colour validation does not make legacy layouts invalid.

## Component Boundaries

- `event-planner-state.js` owns layout-theme normalization, explicit colour updates, effective colour inheritance, stable-ID remapping, and pruning.
- `event-planner.js` renders colour controls, updates theme state, and passes resolved theme metadata to the canvas.
- `event-planner-canvas.js` applies already-resolved presentation colours to SVG nodes and containers.
- `event-planner.css` defines the palette controls and custom-colour visual treatment while retaining focus, selection, validation, and system-managed cues.
- `builder/infrastructure_planner.py` validates the optional persisted theme map alongside coordinates.

No database migration or new API endpoint is required because the existing `infrastructure_layout` JSON column and event update endpoint already persist presentation data.

## Testing

State tests cover:

- normalization of absent, valid, and invalid theme maps;
- explicit zone colours and VM overrides;
- VM inheritance from its parent zone;
- reset behavior;
- theme remapping on site, zone, and VM renames;
- theme pruning when nodes are removed;
- preservation of themes when positions are reset or arranged.

Canvas and template/controller tests cover:

- effective colour metadata on zone and machine nodes;
- custom zone and machine SVG styling;
- selection, invalid, and system-managed class preservation;
- swatches, custom colour input, Reset action, accessible state, and read-only behavior.

Backend tests cover valid theme persistence and rejection of invalid colours, unknown node IDs, malformed entries, and unsupported fields. Planner-focused JavaScript tests, syntax checks, backend tests, and the full project verification suite must continue to pass.

## Acceptance Criteria

- A planner can choose a zone colour from curated swatches or a custom colour picker.
- A zone colour is visibly applied to its container and inherited by all contained machines without explicit overrides.
- A planner can give an individual VM a different colour.
- Resetting a VM restores inheritance; resetting a zone restores its existing semantic default.
- Firewall Zone and Primary Firewall colours are editable without losing system/security cues.
- Colours persist with the event and appear consistently after reload and for other administrators.
- Renames, deletion, arrangement, and position reset preserve or clean up theme data correctly.
- Read-only plans display saved colours but cannot change them.
- Invalid theme data is handled safely and produces precise server errors when submitted.
- Infrastructure and provisioning documents, node coordinates, and network behavior remain unchanged.
