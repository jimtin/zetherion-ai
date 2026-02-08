"""Unit tests for the encryption module."""

import os
import tempfile
from pathlib import Path

import pytest

from zetherion_ai.security.encryption import NONCE_SIZE, FieldEncryptor
from zetherion_ai.security.keys import KEY_SIZE, SALT_SIZE, KeyManager


class TestFieldEncryptor:
    """Tests for FieldEncryptor class."""

    @pytest.fixture
    def key(self) -> bytes:
        """Generate a random 256-bit key for testing."""
        return os.urandom(32)

    @pytest.fixture
    def encryptor(self, key: bytes) -> FieldEncryptor:
        """Create a FieldEncryptor instance."""
        return FieldEncryptor(key)

    def test_init_with_valid_key(self, key: bytes) -> None:
        """Test initialization with a valid 256-bit key."""
        encryptor = FieldEncryptor(key)
        assert encryptor.sensitive_fields == {"content"}

    def test_init_with_custom_sensitive_fields(self, key: bytes) -> None:
        """Test initialization with custom sensitive fields."""
        fields = {"content", "description", "title"}
        encryptor = FieldEncryptor(key, sensitive_fields=fields)
        assert encryptor.sensitive_fields == fields

    def test_init_with_invalid_key_length(self) -> None:
        """Test that initialization fails with wrong key length."""
        with pytest.raises(ValueError, match="Key must be exactly 32 bytes"):
            FieldEncryptor(os.urandom(16))

        with pytest.raises(ValueError, match="Key must be exactly 32 bytes"):
            FieldEncryptor(os.urandom(64))

    def test_encrypt_value_returns_base64(self, encryptor: FieldEncryptor) -> None:
        """Test that encrypted value is base64 encoded."""
        plaintext = "Hello, World!"
        encrypted = encryptor.encrypt_value(plaintext)

        # Should be valid base64
        import base64

        decoded = base64.b64decode(encrypted)
        # Should contain nonce (12 bytes) + ciphertext + tag (16 bytes)
        assert len(decoded) >= NONCE_SIZE + 16 + len(plaintext.encode())

    def test_encrypt_decrypt_roundtrip(self, encryptor: FieldEncryptor) -> None:
        """Test that encryption followed by decryption returns original value."""
        plaintext = "This is a secret message!"
        encrypted = encryptor.encrypt_value(plaintext)
        decrypted = encryptor.decrypt_value(encrypted)
        assert decrypted == plaintext

    def test_encrypt_decrypt_unicode(self, encryptor: FieldEncryptor) -> None:
        """Test encryption/decryption of unicode content."""
        plaintext = "Hello 世界! 🌍 Привет мир!"
        encrypted = encryptor.encrypt_value(plaintext)
        decrypted = encryptor.decrypt_value(encrypted)
        assert decrypted == plaintext

    def test_encrypt_decrypt_empty_string(self, encryptor: FieldEncryptor) -> None:
        """Test encryption/decryption of empty string."""
        plaintext = ""
        encrypted = encryptor.encrypt_value(plaintext)
        decrypted = encryptor.decrypt_value(encrypted)
        assert decrypted == plaintext

    def test_encrypt_decrypt_long_text(self, encryptor: FieldEncryptor) -> None:
        """Test encryption/decryption of long text."""
        plaintext = "A" * 100000
        encrypted = encryptor.encrypt_value(plaintext)
        decrypted = encryptor.decrypt_value(encrypted)
        assert decrypted == plaintext

    def test_unique_nonces(self, encryptor: FieldEncryptor) -> None:
        """Test that each encryption uses a unique nonce."""
        import base64

        plaintext = "Same text"
        encrypted1 = encryptor.encrypt_value(plaintext)
        encrypted2 = encryptor.encrypt_value(plaintext)

        # Should produce different ciphertexts due to random nonces
        assert encrypted1 != encrypted2

        # Extract nonces and verify they're different
        nonce1 = base64.b64decode(encrypted1)[:NONCE_SIZE]
        nonce2 = base64.b64decode(encrypted2)[:NONCE_SIZE]
        assert nonce1 != nonce2

    def test_decrypt_with_wrong_key_fails(self, key: bytes) -> None:
        """Test that decryption fails with a different key."""
        encryptor1 = FieldEncryptor(key)
        encryptor2 = FieldEncryptor(os.urandom(32))

        plaintext = "Secret data"
        encrypted = encryptor1.encrypt_value(plaintext)

        with pytest.raises(ValueError, match="Decryption failed"):
            encryptor2.decrypt_value(encrypted)

    def test_decrypt_tampered_data_fails(self, encryptor: FieldEncryptor) -> None:
        """Test that decryption fails if ciphertext is tampered with."""
        import base64

        plaintext = "Secret data"
        encrypted = encryptor.encrypt_value(plaintext)

        # Tamper with the ciphertext
        data = bytearray(base64.b64decode(encrypted))
        data[-1] ^= 0xFF  # Flip bits in the last byte
        tampered = base64.b64encode(bytes(data)).decode("ascii")

        with pytest.raises(ValueError, match="Decryption failed"):
            encryptor.decrypt_value(tampered)

    def test_encrypt_payload_encrypts_sensitive_fields(self, encryptor: FieldEncryptor) -> None:
        """Test that encrypt_payload only encrypts sensitive fields."""
        payload = {
            "content": "Secret message",
            "user_id": 12345,
            "timestamp": "2024-01-01T00:00:00",
        }

        encrypted_payload = encryptor.encrypt_payload(payload)

        # content should be encrypted (different from original)
        assert encrypted_payload["content"] != payload["content"]
        # Non-sensitive fields should be unchanged
        assert encrypted_payload["user_id"] == 12345
        assert encrypted_payload["timestamp"] == "2024-01-01T00:00:00"

    def test_decrypt_payload_decrypts_sensitive_fields(self, encryptor: FieldEncryptor) -> None:
        """Test that decrypt_payload restores original values."""
        original_payload = {
            "content": "Secret message",
            "user_id": 12345,
            "timestamp": "2024-01-01T00:00:00",
        }

        encrypted_payload = encryptor.encrypt_payload(original_payload)
        decrypted_payload = encryptor.decrypt_payload(encrypted_payload)

        assert decrypted_payload["content"] == original_payload["content"]
        assert decrypted_payload["user_id"] == 12345
        assert decrypted_payload["timestamp"] == "2024-01-01T00:00:00"

    def test_encrypt_payload_with_custom_sensitive_fields(self, key: bytes) -> None:
        """Test encryption with custom sensitive fields."""
        encryptor = FieldEncryptor(key, sensitive_fields={"content", "title"})
        payload = {
            "content": "Secret content",
            "title": "Secret title",
            "public_field": "Not encrypted",
        }

        encrypted = encryptor.encrypt_payload(payload)

        assert encrypted["content"] != payload["content"]
        assert encrypted["title"] != payload["title"]
        assert encrypted["public_field"] == "Not encrypted"

    def test_encrypt_payload_non_string_sensitive_field_raises(
        self, encryptor: FieldEncryptor
    ) -> None:
        """Test that encrypting non-string sensitive field raises error."""
        payload = {
            "content": 12345,  # Should be string
        }

        with pytest.raises(TypeError, match="must be a string"):
            encryptor.encrypt_payload(payload)

    def test_is_encrypted_positive(self, encryptor: FieldEncryptor) -> None:
        """Test is_encrypted returns True for encrypted values."""
        encrypted = encryptor.encrypt_value("test")
        assert encryptor.is_encrypted(encrypted) is True

    def test_is_encrypted_negative(self, encryptor: FieldEncryptor) -> None:
        """Test is_encrypted returns False for plain text."""
        assert encryptor.is_encrypted("plain text") is False
        assert encryptor.is_encrypted("") is False
        assert encryptor.is_encrypted("not base64!!!") is False


