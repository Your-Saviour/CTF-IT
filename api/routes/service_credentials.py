from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.database import get_db
from api.models import IntegrationDestination, ServiceCredential, User, utcnow
from api.routes.auth import get_current_user

router = APIRouter(prefix="/admin/api/credentials", tags=["service_credentials"])


class CredentialCreateRequest(BaseModel):
    service_name: str
    credential_type: str
    username: str | None = None
    password: str
    url: str | None = None
    description: str | None = None


class CredentialUpdateRequest(BaseModel):
    service_name: str
    credential_type: str
    username: str | None = None
    password: str | None = None
    url: str | None = None
    description: str | None = None


def _credential_payload(credential: ServiceCredential) -> dict:
    """Return browser-safe metadata without encrypted or clear secret material."""
    return {
        "id": credential.id,
        "service_name": credential.service_name,
        "credential_type": credential.credential_type,
        "username": credential.username,
        "has_secret": bool(credential.password),
        "masked_secret": "••••••••" if credential.password else None,
        "url": credential.url,
        "description": credential.description,
        "created_at": credential.created_at.isoformat() if credential.created_at else None,
        "created_by": credential.created_by,
    }


@router.get("")
async def list_credentials(
    request: Request,
    db: Session = Depends(get_db),
):
    """List all service credentials (admin only)."""
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    credentials = db.query(ServiceCredential).order_by(ServiceCredential.service_name).all()
    return [_credential_payload(cred) for cred in credentials]


@router.post("/{credential_id}/reveal")
async def reveal_credential(
    credential_id: int, request: Request, db: Session = Depends(get_db),
):
    """Decrypt one secret on demand and prevent storage by browser/proxy caches."""
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    credential = db.query(ServiceCredential).filter(ServiceCredential.id == credential_id).first()
    if not credential:
        return JSONResponse({"error": "not found"}, status_code=404)
    from api.services.secrets import decrypt_secret
    return JSONResponse(
        {"secret": decrypt_secret(credential.password)},
        headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"},
    )


@router.post("")
async def create_credential(
    request: Request,
    data: CredentialCreateRequest,
    db: Session = Depends(get_db),
):
    """Create a new service credential (admin only)."""
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    from api.services.secrets import encrypt_secret

    credential = ServiceCredential(
        service_name=data.service_name,
        credential_type=data.credential_type,
        username=data.username,
        password=encrypt_secret(data.password),
        url=data.url,
        description=data.description,
        created_by=user.id,
        created_at=utcnow(),
    )
    db.add(credential)
    db.commit()
    db.refresh(credential)

    return _credential_payload(credential)


@router.put("/{credential_id}")
async def update_credential(
    request: Request,
    credential_id: int,
    data: CredentialUpdateRequest,
    db: Session = Depends(get_db),
):
    """Update a service credential (admin only)."""
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    credential = db.query(ServiceCredential).filter(ServiceCredential.id == credential_id).first()
    if not credential:
        return JSONResponse({"error": "not found"}, status_code=404)

    from api.services.secrets import encrypt_secret

    credential.service_name = data.service_name
    credential.credential_type = data.credential_type
    credential.username = data.username
    if data.password is not None:
        credential.password = encrypt_secret(data.password)
    credential.url = data.url
    credential.description = data.description
    credential.updated_at = utcnow()

    db.commit()
    db.refresh(credential)

    return _credential_payload(credential)


@router.delete("/{credential_id}")
async def delete_credential(
    request: Request,
    credential_id: int,
    db: Session = Depends(get_db),
):
    """Delete a service credential (admin only)."""
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    credential = db.query(ServiceCredential).filter(ServiceCredential.id == credential_id).first()
    if not credential:
        return JSONResponse({"error": "not found"}, status_code=404)

    if db.query(IntegrationDestination).filter_by(credential_id=credential_id).first():
        return JSONResponse(
            {"error": "credential is used by an integration destination"}, status_code=409
        )

    db.delete(credential)
    db.commit()

    return {"message": "Credential deleted"}
