# General Integrations and Expo-IT Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add optional per-event outbound integrations backed by reusable administrator-managed destinations and a durable outbox, with Expo-IT `/api/v1/data` as the first adapter.

**Architecture:** Generic destination, binding, job, and attempt models feed an in-process database-backed worker through an explicit adapter registry. The Expo-IT adapter reads the current aggregate, replaces CTF-owned phases, scores, and namespaced systems, validates the full merge, and writes it back while preserving Expo-owned data.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Pydantic 2, httpx, SQLite/PostgreSQL, Jinja, browser JavaScript, pytest, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-08-21-general-integrations-expo-it-design.md`

## Global Constraints

- Expo-IT is optional per event and must use an administrator-managed reusable destination and encrypted `ServiceCredential`.
- Network I/O never occurs inside event mutation requests; all delivery uses durable jobs created after successful commits.
- One event may have at most one enabled Expo-IT binding, and one Expo-IT destination may serve at most one enabled event.
- Every Expo-IT write includes the complete `/api/v1/data` aggregate.
- CTF-IT owns phases, scoring, and `ctf-event-{event_id}-vm-{vm_id}` systems; Expo-IT owns all other datasets and infrastructure credentials.
- CTF-IT must not export SSH, VPN, training, infrastructure, or learner secrets.
- Jobs and attempts never store payloads, authentication headers, decrypted secrets, or remote response bodies.
- HTTPS is required unless an administrator explicitly enables private-network HTTP; redirects stay disabled.
- No Redis, Celery, separate worker container, or new runtime dependency is introduced.
- The ordinary CTF-IT test suite must pass without `../../expo-it`; cross-repository verification is separately marked.

## File Structure

### CTF-IT files

- Create `api/integrations/__init__.py`: package exports and adapter registration.
- Create `api/integrations/base.py`: adapter protocol and safe result value objects.
- Create `api/integrations/registry.py`: explicit adapter registry.
- Create `api/integrations/expo_it_contract.py`: strict local Pydantic representation of Expo-IT `/api/v1/data`.
- Create `api/integrations/expo_it.py`: CTF snapshot mapping, ownership merge, and HTTP adapter.
- Create `api/services/integration_outbox.py`: enqueue/coalesce, retry classification, claims, leases, and worker loop.
- Create `api/routes/integrations.py`: administrator destination, binding, test, status, and sync APIs.
- Create `migrations/versions/0017_general_integrations.py`: generic schema and obsolete Expo column removal.
- Modify `api/models.py`: ORM entities and relationships.
- Modify `api/main.py`: router registration, worker lifespan, and removal of compatibility columns.
- Modify `api/routes/admin.py`, `api/routes/vm.py`, `api/routes/learner.py`, `api/routes/vm_goals.py`, `api/services/verification.py`, and `api/services/gamenet_provisioning.py`: explicit post-commit triggers.
- Modify `api/routes/service_credentials.py`: referenced-credential deletion guard.
- Modify `frontend/templates/admin_settings.html`: destination management UI.
- Modify `frontend/templates/admin_resource.html` and `frontend/static/admin-events.js`: event binding editor.
- Modify `frontend/templates/event_dashboard.html`: generic integration status and manual sync UI.
- Delete `api/services/expo_ust.py`: obsolete endpoint-specific implementation.
- Modify `.env.example`, `docker-compose.yml`, production Compose/environment examples, and `README.md`: remove global Expo variables and document database-managed setup.
- Create `tests/test_integration_models.py`, `tests/test_integration_outbox.py`, `tests/test_expo_it_adapter.py`, and `tests/test_integrations_api.py`.
- Modify `tests/conftest.py`: reusable destination, binding, job, token credential, and administrator fixtures.
- Create `tests/fixtures/expo_it_data.json`: self-contained strict contract fixture with no real credentials.
- Create `tests/test_expo_it_live_contract.py`: optional sibling-repository round-trip test.

### Expo-IT sibling repository files

- Modify `../../expo-it/app/api.py`: preserve `system_aliases` in authenticated management responses.
- Modify `../../expo-it/tests/test_app.py`: unchanged GET/PUT round-trip coverage for aliases and credential secrets.
- Modify `../../expo-it/README.md`: document that authenticated management responses expose aliases but redact passwords.

---

### Task 1: Generic Integration Schema and Registry

**Files:**
- Create: `migrations/versions/0017_general_integrations.py`
- Create: `api/integrations/__init__.py`
- Create: `api/integrations/base.py`
- Create: `api/integrations/registry.py`
- Modify: `api/models.py`
- Test: `tests/test_integration_models.py`

**Interfaces:**
- Produces: `IntegrationDestination`, `EventIntegration`, `IntegrationSyncJob`, `IntegrationSyncAttempt` ORM models.
- Produces: `ConnectionTestResult(ok: bool, code: str, message: str)` and `SyncResult(ok: bool, code: str, message: str, http_status: int | None, retryable: bool)`.
- Produces: `register_adapter(adapter)`, `get_adapter(key)`, and `adapter_keys()`.

- [ ] **Step 1: Write failing model and registry tests**

```python
# tests/test_integration_models.py
import pytest
from sqlalchemy.exc import IntegrityError

