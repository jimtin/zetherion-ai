from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from zetherion_ai.skills.clerk.client import ClerkMetadataClient, ClerkMetadataError


def _response(
    *,
    status_code: int = 200,
    json_data: object | None = None,
) -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    if isinstance(json_data, Exception):
        response.json.side_effect = json_data
    else:
        response.json.return_value = json_data
    return response


@pytest.fixture
def clerk_http_client() -> MagicMock:
    with patch("zetherion_ai.skills.clerk.client.httpx.AsyncClient") as mock_cls:
        client = MagicMock()
        client.is_closed = False
        client.get = AsyncMock()
        client.aclose = AsyncMock()
        mock_cls.return_value = client
        yield client


@pytest.mark.asyncio
async def test_get_json_reuses_client_and_closes(clerk_http_client: MagicMock) -> None:
    clerk_http_client.get.return_value = _response(json_data={"keys": []})
    client = ClerkMetadataClient(timeout=10)

    payload = await client.get_jwks("https://clerk.example/jwks")
    second_http_client = await client._get_client()

    assert payload == {"keys": []}
    assert second_http_client is clerk_http_client
    clerk_http_client.get.assert_awaited_once_with("https://clerk.example/jwks")

    await client.close()
    clerk_http_client.aclose.assert_awaited_once()
    assert client._client is None


@pytest.mark.asyncio
async def test_get_json_raises_on_request_status_and_format_errors(
    clerk_http_client: MagicMock,
) -> None:
    client = ClerkMetadataClient()

    clerk_http_client.get.side_effect = httpx.RequestError(
        "boom",
        request=httpx.Request("GET", "https://clerk.example/jwks"),
    )
    with pytest.raises(ClerkMetadataError, match="Request failed: boom"):
        await client.get_jwks("https://clerk.example/jwks")

    clerk_http_client.get.side_effect = None
    clerk_http_client.get.return_value = _response(status_code=503, json_data={"error": "down"})
    with pytest.raises(ClerkMetadataError, match="Metadata request failed with HTTP 503"):
        await client.get_jwks("https://clerk.example/jwks")

    clerk_http_client.get.return_value = _response(json_data=["bad"])
    with pytest.raises(ClerkMetadataError, match="Unexpected Clerk metadata response format"):
        await client.get_jwks("https://clerk.example/jwks")


@pytest.mark.asyncio
async def test_get_openid_configuration_builds_well_known_url(
    clerk_http_client: MagicMock,
) -> None:
    clerk_http_client.get.return_value = _response(json_data={"issuer": "https://clerk.example"})
    client = ClerkMetadataClient()

    payload = await client.get_openid_configuration("https://clerk.example/")

    assert payload == {"issuer": "https://clerk.example"}
    clerk_http_client.get.assert_awaited_once_with(
        "https://clerk.example/.well-known/openid-configuration"
    )