class TestFieldEncryptorStrictMode:
    """Tests for FieldEncryptor strict mode behavior."""

    @pytest.fixture
    def key(self) -> bytes:
        """Generate a random 256-bit key for testing."""
        return os.urandom(32)

    def test_constructor_accepts_strict_parameter(self, key: bytes) -> None:
        """Test that FieldEncryptor accepts the strict parameter."""
        encryptor = FieldEncryptor(key, strict=True)
        assert encryptor._strict is True

    def test_constructor_strict_defaults_to_false(self, key: bytes) -> None:
        """Test that strict defaults to False when not specified."""
        encryptor = FieldEncryptor(key)
        assert encryptor._strict is False

    def test_strict_false_passes_through_bad_data(self, key: bytes) -> None:
        """Test that strict=False passes through unchanged on bad data."""
        encryptor = FieldEncryptor(key, strict=False)
        payload = {
            "content": "not-valid-encrypted-data",
            "user_id": 123,
        }

        result = encryptor.decrypt_payload(payload)

        # Bad data should be passed through unchanged
        assert result["content"] == "not-valid-encrypted-data"
        assert result["user_id"] == 123

    def test_strict_true_raises_on_bad_data(self, key: bytes) -> None:
        """Test that strict=True raises ValueError on bad data."""
        encryptor = FieldEncryptor(key, strict=True)
        payload = {
            "content": "not-valid-encrypted-data",
            "user_id": 123,
        }

        with pytest.raises(ValueError, match="Decryption failed"):
            encryptor.decrypt_payload(payload)

    def test_strict_true_decrypts_valid_data_normally(self, key: bytes) -> None:
        """Test that strict=True still decrypts valid data correctly."""
        encryptor = FieldEncryptor(key, strict=True)
        original_payload = {
            "content": "This is valid data",
            "user_id": 123,
        }

        encrypted = encryptor.encrypt_payload(original_payload)
        decrypted = encryptor.decrypt_payload(encrypted)

        assert decrypted["content"] == "This is valid data"
        assert decrypted["user_id"] == 123

    def test_strict_false_decrypts_valid_data_normally(self, key: bytes) -> None:
        """Test that strict=False still decrypts valid data correctly."""
        encryptor = FieldEncryptor(key, strict=False)
        original_payload = {
            "content": "This is valid data",
            "user_id": 123,
        }

        encrypted = encryptor.encrypt_payload(original_payload)
        decrypted = encryptor.decrypt_payload(encrypted)

        assert decrypted["content"] == "This is valid data"
        assert decrypted["user_id"] == 123

    def test_strict_true_raises_on_wrong_key_data(self, key: bytes) -> None:
        """Test strict=True raises when data was encrypted with a different key."""
        encryptor1 = FieldEncryptor(key, strict=False)
        encryptor2 = FieldEncryptor(os.urandom(32), strict=True)

        payload = {"content": "Encrypted with key 1"}
        encrypted = encryptor1.encrypt_payload(payload)

        with pytest.raises(ValueError, match="Decryption failed"):
            encryptor2.decrypt_payload(encrypted)

    def test_strict_false_passes_through_wrong_key_data(self, key: bytes) -> None:
        """Test strict=False passes through data encrypted with a different key."""
        encryptor1 = FieldEncryptor(key, strict=False)
        encryptor2 = FieldEncryptor(os.urandom(32), strict=False)

        payload = {"content": "Encrypted with key 1"}
        encrypted = encryptor1.encrypt_payload(payload)

        result = encryptor2.decrypt_payload(encrypted)
        # Should pass through the encrypted value unchanged (not raise)
        assert result["content"] == encrypted["content"]