from api.integrations.base import ConnectionTestResult, SyncResult
from api.integrations.registry import adapter_keys, get_adapter, register_adapter
from api.models import Event, EventIntegration, IntegrationDestination, ServiceCredential


class FakeAdapter:
    key = "model-test"


def test_registry_resolves_one_explicit_adapter():
    register_adapter(FakeAdapter())
    assert get_adapter("model-test").key == "model-test"
    assert "model-test" in adapter_keys()
    with pytest.raises(ValueError, match="already registered"):
        register_adapter(FakeAdapter())


def test_destination_and_binding_constraints(db_session):
    credential = ServiceCredential(service_name="Expo", credential_type="token", password="encrypted")
    event = Event(name="Exercise", quota="{}")
    db_session.add_all([credential, event]); db_session.flush()
    destination = IntegrationDestination(
        name="Expo staging", adapter_key="expo_it", base_url="https://expo.example",
        credential_id=credential.id, enabled=True, allow_insecure_http=False, config_json="{}",
    )
    db_session.add(destination); db_session.flush()
    db_session.add_all([
        EventIntegration(event_id=event.id, destination_id=destination.id, enabled=False),
        EventIntegration(event_id=event.id, destination_id=destination.id, enabled=False),
    ])
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_result_objects_are_safe_and_typed():
    assert ConnectionTestResult(True, "ok", "Connected").ok is True
    assert SyncResult(False, "timeout", "Timed out", None, True).retryable is True
```

- [ ] **Step 2: Run the tests and confirm missing modules/models fail**

Run: `pytest -q tests/test_integration_models.py`

Expected: collection fails because `api.integrations` and the four models do not exist.

- [ ] **Step 3: Implement focused ORM models and value objects**

Add dataclasses to `api/integrations/base.py` and the protocol from the spec:

```python
from dataclasses import dataclass
from typing import Protocol

@dataclass(frozen=True)
class ConnectionTestResult:
    ok: bool
    code: str
    message: str

@dataclass(frozen=True)
class SyncResult:
    ok: bool
    code: str
    message: str
    http_status: int | None = None
    retryable: bool = False

class IntegrationAdapter(Protocol):
    key: str
    def validate_destination(self, destination) -> list[str]: ...
    async def test_connection(self, destination, secret: str) -> ConnectionTestResult: ...
    async def synchronize(self, binding, destination, secret: str) -> SyncResult: ...
```

Implement a module-private dictionary in `registry.py`; reject empty and duplicate keys and raise `KeyError("unknown integration adapter: ...")` from `get_adapter`.

Add the four models using the exact fields and cascade/restrict behavior in the spec. Store statuses and JSON as bounded strings/text to match existing model conventions. Add ORM relationships from `Event`, `ServiceCredential`, destination, binding, and job.

- [ ] **Step 4: Add Alembic migration 0017**

Set `revision = "0017_general_integrations"` and `down_revision = "0016_operation_runs"`. Create the four tables, foreign keys, indexes on due-job lookup and binding attempt history, and unique `(event_id, destination_id)`. Drop the four `expo_sync_*` columns with `batch_alter_table("events")`; downgrade restores them before dropping generic tables.

- [ ] **Step 5: Run focused schema tests and migration smoke test**

Run: `pytest -q tests/test_integration_models.py tests/test_user_management.py`

Expected: PASS, including existing migration/startup compatibility coverage.

- [ ] **Step 6: Commit schema and registry**

```bash
git add api/integrations api/models.py migrations/versions/0017_general_integrations.py tests/test_integration_models.py
git commit -m "feat: add generic integration schema"
```

---

### Task 2: Durable Outbox and Worker

**Files:**
- Create: `api/services/integration_outbox.py`
- Modify: `api/main.py`
- Test: `tests/test_integration_outbox.py`
- Modify: `tests/conftest.py`

**Interfaces:**
- Consumes: models and `get_adapter()` from Task 1.
- Produces: `enqueue_event_sync(event_id: int, reason: str, priority: int = 0) -> bool`.
- Produces: `claim_due_job() -> int | None`, `async run_job(job_id: int) -> None`, `recover_stale_claims() -> int`, and `async worker_loop(stop: asyncio.Event) -> None`.

- [ ] **Step 1: Write failing queue behavior tests**

```python
# tests/test_integration_outbox.py
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from api.integrations.base import SyncResult
from api.models import IntegrationSyncJob, utcnow
from api.services.integration_outbox import enqueue_event_sync, recover_stale_claims, run_job


