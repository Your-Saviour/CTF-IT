import pytest

from api.services.secrets import decrypt_secret, encrypt_secret


def test_secret_round_trip(monkeypatch):
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "test-secret-key")
    encrypted = encrypt_secret("firewall-password")
    assert encrypted.startswith("enc:v1:")
    assert len(encrypted) <= 128
    assert "firewall-password" not in encrypted
    assert decrypt_secret(encrypted) == "firewall-password"


def test_encryption_is_idempotent(monkeypatch):
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "test-secret-key")
    encrypted = encrypt_secret("value")
    assert encrypt_secret(encrypted) == encrypted


def test_wrong_key_is_rejected(monkeypatch):
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "first-key")
    encrypted = encrypt_secret("value")
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "second-key")
    with pytest.raises(ValueError, match="cannot be decrypted"):
        decrypt_secret(encrypted)
