# General Integrations and Expo-IT Design

**Date:** 2026-08-21

## Summary

CTF-IT will gain a reusable outbound integration subsystem. Administrators manage reusable destinations and encrypted credentials centrally, then optionally bind an event to a destination. Relevant committed changes enqueue durable, coalesced synchronization work; external outages never roll back or block CTF-IT changes.

Expo-IT is the first adapter. It synchronizes through Expo-IT's authenticated, transactional `GET` and `PUT /api/v1/data` interface. CTF-IT owns exercise phases, scoring teams, and namespaced infrastructure systems. The adapter preserves data owned by Expo-IT and always submits the complete aggregate required by the endpoint.

## Goals

- Provide stable framework boundaries for future outbound integrations without making their protocols generic configuration.
- Make Expo-IT optional per event.
- Let administrators reuse centrally managed destinations and encrypted credentials.
- Synchronize automatically after relevant event, timeline, team, VM, or scoring changes and support an explicit **Sync now** action.
- Persist queued work and retry state across process restarts.
- Preserve Expo-IT-originated data during CTF-IT synchronization.
- Make configuration, progress, last success, and safe failure details visible to administrators.
- Prevent secrets from entering payload records, logs, audit details, or browser responses.

## Non-goals

- Inbound synchronization from arbitrary integrations.
- A user-programmable webhook or payload-template engine.
- Exporting CTF-IT SSH, VPN, training, infrastructure, or learner credentials to Expo-IT.
- Automatically clearing remote data when a binding is disabled.
- Supporting two active CTF-IT events in one Expo-IT destination.
- Building a separate queue service or requiring Redis, Celery, or another broker.
- Treating successful delivery as a prerequisite for normal CTF-IT event operation.

## Existing State

The branch contains a partial Expo-specific implementation:

- `api/services/expo_ust.py` builds a VM payload and targets the obsolete `/api/v1/ust/exercise` route.
- `Event` contains `expo_sync_*` status columns.
- event start and GameNet provisioning schedule synchronization;
- the event dashboard exposes Expo-specific status and retry controls; and
- `EXPO_IT_URL` and `EXPO_IT_API_KEY` are global environment variables.

The current Expo-IT application instead provides an API-key-protected `/api/v1/data` aggregate. `PUT /api/v1/data` requires exactly these keys and replaces them in one transaction:

- `phases`
- `inbox`
- `scoring`
- `spot_reports`
- `ust`
- `collaboration_points`
- `infrastructure`

The general integration subsystem replaces, rather than extends, the partial Expo-specific state and service.

## Architecture

### Configuration plane

Administrators continue to create `ServiceCredential` records through the existing encrypted credential store. An integration destination references one credential and describes a reusable external service instance. An event integration binds one event to one destination and enables or disables synchronization for that event.

The secret remains encrypted with `DATA_ENCRYPTION_KEY`. A destination stores no copy of it, and a job stores only destination and binding identifiers.

### Runtime plane

Relevant domain changes call one trigger service after their database transaction commits. The trigger creates or coalesces an outbox job for each enabled binding. An API-hosted background worker atomically claims due jobs and resolves the adapter by its stable registry key.

The adapter loads a fresh CTF-IT snapshot at execution time. It does not rely on a payload captured when the job was enqueued. This makes coalescing safe and ensures retries deliver the latest committed state.

For Expo-IT, an attempt performs:

1. authenticated `GET /api/v1/data`;
2. merge CTF-owned collections and records into the current remote document;
3. local validation against the supported Expo-IT contract;
4. authenticated `PUT /api/v1/data`; and
5. persistence of the safe result and timing metadata.

### Adapter boundary

The registry maps a stable string such as `expo_it` to an implementation. The core subsystem knows nothing about Expo-IT routes or payload fields. An adapter must provide these operations:

```python
class IntegrationAdapter(Protocol):
    key: str

    def validate_destination(self, destination: IntegrationDestination) -> list[str]: ...
    async def test_connection(self, destination: IntegrationDestination, secret: str) -> ConnectionTestResult: ...
    async def synchronize(
        self,
        binding: EventIntegration,
        destination: IntegrationDestination,
        secret: str,
    ) -> SyncResult: ...
```

Adapter registration is explicit at application startup. Unknown adapter keys are invalid configuration and cannot be enabled.

