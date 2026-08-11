"""Five-minute regression checks for active learner assignments."""

import asyncio
import json
import os
from collections import Counter

from api.database import SessionLocal
from api.models import Event, VM, VMModule
from api.services.verification import verify_assignment, vm_is_busy
from builder.module_loader import load_all_modules

INTERVAL_SECONDS = int(os.environ.get("VERIFICATION_INTERVAL_SECONDS", "300"))
VM_CONCURRENCY = int(os.environ.get("VERIFICATION_VM_CONCURRENCY", "5"))
metrics = Counter()


async def _check_vm(vm_id: int, definitions: dict, semaphore: asyncio.Semaphore):
    async with semaphore:
        db = SessionLocal()
        client = None
        try:
            vm = db.query(VM).filter(VM.id == vm_id).first()
            if not vm or vm.status != "active" or vm.provision_step not in {None, "completed"}:
                metrics["vms_skipped"] += 1
                return
            if vm_is_busy(vm.id):
                metrics["vms_skipped_busy"] += 1
                return
            assignments = db.query(VMModule).filter(VMModule.vm_id == vm.id, VMModule.stage == "preapplied").all()

            def open_client():
                from api.database import SessionLocal
                from api.services.verifier_account import connect_verifier
                connection_db = SessionLocal()
                try:
                    current = connection_db.query(VM).filter(VM.id == vm_id).one()
                    return connect_verifier(current, connection_db)
                finally:
                    connection_db.close()

            try:
                client = await asyncio.to_thread(open_client)
            except Exception:
                client = None

            async def batch_executor(spec):
                if client is None:
                    return 255, ""
                def execute():
                    try:
                        _, stdout, _ = client.exec_command(json.dumps(spec, separators=(",", ":")), timeout=12)
                        payload = json.loads(stdout.read(4096).decode("utf-8", "replace"))
                        return int(payload["status"]), str(payload.get("value", ""))
                    except Exception:
                        return 255, ""
                return await asyncio.to_thread(execute)

            for assignment in assignments:
                definition = definitions.get(assignment.module_id)
                if not definition:
                    metrics["invalid_modules"] += 1
                    continue
                previous_status = assignment.status
                result = await verify_assignment(db, assignment, definition.verification, "periodic",
                                                 ssh_executor=batch_executor)
                metrics[f"result_{result.result}"] += 1
                metrics["latency_ms_total"] += result.duration_ms
                if result.result == "unavailable":
                    metrics["unavailable"] += 1
                if previous_status == "completed" and assignment.status == "regressed":
                    metrics["regressions"] += 1
        finally:
            if client:
                client.close()
            db.close()


async def run_periodic_cycle() -> None:
    db = SessionLocal()
    try:
        vm_ids = [row[0] for row in db.query(VM.id).join(Event).filter(
            Event.status == "open", VM.status == "active"
        ).all()]
    finally:
        db.close()
    metrics["backlog"] = len(vm_ids)
    definitions = {module.id: module for module in load_all_modules()
                   if not module.disabled and module.stage == "preapplied"}
    semaphore = asyncio.Semaphore(max(1, VM_CONCURRENCY))
    await asyncio.gather(*(_check_vm(vm_id, definitions, semaphore) for vm_id in vm_ids))
    metrics["backlog"] = 0
    metrics["cycles"] += 1


async def scheduler_loop() -> None:
    while True:
        await asyncio.sleep(max(1, INTERVAL_SECONDS))
        try:
            await run_periodic_cycle()
        except Exception:
            metrics["cycle_errors"] += 1


def health_metrics() -> dict:
    attempts = sum(metrics[f"result_{name}"] for name in ("pass", "fail", "unavailable", "invalid"))
    return {
        **dict(metrics),
        "average_latency_ms": round(metrics["latency_ms_total"] / attempts, 1) if attempts else 0,
        "pass_rate": round(metrics["result_pass"] / attempts, 4) if attempts else 0,
        "interval_seconds": INTERVAL_SECONDS,
        "vm_concurrency": VM_CONCURRENCY,
    }
