# Multiple Operations per Event

## Goal

Allow administrators to organize large training events into multiple independent, named operation graphs. Every operation has its own trigger, policy, nodes, validation, and preview while sharing its event's infrastructure and assigned module catalogue.

## Data model

Add an `EventOperation` entity with a stable integer ID, parent `event_id`, required name, optional description, ordered position, serialized operation plan, and created/updated timestamps. Names are required after trimming and must be unique within an event. Deleting an event cascades to its operations.

The existing nullable `Event.operation_plan` column remains temporarily for compatibility but is no longer the canonical store. The migration creates one `EventOperation` named `Operation 1` for every event with a saved legacy graph. It is guarded so existing databases and repeated startup migration checks remain safe. Events without a saved graph begin with no operations.

## Administration workflow

`/admin/events/{event_id}/operation` becomes an operations overview rather than a graph editor. It lists operations in position order and shows their name, optional description, trigger type, validation state, and last update. Administrators can create, open, duplicate, rename, edit the description, or delete an operation. An event may have zero operations while it is a draft.

Creating an operation uses the existing empty operation-plan contract. Duplicating copies the source graph and description, gives it a collision-safe name based on `<source> (copy)`, and positions it immediately after the source. Deletion requires confirmation. Operation ordering remains stable; creation appends and duplication inserts after its source.

The full-page graph designer moves to `/admin/events/{event_id}/operations/{operation_id}`. Its canvas behavior remains unchanged. Its Back action returns to the overview and its API calls address the selected operation. Non-draft events remain read-only under the existing rule.

## API and concurrency

Event-scoped collection and item endpoints provide list, create, update metadata, duplicate, and delete operations. Graph get/save/validate/preview endpoints include the operation ID. Every item lookup verifies that the operation belongs to the event; mismatches return 404.

Graph saves use the operation's `updated_at` for optimistic concurrency rather than the event timestamp. Metadata mutations and duplication update positions atomically. Invalid names return 422, duplicate names return 409, stale saves return 409, and deletion of a missing operation returns 404.

## Validation and readiness

Each graph continues through the existing normalization, catalogue, validation, fingerprint, and team-preview functions independently. The overview derives validation status using the current event infrastructure, module assignments, module catalogue, and duration.

This change does not add a new event-start restriction because the current application does not yet use operation-plan validity as a readiness gate. Runtime execution order is intentionally absent: operations are independent and their position is organizational only.

## Testing

Tests cover the model relationship, guarded migration and legacy conversion, scoped CRUD, collision-safe duplication, stable positions, independent graph saves and conflicts, overview/designer routing, frontend controls, and preservation of operation-plan validation/preview behavior. The disposable Docker test service remains the authoritative full-suite verification path.