## Persistence Model

### `integration_destinations`

| Field | Contract |
|---|---|
| `id` | Integer primary key. |
| `name` | Required, unique administrator-facing name, maximum 128 characters. |
| `adapter_key` | Required stable registry key, maximum 64 characters. Initially `expo_it`. |
| `base_url` | Required normalized absolute URL, maximum 512 characters, stored without a trailing slash. |
| `credential_id` | Required foreign key to `service_credentials`; deletion is restricted. |
| `enabled` | Administrator kill switch, default `true`. |
| `allow_insecure_http` | Explicit opt-in for private-network HTTP, default `false`. |
| `config_json` | Adapter-specific non-secret JSON object, default `{}`. |
| `last_test_status` | `successful` or `failed`, nullable before the first test. |
| `last_test_error` | Sanitized bounded text, nullable. |
| `last_tested_at` | UTC timestamp, nullable. |
| timestamps | `created_at` and `updated_at`. |

`base_url` must use HTTPS unless `allow_insecure_http` is true. Userinfo, query strings, and fragments are forbidden. Connection tests and synchronization do not follow redirects.

### `event_integrations`

| Field | Contract |
|---|---|
| `id` | Integer primary key. |
| `event_id` | Foreign key to `events` with cascade delete. |
| `destination_id` | Foreign key to `integration_destinations`; deletion is restricted. |
| `enabled` | Binding state, default `false`. |
| `last_success_at` | UTC timestamp of the most recent successful delivery. |
| `last_status` | Derived cached state for efficient UI reads. |
| `last_error_code` | Sanitized stable error code, nullable. |
| `last_error_message` | Sanitized bounded operator-facing detail, nullable. |
| timestamps | `created_at` and `updated_at`. |

There is a unique constraint on `(event_id, destination_id)`. Application validation permits at most one enabled `expo_it` binding for an event and at most one enabled event binding for an Expo-IT destination. This reflects Expo-IT's single-exercise data model and prevents destructive cross-event replacement.

### `integration_sync_jobs`

| Field | Contract |
|---|---|
| `id` | Integer primary key. |
| `binding_id` | Foreign key to `event_integrations` with cascade delete. |
| `status` | `pending`, `running`, `retrying`, `succeeded`, `failed`, or `cancelled`. |
| `trigger_reason` | Latest bounded reason such as `timeline_updated`, `vm_updated`, or `manual`. |
| `priority` | Integer; manual synchronization raises a pending job's priority. |
| `attempt_count` | Number of started attempts. |
| `next_attempt_at` | UTC time at which the job becomes claimable. |
| `claimed_at` | UTC claim time, nullable. |
| `claim_token` | Random opaque claim identifier, nullable. |
| timestamps | `created_at` and `updated_at`. |

A partial unique index, or the equivalent transactional application invariant on SQLite, permits only one active (`pending`, `running`, or `retrying`) job per binding. A change arriving during `running` records that a follow-up delivery is required; completion then creates or retains one pending job.

### `integration_sync_attempts`

Attempts are immutable audit records containing job and binding identifiers, attempt number, start and finish timestamps, result, HTTP status when safe, error code, sanitized message, and remote request duration. They never contain request/response bodies, headers, URLs with userinfo, or decrypted credentials.

Retention defaults to the latest 100 attempts per binding. Cleanup is best-effort after successful writes and never affects synchronization correctness.

## Queue and Worker Semantics

### Enqueueing

`enqueue_event_sync(db, event_id, reason, priority=0)` is the single entry point. Callers invoke it only after the domain transaction commits. It returns without network I/O.

If an active job exists, enqueueing updates its reason, priority, and follow-up marker rather than creating unbounded work. Disabled bindings or destinations do not receive new jobs. Disabling either cancels pending/retrying jobs; a running attempt may finish, but its result does not re-enable the binding.

Relevant triggers are:

- event start/open and changes to synchronized event fields;
- timeline save;
- team create, rename, reassignment where applicable, and delete;
- VM create, update, provision completion, address/zone/team/status change, and delete;
- any scoring mutation that changes the event scoreboard; and
- administrator **Sync now**.

Code paths that make bulk changes enqueue once after the bulk transaction. The implementation uses explicit domain-level calls rather than broad SQLAlchemy model listeners, so tests, migrations, and unrelated maintenance writes do not cause hidden network work.