def test_enqueue_coalesces_and_manual_sync_raises_priority(integration_binding, sessions):
    assert enqueue_event_sync(integration_binding.event_id, "vm_updated") is True
    assert enqueue_event_sync(integration_binding.event_id, "manual", priority=100) is True
    db = sessions()
    jobs = db.query(IntegrationSyncJob).all()
    assert len(jobs) == 1
    assert (jobs[0].trigger_reason, jobs[0].priority) == ("manual", 100)


def test_disabled_binding_does_not_enqueue(integration_binding, sessions):
    db = sessions(); binding = db.get(type(integration_binding), integration_binding.id)
    binding.enabled = False; db.commit(); db.close()
    assert enqueue_event_sync(integration_binding.event_id, "event_updated") is False


def test_stale_running_job_is_recovered(integration_job, sessions):
    db = sessions(); job = db.get(IntegrationSyncJob, integration_job.id)
    job.status = "running"; job.claimed_at = utcnow() - timedelta(minutes=10); db.commit(); db.close()
    assert recover_stale_claims(stale_after_seconds=300) == 1


async def test_run_job_records_retry_without_secret_or_body(integration_job, fake_adapter, sessions):
    fake_adapter.synchronize = AsyncMock(return_value=SyncResult(False, "timeout", "Timed out", None, True))
    with patch("api.services.integration_outbox.decrypt_secret", return_value="private-key"):
        await run_job(integration_job.id)
    db = sessions(); job = db.get(IntegrationSyncJob, integration_job.id)
    assert job.status == "retrying"
    assert "private-key" not in repr(job.attempts[0].message)
```

- [ ] **Step 2: Confirm tests fail before the service exists**

Run: `pytest -q tests/test_integration_outbox.py`

Expected: FAIL on missing `integration_outbox` imports and fixtures.

- [ ] **Step 3: Add reusable integration fixtures**

In `tests/conftest.py`, add `token_credential`, `integration_destination`, `integration_binding`, and `integration_job` fixtures. Build them with the test's existing database session factory, encrypt `test-api-key` through `encrypt_secret`, use `https://expo.example`, and create an open event. Add a `fake_adapter` fixture whose key is unique per test and register/restore it through a registry reset helper that is private to tests.

- [ ] **Step 4: Implement enqueueing and atomic claims**

Use a new `SessionLocal` inside every public worker function. `enqueue_event_sync` loads enabled bindings for an open event, updates an active job when found, and otherwise inserts `pending`. It commits before returning and never decrypts credentials.

Implement PostgreSQL claim selection with `with_for_update(skip_locked=True)` and SQLite claim selection as a guarded `UPDATE ... WHERE status IN (...) AND next_attempt_at <= now`, followed by claim-token verification. Use a destination lease row or guarded running-job query to ensure only one running job per destination.

- [ ] **Step 5: Implement attempts, retry policy, recovery, and follow-up jobs**

Use these retry delays:

```python
RETRY_DELAYS_SECONDS = (5, 15, 45, 135, 300)
MAX_ATTEMPTS = len(RETRY_DELAYS_SECONDS)
STALE_CLAIM_SECONDS = 300
```

Decrypt only immediately before `adapter.synchronize`. Store stable codes and `message[:500]`. Never store exception reprs or response bodies. If a change arrived during a running attempt, leave one pending follow-up job after success or failure.

- [ ] **Step 6: Wire worker lifecycle into FastAPI**

