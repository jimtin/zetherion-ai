"""Key derivation and salt management for SecureClaw encryption.

Uses PBKDF2-HMAC-SHA256 to derive a 256-bit encryption key from a
user-provided passphrase. The salt is generated once and persisted
to disk for consistent key derivation across restarts.
"""

import os
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from zetherion_ai.logging import get_logger
from zetherion_ai.security.encryption import KEY_SIZE

log = get_logger("zetherion_ai.security.keys")

# PBKDF2 parameters
# 600,000 iterations as recommended by OWASP for PBKDF2-HMAC-SHA256
# https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html
PBKDF2_ITERATIONS = 600_000
SALT_SIZE = 32  # 256-bit salt


class KeyManager:
    """Manages encryption key derivation from a passphrase.

    The salt is automatically generated on first use and persisted to
    the configured salt file path. Subsequent initializations use the
    existing salt for consistent key derivation.
    """

    def __init__(self, passphrase: str, salt_path: str | Path) -> None:
        """Initialize the key manager.

        Args:
            passphrase: The master passphrase to derive the key from.
            salt_path: Path to the salt file. Created if it doesn't exist.

        Raises:
            ValueError: If passphrase is empty or too short.
        """
        if not passphrase or len(passphrase) < 16:
            raise ValueError("Passphrase must be at least 16 characters")

        self._passphrase = passphrase
        self._salt_path = Path(salt_path)
        self._salt = self._load_or_create_salt()
        self._key = self._derive_key()

        log.info(
            "key_manager_initialized",
            salt_path=str(self._salt_path),
            iterations=PBKDF2_ITERATIONS,
        )

    def _load_or_create_salt(self) -> bytes:
        """Load existing salt from file or create a new one.

        Returns:
            The salt bytes (either loaded or newly generated).
        """
        if self._salt_path.exists():
            salt = self._salt_path.read_bytes()
            if len(salt) != SALT_SIZE:
                log.warning(
                    "salt_size_mismatch",
                    expected=SALT_SIZE,
                    actual=len(salt),
                )
                # Regenerate if size is wrong (unlikely but possible corruption)
                salt = self._create_new_salt()
            else:
                log.debug("salt_loaded", path=str(self._salt_path))
            return salt
        else:
            return self._create_new_salt()

    def _create_new_salt(self) -> bytes:
        """Generate and persist a new random salt.

        Returns:
            The newly generated salt bytes.
        """
        salt = os.urandom(SALT_SIZE)
        # Ensure parent directory exists
        self._salt_path.parent.mkdir(parents=True, exist_ok=True)
        self._salt_path.write_bytes(salt)
        log.info("salt_created", path=str(self._salt_path))
        return salt

    def _derive_key(self) -> bytes:
        """Derive the encryption key from passphrase and salt.

        Returns:
            The 256-bit derived key.
        """
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=KEY_SIZE,
            salt=self._salt,
            iterations=PBKDF2_ITERATIONS,
        )
        return kdf.derive(self._passphrase.encode("utf-8"))

    @property
    def key(self) -> bytes:
        """Get the derived encryption key.

        Returns:
            The 256-bit encryption key.
        """
        return self._key

    @property
    def salt(self) -> bytes:
        """Get the salt used for key derivation.

        Returns:
            The salt bytes.
        """
        return self._salt

    def rotate_key(self, new_passphrase: str) -> bytes:
        """Rotate to a new passphrase with a new salt.

        This generates a new salt and derives a new key from the
        new passphrase. The old salt file is overwritten.

        Warning: This will invalidate all previously encrypted data!
        A migration process should be run to re-encrypt existing data
        before calling this method.

        Args:
            new_passphrase: The new passphrase to use.

        Returns:
            The new 256-bit encryption key.

        Raises:
            ValueError: If new passphrase is empty or too short.
        """
        if not new_passphrase or len(new_passphrase) < 16:
            raise ValueError("New passphrase must be at least 16 characters")

        self._passphrase = new_passphrase
        self._salt = self._create_new_salt()
        self._key = self._derive_key()

        log.warning("key_rotated", salt_path=str(self._salt_path))
        return self._key
