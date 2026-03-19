"""Security module for Zetherion AI.

Provides application-layer encryption for sensitive data stored in Qdrant
and encrypted secret storage in PostgreSQL.
"""

from __future__ import annotations

from typing import Any

__all__ = ["FieldEncryptor", "KeyManager", "SecretResolver", "SecretsManager"]


def __getattr__(name: str) -> Any:
    """Lazily expose security primitives without triggering import cycles."""
    if name == "FieldEncryptor":
        from zetherion_ai.security.encryption import FieldEncryptor

        return FieldEncryptor
    if name == "KeyManager":
        from zetherion_ai.security.keys import KeyManager

        return KeyManager
    if name == "SecretResolver":
        from zetherion_ai.security.secret_resolver import SecretResolver

        return SecretResolver
    if name == "SecretsManager":
        from zetherion_ai.security.secrets import SecretsManager

        return SecretsManager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