In `api/main.py` lifespan, call `recover_stale_claims()`, create `stop = asyncio.Event()` and `worker_task = asyncio.create_task(worker_loop(stop))` after migrations/startup repair, then on shutdown set the event, cancel safely, and await under `suppress(asyncio.CancelledError)`.

- [ ] **Step 7: Run queue tests including restart and concurrent claims**

Run: `pytest -q tests/test_integration_outbox.py`

Expected: PASS with tests for coalescing, disabled bindings, retry exhaustion, stale claims, one claim winner, follow-up jobs, and safe attempts.

- [ ] **Step 8: Commit the worker**

```bash
git add api/services/integration_outbox.py api/main.py tests/conftest.py tests/test_integration_outbox.py
git commit -m "feat: add durable integration outbox"
```

---

### Task 3: Expo-IT Contract, Mapping, and Adapter

**Files:**
- Create: `api/integrations/expo_it_contract.py`
- Create: `api/integrations/expo_it.py`
- Create: `tests/fixtures/expo_it_data.json`
- Create: `tests/test_expo_it_adapter.py`
- Modify: `api/integrations/__init__.py`

**Interfaces:**
- Consumes: adapter result objects, registry, event/team/VM/timeline/score models.
- Produces: `ExpoData`, `build_owned_snapshot(db, event_id, now)`, `merge_remote(remote, owned, event_id)`, and registered `ExpoITAdapter(key="expo_it")`.

- [ ] **Step 1: Check in a synthetic full contract fixture and failing validation tests**

The fixture must include every top-level key, two phases, two scoring teams, an Expo-owned system with availability and credential association, one namespaced CTF system, one credential using password value `fixture-secret-not-real`, and one record in each Expo-owned dataset.

```python
# tests/test_expo_it_adapter.py
import json
from pathlib import Path
import pytest
from pydantic import ValidationError

from api.integrations.expo_it_contract import ExpoData

FIXTURE = json.loads(Path("tests/fixtures/expo_it_data.json").read_text())

def test_fixture_matches_complete_strict_contract():
    parsed = ExpoData.model_validate(FIXTURE)
    assert parsed.infrastructure.systems[0].expo_id

def test_contract_rejects_missing_dataset_and_extra_field():
    missing = dict(FIXTURE); missing.pop("ust")
    with pytest.raises(ValidationError): ExpoData.model_validate(missing)
    extra = dict(FIXTURE); extra["unexpected"] = []
    with pytest.raises(ValidationError): ExpoData.model_validate(extra)
```

- [ ] **Step 2: Run the contract tests and confirm they fail**

Run: `pytest -q tests/test_expo_it_adapter.py`

Expected: FAIL because `ExpoData` does not exist.

- [ ] **Step 3: Implement the strict contract models**

Mirror the Pydantic constraints in `../../expo-it/app/api.py` for Phase, InboxMessage, ScoringTeam, SpotReport, Network, AvailabilityCheck, Availability, System, Reply, Ticket, Service, Credential, Infrastructure, Collaboration, and aggregate `ExpoData`. Use `ConfigDict(extra="forbid")`; do not import the sibling repository at runtime.

- [ ] **Step 4: Add failing phase, scoring, system, and merge tests**

Cover phase half-open time boundaries, no current phase before/after the exercise, empty timelines, exact zero-filled scoring categories, namespaced IDs, IPv4/IPv6 normalization, aliases, preserved remote availability/credential IDs, preserved Expo datasets, stale-system removal, and ticket-reference conflict.

Use the existing scoreboard calculation logic from `api/routes/learner.py` through a newly extracted pure helper rather than duplicating score formulas.

- [ ] **Step 5: Implement snapshot and ownership merge helpers**

Parse `Event.timeline` with `normalize_timeline`. Map current event data exactly as the spec states. Build new objects rather than mutating ORM instances or the supplied remote dictionary. Validate the final merge with `ExpoData.model_validate()` before returning JSON.

Raise a typed `ExpoContractError(code="remote_reference_conflict")` when a preserved UST ticket references a stale namespaced system that would be removed.

- [ ] **Step 6: Add failing HTTP behavior tests**

Use `httpx.MockTransport` to assert `X-API-Key`, `GET` before `PUT`, no redirects, complete PUT keys, timeout retryability, `429`/`5xx` retryability, immediate `401` failure, invalid JSON, strict remote-shape errors, and sanitized messages.

