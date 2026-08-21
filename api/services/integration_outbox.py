import secrets
import asyncio
from datetime import timedelta
from time import monotonic

from api.database import SessionLocal
from api.integrations.base import SyncResult
from api.integrations.registry import get_adapter
from api.models import (
    EventIntegration,
    IntegrationSyncAttempt,
    IntegrationSyncJob,
    IntegrationDestination,
    utcnow,
)
from api.services.secrets import decrypt_secret


ACTIVE_STATUSES = ("pending", "running", "retrying")
RETRY_DELAYS_SECONDS = (5, 15, 45, 135, 300)


def enqueue_event_sync(event_id: int, reason: str, priority: int = 0) -> bool:
    with SessionLocal() as db:
        bindings = (
            db.query(EventIntegration)
            .join(IntegrationDestination)
            .filter(
                EventIntegration.event_id == event_id,
                EventIntegration.enabled.is_(True),
                IntegrationDestination.enabled.is_(True),
            )
            .all()
        )
        for binding in bindings:
            job = (
                db.query(IntegrationSyncJob)
                .filter(
                    IntegrationSyncJob.binding_id == binding.id,
                    IntegrationSyncJob.status.in_(ACTIVE_STATUSES),
                )
                .order_by(IntegrationSyncJob.id.desc())
                .first()
            )
            if job:
                job.trigger_reason = reason
                job.priority = max(job.priority, priority)
                if job.status == "running":
                    job.follow_up_required = True
            else:
                db.add(IntegrationSyncJob(
                    binding_id=binding.id,
                    status="pending",
                    trigger_reason=reason[:64],
                    priority=priority,
                    next_attempt_at=utcnow(),
                ))
        db.commit()
        return bool(bindings)


def recover_stale_claims(stale_after_seconds: int = 300) -> int:
    cutoff = utcnow() - timedelta(seconds=stale_after_seconds)
    with SessionLocal() as db:
        jobs = db.query(IntegrationSyncJob).filter(
            IntegrationSyncJob.status == "running",
            IntegrationSyncJob.claimed_at < cutoff,
        ).all()
        for job in jobs:
            job.status = "retrying"
            job.claimed_at = None
            job.claim_token = None
            job.next_attempt_at = utcnow()
        db.commit()
        return len(jobs)


def claim_due_job() -> int | None:
    now = utcnow()
    with SessionLocal() as db:
        candidate = (
            db.query(IntegrationSyncJob)
            .filter(
                IntegrationSyncJob.status.in_(("pending", "retrying")),
                IntegrationSyncJob.next_attempt_at <= now,
            )
            .order_by(IntegrationSyncJob.priority.desc(), IntegrationSyncJob.id)
            .first()
        )
        if candidate is None:
            return None
        token = secrets.token_hex(16)
        changed = db.query(IntegrationSyncJob).filter(
            IntegrationSyncJob.id == candidate.id,
            IntegrationSyncJob.status.in_(("pending", "retrying")),
        ).update({
            IntegrationSyncJob.status: "running",
            IntegrationSyncJob.claimed_at: now,
            IntegrationSyncJob.claim_token: token,
        }, synchronize_session=False)
        db.commit()
        return candidate.id if changed == 1 else None


async def run_job(job_id: int) -> None:
    with SessionLocal() as db:
        job = db.get(IntegrationSyncJob, job_id)
        if not job or job.status != "running":
            return
        binding = job.binding
        destination = binding.destination
        adapter = get_adapter(destination.adapter_key)
        secret = decrypt_secret(destination.credential.password)
        attempt_number = job.attempt_count + 1
        started_at = utcnow()
        began = monotonic()
        try:
            result = await adapter.synchronize(binding, destination, secret)
        except Exception:
            result = SyncResult(False, "unexpected_error", "Integration adapter failed", None, True)
        finally:
            secret = ""
        finished_at = utcnow()
        job.attempt_count = attempt_number
        db.add(IntegrationSyncAttempt(
            job_id=job.id,
            binding_id=binding.id,
            attempt_number=attempt_number,
            result="succeeded" if result.ok else "failed",
            http_status=result.http_status,
            error_code=None if result.ok else result.code[:64],
            message=result.message[:500],
            duration_ms=int((monotonic() - began) * 1000),
            started_at=started_at,
            finished_at=finished_at,
        ))
        if result.ok:
            job.status = "succeeded"
            binding.last_status = "synchronized"
            binding.last_success_at = finished_at
            binding.last_error_code = None
            binding.last_error_message = None
        elif result.retryable and attempt_number < len(RETRY_DELAYS_SECONDS):
            job.status = "retrying"
            job.next_attempt_at = finished_at + timedelta(seconds=RETRY_DELAYS_SECONDS[attempt_number - 1])
            binding.last_status = "retrying"
            binding.last_error_code = result.code[:64]
            binding.last_error_message = result.message[:500]
        else:
            job.status = "failed"
            binding.last_status = "failed"
            binding.last_error_code = result.code[:64]
            binding.last_error_message = result.message[:500]
        db.commit()


async def worker_loop(stop: asyncio.Event, poll_seconds: float = 1.0) -> None:
    while not stop.is_set():
        job_id = claim_due_job()
        if job_id is not None:
            await run_job(job_id)
            continue
        try:
            await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
        except asyncio.TimeoutError:
            pass
