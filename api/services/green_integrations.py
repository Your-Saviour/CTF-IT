"""Materialize integration records emitted by green deployment modules."""

from sqlalchemy import or_
from sqlalchemy.orm import Session

from api.models import (
    EventIntegration, IntegrationDestination, IntegrationSyncJob, ServiceCredential, utcnow,
)
from api.services.secrets import encrypt_secret


def ensure_expo_it_integration(db: Session, event, vm, outputs: dict[str, str]) -> EventIntegration:
    url = outputs.get("expo_it.private_url")
    api_key = outputs.get("expo_it.api_key")
    if not url or not api_key:
        raise ValueError("Expo-IT deployment did not emit its URL and API key")
    conflict = db.query(EventIntegration).join(IntegrationDestination).filter(
        EventIntegration.event_id == event.id,
        EventIntegration.enabled.is_(True),
        IntegrationDestination.adapter_key == "expo_it",
        or_(IntegrationDestination.owner_green_vm_id.is_(None),
            IntegrationDestination.owner_green_vm_id != vm.id),
    ).first()
    if conflict:
        raise ValueError("event already has an administrator-managed Expo-IT binding")

    credential = db.query(ServiceCredential).filter_by(owner_green_vm_id=vm.id).first()
    if not credential:
        credential = ServiceCredential(
            service_name=f"Expo-IT event {event.id}", credential_type="token",
            password=encrypt_secret(api_key), owner_green_vm_id=vm.id,
            description="Managed by green infrastructure deployment",
        )
        db.add(credential); db.flush()
    else:
        credential.password = encrypt_secret(api_key)

    destination = db.query(IntegrationDestination).filter_by(owner_green_vm_id=vm.id).first()
    if not destination:
        destination = IntegrationDestination(
            name=f"Expo-IT event {event.id}", adapter_key="expo_it", base_url=url.rstrip("/"),
            credential_id=credential.id, owner_green_vm_id=vm.id, enabled=True,
            allow_insecure_http=False,
            config_json='{"managed_by":"green_deployment","tls_verify":false}',
        )
        db.add(destination); db.flush()
    else:
        destination.base_url = url.rstrip("/")
        destination.credential_id = credential.id
        destination.enabled = True

    binding = db.query(EventIntegration).filter_by(
        event_id=event.id, destination_id=destination.id,
    ).first()
    if not binding:
        binding = EventIntegration(event_id=event.id, destination_id=destination.id, enabled=True)
        db.add(binding)
    else:
        binding.enabled = True
    db.flush()
    job = db.query(IntegrationSyncJob).filter(
        IntegrationSyncJob.binding_id == binding.id,
        IntegrationSyncJob.status.in_(("pending", "running", "retrying")),
    ).first()
    if not job:
        db.add(IntegrationSyncJob(
            binding_id=binding.id, status="pending", trigger_reason="green_deployment_completed",
            priority=100, next_attempt_at=utcnow(),
        ))
    else:
        job.priority = max(job.priority, 100)
    db.commit(); db.refresh(binding)
    return binding


def delete_owned_integrations(db: Session, event_id: int) -> None:
    from api.models import VM
    vm_ids = [row[0] for row in db.query(VM.id).filter_by(event_id=event_id, role="green_service")]
    if not vm_ids:
        return
    destinations = db.query(IntegrationDestination).filter(
        IntegrationDestination.owner_green_vm_id.in_(vm_ids),
    ).all()
    destination_ids = [item.id for item in destinations]
    if destination_ids:
        db.query(EventIntegration).filter(
            EventIntegration.event_id == event_id,
            EventIntegration.destination_id.in_(destination_ids),
        ).delete(synchronize_session=False)
    credential_ids = [item.credential_id for item in destinations]
    for item in destinations:
        db.delete(item)
    db.flush()
    if credential_ids:
        db.query(ServiceCredential).filter(
            ServiceCredential.id.in_(credential_ids),
            ServiceCredential.owner_green_vm_id.in_(vm_ids),
        ).delete(synchronize_session=False)
