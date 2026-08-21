import json
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from api.database import get_db
from api.integrations.registry import adapter_keys, get_adapter
from api.models import (
    Event, EventIntegration, IntegrationDestination, IntegrationSyncJob,
    ServiceCredential, utcnow,
)
from api.routes.auth import get_current_user
from api.services.integration_outbox import enqueue_event_sync
from api.services.secrets import decrypt_secret


router = APIRouter(tags=["integrations"])


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DestinationRequest(StrictRequest):
    name: str
    adapter_key: str
    base_url: str
    credential_id: int
    enabled: bool = True
    allow_insecure_http: bool = False
    config: dict = Field(default_factory=dict)


class BindingRequest(StrictRequest):
    destination_id: int
    enabled: bool = False


def _admin(request: Request, db: Session):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        raise HTTPException(403, "forbidden")
    return user


def _url(value: str, allow_http: bool) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(422, "base_url must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise HTTPException(422, "base_url cannot contain userinfo, query, or fragment")
    if parsed.scheme == "http" and not allow_http:
        raise HTTPException(422, "HTTP requires allow_insecure_http")
    return value.rstrip("/")


def destination_payload(item: IntegrationDestination) -> dict:
    return {
        "id": item.id, "name": item.name, "adapter_key": item.adapter_key,
        "base_url": item.base_url, "credential_id": item.credential_id,
        "enabled": item.enabled, "allow_insecure_http": item.allow_insecure_http,
        "config": json.loads(item.config_json or "{}"),
        "last_test_status": item.last_test_status, "last_test_error": item.last_test_error,
        "last_tested_at": item.last_tested_at.isoformat() if item.last_tested_at else None,
    }


def binding_payload(item: EventIntegration) -> dict:
    job = max(item.jobs, key=lambda value: value.id, default=None)
    return {
        "id": item.id, "event_id": item.event_id, "destination_id": item.destination_id,
        "enabled": item.enabled, "last_status": item.last_status or ("disabled" if not item.enabled else "pending"),
        "last_success_at": item.last_success_at.isoformat() if item.last_success_at else None,
        "last_error_code": item.last_error_code, "last_error_message": item.last_error_message,
        "job": None if job is None else {
            "status": job.status, "attempt_count": job.attempt_count,
            "trigger_reason": job.trigger_reason,
            "next_attempt_at": job.next_attempt_at.isoformat() if job.next_attempt_at else None,
        },
        "destination": destination_payload(item.destination),
    }


@router.get("/admin/api/integrations/destinations")
def list_destinations(request: Request, db: Session = Depends(get_db)):
    _admin(request, db)
    return [destination_payload(item) for item in db.query(IntegrationDestination).order_by(IntegrationDestination.name)]


@router.post("/admin/api/integrations/destinations", status_code=201)
def create_destination(body: DestinationRequest, request: Request, db: Session = Depends(get_db)):
    _admin(request, db)
    if body.adapter_key not in adapter_keys():
        raise HTTPException(422, "unknown integration adapter")
    credential = db.get(ServiceCredential, body.credential_id)
    if not credential or credential.credential_type != "token":
        raise HTTPException(422, "a token credential is required")
    item = IntegrationDestination(
        name=body.name.strip(), adapter_key=body.adapter_key,
        base_url=_url(body.base_url, body.allow_insecure_http), credential_id=body.credential_id,
        enabled=body.enabled, allow_insecure_http=body.allow_insecure_http,
        config_json=json.dumps(body.config),
    )
    db.add(item); db.commit(); db.refresh(item)
    return destination_payload(item)


@router.put("/admin/api/integrations/destinations/{destination_id}")
def update_destination(destination_id: int, body: DestinationRequest, request: Request, db: Session = Depends(get_db)):
    _admin(request, db)
    item = db.get(IntegrationDestination, destination_id)
    if not item:
        raise HTTPException(404, "destination not found")
    if item.owner_green_vm_id:
        raise HTTPException(409, "managed green deployment destinations cannot be edited directly")
    if body.adapter_key not in adapter_keys():
        raise HTTPException(422, "unknown integration adapter")
    credential = db.get(ServiceCredential, body.credential_id)
    if not credential or credential.credential_type != "token":
        raise HTTPException(422, "a token credential is required")
    item.name, item.adapter_key = body.name.strip(), body.adapter_key
    item.base_url = _url(body.base_url, body.allow_insecure_http)
    item.credential_id, item.enabled = body.credential_id, body.enabled
    item.allow_insecure_http, item.config_json = body.allow_insecure_http, json.dumps(body.config)
    if not body.enabled:
        binding_ids = [binding.id for binding in item.bindings]
        if binding_ids:
            db.query(IntegrationSyncJob).filter(
                IntegrationSyncJob.binding_id.in_(binding_ids),
                IntegrationSyncJob.status.in_(("pending", "retrying")),
            ).update({IntegrationSyncJob.status: "cancelled"}, synchronize_session=False)
    db.commit(); db.refresh(item)
    return destination_payload(item)


@router.post("/admin/api/integrations/destinations/{destination_id}/test")
async def test_destination(destination_id: int, request: Request, db: Session = Depends(get_db)):
    _admin(request, db)
    item = db.get(IntegrationDestination, destination_id)
    if not item:
        raise HTTPException(404, "destination not found")
    result = await get_adapter(item.adapter_key).test_connection(item, decrypt_secret(item.credential.password))
    item.last_test_status = "successful" if result.ok else "failed"
    item.last_test_error = None if result.ok else result.message[:500]
    item.last_tested_at = utcnow(); db.commit(); db.refresh(item)
    return JSONResponse(destination_payload(item), headers={"Cache-Control": "no-store"})


@router.delete("/admin/api/integrations/destinations/{destination_id}", status_code=204)
def delete_destination(destination_id: int, request: Request, db: Session = Depends(get_db)):
    _admin(request, db)
    item = db.get(IntegrationDestination, destination_id)
    if not item:
        raise HTTPException(404, "destination not found")
    if item.owner_green_vm_id:
        raise HTTPException(409, "managed green deployment destinations are removed with their infrastructure")
    if item.bindings:
        raise HTTPException(409, "destination is referenced by an event")
    db.delete(item); db.commit()


@router.get("/admin/api/events/{event_id}/integrations")
def event_integrations(event_id: int, request: Request, db: Session = Depends(get_db)):
    _admin(request, db)
    if not db.get(Event, event_id):
        raise HTTPException(404, "event not found")
    return [binding_payload(item) for item in db.query(EventIntegration).filter_by(event_id=event_id)]


@router.put("/admin/api/events/{event_id}/integrations")
def upsert_event_integration(event_id: int, body: BindingRequest, request: Request, db: Session = Depends(get_db)):
    _admin(request, db)
    event = db.get(Event, event_id); destination = db.get(IntegrationDestination, body.destination_id)
    if not event or not destination:
        raise HTTPException(404, "event or destination not found")
    if body.enabled and event.status != "open":
        raise HTTPException(409, "only an open event can enable synchronization")
    if body.enabled and not destination.enabled:
        raise HTTPException(409, "destination is disabled")
    item = db.query(EventIntegration).filter_by(event_id=event_id, destination_id=body.destination_id).first()
    if not item:
        item = EventIntegration(event_id=event_id, destination_id=body.destination_id)
        db.add(item); db.flush()
    if body.enabled:
        conflict = db.query(EventIntegration).join(IntegrationDestination).filter(
            EventIntegration.enabled.is_(True), EventIntegration.id != item.id,
            IntegrationDestination.adapter_key == destination.adapter_key,
            EventIntegration.destination_id == destination.id,
        ).first()
        if conflict:
            raise HTTPException(409, f"destination is active for event {conflict.event_id}")
        replaced = db.query(EventIntegration).join(IntegrationDestination).filter(
            EventIntegration.enabled.is_(True), EventIntegration.id != item.id,
            EventIntegration.event_id == event_id,
            IntegrationDestination.adapter_key == destination.adapter_key,
        ).all()
        for previous in replaced:
            previous.enabled = False
            previous.last_status = "disabled"
            db.query(IntegrationSyncJob).filter(
                IntegrationSyncJob.binding_id == previous.id,
                IntegrationSyncJob.status.in_(("pending", "retrying")),
            ).update({IntegrationSyncJob.status: "cancelled"}, synchronize_session=False)
    item.enabled = body.enabled
    item.last_status = "pending" if body.enabled else "disabled"
    if not body.enabled:
        db.query(IntegrationSyncJob).filter(
            IntegrationSyncJob.binding_id == item.id,
            IntegrationSyncJob.status.in_(("pending", "retrying")),
        ).update({IntegrationSyncJob.status: "cancelled"}, synchronize_session=False)
    db.commit(); db.refresh(item)
    return binding_payload(item)


@router.post("/admin/api/events/{event_id}/integrations/{binding_id}/sync", status_code=202)
def sync_event_integration(event_id: int, binding_id: int, request: Request, db: Session = Depends(get_db)):
    _admin(request, db)
    item = db.query(EventIntegration).filter_by(id=binding_id, event_id=event_id, enabled=True).first()
    event = db.get(Event, event_id)
    if not item:
        raise HTTPException(404, "enabled binding not found")
    if not event or event.status != "open":
        raise HTTPException(409, "only an open event can synchronize")
    enqueue_event_sync(event_id, "manual", priority=100, binding_id=binding_id)
    return {"status": "pending", "binding_id": binding_id}
