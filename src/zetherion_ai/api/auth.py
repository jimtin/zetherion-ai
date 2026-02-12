"""Authentication utilities for the public API.

Handles API key generation/validation (bcrypt) and session token
management (JWT).
"""

from __future__ import annotations

import secrets
import time
from typing import Any

import bcrypt  # type: ignore[import-not-found]
import jwt  # type: ignore[import-not-found]

from zetherion_ai.logging import get_logger

log = get_logger("zetherion_ai.api.auth")

# ---------------------------------------------------------------------------
# API Key management
# ---------------------------------------------------------------------------

API_KEY_PREFIX = "sk_live_"
API_KEY_TEST_PREFIX = "sk_test_"


def generate_api_key(*, test: bool = False) -> tuple[str, str, str]:
    """Generate a new API key.

    Returns:
        Tuple of (full_key, key_prefix, key_hash).
        The full key is shown once to the user and never stored.
    """
    prefix = API_KEY_TEST_PREFIX if test else API_KEY_PREFIX
    raw = secrets.token_urlsafe(32)
    full_key = f"{prefix}{raw}"
    key_prefix = full_key[:12]
    key_hash = bcrypt.hashpw(full_key.encode(), bcrypt.gensalt()).decode()
    return full_key, key_prefix, key_hash


def verify_api_key(provided_key: str, stored_hash: str) -> bool:
    """Verify an API key against its stored bcrypt hash."""
    try:
        result: bool = bcrypt.checkpw(provided_key.encode(), stored_hash.encode())
        return result
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# Session token management (JWT)
# ---------------------------------------------------------------------------

SESSION_TOKEN_PREFIX = "zt_sess_"  # nosec B105
_DEFAULT_EXPIRY_SECONDS = 86400  # 24 hours


def create_session_token(
    tenant_id: str,
    session_id: str,
    secret: str,
    *,
    expiry_seconds: int = _DEFAULT_EXPIRY_SECONDS,
) -> str:
    """Create a signed JWT session token.

    Args:
        tenant_id: UUID of the tenant.
        session_id: UUID of the chat session.
        secret: JWT signing secret.
        expiry_seconds: Token lifetime in seconds (default 24h).

    Returns:
        Prefixed JWT string (``zt_sess_<jwt>``).
    """
    now = int(time.time())
    payload = {
        "tenant_id": tenant_id,
        "session_id": session_id,
        "iat": now,
        "exp": now + expiry_seconds,
    }
    encoded = jwt.encode(payload, secret, algorithm="HS256")
    return f"{SESSION_TOKEN_PREFIX}{encoded}"


def validate_session_token(token: str, secret: str) -> dict[str, Any]:
    """Validate and decode a session token.

    Args:
        token: The prefixed JWT string.
        secret: JWT signing secret.

    Returns:
        Decoded payload dict with ``tenant_id``, ``session_id``, etc.

    Raises:
        jwt.ExpiredSignatureError: Token has expired.
        jwt.InvalidTokenError: Token is invalid.
    """
    if token.startswith(SESSION_TOKEN_PREFIX):
        token = token[len(SESSION_TOKEN_PREFIX) :]
    result: dict[str, Any] = jwt.decode(token, secret, algorithms=["HS256"])
    return result
