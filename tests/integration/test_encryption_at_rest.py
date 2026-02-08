"""Integration tests for encryption at rest.

Uses real cryptographic operations (no mocking of encryption) to verify that
KeyManager + FieldEncryptor produce correct round-trip encrypt/decrypt
behaviour, handle tampering appropriately, and support Unicode content.
"""

import base64
from pathlib import Path

import pytest

from zetherion_ai.security.encryption import KEY_SIZE, NONCE_SIZE, FieldEncryptor
from zetherion_ai.security.keys import KeyManager

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def key_manager(tmp_path: Path) -> KeyManager:
    """Create a real KeyManager with a test passphrase and temp salt file."""
    salt_file = tmp_path / "test_salt.bin"
    return KeyManager(
        passphrase="integration-test-passphrase-long-enough",
        salt_path=salt_file,
    )


@pytest.fixture()
def encryptor(key_manager: KeyManager) -> FieldEncryptor:
    """Create a real FieldEncryptor using the derived key."""
    return FieldEncryptor(key=key_manager.key)


@pytest.fixture()
def strict_encryptor(key_manager: KeyManager) -> FieldEncryptor:
    """Create a FieldEncryptor in strict mode."""
    return FieldEncryptor(key=key_manager.key, strict=True)


# ---------------------------------------------------------------------------
# Key derivation tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_key_manager_derives_32_byte_key(key_manager: KeyManager) -> None:
    """KeyManager should produce a 256-bit (32-byte) key."""
    assert len(key_manager.key) == KEY_SIZE


@pytest.mark.integration
def test_key_manager_consistent_across_instances(tmp_path: Path) -> None:
    """Two KeyManagers with the same passphrase and salt file yield the same key."""
    salt_file = tmp_path / "shared_salt.bin"
    passphrase = "consistent-key-derivation-test!!"

    km1 = KeyManager(passphrase=passphrase, salt_path=salt_file)
    km2 = KeyManager(passphrase=passphrase, salt_path=salt_file)

    assert km1.key == km2.key


# ---------------------------------------------------------------------------
# Basic encrypt / decrypt round-trip
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_encrypt_payload_round_trip(encryptor: FieldEncryptor) -> None:
    """Encrypting then decrypting a payload should return the original."""
    original = {
        "content": "This is a secret memory",
        "user_id": 42,
        "type": "preference",
    }

    encrypted = encryptor.encrypt_payload(original)

    # content should be encrypted (different from original)
    assert encrypted["content"] != original["content"]
    # non-sensitive fields pass through unchanged
    assert encrypted["user_id"] == 42
    assert encrypted["type"] == "preference"

    decrypted = encryptor.decrypt_payload(encrypted)
    assert decrypted["content"] == original["content"]
    assert decrypted["user_id"] == 42


@pytest.mark.integration
def test_encrypted_value_is_valid_base64(encryptor: FieldEncryptor) -> None:
    """The encrypted content field should be a valid base64 string."""
    encrypted = encryptor.encrypt_payload({"content": "secret"})
    encrypted_value = encrypted["content"]

    # Should not raise
    decoded = base64.b64decode(encrypted_value.encode("ascii"))
    # Must contain at least nonce + 16-byte GCM tag + 1 byte ciphertext
    assert len(decoded) >= NONCE_SIZE + 16 + 1


@pytest.mark.integration
def test_encrypted_value_is_not_plaintext(encryptor: FieldEncryptor) -> None:
    """The encrypted field value should not contain the plaintext."""
    plaintext = "super secret data that must not leak"
    encrypted = encryptor.encrypt_payload({"content": plaintext})

    assert plaintext not in encrypted["content"]


# ---------------------------------------------------------------------------
# Multiple sensitive fields
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_multiple_sensitive_fields(key_manager: KeyManager) -> None:
    """Encryption should work across multiple declared sensitive fields."""
    enc = FieldEncryptor(
        key=key_manager.key,
        sensitive_fields={"content", "notes", "description"},
    )

    original = {
        "content": "primary secret",
        "notes": "secondary secret",
        "description": "tertiary secret",
        "public_tag": "visible",
    }

    encrypted = enc.encrypt_payload(original)

    assert encrypted["content"] != original["content"]
    assert encrypted["notes"] != original["notes"]
    assert encrypted["description"] != original["description"]
    assert encrypted["public_tag"] == "visible"

    decrypted = enc.decrypt_payload(encrypted)
    assert decrypted["content"] == "primary secret"
    assert decrypted["notes"] == "secondary secret"
    assert decrypted["description"] == "tertiary secret"
    assert decrypted["public_tag"] == "visible"


