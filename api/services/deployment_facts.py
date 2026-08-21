"""Encrypted, write-only inputs for event-scoped green deployments."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from api.models import Event, GreenDeploymentFact, utcnow
from api.services.secrets import decrypt_secret, encrypt_secret
from builder.module_loader import DeploymentFactSpec, load_all_modules


def _green_node(event: Event, vm_key: str) -> dict | None:
    infrastructure = json.loads(event.infrastructure or "{}")
    return next((vm for vm in infrastructure.get("green_infrastructure", {}).get("vms", [])
                 if vm.get("key") == vm_key), None)


def declared_inputs(event: Event, vm_key: str) -> dict[str, DeploymentFactSpec]:
    if not _green_node(event, vm_key):
        return {}
    assignment = json.loads(event.module_plan or '{"version":1,"assignments":{}}').get(
        "assignments", {}).get(f"green:{vm_key}", {})
    assigned = set(assignment.get("resolved_module_ids", []))
    result: dict[str, DeploymentFactSpec] = {}
    for module in load_all_modules():
        if module.id in assigned and module.deployment:
            result.update((spec.trait, spec) for spec in module.deployment.inputs)
    return result


def fact_presence(db: Session, event: Event, vm_key: str) -> list[dict]:
    rows = {row.trait: row for row in db.query(GreenDeploymentFact).filter_by(
        event_id=event.id, vm_key=vm_key,
    )}
    return [{
        "trait": trait,
        "label": spec.label,
        "secret": spec.secret,
        "value_type": spec.value_type,
        "configured": trait in rows,
        "updated_at": rows[trait].updated_at.isoformat() if trait in rows else None,
    } for trait, spec in declared_inputs(event, vm_key).items()]


def validate_input(spec: DeploymentFactSpec, value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("fact value is required")
    if spec.value_type == "ssh_private_key":
        valid = (
            value.startswith("-----BEGIN OPENSSH PRIVATE KEY-----\n")
            or value.startswith("-----BEGIN RSA PRIVATE KEY-----\n")
        ) and "\n-----END " in value
        if not valid:
            raise ValueError("fact must be an OpenSSH or PEM private key")
    return value


def put_fact(db: Session, event: Event, vm_key: str, trait: str, value: object) -> GreenDeploymentFact:
    spec = declared_inputs(event, vm_key).get(trait)
    if not spec:
        raise ValueError("fact is not declared by an assigned deployment module")
    plaintext = validate_input(spec, value)
    row = db.query(GreenDeploymentFact).filter_by(
        event_id=event.id, vm_key=vm_key, trait=trait,
    ).first()
    if not row:
        row = GreenDeploymentFact(event_id=event.id, vm_key=vm_key, trait=trait,
                                  encrypted_value="", secret=spec.secret)
        db.add(row)
    row.encrypted_value = encrypt_secret(plaintext)
    row.secret = spec.secret
    row.updated_at = utcnow()
    db.commit()
    db.refresh(row)
    return row


def clear_fact(db: Session, event_id: int, vm_key: str, trait: str) -> None:
    db.query(GreenDeploymentFact).filter_by(
        event_id=event_id, vm_key=vm_key, trait=trait,
    ).delete(synchronize_session=False)
    db.commit()


def resolve_inputs(db: Session, event: Event, vm_key: str, module) -> dict[str, str]:
    declared = {spec.trait for spec in (module.deployment.inputs if module.deployment else ())}
    rows = db.query(GreenDeploymentFact).filter_by(event_id=event.id, vm_key=vm_key).all()
    values = {row.trait: decrypt_secret(row.encrypted_value) for row in rows if row.trait in declared}
    missing = declared - values.keys()
    if missing:
        raise ValueError(f"missing required deployment facts: {', '.join(sorted(missing))}")
    return values
