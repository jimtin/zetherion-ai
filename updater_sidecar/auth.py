"""Authentication for the updater sidecar.

Auto-generates a shared secret on first run, stored in a file
that both the skills container and updater sidecar can read
via a shared volume mount.
"""

from __future__ import annotations

import logging
import secrets
from pathlib import Path

log = logging.getLogger("updater_sidecar.auth")

DEFAULT_SECRET_PATH = "/app/data/.updater-secret"


def get_or_create_secret(secret_path: str = DEFAULT_SECRET_PATH) -> str:
    """Read the shared secret from file, or generate one if missing.

    The secret file is stored on a shared volume (./data/) so both
    the skills container and updater sidecar can access it.
    """
    path = Path(secret_path)

    if path.exists():
        secret = path.read_text().strip()
        if secret:
            log.info("Loaded existing updater secret from %s", secret_path)
            return secret

    # Generate a new secret
    secret = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(secret)
    log.info("Generated new updater secret at %s", secret_path)
    return secret


def validate_secret(
    request_secret: str | None, expected_secret: str
) -> bool:
    """Validate that the request secret matches the expected secret.

    Uses constant-time comparison to prevent timing attacks.
    """
    if not request_secret or not expected_secret:
        return False
    return secrets.compare_digest(request_secret, expected_secret)
