import secrets
import asyncio
from datetime import timedelta
from time import monotonic

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import aliased

from api.database import SessionLocal
from api.integrations.base import SyncResult
from api.integrations.registry import get_adapter
from api.models import (
    Event,
    EventIntegration,
    IntegrationSyncAttempt,
    IntegrationSyncJob,
    IntegrationDestination,
    utcnow,
)
from api.services.secrets import decrypt_secret


ACTIVE_STATUSES = ("pending", "running", "retrying")
RETRY_DELAYS_SECONDS = (5, 15, 45, 135, 300)


def enqueue_event_sync(event_id: int, reason: str, priority: int = 0, binding_id: int | None = None) -> bool:
    with SessionLocal() as db:
        try:
            bindings = (
                db.query(EventIntegration)
                .join(IntegrationDestination)
                .join(Event)
                .filter(
                    EventIntegration.event_id == event_id,
                    EventIntegration.enabled.is_(True),
                    IntegrationDestination.enabled.is_(True),
                    Event.status == "open",
                    *([EventIntegration.id == binding_id] if binding_id is not None else []),
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
        except SQLAlchemyError:
            db.rollback()
            return False


def recover_stale_claims(stale_after_seconds: int = 300) -> int:
    cutoff = utcnow() - timedelta(seconds=stale_after_seconds)
    with SessionLocal() as db:
        jobs = db.query(IntegrationSyncJob).filter(
            IntegrationSyncJob.status == "running",
            IntegrationSyncJob.claimed_at < cutoff,
        ).all()
        for job in jobs:
            job.status = (
                "retrying"
                if job.binding.enabled and job.binding.destination.enabled and job.binding.event.status == "open"
                else "cancelled"
            )
            job.claimed_at = None
            job.claim_token = None
            if job.status == "retrying":
                job.next_attempt_at = utcnow()
        db.commit()
        return len(jobs)


def claim_due_job() -> int | None:
    now = utcnow()
    with SessionLocal() as db:
        candidates = (
            db.query(IntegrationSyncJob)
            .filter(
                IntegrationSyncJob.status.in_(("pending", "retrying")),
                IntegrationSyncJob.next_attempt_at <= now,
            )
            .order_by(IntegrationSyncJob.priority.desc(), IntegrationSyncJob.id)
            .all()
        )
        for candidate in candidates:
            if (
                not candidate.binding.enabled
                or not candidate.binding.destination.enabled
                or candidate.binding.event.status != "open"
            ):
                candidate.status = "cancelled"
                db.commit()
                continue
            destination_id = candidate.binding.destination_id
            locked_destination = (
                db.query(IntegrationDestination)
                .filter_by(id=destination_id)
                .with_for_update(skip_locked=True)
                .first()
            )
            if locked_destination is None:
                db.rollback()
                continue
            running_job = aliased(IntegrationSyncJob)
            running_binding = aliased(EventIntegration)
            running_for_destination = (
                db.query(running_job.id)
                .join(running_binding, running_binding.id == running_job.binding_id)
                .filter(
                    running_job.status == "running",
                    running_binding.destination_id == destination_id,
                )
                .exists()
            )
            token = secrets.token_hex(16)
            changed = db.query(IntegrationSyncJob).filter(
                IntegrationSyncJob.id == candidate.id,
                IntegrationSyncJob.status.in_(("pending", "retrying")),
                ~running_for_destination,
            ).update({
                IntegrationSyncJob.status: "running",
                IntegrationSyncJob.claimed_at: now,
                IntegrationSyncJob.claim_token: token,
            }, synchronize_session=False)
            db.commit()
            if changed == 1:
                return candidate.id
        return None


async def run_job(job_id: int) -> None:
    with SessionLocal() as db:
        job = db.get(IntegrationSyncJob, job_id)
        if not job or job.status != "running":
            return
        claim_token = job.claim_token
        binding = job.binding
        destination = binding.destination
        attempt_number = job.attempt_count + 1
        started_at = utcnow()
        began = monotonic()
        secret = ""
        try:
            adapter = get_adapter(destination.adapter_key)
            secret = decrypt_secret(destination.credential.password)
        except Exception:
            result = SyncResult(False, "invalid_configuration", "Integration configuration is invalid", None, False)
        else:
            try:
                result = await adapter.synchronize(binding, destination, secret)
            except Exception:
                result = SyncResult(False, "unexpected_error", "Integration adapter failed", None, True)
        finally:
            secret = ""
        finished_at = utcnow()
        db.refresh(job)
        if job.status != "running" or job.claim_token != claim_token:
            db.rollback()
            return
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
            job.follow_up_required = False
            binding.last_status = "retrying"
            binding.last_error_code = result.code[:64]
            binding.last_error_message = result.message[:500]
        else:
            job.status = "failed"
            binding.last_status = "failed"
            binding.last_error_code = result.code[:64]
            binding.last_error_message = result.message[:500]
        if (
            job.follow_up_required
            and job.status in {"succeeded", "failed"}
            and binding.enabled
            and destination.enabled
            and binding.event.status == "open"
        ):
            job.follow_up_required = False
            db.flush()
            db.add(IntegrationSyncJob(
                binding_id=binding.id,
                status="pending",
                trigger_reason=job.trigger_reason,
                priority=job.priority,
                next_attempt_at=finished_at,
            ))
        db.commit()
        try:
            with SessionLocal() as cleanup:
                retained_ids = [row[0] for row in (
                    cleanup.query(IntegrationSyncAttempt.id)
                    .filter_by(binding_id=binding.id)
                    .order_by(IntegrationSyncAttempt.created_at.desc(), IntegrationSyncAttempt.id.desc())
                    .limit(100)
                )]
                if retained_ids:
                    cleanup.query(IntegrationSyncAttempt).filter(
                        IntegrationSyncAttempt.binding_id == binding.id,
                        ~IntegrationSyncAttempt.id.in_(retained_ids),
                    ).delete(synchronize_session=False)
                    cleanup.commit()
        except SQLAlchemyError:
            pass


async def worker_loop(stop: asyncio.Event, poll_seconds: float = 1.0) -> None:
    last_recovery = monotonic() - 60
    while not stop.is_set():
        try:
            if monotonic() - last_recovery >= 60:
                recover_stale_claims()
                last_recovery = monotonic()
            job_id = claim_due_job()
            if job_id is not None:
                await run_job(job_id)
                continue
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
        except asyncio.TimeoutError:
            pass