- [ ] **Step 7: Implement and register `ExpoITAdapter`**

Normalize URL joins with `destination.base_url.rstrip("/") + "/api/v1/data"`. Use `httpx.AsyncClient(timeout=15, follow_redirects=False)`. Return `ConnectionTestResult` for non-mutating tests and `SyncResult` for delivery; do not raise raw httpx exceptions past the adapter boundary.

Register one `ExpoITAdapter` from `api/integrations/__init__.py` during app import.

- [ ] **Step 8: Run adapter tests**

Run: `pytest -q tests/test_expo_it_adapter.py`

Expected: PASS.

- [ ] **Step 9: Commit the first adapter**

```bash
git add api/integrations tests/fixtures/expo_it_data.json tests/test_expo_it_adapter.py api/routes/learner.py
git commit -m "feat: add Expo-IT integration adapter"
```

---

### Task 4: Destination and Event-Binding APIs

**Files:**
- Create: `api/routes/integrations.py`
- Modify: `api/routes/__init__.py`
- Modify: `api/main.py`
- Modify: `api/routes/service_credentials.py`
- Test: `tests/test_integrations_api.py`

**Interfaces:**
- Consumes: registry, models, connection testing, `enqueue_event_sync`.
- Produces: `/admin/api/integrations/destinations`, `/admin/api/events/{event_id}/integrations`, and manual sync/status endpoints.

- [ ] **Step 1: Write failing authorization and safe-payload tests**

```python
def test_non_admin_cannot_manage_destinations(client, participant):
    login(client, participant)
    assert client.get("/admin/api/integrations/destinations").status_code == 403

def test_admin_destination_response_never_contains_secret(admin_client, token_credential):
    response = admin_client.post("/admin/api/integrations/destinations", json={
        "name": "Expo staging", "adapter_key": "expo_it",
        "base_url": "https://expo.example", "credential_id": token_credential.id,
        "enabled": True, "allow_insecure_http": False, "config": {},
    })
    assert response.status_code == 201
    assert "password" not in response.text and "secret" not in response.text
```

Add tests for URL rules, credential type, unknown adapters, connection test behavior, destination/binding conflict `409`s, disabled states, deletion guards, status payloads, and manual sync returning `202` without invoking the adapter inline.

- [ ] **Step 2: Run API tests and confirm route failures**

Run: `pytest -q tests/test_integrations_api.py`

Expected: FAIL with `404` for the new routes.

- [ ] **Step 3: Implement strict request models and destination routes**

Use Pydantic models with `extra="forbid"`. Parse URLs with `urlsplit`; require absolute HTTP(S), reject username/password, query, and fragment, normalize trailing slashes, and require `allow_insecure_http=True` for HTTP.

Connection test decrypts the referenced credential only inside the request, calls `adapter.test_connection`, persists safe status, and sends `Cache-Control: no-store`.

- [ ] **Step 4: Implement binding, status, and manual sync routes**

Enabling a binding performs one transaction that checks:

```python
conflict = db.query(EventIntegration).join(IntegrationDestination).filter(
    EventIntegration.enabled.is_(True),
    EventIntegration.id != binding.id,
    IntegrationDestination.adapter_key == "expo_it",
    ((EventIntegration.event_id == event.id) |
     (EventIntegration.destination_id == destination.id)),
).first()
```

Return `409` with the conflicting event ID and name. Manual sync requires an open event and enabled binding, calls `enqueue_event_sync(..., "manual", 100)`, and returns `202` plus current status.

- [ ] **Step 5: Guard credential deletion**

In `delete_credential`, query `IntegrationDestination` before delete and return `409 {"error":"credential is used by an integration destination"}` when referenced.

- [ ] **Step 6: Register the router and run API/security tests**

Run: `pytest -q tests/test_integrations_api.py tests/test_auth_security.py`

Expected: PASS.

- [ ] **Step 7: Commit the APIs**

```bash
git add api/routes/integrations.py api/routes/__init__.py api/main.py api/routes/service_credentials.py tests/test_integrations_api.py
git commit -m "feat: manage integration destinations and bindings"
```

---

### Task 5: Explicit Domain Change Triggers

**Files:**
- Modify: `api/routes/admin.py`
- Modify: `api/routes/vm.py`
- Modify: `api/routes/learner.py`
- Modify: `api/routes/vm_goals.py`
- Modify: `api/services/gamenet_provisioning.py`
- Modify: `api/services/verification.py`
- Delete: `api/services/expo_ust.py`
- Modify: existing route/service tests adjacent to each mutation
- Test: `tests/test_integration_triggers.py`

