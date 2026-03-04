"""Targeted helper coverage for CGS runtime routes."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from zetherion_ai.cgs_gateway.errors import GatewayError
from zetherion_ai.cgs_gateway.models import DocumentQueryRequest
from zetherion_ai.cgs_gateway.routes import runtime as runtime_routes
from zetherion_ai.cgs_gateway.routes._utils import fingerprint_payload


def _request(
    *,
    method: str = "POST",
    path: str = "/service/ai/v1/test",
    app: web.Application | None = None,
    headers: dict[str, str] | None = None,
) -> web.Request:
    request = make_mocked_request(
        method,
        path,
        headers=headers or {},
        app=app or web.Application(),
    )
    request["request_id"] = "req_runtime_helpers"
    return request


def test_runtime_normalization_helpers_cover_aliases_and_fallbacks() -> None:
    assert runtime_routes._normalize_document_status("queued") == "processing"
    assert runtime_routes._normalize_document_status("ready") == "indexed"
    assert runtime_routes._normalize_document_status("unexpected-status") == "processing"

    payload = {
        "status": "complete",
        "items": [
            {"status": "pending"},
            {"status": "weird-value"},
            {"other": "x"},
        ],
    }
    normalized = runtime_routes._normalize_document_payload(payload)
    assert normalized["status"] == "indexed"
    assert normalized["items"][0]["status"] == "processing"
    assert normalized["items"][1]["status"] == "processing"
    assert normalized["items"][2]["other"] == "x"

    assert runtime_routes._normalize_provider(None) is None
    assert runtime_routes._normalize_provider("   ") is None
    assert runtime_routes._normalize_provider("claude") == "anthropic"
    assert runtime_routes._normalize_provider("openai") == "openai"

    catalog = runtime_routes._normalize_provider_catalog(
        {
            "providers": ["claude", "anthropic", "", "openai", " openai "],
            "defaults": {"claude": "m1", "": "skip", "openai": "o1"},
        }
    )
    assert catalog["providers"] == ["anthropic", "openai"]
    assert catalog["defaults"] == {"anthropic": "m1", "openai": "o1"}
    assert runtime_routes._normalize_provider_catalog(["not-a-dict"]) == ["not-a-dict"]


def test_runtime_rag_allowlist_and_alias_enforcement() -> None:
    app = web.Application()
    app["cgs_rag_allowed_providers"] = {"openai", "anthropic"}
    app["cgs_rag_allowed_models"] = {"allowed-model"}
    request = _request(app=app)

    aliased_payload = DocumentQueryRequest(
        tenant_id="tenant-a",
        query="hello",
        provider="claude",
        model=" allowed-model ",
    )
    upstream = runtime_routes._build_rag_upstream_body(request, aliased_payload)
    assert upstream["provider"] == "anthropic"
    assert upstream["model"] == "allowed-model"

    bad_provider = DocumentQueryRequest(
        tenant_id="tenant-a",
        query="hello",
        provider="groq",
    )
    with pytest.raises(GatewayError, match="provider is not allowed"):
        runtime_routes._build_rag_upstream_body(request, bad_provider)

    bad_model = DocumentQueryRequest(
        tenant_id="tenant-a",
        query="hello",
        provider="openai",
        model="disallowed",
    )
    with pytest.raises(GatewayError, match="model is not allowed"):
        runtime_routes._build_rag_upstream_body(request, bad_model)

    default_request = _request()
    assert runtime_routes._allowed_providers(default_request) == {
        "anthropic",
        "groq",
        "openai",
    }
    assert runtime_routes._allowed_models(default_request) == set()


@pytest.mark.asyncio
async def test_runtime_public_request_typed_and_fallback_paths() -> None:
    client = MagicMock()
    client.request_json = AsyncMock(return_value=(201, {"fallback": True}, {"x": "1"}))
    client.request_raw = AsyncMock(return_value=(202, b"fallback", {"x": "2"}))

    async def typed_json(**_: object) -> tuple[int, dict[str, bool], dict[str, str]]:
        return 200, {"typed": True}, {"y": "1"}

    async def typed_raw(**_: object) -> tuple[int, bytes, dict[str, str]]:
        return 200, b"typed", {"y": "2"}

    client.typed_json = typed_json
    client.typed_raw = typed_raw
    client.not_async = MagicMock(return_value=(204, {}, {}))

    app = web.Application()
    app["cgs_public_client"] = client
    request = _request(app=app)

    status, payload, headers = await runtime_routes._public_request_json(
        request,
        method="POST",
        path="/x",
        headers={"A": "B"},
        typed_method="typed_json",
        typed_kwargs={"payload": {"k": "v"}},
    )
    assert status == 200
    assert payload == {"typed": True}
    assert headers == {"y": "1"}

    status, payload, headers = await runtime_routes._public_request_json(
        request,
        method="POST",
        path="/x",
        headers={"A": "B"},
        typed_method="not_async",
    )
    assert status == 201
    assert payload == {"fallback": True}
    assert headers == {"x": "1"}
    client.request_json.assert_awaited_once()

    status, payload, headers = await runtime_routes._public_request_raw(
        request,
        method="GET",
        path="/raw",
        headers={},
        typed_method="typed_raw",
        typed_kwargs={"suffix": "preview"},
    )
    assert status == 200
    assert payload == b"typed"
    assert headers == {"y": "2"}

    status, payload, headers = await runtime_routes._public_request_raw(
        request,
        method="GET",
        path="/raw",
        headers={},
        typed_method="not_async",
    )
    assert status == 202
    assert payload == b"fallback"
    assert headers == {"x": "2"}
    client.request_raw.assert_awaited_once()


@pytest.mark.asyncio
async def test_runtime_idempotency_helpers_conflict_cached_and_save_paths() -> None:
    storage = MagicMock()
    storage.get_idempotency_record = AsyncMock(return_value=None)
    storage.save_idempotency_record = AsyncMock(return_value=None)

    app = web.Application()
    app["cgs_storage"] = storage

    no_key_request = _request(app=app)
    key, fingerprint, cached = await runtime_routes._idempotency_check(
        no_key_request,
        cgs_tenant_id="tenant-a",
        payload={"k": "v"},
    )
    assert key is None
    assert fingerprint is None
    assert cached is None

    request = _request(app=app, headers={"Idempotency-Key": "idem-1"})
    payload = {"k": "v"}
    expected_fp = fingerprint_payload(payload)

    key, fingerprint, cached = await runtime_routes._idempotency_check(
        request,
        cgs_tenant_id="tenant-a",
        payload=payload,
    )
    assert key == "idem-1"
    assert fingerprint == expected_fp
    assert cached is None

    storage.get_idempotency_record = AsyncMock(
        return_value={
            "request_fingerprint": "different",
            "response_status": 200,
            "response_body": {"request_id": "old", "data": {}, "error": None},
        }
    )
    with pytest.raises(GatewayError, match="Idempotency key already used"):
        await runtime_routes._idempotency_check(
            request,
            cgs_tenant_id="tenant-a",
            payload=payload,
        )

    storage.get_idempotency_record = AsyncMock(
        return_value={
            "request_fingerprint": expected_fp,
            "response_status": 202,
            "response_body": "not-an-object",
        }
    )
    _, _, cached = await runtime_routes._idempotency_check(
        request,
        cgs_tenant_id="tenant-a",
        payload=payload,
    )
    assert cached is not None
    assert cached["status"] == 202
    assert cached["body"]["request_id"] == "req_runtime_helpers"
    assert cached["body"]["data"] is None

    await runtime_routes._save_idempotency(
        request,
        cgs_tenant_id="tenant-a",
        idempotency_key=None,
        request_fingerprint=expected_fp,
        response_status=200,
        response_body={"request_id": "r", "data": {}, "error": None},
    )
    storage.save_idempotency_record.assert_not_awaited()

    await runtime_routes._save_idempotency(
        request,
        cgs_tenant_id="tenant-a",
        idempotency_key="idem-1",
        request_fingerprint=expected_fp,
        response_status=200,
        response_body={"request_id": "r", "data": {}, "error": None},
    )
    storage.save_idempotency_record.assert_awaited_once()
