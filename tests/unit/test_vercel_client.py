from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from zetherion_ai.skills.vercel.client import VercelAPIError, VercelAuthError, VercelClient


def _response(
    *,
    status_code: int = 200,
    json_data: object | None = None,
    text: str = "",
) -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.text = text
    if isinstance(json_data, Exception):
        response.json.side_effect = json_data
    else:
        response.json.return_value = json_data
    return response


@pytest.fixture
def vercel_http_client() -> MagicMock:
    with patch("zetherion_ai.skills.vercel.client.httpx.AsyncClient") as mock_cls:
        client = MagicMock()
        client.is_closed = False
        client.get = AsyncMock()
        client.aclose = AsyncMock()
        mock_cls.return_value = client
        yield client


@pytest.mark.asyncio
async def test_request_reuses_client_and_closes(vercel_http_client: MagicMock) -> None:
    vercel_http_client.get.return_value = _response(json_data={"id": "proj_1"})
    client = VercelClient("token-123")

    result = await client.get_project("proj_1", team_id=" team_1 ")
    second_http_client = await client._get_client()

    assert result == {"id": "proj_1"}
    assert second_http_client is vercel_http_client
    _, kwargs = vercel_http_client.get.await_args
    assert kwargs["params"] == {"teamId": "team_1"}

    await client.close()
    vercel_http_client.aclose.assert_awaited_once()
    assert client._client is None


@pytest.mark.asyncio
async def test_request_raises_typed_errors(vercel_http_client: MagicMock) -> None:
    client = VercelClient("token-123")

    vercel_http_client.get.side_effect = httpx.RequestError(
        "boom",
        request=httpx.Request("GET", "https://vercel.example/v9/projects/proj_1"),
    )
    with pytest.raises(VercelAPIError, match="Request failed: boom"):
        await client.get_project("proj_1")

    vercel_http_client.get.side_effect = None
    vercel_http_client.get.return_value = _response(status_code=401, json_data={"error": {}})
    with pytest.raises(VercelAuthError, match="Authentication failed"):
        await client.get_project("proj_1")

    vercel_http_client.get.return_value = _response(
        status_code=404,
        json_data={"error": {"message": "Missing project"}},
    )
    with pytest.raises(VercelAPIError, match="Missing project") as exc_info:
        await client.get_project("proj_1")
    assert exc_info.value.status_code == 404

    vercel_http_client.get.return_value = _response(json_data=["bad"])
    with pytest.raises(VercelAPIError, match="Unexpected Vercel response format"):
        await client.get_project("proj_1")


def test_params_only_includes_non_empty_team_id() -> None:
    assert VercelClient._params(None) == {}
    assert VercelClient._params("   ") == {}
    assert VercelClient._params(" team_1 ") == {"teamId": "team_1"}


@pytest.mark.asyncio
async def test_get_project_and_deployment_validate_required_ids(
    vercel_http_client: MagicMock,
) -> None:
    client = VercelClient("token-123")

    with pytest.raises(ValueError, match="project_ref is required"):
        await client.get_project("")
    with pytest.raises(ValueError, match="deployment_id is required"):
        await client.get_deployment("")


@pytest.mark.asyncio
async def test_list_deployments_builds_params_and_filters_rows(
    vercel_http_client: MagicMock,
) -> None:
    vercel_http_client.get.return_value = _response(
        json_data={"deployments": [{"id": "dep_1"}, "skip"]},
    )
    client = VercelClient("token-123")

    rows = await client.list_deployments(
        project_id="proj_1",
        project_name="ignored",
        team_id="team_1",
        limit=999,
    )

    assert rows == [{"id": "dep_1"}]
    _, kwargs = vercel_http_client.get.await_args
    assert kwargs["params"] == {
        "teamId": "team_1",
        "limit": 20,
        "projectId": "proj_1",
    }

    vercel_http_client.get.return_value = _response(json_data={"deployments": "bad"})
    assert await client.list_deployments(project_name="project-name", limit=0) == []


@pytest.mark.asyncio
async def test_list_domain_env_and_event_views_filter_non_list_payloads(
    vercel_http_client: MagicMock,
) -> None:
    vercel_http_client.get.side_effect = [
        _response(json_data={"domains": [{"name": "example.com"}, "skip"]}),
        _response(json_data={"envs": [{"key": "API_KEY"}, "skip"]}),
        _response(json_data={"events": [{"id": "evt_1"}, "skip"]}),
        _response(json_data={"logs": [{"id": "log_1"}, "skip"]}),
        _response(json_data={"events": "bad"}),
    ]
    client = VercelClient("token-123")

    assert await client.list_domains("proj_1", team_id="team_1") == [{"name": "example.com"}]
    assert await client.list_env_vars("proj_1", team_id="team_1") == [{"key": "API_KEY"}]
    assert await client.get_deployment_events("dep_1", team_id="team_1", limit=999) == [
        {"id": "evt_1"}
    ]
    assert await client.get_deployment_events("dep_1") == [{"id": "log_1"}]
    assert await client.get_deployment_events("dep_1") == []

    request_calls = vercel_http_client.get.await_args_list
    assert request_calls[0].kwargs["params"] == {"teamId": "team_1"}
    assert request_calls[1].kwargs["params"] == {"teamId": "team_1", "decrypt": "false"}
    assert request_calls[2].kwargs["params"] == {"teamId": "team_1", "limit": 200}
    assert request_calls[3].kwargs["params"] == {"limit": 50}

    with pytest.raises(ValueError, match="deployment_id is required"):
        await client.get_deployment_events("")
