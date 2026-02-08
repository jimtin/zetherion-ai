"""AES-256-GCM field-level encryption for Qdrant payloads.

Provides transparent encryption/decryption of sensitive payload fields
while maintaining compatibility with Qdrant's JSON storage format.
"""

import base64
import os
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from zetherion_ai.logging import get_logger

log = get_logger("zetherion_ai.security.encryption")

# AES-256-GCM parameters
NONCE_SIZE = 12  # 96-bit nonce (recommended for GCM)
KEY_SIZE = 32  # 256-bit key


class FieldEncryptor:
    """Encrypts and decrypts individual payload fields using AES-256-GCM.

    Each encryption generates a unique random nonce, which is prepended to
    the ciphertext. The combined nonce+ciphertext is base64-encoded for
    safe storage in JSON payloads.

    Attributes:
        sensitive_fields: Set of field names that should be encrypted.
    """

    def __init__(
        self,
        key: bytes,
        sensitive_fields: set[str] | None = None,
        strict: bool = False,
    ) -> None:
        """Initialize the field encryptor.

        Args:
            key: 256-bit (32-byte) encryption key.
            sensitive_fields: Set of field names to encrypt. Defaults to {"content"}.

        Raises:
            ValueError: If key is not exactly 32 bytes.
        """
        if len(key) != KEY_SIZE:
            raise ValueError(f"Key must be exactly {KEY_SIZE} bytes, got {len(key)}")

        self._aesgcm = AESGCM(key)
        self.sensitive_fields = sensitive_fields or {"content"}
        self._strict = strict
        log.info(
            "field_encryptor_initialized",
            sensitive_fields=list(self.sensitive_fields),
            strict=strict,
        )

    def encrypt_value(self, plaintext: str) -> str:
        """Encrypt a single string value.

        Args:
            plaintext: The string to encrypt.

        Returns:
            Base64-encoded string containing nonce + ciphertext.
        """
        nonce = os.urandom(NONCE_SIZE)
        ciphertext = self._aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
        # Prepend nonce to ciphertext and encode as base64
        combined = nonce + ciphertext
        return base64.b64encode(combined).decode("ascii")

    def decrypt_value(self, encrypted: str) -> str:
        """Decrypt a single encrypted value.

        Args:
            encrypted: Base64-encoded string containing nonce + ciphertext.

        Returns:
            The decrypted plaintext string.

        Raises:
            ValueError: If decryption fails (wrong key, tampered data, etc.).
        """
        try:
            combined = base64.b64decode(encrypted.encode("ascii"))
            nonce = combined[:NONCE_SIZE]
            ciphertext = combined[NONCE_SIZE:]
            plaintext = self._aesgcm.decrypt(nonce, ciphertext, None)
            return plaintext.decode("utf-8")
        except Exception as e:
            log.error("decryption_failed", error=str(e))
            raise ValueError(f"Decryption failed: {e}") from e

    def encrypt_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Encrypt sensitive fields in a payload dictionary.

        Non-sensitive fields are passed through unchanged. Only string values
        in sensitive fields are encrypted; other types raise an error.

        Args:
            payload: Dictionary containing payload fields.

        Returns:
            New dictionary with sensitive fields encrypted.

        Raises:
            TypeError: If a sensitive field contains a non-string value.
        """
        result: dict[str, Any] = {}
        for key, value in payload.items():
            if key in self.sensitive_fields:
                if not isinstance(value, str):
                    raise TypeError(
                        f"Sensitive field '{key}' must be a string, got {type(value).__name__}"
                    )
                result[key] = self.encrypt_value(value)
            else:
                result[key] = value
        return result

    def decrypt_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Decrypt sensitive fields in a payload dictionary.

        Non-sensitive fields are passed through unchanged.

        Args:
            payload: Dictionary containing encrypted payload fields.

        Returns:
            New dictionary with sensitive fields decrypted.
        """
        result: dict[str, Any] = {}
        for key, value in payload.items():
            if key in self.sensitive_fields and isinstance(value, str):
                try:
                    result[key] = self.decrypt_value(value)
                except ValueError:
                    if self._strict:
                        raise
                    # If decryption fails, pass through unchanged
                    # (might be unencrypted legacy data)
                    log.warning(
                        "decryption_passthrough",
                        field=key,
                        reason="decryption failed, may be unencrypted",
                    )
                    result[key] = value
            else:
                result[key] = value
        return result

    def is_encrypted(self, value: str) -> bool:
        """Check if a value appears to be encrypted.

        This is a heuristic check based on base64 format and length.
        It's not foolproof but helps detect unencrypted legacy data.

        Args:
            value: The string to check.

        Returns:
            True if the value appears to be encrypted, False otherwise.
        """
        try:
            decoded = base64.b64decode(value.encode("ascii"))
            # Minimum size: nonce (12) + tag (16) + at least 1 byte ciphertext
            return len(decoded) >= NONCE_SIZE + 16 + 1
        except Exception:
            return False