class TestKeyManager:
    """Tests for KeyManager class."""

    @pytest.fixture
    def temp_salt_path(self) -> Path:
        """Create a temporary path for salt file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir) / "salt.bin"

    def test_init_creates_salt_file(self, temp_salt_path: Path) -> None:
        """Test that initialization creates salt file if it doesn't exist."""
        assert not temp_salt_path.exists()

        KeyManager("secure_passphrase_16", temp_salt_path)

        assert temp_salt_path.exists()
        assert len(temp_salt_path.read_bytes()) == SALT_SIZE

    def test_init_loads_existing_salt(self, temp_salt_path: Path) -> None:
        """Test that initialization uses existing salt file."""
        # Create salt file first
        original_salt = os.urandom(SALT_SIZE)
        temp_salt_path.parent.mkdir(parents=True, exist_ok=True)
        temp_salt_path.write_bytes(original_salt)

        km = KeyManager("secure_passphrase_16", temp_salt_path)

        assert km.salt == original_salt

    def test_init_rejects_short_passphrase(self, temp_salt_path: Path) -> None:
        """Test that initialization fails with short passphrase."""
        with pytest.raises(ValueError, match="at least 16 characters"):
            KeyManager("short", temp_salt_path)

    def test_init_rejects_empty_passphrase(self, temp_salt_path: Path) -> None:
        """Test that initialization fails with empty passphrase."""
        with pytest.raises(ValueError, match="at least 16 characters"):
            KeyManager("", temp_salt_path)

    def test_key_is_correct_length(self, temp_salt_path: Path) -> None:
        """Test that derived key is exactly 256 bits."""
        km = KeyManager("secure_passphrase_16", temp_salt_path)
        assert len(km.key) == KEY_SIZE

    def test_same_passphrase_same_salt_same_key(self, temp_salt_path: Path) -> None:
        """Test that same passphrase + salt produces same key."""
        km1 = KeyManager("secure_passphrase_16", temp_salt_path)
        km2 = KeyManager("secure_passphrase_16", temp_salt_path)

        assert km1.key == km2.key
        assert km1.salt == km2.salt

    def test_different_passphrase_different_key(self, temp_salt_path: Path) -> None:
        """Test that different passphrases produce different keys."""
        km1 = KeyManager("secure_passphrase_16", temp_salt_path)
        key1 = km1.key

        # Use a different temp path for different salt
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_path2 = Path(tmpdir) / "salt2.bin"
            # Copy the salt to ensure same salt
            temp_path2.write_bytes(km1.salt)

            km2 = KeyManager("different_passphrase!", temp_path2)
            key2 = km2.key

        assert key1 != key2

    def test_different_salt_different_key(self) -> None:
        """Test that different salts produce different keys."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path1 = Path(tmpdir) / "salt1.bin"
            path2 = Path(tmpdir) / "salt2.bin"

            km1 = KeyManager("secure_passphrase_16", path1)
            km2 = KeyManager("secure_passphrase_16", path2)

            assert km1.key != km2.key
            assert km1.salt != km2.salt

    def test_rotate_key_creates_new_salt(self, temp_salt_path: Path) -> None:
        """Test that key rotation creates a new salt."""
        km = KeyManager("secure_passphrase_16", temp_salt_path)
        original_salt = km.salt
        original_key = km.key

        km.rotate_key("new_secure_passphrase_16")

        assert km.salt != original_salt
        assert km.key != original_key

    def test_rotate_key_rejects_short_passphrase(self, temp_salt_path: Path) -> None:
        """Test that key rotation fails with short passphrase."""
        km = KeyManager("secure_passphrase_16", temp_salt_path)

        with pytest.raises(ValueError, match="at least 16 characters"):
            km.rotate_key("short")

    def test_key_derivation_is_deterministic(self, temp_salt_path: Path) -> None:
        """Test that key derivation is deterministic given same inputs."""
        # Create key manager and get key
        km = KeyManager("secure_passphrase_16", temp_salt_path)
        key1 = km.key
        salt = km.salt

        # Delete and recreate with same salt
        del km
        temp_salt_path.write_bytes(salt)

        km2 = KeyManager("secure_passphrase_16", temp_salt_path)
        key2 = km2.key

        assert key1 == key2


class TestIntegration:
    """Integration tests for encryption + key management."""

    def test_end_to_end_encryption(self) -> None:
        """Test complete encryption workflow: key derivation -> encrypt -> decrypt."""
        with tempfile.TemporaryDirectory() as tmpdir:
            salt_path = Path(tmpdir) / "salt.bin"

            # Initialize key manager
            km = KeyManager("my_secure_passphrase_here", salt_path)

            # Create encryptor with custom sensitive fields
            encryptor = FieldEncryptor(km.key, sensitive_fields={"content", "description"})

            # Create a payload
            original_payload = {
                "content": "This is sensitive user data",
                "description": "Also sensitive",
                "user_id": 12345,
                "timestamp": "2024-01-01T00:00:00",
            }

            # Encrypt
            encrypted = encryptor.encrypt_payload(original_payload)

            # Verify sensitive fields are encrypted
            assert encrypted["content"] != original_payload["content"]
            assert encrypted["description"] != original_payload["description"]
            assert encrypted["user_id"] == 12345

            # Decrypt
            decrypted = encryptor.decrypt_payload(encrypted)

            # Verify roundtrip
            assert decrypted == original_payload

    def test_persistence_across_restarts(self) -> None:
        """Test that encryption persists across key manager restarts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            salt_path = Path(tmpdir) / "salt.bin"
            passphrase = "persistent_passphrase_16"

            # First session: encrypt data
            km1 = KeyManager(passphrase, salt_path)
            encryptor1 = FieldEncryptor(km1.key)
            encrypted_value = encryptor1.encrypt_value("Persistent secret")

            # Second session: new key manager instance (simulating restart)
            km2 = KeyManager(passphrase, salt_path)
            encryptor2 = FieldEncryptor(km2.key)
            decrypted_value = encryptor2.decrypt_value(encrypted_value)

            assert decrypted_value == "Persistent secret"
