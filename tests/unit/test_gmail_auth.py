"""Tests for Gmail OAuth2 authentication module."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from zetherion_ai.skills.gmail.auth import (
    DEFAULT_SCOPES,
    GOOGLE_AUTH_URL,
    GOOGLE_TOKEN_URL,
    GOOGLE_USERINFO_URL,
    STATE_TOKEN_EXPIRY,
    GmailAuth,
    OAuthError,
    _b64_decode,
    _b64_encode,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def auth() -> GmailAuth:
    """Create a GmailAuth instance with valid test credentials."""
    return GmailAuth(
        client_id="test-client-id",
        client_secret="test-client-secret",
        redirect_uri="http://localhost:8080/callback",
        state_secret="test-state-secret",
    )


@pytest.fixture
def auth_no_state_secret() -> GmailAuth:
    """Create a GmailAuth instance without an explicit state_secret."""
    return GmailAuth(
        client_id="test-client-id",
        client_secret="test-client-secret",
        redirect_uri="http://localhost:8080/callback",
    )


@pytest.fixture
def mock_httpx_response():
    """Factory for creating mock httpx.Response objects."""

    def _create(
        status_code: int = 200,
        json_data: dict | None = None,
        text: str = "",
        raise_for_status_error: bool = False,
    ) -> MagicMock:
        response = MagicMock(spec=httpx.Response)
        response.status_code = status_code
        response.text = text
        if json_data is not None:
            response.json.return_value = json_data
        if raise_for_status_error:
            http_error = httpx.HTTPStatusError(
                message=f"HTTP {status_code}",
                request=MagicMock(spec=httpx.Request),
                response=response,
            )
            response.raise_for_status.side_effect = http_error
        else:
            response.raise_for_status.return_value = None
        return response

    return _create


# ===========================================================================
# 1. Constructor tests
# ===========================================================================


class TestGmailAuthConstructor:
    """Tests for GmailAuth.__init__."""

    def test_valid_initialization(self, auth: GmailAuth) -> None:
        """GmailAuth initializes with valid credentials."""
        assert auth._client_id == "test-client-id"
        assert auth._client_secret == "test-client-secret"
        assert auth._redirect_uri == "http://localhost:8080/callback"
        assert auth._state_secret == "test-state-secret"

    def test_empty_client_id_raises(self) -> None:
        """Empty client_id raises ValueError."""
        with pytest.raises(ValueError, match="client_id is required"):
            GmailAuth(
                client_id="",
                client_secret="secret",
                redirect_uri="http://localhost/callback",
            )

    def test_empty_client_secret_raises(self) -> None:
        """Empty client_secret raises ValueError."""
        with pytest.raises(ValueError, match="client_secret is required"):
            GmailAuth(
                client_id="id",
                client_secret="",
                redirect_uri="http://localhost/callback",
            )

    def test_empty_redirect_uri_raises(self) -> None:
        """Empty redirect_uri raises ValueError."""
        with pytest.raises(ValueError, match="redirect_uri is required"):
            GmailAuth(
                client_id="id",
                client_secret="secret",
                redirect_uri="",
            )

    def test_state_secret_defaults_to_client_secret(self, auth_no_state_secret: GmailAuth) -> None:
        """When state_secret is not provided, it defaults to client_secret."""
        assert auth_no_state_secret._state_secret == "test-client-secret"


# ===========================================================================
# 2. generate_auth_url tests
# ===========================================================================


class TestGenerateAuthUrl:
    """Tests for GmailAuth.generate_auth_url."""

    def test_generates_valid_url_with_required_params(self, auth: GmailAuth) -> None:
        """Auth URL contains all required OAuth2 parameters."""
        url, state = auth.generate_auth_url(user_id=12345)

        assert url.startswith(GOOGLE_AUTH_URL)
        assert "client_id=test-client-id" in url
        assert "redirect_uri=" in url
        assert "response_type=code" in url
        assert "access_type=offline" in url
        assert "prompt=consent" in url
        assert f"state={state}" in url

    def test_uses_default_scopes_when_none_provided(self, auth: GmailAuth) -> None:
        """DEFAULT_SCOPES are used when no custom scopes are given."""
        from urllib.parse import unquote

        url, _ = auth.generate_auth_url(user_id=12345)
        decoded_url = unquote(url)

        for scope in DEFAULT_SCOPES:
            assert scope in decoded_url

    def test_custom_scopes_are_used(self, auth: GmailAuth) -> None:
        """Custom scopes override DEFAULT_SCOPES."""
        custom_scopes = ["https://www.googleapis.com/auth/gmail.readonly"]
        url, _ = auth.generate_auth_url(user_id=12345, scopes=custom_scopes)

        assert "gmail.readonly" in url
        # Ensure default-only scopes are not present
        assert "calendar.readonly" not in url

    def test_returns_state_token(self, auth: GmailAuth) -> None:
        """generate_auth_url returns a valid state token as second element."""
        _, state = auth.generate_auth_url(user_id=99)

        # State token has 3 dot-separated parts: payload.timestamp.signature
        parts = state.split(".")
        assert len(parts) == 3

        # Verify the payload decodes to contain user_id
        payload = json.loads(_b64_decode(parts[0]))
        assert payload["user_id"] == 99


# ===========================================================================
# 3. validate_state_token tests
# ===========================================================================


class TestValidateStateToken:
    """Tests for GmailAuth.validate_state_token."""

    def test_valid_token_returns_user_id(self, auth: GmailAuth) -> None:
        """A freshly created state token validates successfully."""
        _, state = auth.generate_auth_url(user_id=42)
        user_id = auth.validate_state_token(state)
        assert user_id == 42

    def test_expired_token_raises_oauth_error(self, auth: GmailAuth) -> None:
        """An expired state token raises OAuthError."""
        _, state = auth.generate_auth_url(user_id=42)

        # Fast-forward time past expiry
        with patch("zetherion_ai.skills.gmail.auth.time") as mock_time:
            # Make time.time() return a value past the expiry
            mock_time.time.return_value = time.time() + STATE_TOKEN_EXPIRY + 100
            with pytest.raises(OAuthError, match="State token expired"):
                auth.validate_state_token(state)

    def test_tampered_signature_raises_oauth_error(self, auth: GmailAuth) -> None:
        """A token with tampered signature raises OAuthError."""
        _, state = auth.generate_auth_url(user_id=42)
        parts = state.split(".")
        # Replace signature with garbage
        tampered = f"{parts[0]}.{parts[1]}.{'0' * 64}"

        with pytest.raises(OAuthError, match="signature mismatch"):
            auth.validate_state_token(tampered)

    def test_wrong_format_raises_oauth_error(self, auth: GmailAuth) -> None:
        """A token without 3 dot-separated parts raises OAuthError."""
        with pytest.raises(OAuthError, match="Invalid state token format"):
            auth.validate_state_token("only.two")

    def test_too_many_parts_raises_oauth_error(self, auth: GmailAuth) -> None:
        """A token with more than 3 parts raises OAuthError."""
        with pytest.raises(OAuthError, match="Invalid state token format"):
            auth.validate_state_token("a.b.c.d")

    def test_invalid_payload_raises_oauth_error(self, auth: GmailAuth) -> None:
        """A token with invalid payload raises OAuthError."""
        # Create a token with a bad base64 payload that won't decode to valid JSON
        timestamp = str(int(time.time()))
        bad_payload = "not-valid-base64!!!"
        message = f"{bad_payload}.{timestamp}"
        sig = auth._sign(message)
        bad_token = f"{bad_payload}.{timestamp}.{sig}"

        with pytest.raises(OAuthError, match="Failed to validate state token"):
            auth.validate_state_token(bad_token)

    def test_payload_missing_user_id_raises_oauth_error(self, auth: GmailAuth) -> None:
        """A token whose payload lacks user_id raises OAuthError."""
        payload = json.dumps({"not_user_id": 123})
        payload_b64 = _b64_encode(payload)
        timestamp = str(int(time.time()))
        message = f"{payload_b64}.{timestamp}"
        sig = auth._sign(message)
        token = f"{payload_b64}.{timestamp}.{sig}"

        with pytest.raises(OAuthError, match="Failed to validate state token"):
            auth.validate_state_token(token)


# ===========================================================================
# 4. exchange_code tests
# ===========================================================================


class TestExchangeCode:
    """Tests for GmailAuth.exchange_code."""

    @pytest.mark.asyncio
    async def test_successful_code_exchange(self, auth: GmailAuth, mock_httpx_response) -> None:
        """Successful code exchange returns token dict."""
        token_data = {
            "access_token": "ya29.access",
            "refresh_token": "1//refresh",
            "expires_in": 3600,
            "scope": "email",
            "token_type": "Bearer",
        }
        mock_resp = mock_httpx_response(200, json_data=token_data)

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("zetherion_ai.skills.gmail.auth.httpx.AsyncClient", return_value=mock_client):
            result = await auth.exchange_code("auth-code-123")

        assert result == token_data
        mock_client.post.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_error_response_raises_oauth_error(
        self, auth: GmailAuth, mock_httpx_response
    ) -> None:
        """Google returning an error field in JSON raises OAuthError."""
        error_data = {
            "error": "invalid_grant",
            "error_description": "Code already used",
        }
        mock_resp = mock_httpx_response(200, json_data=error_data)

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("zetherion_ai.skills.gmail.auth.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(OAuthError, match="Token exchange error.*invalid_grant"):
                await auth.exchange_code("bad-code")

    @pytest.mark.asyncio
    async def test_http_error_raises_oauth_error(
        self, auth: GmailAuth, mock_httpx_response
    ) -> None:
        """HTTP error status from Google raises OAuthError."""
        mock_resp = mock_httpx_response(400, text="Bad Request", raise_for_status_error=True)

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("zetherion_ai.skills.gmail.auth.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(OAuthError, match="Token exchange HTTP error"):
                await auth.exchange_code("code")

    @pytest.mark.asyncio
    async def test_request_error_raises_oauth_error(self, auth: GmailAuth) -> None:
        """Network-level request error raises OAuthError."""
        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.RequestError(
            "Connection refused", request=MagicMock(spec=httpx.Request)
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("zetherion_ai.skills.gmail.auth.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(OAuthError, match="Token exchange request failed"):
                await auth.exchange_code("code")

    @pytest.mark.asyncio
    async def test_exchange_code_sends_correct_data(
        self, auth: GmailAuth, mock_httpx_response
    ) -> None:
        """exchange_code sends the correct form data to Google."""
        mock_resp = mock_httpx_response(
            200, json_data={"access_token": "tok", "refresh_token": "ref"}
        )

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("zetherion_ai.skills.gmail.auth.httpx.AsyncClient", return_value=mock_client):
            await auth.exchange_code("the-code")

        call_args = mock_client.post.call_args
        assert call_args[0][0] == GOOGLE_TOKEN_URL
        sent_data = call_args[1]["data"]
        assert sent_data["code"] == "the-code"
        assert sent_data["grant_type"] == "authorization_code"
        assert sent_data["client_id"] == "test-client-id"
        assert sent_data["client_secret"] == "test-client-secret"
        assert sent_data["redirect_uri"] == "http://localhost:8080/callback"


# ===========================================================================
# 5. refresh_access_token tests
# ===========================================================================


class TestRefreshAccessToken:
    """Tests for GmailAuth.refresh_access_token."""

    @pytest.mark.asyncio
    async def test_successful_refresh(self, auth: GmailAuth, mock_httpx_response) -> None:
        """Successful refresh returns new token data."""
        new_token_data = {
            "access_token": "ya29.new-access",
            "expires_in": 3600,
            "scope": "email",
            "token_type": "Bearer",
        }
        mock_resp = mock_httpx_response(200, json_data=new_token_data)

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("zetherion_ai.skills.gmail.auth.httpx.AsyncClient", return_value=mock_client):
            result = await auth.refresh_access_token("1//refresh-token")

        assert result == new_token_data

    @pytest.mark.asyncio
    async def test_error_response_raises_oauth_error(
        self, auth: GmailAuth, mock_httpx_response
    ) -> None:
        """Error field in refresh response raises OAuthError."""
        error_data = {
            "error": "invalid_grant",
            "error_description": "Token revoked",
        }
        mock_resp = mock_httpx_response(200, json_data=error_data)

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("zetherion_ai.skills.gmail.auth.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(OAuthError, match="Token refresh error.*invalid_grant"):
                await auth.refresh_access_token("bad-refresh")

    @pytest.mark.asyncio
    async def test_http_error_raises_oauth_error(
        self, auth: GmailAuth, mock_httpx_response
    ) -> None:
        """HTTP status error on refresh raises OAuthError."""
        mock_resp = mock_httpx_response(401, text="Unauthorized", raise_for_status_error=True)

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("zetherion_ai.skills.gmail.auth.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(OAuthError, match="Token refresh HTTP error"):
                await auth.refresh_access_token("refresh")

    @pytest.mark.asyncio
    async def test_request_error_raises_oauth_error(self, auth: GmailAuth) -> None:
        """Network error on refresh raises OAuthError."""
        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.RequestError(
            "DNS failure", request=MagicMock(spec=httpx.Request)
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("zetherion_ai.skills.gmail.auth.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(OAuthError, match="Token refresh request failed"):
                await auth.refresh_access_token("refresh")

    @pytest.mark.asyncio
    async def test_refresh_sends_correct_data(self, auth: GmailAuth, mock_httpx_response) -> None:
        """refresh_access_token sends the correct form data."""
        mock_resp = mock_httpx_response(200, json_data={"access_token": "new-tok"})

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("zetherion_ai.skills.gmail.auth.httpx.AsyncClient", return_value=mock_client):
            await auth.refresh_access_token("my-refresh-token")

        call_args = mock_client.post.call_args
        assert call_args[0][0] == GOOGLE_TOKEN_URL
        sent_data = call_args[1]["data"]
        assert sent_data["refresh_token"] == "my-refresh-token"
        assert sent_data["grant_type"] == "refresh_token"
        assert sent_data["client_id"] == "test-client-id"
        assert sent_data["client_secret"] == "test-client-secret"


# ===========================================================================
# 6. get_user_email tests
# ===========================================================================


class TestGetUserEmail:
    """Tests for GmailAuth.get_user_email."""

    @pytest.mark.asyncio
    async def test_returns_email_on_success(self, auth: GmailAuth, mock_httpx_response) -> None:
        """Successful userinfo request returns the email."""
        mock_resp = mock_httpx_response(
            200, json_data={"email": "user@gmail.com", "verified_email": True}
        )

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("zetherion_ai.skills.gmail.auth.httpx.AsyncClient", return_value=mock_client):
            email = await auth.get_user_email("ya29.access-token")

        assert email == "user@gmail.com"
        # Verify auth header was sent
        call_args = mock_client.get.call_args
        assert call_args[0][0] == GOOGLE_USERINFO_URL
        headers = call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer ya29.access-token"

    @pytest.mark.asyncio
    async def test_empty_email_raises_oauth_error(
        self, auth: GmailAuth, mock_httpx_response
    ) -> None:
        """Empty email in userinfo response raises OAuthError."""
        mock_resp = mock_httpx_response(200, json_data={"email": ""})

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("zetherion_ai.skills.gmail.auth.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(OAuthError, match="No email in userinfo response"):
                await auth.get_user_email("token")

    @pytest.mark.asyncio
    async def test_missing_email_field_raises_oauth_error(
        self, auth: GmailAuth, mock_httpx_response
    ) -> None:
        """Missing email key in userinfo response raises OAuthError."""
        mock_resp = mock_httpx_response(200, json_data={"name": "User"})

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("zetherion_ai.skills.gmail.auth.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(OAuthError, match="No email in userinfo response"):
                await auth.get_user_email("token")

    @pytest.mark.asyncio
    async def test_http_error_raises_oauth_error(
        self, auth: GmailAuth, mock_httpx_response
    ) -> None:
        """HTTP status error on userinfo raises OAuthError."""
        mock_resp = mock_httpx_response(403, text="Forbidden", raise_for_status_error=True)

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("zetherion_ai.skills.gmail.auth.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(OAuthError, match="Userinfo request failed"):
                await auth.get_user_email("token")

    @pytest.mark.asyncio
    async def test_request_error_raises_oauth_error(self, auth: GmailAuth) -> None:
        """Network error on userinfo raises OAuthError."""
        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.RequestError(
            "Timeout", request=MagicMock(spec=httpx.Request)
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("zetherion_ai.skills.gmail.auth.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(OAuthError, match="Userinfo request error"):
                await auth.get_user_email("token")


# ===========================================================================
# 7. Helper function tests
# ===========================================================================


class TestHelperFunctions:
    """Tests for module-level helper functions and internal methods."""

    def test_b64_encode_decode_roundtrip(self) -> None:
        """_b64_encode and _b64_decode are inverse operations."""
        original = '{"user_id": 12345, "extra": "data with spaces & symbols!"}'
        encoded = _b64_encode(original)
        decoded = _b64_decode(encoded)
        assert decoded == original

    def test_b64_encode_produces_url_safe_output(self) -> None:
        """_b64_encode produces URL-safe output without padding."""
        # This string, when base64-encoded normally, would include '+' and '/'
        data = "\xff\xfe\xfd"
        encoded = _b64_encode(data)
        assert "+" not in encoded
        assert "/" not in encoded
        assert "=" not in encoded

    def test_create_state_token_format(self, auth: GmailAuth) -> None:
        """_create_state_token returns payload.timestamp.signature format."""
        token = auth._create_state_token(user_id=777)
        parts = token.split(".")
        assert len(parts) == 3

        # Payload decodes to JSON with user_id
        payload = json.loads(_b64_decode(parts[0]))
        assert payload["user_id"] == 777

        # Timestamp is a valid integer
        timestamp = int(parts[1])
        assert timestamp > 0

        # Signature is a hex string (SHA-256 = 64 hex chars)
        assert len(parts[2]) == 64

    def test_sign_produces_consistent_output(self, auth: GmailAuth) -> None:
        """_sign produces the same output for the same input."""
        sig1 = auth._sign("hello.world")
        sig2 = auth._sign("hello.world")
        assert sig1 == sig2

        # Different input produces different output
        sig3 = auth._sign("different.input")
        assert sig1 != sig3

    def test_sign_matches_hmac_sha256(self, auth: GmailAuth) -> None:
        """_sign output matches manual HMAC-SHA256 computation."""
        message = "test-message"
        expected = hmac.new(
            b"test-state-secret",
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        assert auth._sign(message) == expected

    def test_oauth_error_is_exception(self) -> None:
        """OAuthError is a proper Exception subclass."""
        err = OAuthError("something went wrong")
        assert isinstance(err, Exception)
        assert str(err) == "something went wrong"
