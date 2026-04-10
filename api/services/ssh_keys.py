"""Global platform SSH key management.

Generates an Ed25519 keypair on first use and stores it in the PlatformSettings
table so it persists across container restarts. The public key is displayed to
admins so they can add it to target VMs' authorized_keys.
"""

from __future__ import annotations

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)
from sqlalchemy.orm import Session

_PRIVATE_KEY = "ssh_private_key"
_PUBLIC_KEY = "ssh_public_key"


def get_or_create_platform_keypair(db: Session) -> tuple[str, str]:
    """Return (private_key_pem, public_key_openssh).

    Generates a new Ed25519 keypair on first call and stores both halves in
    PlatformSettings. Returns the same values on subsequent calls.
    """
    from api.models import PlatformSettings

    priv_row = db.query(PlatformSettings).filter_by(key=_PRIVATE_KEY).first()
    pub_row = db.query(PlatformSettings).filter_by(key=_PUBLIC_KEY).first()

    if priv_row and pub_row:
        return priv_row.value, pub_row.value

    # Generate a new Ed25519 keypair
    private_key = Ed25519PrivateKey.generate()

    private_pem = private_key.private_bytes(
        encoding=Encoding.PEM,
        format=PrivateFormat.OpenSSH,
        encryption_algorithm=NoEncryption(),
    ).decode()

    public_openssh = private_key.public_key().public_bytes(
        encoding=Encoding.OpenSSH,
        format=PublicFormat.OpenSSH,
    ).decode()

    # Strip trailing newline from public key for clean display
    public_openssh = public_openssh.strip()

    db.add(PlatformSettings(key=_PRIVATE_KEY, value=private_pem))
    db.add(PlatformSettings(key=_PUBLIC_KEY, value=public_openssh))
    db.commit()

    return private_pem, public_openssh


def get_public_key(db: Session) -> str | None:
    """Return the stored public key, or None if no key has been generated yet."""
    from api.models import PlatformSettings

    row = db.query(PlatformSettings).filter_by(key=_PUBLIC_KEY).first()
    return row.value if row else None