**Interfaces:**
- Consumes: `enqueue_event_sync(event_id, reason, priority=0)`.
- Produces: explicit post-commit enqueue calls for every approved trigger.

- [ ] **Step 1: Inventory exact mutation sites and codify them in parametrized tests**

Run: `rg -n "db\.commit\(\)|\.commit\(\)" api/routes/admin.py api/routes/vm.py api/routes/learner.py api/routes/vm_goals.py api/services/gamenet_provisioning.py`

Write route/service tests that patch `api.services.integration_outbox.enqueue_event_sync` and assert one call after successful event/timeline/team/VM/score mutations, no call on validation failure/rollback, and no duplicate calls for bulk provisioning.

- [ ] **Step 2: Run trigger tests and confirm missing calls**

Run: `pytest -q tests/test_integration_triggers.py`

Expected: FAIL because mutation paths do not enqueue generic jobs.

- [ ] **Step 3: Add explicit post-commit calls**

Use a tiny helper at each boundary:

```python
db.commit()
enqueue_event_sync(event_id, "timeline_updated")
```

For background/bulk operations, enqueue once after the final successful transaction. Event start uses `event_opened`; GameNet uses `provisioning_completed`; scoring uses `score_updated`; VM address/status mutations use `vm_updated`.

- [ ] **Step 4: Remove obsolete Expo hooks and service**

Delete imports/calls to `expo_ust.schedule`, `configured`, and `synchronize`. Delete `api/services/expo_ust.py`. Remove the `warning: Expo-IT integration is not configured` response field; generic disabled integrations are normal, not warnings.

- [ ] **Step 5: Run trigger and affected lifecycle tests**

Run: `pytest -q tests/test_integration_triggers.py tests/test_module_repo_start_hook.py tests/test_event_lifecycle.py tests/test_gamenet.py tests/test_training_release.py tests/test_vm_goals_api.py`

Expected: PASS.

- [ ] **Step 6: Commit trigger wiring**

```bash
git add api/routes api/services tests
git commit -m "feat: enqueue integrations after domain changes"
```

---

### Task 6: Administrator and Event UI

**Files:**
- Modify: `frontend/templates/admin_settings.html`
- Modify: `frontend/templates/admin_resource.html`
- Modify: `frontend/static/admin-events.js`
- Modify: `frontend/templates/event_dashboard.html`
- Modify: `frontend/static/admin.css`
- Test: `tests/test_event_dashboard.py`
- Create: `tests/integration-ui-state.test.mjs`

**Interfaces:**
- Consumes: APIs from Task 4.
- Produces: destination CRUD/test UI, event binding editor, status card, and **Sync now** control.

- [ ] **Step 1: Write failing template and browser-state tests**

Assert factual strings and hooks:

```python
def test_settings_contains_integration_destination_controls(admin_client):
    text = admin_client.get("/admin/settings").text
    for value in ("Integration destinations", "Test connection", "allow-insecure-http"):
        assert value in text

def test_event_dashboard_uses_generic_integration_status(admin_client, open_event):
    text = admin_client.get(f"/admin/events/{open_event.id}/dashboard").text
    assert "Integration synchronization" in text
    assert "Expo-IT UST synchronization" not in text
```

The Node test imports pure state/render helpers from `integration-ui-state.js` and covers status labels, safe error rendering, conflict messages, and polling termination.

- [ ] **Step 2: Run UI tests and confirm missing hooks**

Run: `pytest -q tests/test_event_dashboard.py -k integration && node --test tests/integration-ui-state.test.mjs`

Expected: FAIL.

- [ ] **Step 3: Implement destination UI**

Add a focused destination section to Admin Settings. Credential options display service name and username, never reveal secrets. Require explicit confirmation for disable/delete, display last connection-test result, and use the shared toast/drawer conventions.

- [ ] **Step 4: Implement event binding editor**

Extend the event editor with one optional Expo-IT destination select and enabled checkbox. Load destinations and current binding when editing. Display a factual empty state when none exist and link to Admin Settings.

- [ ] **Step 5: Replace dashboard Expo card with generic status UI**

Render the six approved states, last success, reason, attempts, next retry, and safe error. **Sync now** calls the binding endpoint, shows queued state immediately, and polls until synchronized or failed.

