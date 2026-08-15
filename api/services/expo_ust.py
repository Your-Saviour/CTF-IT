import asyncio
import os

import httpx

from api.database import SessionLocal
from api.models import Event, VM, utcnow


def configured() -> bool:
    return bool(os.getenv("EXPO_IT_URL", "").strip() and os.getenv("EXPO_IT_API_KEY", "").strip())


def payload_for(event: Event, vms: list[VM]) -> dict:
    return {
        "external_event_id": str(event.id),
        "name": event.name,
        "starts_at": (event.started_at or utcnow()).isoformat(),
        "systems": [{
            "system_id": str(vm.id),
            "team": vm.team.name,
            "hostname": vm.hostname or f"vm-{vm.id}",
            "os": vm.os,
            "role": vm.role or vm.vm_type,
            "zone": vm.zone.name if vm.zone else None,
            "addresses": [value for value in (vm.ip_address, vm.public_ip, vm.private_ip, vm.vpc_ip) if value],
            "ust_prompt": vm.ust_prompt,
        } for vm in vms if vm.team],
    }


async def synchronize(event_id: int, retries: int = 3) -> bool:
    if not configured():
        return False
    for attempt in range(retries):
        db = SessionLocal()
        try:
            event = db.query(Event).filter_by(id=event_id).first()
            if not event:
                return False
            event.expo_sync_status = "syncing"
            event.expo_sync_attempts = (event.expo_sync_attempts or 0) + 1
            db.commit()
            body = payload_for(event, db.query(VM).filter_by(event_id=event_id).all())
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.put(
                    os.environ["EXPO_IT_URL"].rstrip("/") + "/api/v1/ust/exercise",
                    headers={"X-API-Key": os.environ["EXPO_IT_API_KEY"]}, json=body,
                )
                response.raise_for_status()
            event.expo_sync_status = "synchronized"
            event.expo_sync_last_error = None
            event.expo_sync_completed_at = utcnow()
            db.commit()
            return True
        except Exception as error:
            db.rollback()
            event = db.query(Event).filter_by(id=event_id).first()
            if event:
                event.expo_sync_status = "failed"
                event.expo_sync_last_error = type(error).__name__
                db.commit()
        finally:
            db.close()
        if attempt + 1 < retries:
            await asyncio.sleep(2 ** attempt)
    return False


def schedule(event_id: int) -> bool:
    if not configured():
        return False
    asyncio.create_task(synchronize(event_id))
    return True
