"""Unit tests for CGS gateway upstream clients."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest

import zetherion_ai.cgs_gateway.upstream.public_api_client as public_mod
import zetherion_ai.cgs_gateway.upstream.skills_client as skills_mod
from zetherion_ai.cgs_gateway.upstream.public_api_client import PublicAPIClient
from zetherion_ai.cgs_gateway.upstream.skills_client import SkillsClient


class _DummyResponseCtx:
    def __init__(
        self,
        *,
        status: int,
        headers: dict[str, str] | None = None,
        json_payload: object | None = None,
        text_payload: str = "",
        raw_payload: bytes = b"",
        raise_json: bool = False,
    ) -> None:
        self.status = status
        self.headers = headers or {}
        self._json_payload = json_payload
        self._text_payload = text_payload
        self._raw_payload = raw_payload
        self._raise_json = raise_json

    async def __aenter__(self) -> _DummyResponseCtx:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    async def json(self) -> object:
        if self._raise_json:
            raise ValueError("not json")
        return self._json_payload

    async def text(self) -> str:
        return self._text_payload

    async def read(self) -> bytes:
        return self._raw_payload


class _DummyJSONSession:
    def __init__(self, response: _DummyResponseCtx) -> None:
        self._response = response
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def request(self, *args: object, **kwargs: object) -> _DummyResponseCtx:
        self.calls.append((args, kwargs))
        return self._response


class _FailoverJSONSession:
    def __init__(self, failing_url: str, success_response: _DummyResponseCtx) -> None:
        self.failing_url = failing_url
        self.success_response = success_response
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def request(self, *args: object, **kwargs: object) -> _DummyResponseCtx:
        self.calls.append((args, kwargs))
        if len(args) > 1 and args[1] == self.failing_url:
            raise aiohttp.ClientConnectionError("dial failed")
        return self.success_response


class _DummyAsyncStreamSession:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def request(self, *args: object, **kwargs: object) -> object:
        self.calls.append((args, kwargs))
        return self.response


@pytest.mark.asyncio
async def test_public_and_skills_clients_start_and_close(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_session = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(public_mod.aiohttp, "ClientSession", lambda **_kwargs: fake_session)
    client = PublicAPIClient(base_url="https://api.example")
    await client.start()
    assert client.session is fake_session
    await client.close()
    fake_session.close.assert_awaited_once()

    fake_session2 = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(skills_mod.aiohttp, "ClientSession", lambda **_kwargs: fake_session2)
    skills = SkillsClient(base_url="https://skills.example", api_secret="secret")
    await skills.start()
    assert skills.session is fake_session2
    await skills.close()
    fake_session2.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_public_api_client_request_json_success_and_text_fallback() -> None:
    client = PublicAPIClient(base_url="https://api.example")

    session_ok = _DummyJSONSession(
        _DummyResponseCtx(
            status=200,
            headers={"x-header": "value"},
            json_payload={"ok": True},
        )
    )
    client._session = session_ok  # type: ignore[assignment]

    status, payload, headers = await client.request_json(
        "GET",
        "/health",
        headers={"X-Test": "1"},
    )
    assert status == 200
    assert payload == {"ok": True}
    assert headers == {"x-header": "value"}
    assert session_ok.calls[0][0][0] == "GET"
    assert session_ok.calls[0][0][1] == "https://api.example/health"

    session_text = _DummyJSONSession(
        _DummyResponseCtx(status=502, raise_json=True, text_payload="upstream-error")
    )
    client._session = session_text  # type: ignore[assignment]
    status2, payload2, _ = await client.request_json("POST", "v1/chat")
    assert status2 == 502
    assert payload2 == "upstream-error"


@pytest.mark.asyncio
async def test_public_api_client_retries_secondary_base_url() -> None:
    client = PublicAPIClient(base_url="https://api-green.example,https://api-blue.example")
    session = _FailoverJSONSession(
        "https://api-green.example/health",
        _DummyResponseCtx(status=200, json_payload={"ok": True}),
    )
    client._session = session  # type: ignore[assignment]

    status, payload, _headers = await client.request_json("GET", "/health")

    assert status == 200
    assert payload == {"ok": True}
    assert client._base_url == "https://api-blue.example"
    assert [call[0][1] for call in session.calls] == [
        "https://api-green.example/health",
        "https://api-blue.example/health",
    ]


@pytest.mark.asyncio
async def test_public_api_client_open_stream_and_session_guard() -> None:
    client = PublicAPIClient(base_url="https://api.example")
    with pytest.raises(RuntimeError, match="not started"):
        _ = client.session

    response = SimpleNamespace(status=200)
    session = _DummyAsyncStreamSession(response=response)
    client._session = session  # type: ignore[assignment]

    opened = await client.open_stream("POST", "/stream", json_body={"x": 1})
    assert opened is response
    assert session.calls[0][0][0] == "POST"
    assert session.calls[0][0][1] == "https://api.example/stream"


@pytest.mark.asyncio
async def test_public_api_client_request_raw() -> None:
    client = PublicAPIClient(base_url="https://api.example")
    session = _DummyJSONSession(
        _DummyResponseCtx(
            status=200,
            headers={"content-type": "application/pdf"},
            raw_payload=b"%PDF-1.7",
        )
    )
    client._session = session  # type: ignore[assignment]

    status, payload, headers = await client.request_raw("GET", "/files/1")
    assert status == 200
    assert payload == b"%PDF-1.7"
    assert headers == {"content-type": "application/pdf"}


@pytest.mark.asyncio
async def test_skills_client_handle_intent_success_and_text_fallback() -> None:
    client = SkillsClient(base_url="https://skills.example", api_secret="secret-1")

    session_ok = _DummyJSONSession(
        _DummyResponseCtx(status=200, json_payload={"ok": True, "data": {"k": "v"}})
    )
    client._session = session_ok  # type: ignore[assignment]

    status, payload = await client.handle_intent(
        intent="client_create",
        context={"name": "Tenant A"},
        user_id="operator-1",
        request_id="req-1",
    )
    assert status == 200
    assert payload == {"ok": True, "data": {"k": "v"}}
    call_args, call_kwargs = session_ok.calls[0]
    assert call_args[0] == "POST"
    assert call_args[1] == "https://skills.example/handle"
    assert call_kwargs["headers"]["X-API-Secret"] == "secret-1"
    assert call_kwargs["json"]["intent"] == "client_create"

    session_text = _DummyJSONSession(
        _DummyResponseCtx(status=500, raise_json=True, text_payload="bad-gateway")
    )
    client._session = session_text  # type: ignore[assignment]
    status2, payload2 = await client.handle_intent(
        intent="client_configure",
        context={},
        user_id="operator-1",
    )
    assert status2 == 500
    assert payload2 == "bad-gateway"


@pytest.mark.asyncio
async def test_skills_client_request_admin_json_signs_actor_context() -> None:
    client = SkillsClient(base_url="https://skills.example", api_secret="secret-1")
    session = _DummyJSONSession(_DummyResponseCtx(status=200, json_payload={"ok": True}))
    client._session = session  # type: ignore[assignment]

    status, payload = await client.request_admin_json(
        "PUT",
        "/admin/tenants/t-1/secrets/OPENAI_API_KEY",
        actor={
            "actor_sub": "operator-1",
            "actor_roles": ["operator"],
            "request_id": "req-1",
            "timestamp": "2026-03-03T12:00:00+00:00",
            "nonce": "n-1",
        },
        json_body={"value": "secret"},
    )
    assert status == 200
    assert payload == {"ok": True}
    call_args, call_kwargs = session.calls[0]
    assert call_args[0] == "PUT"
    assert call_args[1] == "https://skills.example/admin/tenants/t-1/secrets/OPENAI_API_KEY"
    headers = call_kwargs["headers"]
    assert headers["X-API-Secret"] == "secret-1"
    assert "X-Admin-Actor" in headers
    assert "X-Admin-Signature" in headers
