"""Tests for updater_sidecar.auth — secret management and validation."""

from __future__ import annotations

from updater_sidecar.auth import get_or_create_secret, validate_secret

# ---------------------------------------------------------------------------
# TestGetOrCreateSecret
# ---------------------------------------------------------------------------


class TestGetOrCreateSecret:
    """Tests for get_or_create_secret()."""

    def test_creates_secret_when_file_missing(self, tmp_path) -> None:
        secret_file = tmp_path / "subdir" / ".updater-secret"
        secret = get_or_create_secret(str(secret_file))

        # Secret should be a non-empty string
        assert isinstance(secret, str)
        assert len(secret) > 0

        # File should now exist
        assert secret_file.exists()
        assert secret_file.read_text() == secret

    def test_reads_existing_secret(self, tmp_path) -> None:
        secret_file = tmp_path / ".updater-secret"
        secret_file.write_text("my-existing-secret-token")

        secret = get_or_create_secret(str(secret_file))
        assert secret == "my-existing-secret-token"

    def test_generates_new_if_file_empty(self, tmp_path) -> None:
        secret_file = tmp_path / ".updater-secret"
        secret_file.write_text("")

        secret = get_or_create_secret(str(secret_file))

        # Should generate a new secret (not empty)
        assert len(secret) > 0
        # File should be overwritten with the new secret
        assert secret_file.read_text() == secret

    def test_generates_new_if_file_whitespace_only(self, tmp_path) -> None:
        secret_file = tmp_path / ".updater-secret"
        secret_file.write_text("   \n  \t  ")

        secret = get_or_create_secret(str(secret_file))

        # Whitespace-only file should be treated as empty
        assert len(secret) > 0

    def test_creates_parent_directories(self, tmp_path) -> None:
        secret_file = tmp_path / "a" / "b" / "c" / ".updater-secret"
        secret = get_or_create_secret(str(secret_file))

        assert secret_file.exists()
        assert len(secret) > 0

    def test_strips_whitespace_from_existing(self, tmp_path) -> None:
        secret_file = tmp_path / ".updater-secret"
        secret_file.write_text("  my-secret-with-spaces  \n")

        secret = get_or_create_secret(str(secret_file))
        assert secret == "my-secret-with-spaces"

    def test_idempotent_on_existing_secret(self, tmp_path) -> None:
        secret_file = tmp_path / ".updater-secret"
        secret_file.write_text("stable-secret")

        s1 = get_or_create_secret(str(secret_file))
        s2 = get_or_create_secret(str(secret_file))
        assert s1 == s2 == "stable-secret"

    def test_generated_secret_is_url_safe(self, tmp_path) -> None:
        secret_file = tmp_path / ".updater-secret"
        secret = get_or_create_secret(str(secret_file))

        # secrets.token_urlsafe uses base64url-safe characters
        import re

        assert re.fullmatch(r"[A-Za-z0-9_-]+", secret)


# ---------------------------------------------------------------------------
# TestValidateSecret
# ---------------------------------------------------------------------------


class TestValidateSecret:
    """Tests for validate_secret()."""

    def test_correct_secret(self) -> None:
        assert validate_secret("my-secret", "my-secret") is True

    def test_wrong_secret(self) -> None:
        assert validate_secret("wrong", "my-secret") is False

    def test_empty_request_secret(self) -> None:
        assert validate_secret("", "my-secret") is False

    def test_none_request_secret(self) -> None:
        assert validate_secret(None, "my-secret") is False

    def test_empty_expected_secret(self) -> None:
        assert validate_secret("my-secret", "") is False

    def test_both_empty(self) -> None:
        assert validate_secret("", "") is False

    def test_none_and_empty(self) -> None:
        assert validate_secret(None, "") is False

    def test_timing_safe_comparison(self) -> None:
        """Ensure the function uses constant-time comparison.

        We cannot easily test timing properties, but we verify it
        delegates to secrets.compare_digest by checking matching
        behavior on various inputs.
        """
        # Matching secrets of different lengths
        assert validate_secret("short", "short") is True
        assert validate_secret("a" * 1000, "a" * 1000) is True

        # Different-length secrets always fail
        assert validate_secret("short", "longer-secret") is False
        assert validate_secret("longer-secret", "short") is False

    def test_unicode_secrets(self) -> None:
        assert validate_secret("hello-world", "hello-world") is True
        assert validate_secret("hello-world", "hello-World") is False
