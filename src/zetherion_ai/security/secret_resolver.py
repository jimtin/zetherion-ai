"""Cascade secret resolver: DB (encrypted) -> .env -> default.

Provides a single ``get_secret()`` entry point that checks the encrypted
PostgreSQL-backed :class:`SecretsManager` first, falls back to the
Pydantic :class:`Settings` (populated from ``.env``), and finally returns a
caller-supplied default.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import SecretStr

from zetherion_ai.logging import get_logger

if TYPE_CHECKING:
    from zetherion_ai.config import Settings
    from zetherion_ai.security.secrets import SecretsManager

log = get_logger("zetherion_ai.security.secret_resolver")

# Map from secret name -> Settings attribute name.
# Only SecretStr fields are included.
_SETTINGS_FIELD_MAP: dict[str, str] = {  # nosec B105 — field name mapping, not passwords
    "discord_token": "discord_token",
    "gemini_api_key": "gemini_api_key",
    "anthropic_api_key": "anthropic_api_key",
    "openai_api_key": "openai_api_key",
    "google_client_secret": "google_client_secret",
    "github_token": "github_token",
    "skills_api_secret": "skills_api_secret",
    "api_jwt_secret": "api_jwt_secret",
}


class SecretResolver:
    """Resolves secrets with cascade: DB -> .env -> default."""

    def __init__(
        self,
        secrets_manager: SecretsManager | None,
        settings: Settings,
    ) -> None:
        self._secrets_manager = secrets_manager
        self._settings = settings

    def get_secret(self, name: str, default: str | None = None) -> str | None:
        """Get a secret value with cascade resolution.

        Resolution order:
        1. SecretsManager (encrypted DB) — if initialised
        2. Settings (.env / environment) — via field map
        3. Caller-supplied default

        Args:
            name: Secret name (e.g. ``"anthropic_api_key"``).
            default: Fallback if not found anywhere.

        Returns:
            The secret value, or *default*.
        """
        # 1. Try DB-backed secrets manager
        if self._secrets_manager is not None:
            val = self._secrets_manager.get(name)
            if val is not None:
                return val

        # 2. Try Settings (.env)
        env_val = self._get_from_settings(name)
        if env_val is not None:
            return env_val

        # 3. Default
        return default

    def _get_from_settings(self, name: str) -> str | None:
        """Retrieve a secret from the Pydantic Settings model.

        Returns the decrypted string value, or ``None`` if the field is unset
        or not mapped.
        """
        field_name = _SETTINGS_FIELD_MAP.get(name)
        if field_name is None:
            return None

        value = getattr(self._settings, field_name, None)
        if value is None:
            return None

        if isinstance(value, SecretStr):
            return value.get_secret_value()

        return str(value)