- [ ] **Step 6: Run UI and syntax checks**

Run: `pytest -q tests/test_event_dashboard.py tests/test_integrations_api.py && node --check frontend/static/admin-events.js && node --test tests/integration-ui-state.test.mjs`

Expected: PASS.

- [ ] **Step 7: Commit operator UI**

```bash
git add frontend tests/test_event_dashboard.py tests/integration-ui-state.test.mjs
git commit -m "feat: add integration administration UI"
```

---

### Task 7: Expo-IT Alias-Preservation Compatibility Change

**Files:**
- Modify: `../../expo-it/app/api.py`
- Modify: `../../expo-it/tests/test_app.py`
- Modify: `../../expo-it/README.md`

**Interfaces:**
- Produces: authenticated management GET responses that include `system_aliases` while continuing to redact `password` and `original_password`.
- Consumed by: CTF-IT adapter read-merge-write behavior.

- [ ] **Step 1: Write a failing unchanged round-trip test in Expo-IT**

```python
def test_management_aggregate_round_trip_preserves_aliases_and_passwords():
    headers = {"X-API-Key": "test-api-key"}
    with TestClient(app) as client:
        aggregate = client.get("/api/v1/data", headers=headers).json()
        assert aggregate["infrastructure"]["systems"][0]["system_aliases"]
        assert "password" not in aggregate["infrastructure"]["credentials"][0]
        assert client.put("/api/v1/data", headers=headers, json=aggregate).status_code == 200
        again = client.get("/api/v1/data", headers=headers).json()
        assert again["infrastructure"]["systems"][0]["system_aliases"] == aggregate["infrastructure"]["systems"][0]["system_aliases"]
```

Also inspect the SQLite row inside the test to prove its encrypted/plain fixture password value was preserved across the PUT without exposing it in either response.

- [ ] **Step 2: Run the Expo-IT test and confirm alias failure**

Run from `../../expo-it`: `pytest -q tests/test_app.py -k management_aggregate_round_trip`

Expected: FAIL because `public_infrastructure` removes `system_aliases`.

- [ ] **Step 3: Preserve aliases in authenticated management responses**

Remove only this line from `public_infrastructure`:

```python
system.pop("system_aliases", None)
```

Keep both credential secret removals. Confirm browser route serializers do not expose aliases unintentionally; this helper is scoped to authenticated management routes.

- [ ] **Step 4: Update Expo-IT API documentation**

State that management GET responses include aliases for lossless replacement and continue to redact credential password fields. Document omission-preserves-existing-password behavior.

- [ ] **Step 5: Run the full Expo-IT suite and commit in that repository**

Run from `../../expo-it`: `pytest -q && git diff --check`

Expected: PASS.

```bash
git -C ../../expo-it add app/api.py tests/test_app.py README.md
git -C ../../expo-it commit -m "Preserve aliases in management API round trips"
```

---

### Task 8: Cross-Repository Contract Test

