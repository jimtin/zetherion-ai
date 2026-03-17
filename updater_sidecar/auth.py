"""Authentication for the updater sidecar.

Auto-generates a shared secret on first run, stored in a file
that both the skills container and updater sidecar can read
via a shared volume mount.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import secrets
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

log = logging.getLogger("updater_sidecar.auth")

DEFAULT_SECRET_PATH = "/app/data/.updater-secret"  # nosec B105
_ENCRYPTED_SECRET_FORMAT = "zetherion_updater_secret_v1"
_PBKDF2_ITERATIONS = 600_000
_SALT_SIZE = 32
_NONCE_SIZE = 12
_KEY_SIZE = 32


def _derive_key(*, passphrase: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=_KEY_SIZE,
        salt=salt,
        iterations=_PBKDF2_ITERATIONS,
    )
    return kdf.derive(passphrase.encode("utf-8"))


def _encrypt_secret(secret: str, *, passphrase: str) -> dict[str, str | int]:
    salt = os.urandom(_SALT_SIZE)
    nonce = os.urandom(_NONCE_SIZE)
    ciphertext = AESGCM(_derive_key(passphrase=passphrase, salt=salt)).encrypt(
        nonce,
        secret.encode("utf-8"),
        None,
    )
    return {
        "format": _ENCRYPTED_SECRET_FORMAT,
        "iterations": _PBKDF2_ITERATIONS,
        "salt": base64.b64encode(salt).decode("ascii"),
        "ciphertext": base64.b64encode(nonce + ciphertext).decode("ascii"),
    }


def _decrypt_secret(payload: dict[str, object], *, passphrase: str) -> str:
    salt_encoded = payload.get("salt")
    ciphertext_encoded = payload.get("ciphertext")
    if not isinstance(salt_encoded, str) or not isinstance(ciphertext_encoded, str):
        raise ValueError("Encrypted updater secret payload is incomplete.")

    salt = base64.b64decode(salt_encoded.encode("ascii"))
    combined = base64.b64decode(ciphertext_encoded.encode("ascii"))
    nonce = combined[:_NONCE_SIZE]
    ciphertext = combined[_NONCE_SIZE:]
    plaintext = AESGCM(_derive_key(passphrase=passphrase, salt=salt)).decrypt(
        nonce,
        ciphertext,
        None,
    )
    return plaintext.decode("utf-8")


def _load_secret_from_file(path: Path) -> tuple[str, bool]:
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return "", False

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw, False

    if not isinstance(payload, dict) or payload.get("format") != _ENCRYPTED_SECRET_FORMAT:
        return raw, False

    passphrase = os.environ.get("ENCRYPTION_PASSPHRASE", "").strip()
    if not passphrase:
        raise RuntimeError(
            "ENCRYPTION_PASSPHRASE is required to read the encrypted updater secret."
        )
    return _decrypt_secret(payload, passphrase=passphrase), True


def _persist_secret(path: Path, secret: str) -> None:
    passphrase = os.environ.get("ENCRYPTION_PASSPHRASE", "").strip()
    if not passphrase:
        raise RuntimeError(
            "ENCRYPTION_PASSPHRASE is required to create the encrypted updater secret."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_encrypt_secret(secret, passphrase=passphrase), indent=2, ensure_ascii=True)
        + "\n",
        encoding="utf-8",
    )


def get_or_create_secret(secret_path: str = DEFAULT_SECRET_PATH) -> str:
    """Read the shared secret from file, or generate one if missing.

    The secret file is stored on a shared volume (./data/) so both
    the skills container and updater sidecar can access it.
    """
    path = Path(secret_path)

    if path.exists():
        secret, encrypted = _load_secret_from_file(path)
        if secret:
            if not encrypted and os.environ.get("ENCRYPTION_PASSPHRASE", "").strip():
                _persist_secret(path, secret)
                log.info("Migrated updater secret to encrypted storage at %s", secret_path)
            log.info("Loaded existing updater secret from %s", secret_path)
            return secret

    # Generate a new secret
    secret = secrets.token_urlsafe(32)
    _persist_secret(path, secret)
    log.info("Generated new updater secret at %s", secret_path)
    return secret


def validate_secret(request_secret: str | None, expected_secret: str) -> bool:
    """Validate that the request secret matches the expected secret.

    Uses constant-time comparison to prevent timing attacks.
    """
    if not request_secret or not expected_secret:
        return False
    return secrets.compare_digest(request_secret, expected_secret)