### Claiming and recovery

The worker runs from the FastAPI lifespan and polls with a short configurable interval. A claim transaction selects one due job, marks it `running`, assigns a random claim token, and commits before external I/O. PostgreSQL uses row locking with skip-locked behavior. SQLite uses a guarded update and verifies the claim token. This supports multiple API replicas without duplicate intentional claims.

A `running` job whose `claimed_at` exceeds the stale-claim timeout is returned to `retrying` at startup and during polling. Synchronization is therefore at-least-once. The Expo-IT operation is an idempotent replacement, so repeating a completed request is safe.

### Destination serialization

Only one job per destination may run at a time. The worker obtains a destination-scoped database lease before calling an adapter. This prevents read-merge-write races between jobs and connection tests do not acquire the write lease because they are non-mutating.

### Retry policy

Timeouts, connection failures, HTTP `429`, and HTTP `5xx` responses retry with exponential backoff and jitter. The initial delays are approximately 5, 15, 45, 135, and 300 seconds, capped at five minutes. The initial job exhausts after five attempts and becomes `failed`.

HTTP `401`/`403`, invalid destination configuration, local contract validation failures, and other non-transient `4xx` responses fail immediately. A later relevant change or **Sync now** creates a fresh job after an operator has corrected the problem.

Integration failure never rolls back the originating CTF-IT transaction, prevents event start, or stops provisioning.

## Expo-IT Adapter Contract

### Authentication and transport

The adapter sends the referenced credential's decrypted secret as `X-API-Key`. It uses a 15-second total request timeout, does not follow redirects, and accepts JSON only. Connection testing is a non-mutating `GET /api/v1/data` and reports authentication, reachability, and contract compatibility separately.

### Ownership

Every `PUT` contains all aggregate keys required by Expo-IT. Ownership is:

| Dataset | Owner and merge rule |
|---|---|
| `phases` | CTF-IT collection ownership; replace from the bound event timeline. |
| `scoring` | CTF-IT collection ownership; replace from current event teams and scores. |
| `infrastructure.systems` | Record-level shared ownership; replace only CTF-IT namespaced systems for the bound event and preserve all other systems. |
| `infrastructure.credentials` | Expo-IT ownership; preserve the remote catalog and never introduce CTF-IT secrets. |
| `inbox` | Expo-IT ownership; preserve current records. |
| `spot_reports` | Expo-IT ownership; preserve current records. |
| `ust` | Expo-IT ownership; preserve current records. |
| `collaboration_points` | Expo-IT ownership; preserve current records. |

CTF-IT-owned system IDs use `ctf-event-{event_id}-vm-{vm_id}`. The prefix is reserved for this adapter. A remote record using that prefix but not corresponding to a current VM is considered a stale CTF-owned record and is removed unless the resulting aggregate violates Expo-IT references. If a UST ticket still references it, synchronization fails with `remote_reference_conflict`; the adapter does not rewrite or delete the Expo-owned ticket.

### Phase mapping

Timeline phases are ordered by `start_offset_minutes` and numbered from zero because Expo-IT's contract identifies phases by a non-negative integer. `time_range` is an ISO-8601 UTC start/end display derived from `Event.started_at` and each phase's offsets. A phase is `current` when the event is open and the current time is within its half-open `[start, end)` interval. Before the first phase or after the final phase, no phase is current. A draft event cannot be actively bound; an open event without a timeline emits an empty phase collection.

### Scoring mapping

Each CTF-IT team produces one Expo-IT scoring record using the exact CTF-IT team name. The initial field mapping is:

- `defense`: blue defensive plus blue reactive score;
- `reverts`: blue reactive score;
- `availability`: CTF-IT availability score when present, otherwise `0`;
- `collaboration`: the sum of preserved Expo-IT collaboration records awarded to that team;
- `usability`, `ctirep`, `sitrep`, `forensics`, `legal`, `stratcom`, `stratex`, and `xpoints`: `0` until CTF-IT gains an explicit source.

All values are non-negative numbers. The adapter does not infer or fabricate score categories.

### Infrastructure mapping

Each event VM with a team maps to one system:

