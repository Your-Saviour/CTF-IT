"""Small encrypted-at-rest wrapper for credentials stored in the application DB."""

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken

_PREFIX = "enc:v1:"


def _fernet() -> Fernet:
    secret_key = os.environ.get("DATA_ENCRYPTION_KEY")
    if not secret_key:
        raise RuntimeError("DATA_ENCRYPTION_KEY is required for secret encryption")
    derived = hashlib.sha256(secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt_secret(value: str | None) -> str | None:
    if not value or value.startswith(_PREFIX):
        return value
    token = _fernet().encrypt(value.encode("utf-8")).decode("ascii")
    return _PREFIX + token


def decrypt_secret(value: str | None) -> str | None:
    if not value:
        return value
    if not value.startswith(_PREFIX):
        # Legacy plaintext is accepted only long enough for the startup migration.
        return value
    try:
        return _fernet().decrypt(value[len(_PREFIX):].encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Stored credential cannot be decrypted with the current SECRET_KEY") from exc