**Files:**
- Create: `tests/test_expo_it_live_contract.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: `ExpoITAdapter` and sibling Expo-IT app.
- Produces: optional `expo_it_live` pytest marker proving real round-trip compatibility.

- [ ] **Step 1: Register the optional marker and write the failing live test**

The test skips with `pytest.skip("../../expo-it is not available")` when the sibling app is absent. When present, load it under an isolated module name, point `EXPO_DATABASE_PATH` to `tmp_path`, set `EXPO_API_KEY`, seed all datasets, and use `httpx.ASGITransport` or a local adapter transport injection to exercise GET/PUT without a network listener.

Assert the twelve acceptance behaviors listed in the spec, especially preserved Expo-owned data, credential password omission/preservation, aliases, availability, and exact CTF-owned replacements.

- [ ] **Step 2: Run the test before transport injection and confirm failure**

Run: `pytest -q -m expo_it_live tests/test_expo_it_live_contract.py`

Expected: FAIL because the adapter cannot yet receive the in-process ASGI transport.

- [ ] **Step 3: Add test-only client injection without widening production configuration**

Allow `ExpoITAdapter(client_factory=...)` in its constructor, defaulting to the production `httpx.AsyncClient` factory. Keep it out of destination JSON and public APIs.

- [ ] **Step 4: Run live and ordinary adapter tests**

Run: `pytest -q tests/test_expo_it_adapter.py && pytest -q -m expo_it_live tests/test_expo_it_live_contract.py`

Expected: PASS when the sibling repository exists; clean SKIP otherwise.

- [ ] **Step 5: Commit contract verification**

```bash
git add api/integrations/expo_it.py tests/test_expo_it_live_contract.py pytest.ini README.md
git commit -m "test: verify Expo-IT aggregate round trips"
```

Register the marker in the existing pytest configuration location used by the repository; if none exists, create `pytest.ini` containing only `[pytest]` and `markers = expo_it_live: requires the sibling Expo-IT application`.

---

### Task 9: Migration Cleanup, Deployment Documentation, and Full Verification

**Files:**
- Modify: `api/main.py`
- Modify: `.env.example`
- Modify: `docker-compose.yml`
- Modify: `deploy/.env.example`
- Modify: `deploy/docker-compose.yml`
- Modify: `README.md`
- Modify: migration/startup tests that assert obsolete Expo columns.

**Interfaces:**
- Consumes: all previous tasks.
- Produces: documented, migration-safe release with no global Expo configuration.

- [ ] **Step 1: Write failing cleanup assertions**

Update migration tests to require the four generic tables and to assert the runtime compatibility block no longer adds `expo_sync_*`. Add Compose tests asserting `EXPO_IT_URL` and `EXPO_IT_API_KEY` are absent.

- [ ] **Step 2: Run cleanup tests and confirm obsolete configuration fails them**

Run: `pytest -q tests/test_gamenet.py tests/test_deploy_compose.py`

Expected: FAIL while old columns and environment variables remain.

- [ ] **Step 3: Remove obsolete runtime compatibility and environment settings**

Delete `expo_sync_status`, `expo_sync_last_error`, `expo_sync_attempts`, and `expo_sync_completed_at` from `api/main.py`'s legacy repair dictionary. Remove `EXPO_IT_URL` and `EXPO_IT_API_KEY` from `.env.example`, local Compose, production examples, and production Compose.

- [ ] **Step 4: Document administrator workflow and operations**

Add README sections covering credential creation, destination creation, HTTPS/HTTP rules, connection test, per-event binding, automatic triggers, status meanings, retry timing, **Sync now**, one-event-per-destination limitation, migration order, and Expo-IT compatibility minimum.

- [ ] **Step 5: Run static and focused verification**

Run:

```bash
python -m compileall -q api builder
node --check frontend/static/admin-events.js
node --test tests/*.test.mjs
pytest -q tests/test_integration_models.py tests/test_integration_outbox.py tests/test_expo_it_adapter.py tests/test_integrations_api.py tests/test_integration_triggers.py tests/test_event_dashboard.py
git diff --check
```

Expected: every command exits `0`.

- [ ] **Step 6: Run full containerized CTF-IT verification**

Run: `docker compose --profile test run --rm --build tests`

Expected: the complete pytest suite passes.

- [ ] **Step 7: Run deployment and cross-repository verification**

Run:

```bash
docker compose config
pytest -q -m expo_it_live tests/test_expo_it_live_contract.py
git -C ../../expo-it diff --check
git status --short
```

Expected: Compose validates; live contract passes; both repositories have only intentional changes.

- [ ] **Step 8: Commit release cleanup**

```bash
git add api/main.py .env.example docker-compose.yml deploy/.env.example deploy/docker-compose.yml README.md tests
git commit -m "docs: document Expo-IT integration operations"
```

---

## Final Review Checklist

- [ ] Trace every acceptance criterion in the spec to at least one passing test.
- [ ] Search for obsolete implementation references: `rg -n "expo_ust|expo_sync_|EXPO_IT_URL|EXPO_IT_API_KEY|/api/v1/ust/exercise" .` returns only historical design/plan text.
- [ ] Search for accidental secrets or payload persistence: inspect job/attempt models, logs, API serializers, and fixtures.
- [ ] Confirm Expo-IT GET/unchanged-PUT preserves aliases, availability, credential associations, and credential passwords.
- [ ] Confirm a stopped/disabled/unconfigured event operates without warnings or integration jobs.
- [ ] Confirm transient outages leave visible retry state and do not fail event mutations.
- [ ] Confirm the worker recovers a stale running claim after application restart.
- [ ] Confirm one destination cannot be active for two events.
- [ ] Confirm ordinary tests cleanly pass when `../../expo-it` is absent.