- `expo_id`: the stable namespaced ID;
- `system_aliases`: unique non-empty values from hostname and other stable VM names;
- `team` and `team_name`: the CTF-IT team name;
- `role`: VM role, falling back to VM type;
- `os`: known OS value or omitted;
- `zones`: the VM zone name when present;
- `networks`: normalized known IPv4/IPv6 values from the VM address fields, with deterministic interface labels; and
- `credential_ids`: preserved only for an existing CTF-owned remote system and only when every reference remains in the preserved credential catalog.

Duplicate addresses are removed. Invalid or empty addresses are omitted. Availability data is preserved for an existing CTF-owned system until CTF-IT has an authoritative availability mapping; it is not copied onto a new or different VM.

### Required Expo-IT compatibility adjustment

The current Expo-IT `GET /api/v1/data` response removes credential passwords, which is compatible because an omitted password preserves the existing secret on `PUT`. It also removes `system_aliases`, but `PUT` defaults an omitted alias list to empty. A read-merge-write client would therefore erase Expo-IT-managed aliases.

Before enabling synchronization, Expo-IT must change its authenticated management API so `GET /api/v1/data` and `GET /api/v1/infrastructure` include `system_aliases`. Browser-facing page data remains unchanged. A cross-repository contract test proves that a GET followed by an unchanged PUT preserves passwords, aliases, credential associations, availability, and all datasets.

The adapter declares a supported contract version in its `User-Agent` and validates the returned shape. If Expo-IT later publishes an explicit API version/revision field, the adapter will validate it; this design does not invent an undocumented version header.

### Concurrency limitation

Expo-IT currently has no ETag or conditional-update token. Destination-level serialization prevents races between CTF-IT jobs, but it cannot prevent an Expo-IT user mutation between the adapter's GET and PUT. The initial adapter minimizes this window and preserves all data returned by GET. Adding conditional `If-Match` support to Expo-IT is recommended follow-up work, but not required for the first integration because the API does not presently expose revision semantics.

## API and Operator Experience

### Destination administration

Admin Settings gains an **Integration destinations** section. Administrators can:

- list destinations without secret material;
- create or edit a destination and select a compatible token `ServiceCredential`;
- enable or disable a destination;
- perform a non-mutating connection test; and
- delete an unreferenced destination.

Destination creation rejects an unknown adapter, incompatible credential type, invalid URL, or insecure HTTP without the explicit override. Credential deletion returns `409 Conflict` while a destination references it. Destination deletion returns `409 Conflict` while an event binding references it.

### Event binding

Event configuration gains an **Integrations** section. An administrator selects an existing destination and enables the binding. Enabling validates destination state and rejects conflicts with another enabled event, naming that event in the error response. Draft events may be configured but the binding remains inactive until the event opens; stopped events do not enqueue automatic synchronization.

### Event status

The dashboard replaces the Expo-specific card with a generic integrations card. Each binding displays:

- adapter and destination name;
- `disabled`, `pending`, `syncing`, `synchronized`, `retrying`, or `failed` state;
- queued trigger reason;
- last success time;
- current/recent attempt count;
- next retry time when applicable; and
- sanitized error code and summary.

**Sync now** is available for enabled bindings on open events. It enqueues or prioritizes a job and returns HTTP `202`; the browser polls status rather than holding the request open.

## Security and Privacy

- Only administrators may manage destinations, bindings, connection tests, or manual synchronization.
- Destination and binding API responses never contain encrypted or clear credentials.
- Secrets are decrypted immediately before the outbound request and are not retained by the adapter result.
- Logging uses destination and binding IDs, never authentication headers or full payloads.
- Stored errors are sanitized, bounded, and must not include response bodies because remote services can reflect secrets or sensitive exercise data.
- Redirects are disabled so API keys cannot be forwarded to another origin.
- HTTPS is required by default. The HTTP override is explicit and visible in destination metadata.
- Full Expo-IT payloads are not persisted in jobs or attempts; they can include credentials and exercise-sensitive information.
- Existing SSRF exposure is reduced by strict URL parsing. This first version permits administrator-selected hosts because administrators already control infrastructure integrations; host allow-listing is an optional deployment-hardening follow-up.

## Migration and Compatibility

The database migration creates the four generic integration tables and their indexes. It does not silently convert global environment settings into a database credential because doing so would copy a secret without an administrator-visible ownership decision.

