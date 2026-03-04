"""Unit tests for CGS gateway core modules (errors, middleware, server)."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from pydantic import SecretStr

import zetherion_ai.cgs_gateway.middleware as middleware_mod
import zetherion_ai.cgs_gateway.server as server_mod
from zetherion_ai.cgs_gateway.errors import (
    GatewayError,
    error_response,
    from_exception,
    success_response,
)
from zetherion_ai.cgs_gateway.middleware import (
    JWTVerifier,
    create_auth_middleware,
    create_cors_middleware,
    create_request_context_middleware,
    principal_is_operator,
)
from zetherion_ai.cgs_gateway.models import AuthPrincipal
from zetherion_ai.cgs_gateway.server import (
    CGSGatewayServer,
    _split_csv,
    create_error_middleware,
    create_request_logging_middleware,
)


def _response_json(response: web.Response) -> dict[str, object]:
    return json.loads(response.text)


def test_success_and_error_response_helpers() -> None:
    ok = success_response("req-1", {"x": 1}, status=201)
    assert ok.status == 201
    assert _response_json(ok) == {"request_id": "req-1", "data": {"x": 1}, "error": None}

    err = error_response(
        "req-2",
        code="AI_BAD_REQUEST",
        message="bad input",
        status=400,
        details={"field": "tenant_id"},
    )
    assert err.status == 400
    assert _response_json(err) == {
        "request_id": "req-2",
        "data": None,
        "error": {
            "code": "AI_BAD_REQUEST",
            "message": "bad input",
            "retryable": False,
            "details": {"field": "tenant_id"},
        },
    }


def test_from_exception_maps_gateway_and_generic() -> None:
    mapped = from_exception(
        "req-3",
        GatewayError(code="AI_AUTH_MISSING", message="missing", status=401),
    )
    assert mapped.status == 401
    assert _response_json(mapped)["error"] == {
        "code": "AI_AUTH_MISSING",
        "message": "missing",
        "retryable": False,
        "details": {},
    }

    generic = from_exception("req-4", RuntimeError("boom"))
    assert generic.status == 500
    assert _response_json(generic)["error"] == {
        "code": "AI_INTERNAL_ERROR",
        "message": "Unexpected gateway error",
        "retryable": True,
        "details": {},
    }


def test_jwt_verifier_success(monkeypatch: pytest.MonkeyPatch) -> None:
    class DummyJWKClient:
        def __init__(self, _: str) -> None:
            pass

        def get_signing_key_from_jwt(self, _: str) -> SimpleNamespace:
            return SimpleNamespace(key="public-key")

    def fake_decode(token: str, key: str, **kwargs: object) -> dict[str, object]:
        assert token == "valid-token"
        assert key == "public-key"
        assert kwargs["issuer"] == "issuer-1"
        assert kwargs["audience"] == "aud-1"
        return {
            "sub": "user-1",
            "tenant_id": "tenant-a",
            "roles": "operator admin",
            "scope": "cgs:internal cgs:foo",
            "scopes": ["cgs:internal", "cgs:bar"],
        }

    monkeypatch.setattr(middleware_mod.jwt, "PyJWKClient", DummyJWKClient)
    monkeypatch.setattr(middleware_mod.jwt, "decode", fake_decode)

    verifier = JWTVerifier(jwks_url="https://issuer/jwks", issuer="issuer-1", audience="aud-1")
    principal = verifier.verify("valid-token")

    assert principal.sub == "user-1"
    assert principal.tenant_id == "tenant-a"
    assert sorted(principal.roles) == ["admin", "operator"]
    assert set(principal.scopes) == {"cgs:bar", "cgs:foo", "cgs:internal"}


def test_jwt_verifier_invalid_token(monkeypatch: pytest.MonkeyPatch) -> None:
    class DummyJWKClient:
        def __init__(self, _: str) -> None:
            pass

        def get_signing_key_from_jwt(self, _: str) -> SimpleNamespace:
            return SimpleNamespace(key="public-key")

    def fake_decode(_: str, __: str, **___: object) -> dict[str, object]:
        raise ValueError("invalid")

    monkeypatch.setattr(middleware_mod.jwt, "PyJWKClient", DummyJWKClient)
    monkeypatch.setattr(middleware_mod.jwt, "decode", fake_decode)

    verifier = JWTVerifier(jwks_url="https://issuer/jwks")
    with pytest.raises(GatewayError, match="Invalid or expired auth token"):
        verifier.verify("bad-token")


def test_jwt_verifier_handles_roles_list_and_optional_scope_shapes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DummyJWKClient:
        def __init__(self, _: str) -> None:
            pass

        def get_signing_key_from_jwt(self, _: str) -> SimpleNamespace:
            return SimpleNamespace(key="public-key")

    monkeypatch.setattr(middleware_mod.jwt, "PyJWKClient", DummyJWKClient)
    monkeypatch.setattr(
        middleware_mod.jwt,
        "decode",
        lambda *_args, **_kwargs: {
            "sub": "user-2",
            "cgs_tenant_id": "tenant-b",
            "roles": ["viewer", 7],
            "scope": ["unexpected-shape"],
            "scopes": "unexpected-shape",
        },
    )

    verifier = JWTVerifier(jwks_url="https://issuer/jwks")
    principal = verifier.verify("token-2")

    assert principal.sub == "user-2"
    assert principal.tenant_id == "tenant-b"
    assert principal.roles == ["viewer", "7"]
    assert principal.scopes == []


def test_jwt_verifier_handles_non_collection_roles(monkeypatch: pytest.MonkeyPatch) -> None:
    class DummyJWKClient:
        def __init__(self, _: str) -> None:
            pass

        def get_signing_key_from_jwt(self, _: str) -> SimpleNamespace:
            return SimpleNamespace(key="public-key")

    monkeypatch.setattr(middleware_mod.jwt, "PyJWKClient", DummyJWKClient)
    monkeypatch.setattr(
        middleware_mod.jwt,
        "decode",
        lambda *_args, **_kwargs: {"sub": "user-3", "roles": {"admin": True}},
    )

    verifier = JWTVerifier(jwks_url="https://issuer/jwks")
    principal = verifier.verify("token-3")

    assert principal.sub == "user-3"
    assert principal.roles == []


@pytest.mark.asyncio
async def test_request_context_middleware_sets_and_propagates_request_id() -> None:
    app = web.Application(middlewares=[create_request_context_middleware()])
    app.router.add_get("/ok", lambda _: web.json_response({"ok": True}))

    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/ok")
        assert resp.status == 200
        assert resp.headers["X-Request-Id"].startswith("req_")

        resp_with_header = await client.get("/ok", headers={"X-Request-Id": "req-custom"})
        assert resp_with_header.status == 200
        assert resp_with_header.headers["X-Request-Id"] == "req-custom"


@pytest.mark.asyncio
async def test_cors_middleware_adds_headers_and_handles_options() -> None:
    app = web.Application(middlewares=[create_cors_middleware(["https://app.example"])])
    app.router.add_get("/ok", lambda _: web.json_response({"ok": True}))
    app.router.add_options("/ok", lambda _: web.Response(status=204))

    async with TestClient(TestServer(app)) as client:
        get_resp = await client.get("/ok", headers={"Origin": "https://app.example"})
        assert get_resp.status == 200
        assert get_resp.headers["Access-Control-Allow-Origin"] == "https://app.example"

        options_resp = await client.options("/ok", headers={"Origin": "https://app.example"})
        assert options_resp.status == 204
        assert options_resp.headers["Access-Control-Allow-Origin"] == "https://app.example"


@pytest.mark.asyncio
async def test_cors_middleware_without_allowed_origins_omits_headers() -> None:
    app = web.Application(middlewares=[create_cors_middleware(None)])
    app.router.add_get("/ok", lambda _: web.json_response({"ok": True}))

    async with TestClient(TestServer(app)) as client:
        get_resp = await client.get("/ok", headers={"Origin": "https://app.example"})
        assert get_resp.status == 200
        assert "Access-Control-Allow-Origin" not in get_resp.headers


@pytest.mark.asyncio
async def test_auth_middleware_missing_and_valid_token() -> None:
    verifier = MagicMock()
    verifier.verify.return_value = AuthPrincipal(sub="user-1", tenant_id="tenant-a", claims={})

    @web.middleware
    async def assert_principal(request: web.Request, handler):
        if request.path == "/service/ai/v1/private":
            assert request["principal"].sub == "user-1"
        return await handler(request)

    app = web.Application(
        middlewares=[
            create_request_context_middleware(),
            create_auth_middleware(verifier),
            assert_principal,
        ]
    )
    app.router.add_get("/service/ai/v1/health", lambda _: web.json_response({"ok": True}))
    app.router.add_get("/service/ai/v1/private", lambda _: web.json_response({"ok": True}))

    async with TestClient(TestServer(app)) as client:
        missing = await client.get("/service/ai/v1/private")
        assert missing.status == 401
        missing_body = await missing.json()
        assert missing_body["error"]["code"] == "AI_AUTH_MISSING"

        public = await client.get("/service/ai/v1/health")
        assert public.status == 200

        authed = await client.get(
            "/service/ai/v1/private",
            headers={"Authorization": "Bearer good-token"},
        )
        assert authed.status == 200
        verifier.verify.assert_called_once_with("good-token")


@pytest.mark.asyncio
async def test_auth_middleware_surfaces_gateway_error_from_verifier() -> None:
    verifier = MagicMock()
    verifier.verify.side_effect = GatewayError(
        code="AI_AUTH_INVALID_TOKEN",
        message="Invalid or expired auth token",
        status=401,
    )

    app = web.Application(
        middlewares=[
            create_request_context_middleware(),
            create_auth_middleware(verifier),
        ]
    )
    app.router.add_get("/service/ai/v1/private", lambda _: web.json_response({"ok": True}))

    async with TestClient(TestServer(app)) as client:
        resp = await client.get(
            "/service/ai/v1/private",
            headers={"Authorization": "Bearer bad-token"},
        )
        assert resp.status == 401
        body = await resp.json()
        assert body["error"]["code"] == "AI_AUTH_INVALID_TOKEN"


def test_principal_is_operator_role_or_scope() -> None:
    assert principal_is_operator(AuthPrincipal(sub="u1", roles=["admin"], claims={}))
    assert principal_is_operator(AuthPrincipal(sub="u2", scopes=["cgs:internal"], claims={}))
    assert not principal_is_operator(AuthPrincipal(sub="u3", roles=["viewer"], claims={}))


def test_jwt_verifier_requires_jwks_url() -> None:
    with pytest.raises(ValueError, match="CGS_AUTH_JWKS_URL is required"):
        JWTVerifier(jwks_url="")


def test_jwt_verifier_rejects_missing_subject(monkeypatch: pytest.MonkeyPatch) -> None:
    class DummyJWKClient:
        def __init__(self, _: str) -> None:
            pass

        def get_signing_key_from_jwt(self, _: str) -> SimpleNamespace:
            return SimpleNamespace(key="public-key")

    monkeypatch.setattr(middleware_mod.jwt, "PyJWKClient", DummyJWKClient)
    monkeypatch.setattr(middleware_mod.jwt, "decode", lambda *_args, **_kwargs: {"scope": "x"})

    verifier = JWTVerifier(jwks_url="https://issuer/jwks")
    with pytest.raises(GatewayError, match="Token subject is missing"):
        verifier.verify("missing-sub")


@pytest.mark.asyncio
async def test_error_middleware_returns_envelope_for_exception() -> None:
    async def raise_error(_: web.Request) -> web.Response:
        raise RuntimeError("boom")

    app = web.Application(
        middlewares=[create_request_context_middleware(), create_error_middleware()]
    )
    app.router.add_get("/explode", raise_error)

    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/explode")
        assert resp.status == 500
        body = await resp.json()
        assert body["error"]["code"] == "AI_INTERNAL_ERROR"


@pytest.mark.asyncio
async def test_request_logging_middleware_logs_request_metadata() -> None:
    storage = MagicMock()
    storage.log_request = AsyncMock()

    app = web.Application(
        middlewares=[
            create_request_context_middleware(),
            create_request_logging_middleware(),
        ]
    )
    app["cgs_storage"] = storage
    app.router.add_get(
        "/service/ai/v1/tenants/{tenant_id}/conversations/{conversation_id}",
        lambda _: web.json_response({"ok": True}),
    )

    async with TestClient(TestServer(app)) as client:
        resp = await client.get(
            "/service/ai/v1/tenants/tenant-a/conversations/cgs_conv_1",
            headers={"X-Request-Id": "req-log-1"},
        )
        assert resp.status == 200

    storage.log_request.assert_awaited_once()
    kwargs = storage.log_request.await_args.kwargs
    assert kwargs["request_id"] == "req-log-1"
    assert kwargs["endpoint"] == "/service/ai/v1/tenants/tenant-a/conversations/cgs_conv_1"
    assert kwargs["details"] == {"tenant_id": "tenant-a", "conversation_id": "cgs_conv_1"}


@pytest.mark.asyncio
async def test_gateway_server_create_app_and_start_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    storage = MagicMock()
    storage.initialize = AsyncMock()
    storage.close = AsyncMock()

    public_client = MagicMock()
    public_client.start = AsyncMock()
    public_client.close = AsyncMock()

    skills_client = MagicMock()
    skills_client.start = AsyncMock()
    skills_client.close = AsyncMock()

    verifier = MagicMock()
    verifier.verify.return_value = AuthPrincipal(sub="u1", tenant_id="tenant-a", claims={})

    started: dict[str, object] = {}

    class DummySite:
        def __init__(self, runner: web.AppRunner, host: str, port: int) -> None:
            started["runner"] = runner
            started["host"] = host
            started["port"] = port

        async def start(self) -> None:
            started["started"] = True

    monkeypatch.setattr(server_mod.web, "TCPSite", DummySite)

    server = CGSGatewayServer(
        host="127.0.0.1",
        port=8743,
        allowed_origins=["https://app.example"],
        jwt_verifier=verifier,
        storage=storage,
        public_client=public_client,
        skills_client=skills_client,
    )
    app = server.create_app()

    async with TestClient(TestServer(app)) as client:
        health = await client.get("/service/ai/v1/health")
        assert health.status == 200

        unauthenticated = await client.post("/service/ai/v1/conversations", json={})
        assert unauthenticated.status == 401

        authenticated = await client.post(
            "/service/ai/v1/conversations",
            headers={"Authorization": "Bearer valid-token"},
            json={},
        )
        assert authenticated.status == 400

    await server.start()
    assert started["started"] is True
    assert started["host"] == "127.0.0.1"
    assert started["port"] == 8743
    storage.initialize.assert_awaited_once()
    public_client.start.assert_awaited_once()
    skills_client.start.assert_awaited_once()

    await server.stop()
    skills_client.close.assert_awaited_once()
    public_client.close.assert_awaited_once()
    storage.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_server_starts_and_stops_on_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_server = MagicMock()
    fake_server.start = AsyncMock()
    fake_server.stop = AsyncMock()

    monkeypatch.setattr(
        server_mod,
        "KeyManager",
        lambda *_args, **_kwargs: SimpleNamespace(key=b"k"),
    )
    monkeypatch.setattr(server_mod, "FieldEncryptor", lambda **_kwargs: SimpleNamespace())
    monkeypatch.setattr(server_mod, "CGSGatewayStorage", lambda **_kwargs: SimpleNamespace())
    monkeypatch.setattr(server_mod, "PublicAPIClient", lambda **_kwargs: SimpleNamespace())
    monkeypatch.setattr(server_mod, "SkillsClient", lambda **_kwargs: SimpleNamespace())
    monkeypatch.setattr(server_mod, "JWTVerifier", lambda **_kwargs: SimpleNamespace())
    monkeypatch.setattr(server_mod, "CGSGatewayServer", lambda **_kwargs: fake_server)

    async def cancel_sleep(_: int) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(server_mod.asyncio, "sleep", cancel_sleep)

    await server_mod.run_server(
        host="127.0.0.1",
        port=8743,
        allowed_origins=None,
        jwks_url="https://issuer/jwks",
        issuer="issuer",
        audience="aud",
        postgres_dsn="postgres://db",
        encryption_passphrase="passphrase-012345",
        encryption_salt_path="/tmp/salt",
        zetherion_public_api_base_url="http://public",
        zetherion_skills_api_base_url="http://skills",
        zetherion_skills_api_secret="secret",
    )

    fake_server.start.assert_awaited_once()
    fake_server.stop.assert_awaited_once()


def test_split_csv() -> None:
    assert _split_csv(" a, b ,,c ") == ["a", "b", "c"]
    assert _split_csv("") == []


def _fake_settings(**overrides: object) -> SimpleNamespace:
    base = {
        "cgs_gateway_host": "0.0.0.0",
        "cgs_gateway_port": 8743,
        "cgs_gateway_allowed_origins": "",
        "cgs_auth_jwks_url": "https://issuer/jwks",
        "cgs_auth_issuer": "issuer",
        "cgs_auth_audience": "aud",
        "zetherion_public_api_base_url": "http://public",
        "zetherion_skills_api_base_url": "http://skills",
        "skills_api_secret": SecretStr("skills-secret"),
        "postgres_dsn": "postgres://db",
        "encryption_passphrase": SecretStr("passphrase-012345"),
        "encryption_salt_path": "/tmp/salt",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_main_exits_without_required_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    # Ensure ambient shell env cannot override test fixture settings.
    monkeypatch.delenv("POSTGRES_DSN", raising=False)
    monkeypatch.setenv("CGS_AUTH_JWKS_URL", "")
    monkeypatch.delenv("CGS_AUTH_ISSUER", raising=False)
    monkeypatch.delenv("CGS_AUTH_AUDIENCE", raising=False)
    monkeypatch.setenv("ZETHERION_SKILLS_API_SECRET", "")
    monkeypatch.setattr(server_mod, "get_settings", lambda: _fake_settings(postgres_dsn=""))
    with pytest.raises(SystemExit, match="1"):
        server_mod.main()

    monkeypatch.setattr(server_mod, "get_settings", lambda: _fake_settings(cgs_auth_jwks_url=""))
    with pytest.raises(SystemExit, match="1"):
        server_mod.main()

    monkeypatch.setattr(
        server_mod,
        "get_settings",
        lambda: _fake_settings(skills_api_secret=None),
    )
    with pytest.raises(SystemExit, match="1"):
        server_mod.main()


def test_main_runs_server_and_handles_keyboard_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server_mod, "get_settings", lambda: _fake_settings())
    monkeypatch.setenv("ZETHERION_SKILLS_API_SECRET", "override-secret")

    called: dict[str, object] = {}

    def fake_asyncio_run(coro) -> None:  # noqa: ANN001
        called["ran"] = True
        coro.close()

    monkeypatch.setattr(server_mod.asyncio, "run", fake_asyncio_run)
    server_mod.main()
    assert called["ran"] is True

    def fake_interrupt_run(coro) -> None:  # noqa: ANN001
        coro.close()
        raise KeyboardInterrupt

    monkeypatch.setattr(server_mod.asyncio, "run", fake_interrupt_run)
    server_mod.main()
