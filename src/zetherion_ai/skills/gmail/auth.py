"""OAuth2 flow for Gmail integration.

Handles Google OAuth2 authorization, token exchange, refresh,
and state validation for secure multi-account Gmail connections.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import urlencode

import httpx

from zetherion_ai.logging import get_logger

log = get_logger("zetherion_ai.skills.gmail.auth")

# Google OAuth2 endpoints
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"  # nosec B105
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

# Required scopes for Gmail + Calendar
DEFAULT_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/userinfo.email",
]

# State token expiry (10 minutes)
STATE_TOKEN_EXPIRY = 600


class OAuthError(Exception):
    """Raised when OAuth2 operations fail."""


class GmailAuth:
    """Handles Google OAuth2 flows for Gmail integration.

    Generates auth URLs, validates state tokens, exchanges
    authorization codes for access/refresh tokens, and refreshes
    expired tokens.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        *,
        state_secret: str = "",  # nosec B105
    ) -> None:
        """Initialize Gmail OAuth handler.

        Args:
            client_id: Google OAuth2 client ID.
            client_secret: Google OAuth2 client secret.
            redirect_uri: Callback URL for OAuth2 redirect.
            state_secret: Secret key for HMAC-signing state tokens.
        """
        if not client_id:
            raise ValueError("client_id is required")
        if not client_secret:
            raise ValueError("client_secret is required")
        if not redirect_uri:
            raise ValueError("redirect_uri is required")

        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._state_secret = state_secret or client_secret
        log.info("gmail_auth_initialized", redirect_uri=redirect_uri)

    def generate_auth_url(
        self,
        user_id: int,
        *,
        scopes: list[str] | None = None,
    ) -> tuple[str, str]:
        """Generate a Google OAuth2 authorization URL.

        Args:
            user_id: Discord user ID to bind to this auth flow.
            scopes: OAuth scopes to request. Defaults to DEFAULT_SCOPES.

        Returns:
            Tuple of (auth_url, state_token).
        """
        scope_list = scopes or DEFAULT_SCOPES
        state_token = self._create_state_token(user_id)

        params = {
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "response_type": "code",
            "scope": " ".join(scope_list),
            "access_type": "offline",
            "prompt": "consent",
            "state": state_token,
        }

        auth_url = f"{GOOGLE_AUTH_URL}?{urlencode(params)}"
        log.info("auth_url_generated", user_id=user_id)
        return auth_url, state_token

    def validate_state_token(self, state: str) -> int:
        """Validate and extract user_id from a state token.

        Args:
            state: The state token from the OAuth callback.

        Returns:
            The user_id encoded in the state.

        Raises:
            OAuthError: If the token is invalid, expired, or tampered.
        """
        try:
            parts = state.split(".")
            if len(parts) != 3:
                raise OAuthError("Invalid state token format")

            payload_b64, timestamp_str, signature = parts

            # Verify signature
            expected_sig = self._sign(f"{payload_b64}.{timestamp_str}")
            if not hmac.compare_digest(signature, expected_sig):
                raise OAuthError("State token signature mismatch")

            # Check expiry
            timestamp = int(timestamp_str)
            if time.time() - timestamp > STATE_TOKEN_EXPIRY:
                raise OAuthError("State token expired")

            # Decode payload
            payload = json.loads(_b64_decode(payload_b64))
            user_id = int(payload["user_id"])
            log.info("state_token_validated", user_id=user_id)
            return user_id

        except OAuthError:
            raise
        except Exception as exc:
            raise OAuthError(f"Failed to validate state token: {exc}") from exc

    async def exchange_code(self, code: str) -> dict[str, Any]:
        """Exchange an authorization code for tokens.

        Args:
            code: The authorization code from the OAuth callback.

        Returns:
            Dict with access_token, refresh_token, expires_in, scope, etc.

        Raises:
            OAuthError: If the token exchange fails.
        """
        data = {
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": self._redirect_uri,
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(GOOGLE_TOKEN_URL, data=data)
                response.raise_for_status()
                result: dict[str, Any] = response.json()

                if "error" in result:
                    raise OAuthError(
                        f"Token exchange error: {result['error']}"
                        f" - {result.get('error_description', '')}"
                    )

                log.info("code_exchanged_for_tokens")
                return result

            except httpx.HTTPStatusError as exc:
                body = exc.response.text
                raise OAuthError(
                    f"Token exchange HTTP error {exc.response.status_code}: {body}"
                ) from exc
            except httpx.RequestError as exc:
                raise OAuthError(f"Token exchange request failed: {exc}") from exc

    async def refresh_access_token(self, refresh_token: str) -> dict[str, Any]:
        """Refresh an expired access token.

        Args:
            refresh_token: The refresh token.

        Returns:
            Dict with new access_token, expires_in, etc.

        Raises:
            OAuthError: If the refresh fails.
        """
        data = {
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(GOOGLE_TOKEN_URL, data=data)
                response.raise_for_status()
                result: dict[str, Any] = response.json()

                if "error" in result:
                    raise OAuthError(
                        f"Token refresh error: {result['error']}"
                        f" - {result.get('error_description', '')}"
                    )

                log.info("access_token_refreshed")
                return result

            except httpx.HTTPStatusError as exc:
                body = exc.response.text
                raise OAuthError(
                    f"Token refresh HTTP error {exc.response.status_code}: {body}"
                ) from exc
            except httpx.RequestError as exc:
                raise OAuthError(f"Token refresh request failed: {exc}") from exc

    async def get_user_email(self, access_token: str) -> str:
        """Fetch the authenticated user's email address.

        Args:
            access_token: Valid Google access token.

        Returns:
            The user's email address.

        Raises:
            OAuthError: If the request fails.
        """
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    GOOGLE_USERINFO_URL,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                response.raise_for_status()
                data: dict[str, Any] = response.json()
                email_addr: str = data.get("email", "")
                if not email_addr:
                    raise OAuthError("No email in userinfo response")
                return email_addr

            except httpx.HTTPStatusError as exc:
                raise OAuthError(f"Userinfo request failed: {exc.response.status_code}") from exc
            except httpx.RequestError as exc:
                raise OAuthError(f"Userinfo request error: {exc}") from exc

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _create_state_token(self, user_id: int) -> str:
        """Create an HMAC-signed state token encoding user_id + timestamp."""
        payload = json.dumps({"user_id": user_id})
        payload_b64 = _b64_encode(payload)
        timestamp = str(int(time.time()))
        message = f"{payload_b64}.{timestamp}"
        signature = self._sign(message)
        return f"{payload_b64}.{timestamp}.{signature}"

    def _sign(self, message: str) -> str:
        """Create HMAC-SHA256 signature of message."""
        return hmac.new(
            self._state_secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()


def _b64_encode(data: str) -> str:
    """URL-safe base64 encode without padding."""
    import base64

    return base64.urlsafe_b64encode(data.encode("utf-8")).rstrip(b"=").decode("ascii")


def _b64_decode(data: str) -> str:
    """URL-safe base64 decode with padding restoration."""
    import base64

    padded = data + "=" * (4 - len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