# ---------------------------------------------------------------------------
# Unicode support
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_unicode_content_round_trip(encryptor: FieldEncryptor) -> None:
    """Encryption should correctly handle Unicode content."""
    original = {
        "content": (
            "Emoji test: \U0001f680\U0001f30d \u2014 CJK: \u4f60\u597d"
            " \u2014 Cyrillic: \u041f\u0440\u0438\u0432\u0435\u0442"
        ),
    }

    encrypted = encryptor.encrypt_payload(original)
    assert encrypted["content"] != original["content"]

    decrypted = encryptor.decrypt_payload(encrypted)
    assert decrypted["content"] == original["content"]


@pytest.mark.integration
def test_unicode_multibyte_round_trip(encryptor: FieldEncryptor) -> None:
    """Multibyte characters should survive encryption round-trip."""
    text = "\u00e9\u00e8\u00ea\u00eb\u00f1\u00fc\u00df\u00e5\u00e6\u00f8"
    encrypted = encryptor.encrypt_value(text)
    decrypted = encryptor.decrypt_value(encrypted)
    assert decrypted == text


# ---------------------------------------------------------------------------
# Tamper detection -- strict mode
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_strict_mode_rejects_tampered_ciphertext(strict_encryptor: FieldEncryptor) -> None:
    """In strict mode, tampered ciphertext should raise ValueError."""
    encrypted = strict_encryptor.encrypt_payload({"content": "important"})

    # Tamper with the encrypted value by flipping a byte
    raw = base64.b64decode(encrypted["content"].encode("ascii"))
    tampered_bytes = bytearray(raw)
    # Flip a byte in the ciphertext portion (after the nonce)
    tampered_bytes[NONCE_SIZE + 1] ^= 0xFF
    tampered_b64 = base64.b64encode(bytes(tampered_bytes)).decode("ascii")

    tampered_payload = {"content": tampered_b64}

    with pytest.raises(ValueError, match="Decryption failed"):
        strict_encryptor.decrypt_payload(tampered_payload)


@pytest.mark.integration
def test_strict_mode_rejects_garbage_ciphertext(strict_encryptor: FieldEncryptor) -> None:
    """In strict mode, completely invalid ciphertext should raise ValueError."""
    garbage_b64 = base64.b64encode(b"this is not real ciphertext at all!!").decode("ascii")
    tampered_payload = {"content": garbage_b64}

    with pytest.raises(ValueError, match="Decryption failed"):
        strict_encryptor.decrypt_payload(tampered_payload)


# ---------------------------------------------------------------------------
# Tamper handling -- non-strict (default) mode
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_non_strict_mode_passes_through_tampered_value(encryptor: FieldEncryptor) -> None:
    """In non-strict mode, tampered ciphertext should pass through unchanged."""
    encrypted = encryptor.encrypt_payload({"content": "important"})

    raw = base64.b64decode(encrypted["content"].encode("ascii"))
    tampered_bytes = bytearray(raw)
    tampered_bytes[NONCE_SIZE + 1] ^= 0xFF
    tampered_b64 = base64.b64encode(bytes(tampered_bytes)).decode("ascii")

    tampered_payload = {"content": tampered_b64}

    # Should NOT raise -- passes through with a warning
    result = encryptor.decrypt_payload(tampered_payload)
    assert result["content"] == tampered_b64


@pytest.mark.integration
def test_non_strict_mode_passes_through_unencrypted_legacy_data(
    encryptor: FieldEncryptor,
) -> None:
    """Non-strict mode should pass through plaintext that was never encrypted."""
    legacy_payload = {"content": "plain old text from before encryption was enabled"}

    result = encryptor.decrypt_payload(legacy_payload)
    assert result["content"] == legacy_payload["content"]


# ---------------------------------------------------------------------------
# is_encrypted heuristic
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_is_encrypted_detects_encrypted_value(encryptor: FieldEncryptor) -> None:
    """is_encrypted should return True for genuinely encrypted values."""
    enc_val = encryptor.encrypt_value("hello")
    assert encryptor.is_encrypted(enc_val) is True


@pytest.mark.integration
def test_is_encrypted_rejects_plaintext(encryptor: FieldEncryptor) -> None:
    """is_encrypted should return False for obvious plaintext."""
    assert encryptor.is_encrypted("hello world") is False


# ---------------------------------------------------------------------------
# Different keys cannot decrypt each other
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_different_keys_cannot_decrypt(tmp_path: Path) -> None:
    """Data encrypted with one key should not decrypt with a different key."""
    salt1 = tmp_path / "salt1.bin"
    salt2 = tmp_path / "salt2.bin"

    km1 = KeyManager(passphrase="first-passphrase-long-enough!!", salt_path=salt1)
    km2 = KeyManager(passphrase="second-passphrase-different!!", salt_path=salt2)

    enc1 = FieldEncryptor(key=km1.key, strict=True)
    enc2 = FieldEncryptor(key=km2.key, strict=True)

    encrypted = enc1.encrypt_payload({"content": "secret"})

    with pytest.raises(ValueError, match="Decryption failed"):
        enc2.decrypt_payload(encrypted)
