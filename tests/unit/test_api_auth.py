"""Tests for API authentication utilities (key generation, JWT tokens)."""

from __future__ import annotations

import bcrypt
import jwt
import pytest

from zetherion_ai.api.auth import (
    API_KEY_PREFIX,
    API_KEY_TEST_PREFIX,
    SESSION_TOKEN_PREFIX,
    create_session_token,
    generate_api_key,
    validate_session_token,
    verify_api_key,
)


class TestAPIKeyGeneration:
    """Tests for API key generation and verification."""

    def test_generate_live_key_format(self):
        """Live API key starts with sk_live_ prefix."""
        full_key, prefix, key_hash = generate_api_key()
        assert full_key.startswith(API_KEY_PREFIX)
        assert prefix == full_key[:12]
        assert len(full_key) > 20

    def test_generate_test_key_format(self):
        """Test API key starts with sk_test_ prefix."""
        full_key, prefix, key_hash = generate_api_key(test=True)
        assert full_key.startswith(API_KEY_TEST_PREFIX)
        assert prefix == full_key[:12]

    def test_generate_key_hash_is_bcrypt(self):
        """Generated hash is valid bcrypt."""
        full_key, _, key_hash = generate_api_key()
        assert key_hash.startswith("$2")
        assert bcrypt.checkpw(full_key.encode(), key_hash.encode())

    def test_generate_unique_keys(self):
        """Each call generates a unique key."""
        key1, _, _ = generate_api_key()
        key2, _, _ = generate_api_key()
        assert key1 != key2

    def test_verify_api_key_valid(self):
        """verify_api_key returns True for correct key."""
        full_key, _, key_hash = generate_api_key()
        assert verify_api_key(full_key, key_hash) is True

    def test_verify_api_key_invalid(self):
        """verify_api_key returns False for wrong key."""
        _, _, key_hash = generate_api_key()
        assert verify_api_key("sk_live_wrong_key_here", key_hash) is False

    def test_verify_api_key_malformed_hash(self):
        """verify_api_key returns False for malformed hash."""
        assert verify_api_key("some-key", "not-a-bcrypt-hash") is False

    def test_verify_api_key_empty_inputs(self):
        """verify_api_key handles empty strings without crashing."""
        assert verify_api_key("", "") is False


class TestSessionTokens:
    """Tests for JWT session token creation and validation."""

    JWT_SECRET = "test-secret-for-unit-tests"

    def test_create_token_has_prefix(self):
        """Created token starts with zt_sess_ prefix."""
        token = create_session_token("tid-1", "sid-1", self.JWT_SECRET)
        assert token.startswith(SESSION_TOKEN_PREFIX)

    def test_roundtrip(self):
        """Created token can be validated back to original claims."""
        token = create_session_token("tid-1", "sid-1", self.JWT_SECRET)
        payload = validate_session_token(token, self.JWT_SECRET)
        assert payload["tenant_id"] == "tid-1"
        assert payload["session_id"] == "sid-1"

    def test_token_contains_exp_and_iat(self):
        """Token payload includes standard iat and exp claims."""
        token = create_session_token("tid", "sid", self.JWT_SECRET)
        payload = validate_session_token(token, self.JWT_SECRET)
        assert "iat" in payload
        assert "exp" in payload
        assert payload["exp"] > payload["iat"]

    def test_default_expiry_24h(self):
        """Default expiry is 24 hours from iat."""
        token = create_session_token("tid", "sid", self.JWT_SECRET)
        payload = validate_session_token(token, self.JWT_SECRET)
        assert payload["exp"] - payload["iat"] == 86400

    def test_custom_expiry(self):
        """Custom expiry overrides default."""
        token = create_session_token("tid", "sid", self.JWT_SECRET, expiry_seconds=3600)
        payload = validate_session_token(token, self.JWT_SECRET)
        assert payload["exp"] - payload["iat"] == 3600

    def test_validate_without_prefix(self):
        """validate_session_token works even if prefix is stripped."""
        token = create_session_token("tid", "sid", self.JWT_SECRET)
        raw_jwt = token[len(SESSION_TOKEN_PREFIX) :]
        payload = validate_session_token(raw_jwt, self.JWT_SECRET)
        assert payload["tenant_id"] == "tid"

    def test_validate_wrong_secret(self):
        """Invalid secret raises an error."""
        token = create_session_token("tid", "sid", self.JWT_SECRET)
        with pytest.raises(jwt.InvalidSignatureError):
            validate_session_token(token, "wrong-secret")

    def test_validate_expired_token(self):
        """Expired token raises ExpiredSignatureError."""
        token = create_session_token("tid", "sid", self.JWT_SECRET, expiry_seconds=-1)
        with pytest.raises(jwt.ExpiredSignatureError):
            validate_session_token(token, self.JWT_SECRET)

    def test_validate_garbage_token(self):
        """Completely invalid token raises an error."""
        with pytest.raises(jwt.DecodeError):
            validate_session_token("zt_sess_not-a-real-jwt", self.JWT_SECRET)
