from datetime import timedelta
from unittest.mock import patch

import pytest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from api.database import Base
from api.models import (
    Event,
    EventIntegration,
    IntegrationDestination,
    IntegrationSyncJob,
    ServiceCredential,
    utcnow,
)
from api.integrations.base import SyncResult
from api.integrations.registry import adapter_keys, register_adapter


class QueueAdapter:
    key = "queue-test"
    result = SyncResult(True, "ok", "Synchronized", 200, False)

    async def synchronize(self, binding, destination, secret):
        return self.result


def integration_database(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr("api.services.integration_outbox.SessionLocal", sessions)
    with Session(engine) as db:
        credential = ServiceCredential(service_name="Expo", credential_type="token", password="encrypted")
        event = Event(name="Exercise", quota="{}", status="open", open=True)
        db.add_all([credential, event]); db.flush()
        destination = IntegrationDestination(
            name="Expo", adapter_key="queue-test", base_url="https://expo.example",
            credential_id=credential.id, enabled=True, allow_insecure_http=False, config_json="{}",
        )
        db.add(destination); db.flush()
        binding = EventIntegration(event_id=event.id, destination_id=destination.id, enabled=True)
        db.add(binding); db.commit()
        return sessions, event.id, binding.id


@pytest.fixture(autouse=True)
def queue_adapter():
    if "queue-test" not in adapter_keys():
        register_adapter(QueueAdapter())
    QueueAdapter.result = SyncResult(True, "ok", "Synchronized", 200, False)


def test_enqueue_coalesces_and_manual_sync_raises_priority(monkeypatch):
    sessions, event_id, _ = integration_database(monkeypatch)
    from api.services.integration_outbox import enqueue_event_sync

    assert enqueue_event_sync(event_id, "vm_updated") is True
    assert enqueue_event_sync(event_id, "manual", priority=100) is True
    with sessions() as db:
        jobs = db.query(IntegrationSyncJob).all()
        assert len(jobs) == 1
        assert (jobs[0].trigger_reason, jobs[0].priority) == ("manual", 100)


def test_disabled_binding_does_not_enqueue(monkeypatch):
    sessions, event_id, binding_id = integration_database(monkeypatch)
    from api.services.integration_outbox import enqueue_event_sync

    with sessions() as db:
        db.get(EventIntegration, binding_id).enabled = False
        db.commit()
    assert enqueue_event_sync(event_id, "event_updated") is False


def test_draft_event_does_not_enqueue(monkeypatch):
    sessions, event_id, _ = integration_database(monkeypatch)
    from api.services.integration_outbox import enqueue_event_sync

    with sessions() as db:
        event = db.get(Event, event_id)
        event.status = "draft"; event.open = False
        db.commit()
    assert enqueue_event_sync(event_id, "event_updated") is False


def test_stale_running_job_is_recovered(monkeypatch):
    sessions, _, binding_id = integration_database(monkeypatch)
    from api.services.integration_outbox import recover_stale_claims

    with sessions() as db:
        db.add(IntegrationSyncJob(
            binding_id=binding_id, status="running", trigger_reason="manual",
            next_attempt_at=utcnow(), claimed_at=utcnow() - timedelta(minutes=10), claim_token="stale",
        ))
        db.commit()
    assert recover_stale_claims(stale_after_seconds=300) == 1
    with sessions() as db:
        job = db.query(IntegrationSyncJob).one()
        assert job.status == "retrying"
        assert job.claim_token is None


def test_claim_due_job_has_one_winner(monkeypatch):
    sessions, event_id, _ = integration_database(monkeypatch)
    from api.services.integration_outbox import claim_due_job, enqueue_event_sync

    enqueue_event_sync(event_id, "manual")
    claimed = claim_due_job()
    assert claimed is not None
    assert claim_due_job() is None
    with sessions() as db:
        assert db.get(IntegrationSyncJob, claimed).status == "running"


def test_claim_cancels_job_when_event_has_stopped(monkeypatch):
    sessions, event_id, _ = integration_database(monkeypatch)
    from api.services.integration_outbox import claim_due_job, enqueue_event_sync

    enqueue_event_sync(event_id, "vm_updated")
    with sessions() as db:
        event = db.get(Event, event_id)
        event.status = "stopped"; event.open = False
        db.commit()
    assert claim_due_job() is None
    with sessions() as db:
        assert db.query(IntegrationSyncJob).one().status == "cancelled"


@pytest.mark.asyncio
async def test_run_job_records_success_without_secret(monkeypatch):
    sessions, event_id, binding_id = integration_database(monkeypatch)
    from api.services.integration_outbox import claim_due_job, enqueue_event_sync, run_job

    enqueue_event_sync(event_id, "manual")
    job_id = claim_due_job()
    with patch("api.services.integration_outbox.decrypt_secret", return_value="private-key"):
        await run_job(job_id)
    with sessions() as db:
        job = db.get(IntegrationSyncJob, job_id)
        assert job.status == "succeeded"
        assert job.binding.last_status == "synchronized"
        assert len(job.attempts) == 1
        assert "private-key" not in job.attempts[0].message


@pytest.mark.asyncio
async def test_run_job_retries_transient_result(monkeypatch):
    sessions, event_id, _ = integration_database(monkeypatch)
    from api.services.integration_outbox import claim_due_job, enqueue_event_sync, run_job

    QueueAdapter.result = SyncResult(False, "timeout", "Timed out", None, True)
    enqueue_event_sync(event_id, "manual")
    job_id = claim_due_job()
    with patch("api.services.integration_outbox.decrypt_secret", return_value="private-key"):
        await run_job(job_id)
    with sessions() as db:
        job = db.get(IntegrationSyncJob, job_id)
        assert job.status == "retrying"
        assert job.attempt_count == 1
        assert job.next_attempt_at > job.claimed_at


@pytest.mark.asyncio
async def test_run_job_creates_one_follow_up_for_change_during_delivery(monkeypatch):
    sessions, event_id, _ = integration_database(monkeypatch)
    from api.services.integration_outbox import claim_due_job, enqueue_event_sync, run_job

    enqueue_event_sync(event_id, "vm_updated")
    job_id = claim_due_job()
    assert enqueue_event_sync(event_id, "timeline_updated") is True
    with patch("api.services.integration_outbox.decrypt_secret", return_value="private-key"):
        await run_job(job_id)
    with sessions() as db:
        jobs = db.query(IntegrationSyncJob).order_by(IntegrationSyncJob.id).all()
        assert [(job.status, job.trigger_reason) for job in jobs] == [
            ("succeeded", "timeline_updated"), ("pending", "timeline_updated")
        ]


def test_claim_serializes_distinct_jobs_for_one_destination(monkeypatch):
    sessions, event_id, binding_id = integration_database(monkeypatch)
    from api.services.integration_outbox import claim_due_job, enqueue_event_sync

    enqueue_event_sync(event_id, "manual")
    with sessions() as db:
        binding = db.get(EventIntegration, binding_id)
        second_event = Event(name="Second", quota="{}", status="open", open=True)
        db.add(second_event); db.flush()
        second_binding = EventIntegration(
            event_id=second_event.id, destination_id=binding.destination_id, enabled=False
        )
        db.add(second_binding); db.flush()
        db.add(IntegrationSyncJob(
            binding_id=second_binding.id, status="pending", trigger_reason="manual",
            next_attempt_at=utcnow(),
        ))
        db.commit()
    assert claim_due_job() is not None
    assert claim_due_job() is None