After deployment:

1. existing `EXPO_IT_URL` and `EXPO_IT_API_KEY` values are ignored with a startup deprecation warning;
2. an administrator creates or selects a token credential;
3. the administrator creates an Expo-IT destination and tests it;
4. the administrator binds the intended event; and
5. the administrator enables the binding and uses **Sync now** or starts the event.

After the generic UI and worker are operational, the obsolete `Event.expo_sync_status`, `expo_sync_last_error`, `expo_sync_attempts`, and `expo_sync_completed_at` columns and the `api/services/expo_ust.py` service are removed. Existing values are historical-only and need not be migrated because attempt history did not previously capture actionable details and the old endpoint is incompatible.

Local and production Compose files remove the global Expo variables. No new worker container or external runtime dependency is introduced.

## Testing Strategy

### Core unit tests

- registry accepts unique known adapters and rejects duplicate/unknown keys;
- destination URL and credential compatibility validation;
- binding uniqueness and active-destination conflict rules;
- enqueue coalescing, manual priority, disabled-state cancellation, and follow-up behavior;
- retry classification and capped backoff;
- stale claim recovery and destination lease behavior; and
- secret/error redaction.

### Expo-IT adapter tests

- phases before, during, and after event time boundaries;
- empty timeline behavior;
- score field mapping and non-negative values;
- stable system IDs, address normalization, and optional fields;
- preservation of Expo-owned datasets, credentials, availability, aliases, and unrelated systems;
- removal of stale namespaced systems;
- referenced stale-system conflict reporting;
- authentication, transport, timeout, redirect, and response-shape errors; and
- a full payload accepted by Expo-IT's strict Pydantic models.

### API and UI tests

- admin authorization on all mutation and test routes;
- browser-safe destination metadata;
- credential and destination deletion conflicts;
- event configuration conflict messaging;
- status state rendering and polling; and
- **Sync now** returning `202` without making an inline remote call.

### Cross-repository contract test

Tests start the neighboring Expo-IT FastAPI app with a temporary database and API key, seed every dataset plus credential secrets and aliases, run the adapter, and assert:

- Expo-IT returns HTTP `200`;
- every required dataset remains present;
- Expo-owned records are unchanged;
- credential passwords remain usable and never appear in responses;
- aliases and availability survive the round trip; and
- CTF-owned phases, scores, and systems match the CTF-IT snapshot.

The normal CTF-IT suite must not require `../../expo-it` to exist. The cross-repository test is a separately marked integration test or Compose profile used in this feature branch and CI when both repositories are available. Unit tests retain a checked-in contract fixture so the core suite remains self-contained.

## Documentation and Rollout

- Update `.env.example`, local Compose, production Compose, and README to remove the obsolete global Expo variables and describe database-managed destinations.
- Document destination creation, per-event enablement, connection testing, synchronization states, retry behavior, and the HTTP override.
- Apply the Expo-IT alias-preservation compatibility change before enabling any binding.
- Run database migrations before starting the new worker.
- Create a destination, test it, bind a non-production event, and perform **Sync now**.
- Verify preservation of Expo-owned records, then enable automatic synchronization for the intended event.

## Acceptance Criteria

1. An administrator can create an Expo-IT destination referencing one existing encrypted token credential and bind it optionally to one event.
2. The same credential can be referenced by multiple destinations or events without copying its secret.
3. Relevant committed changes and **Sync now** create durable, coalesced work without making external calls in the mutation request.
4. Jobs survive restart, retry transient failures, recover stale claims, and serialize writes per destination.
5. A successful Expo-IT sync sends a complete `/api/v1/data` document with CTF-owned phases, scoring, and systems while preserving Expo-owned records and secrets.
6. No CTF-IT infrastructure, SSH, VPN, learner, or training credential is exported.
7. One Expo-IT destination cannot be enabled for two events concurrently.
8. Failures are visible and retryable but never roll back event changes or prevent event operation.
9. Administrators can test a destination without mutating Expo-IT.
10. Secrets and full payloads do not appear in browser responses, job records, attempt records, or logs.
11. The partial `/api/v1/ust/exercise` integration and Expo-specific event status are removed.
12. Core tests run without the sibling repository, and the optional cross-repository test proves round-trip compatibility with Expo-IT.
