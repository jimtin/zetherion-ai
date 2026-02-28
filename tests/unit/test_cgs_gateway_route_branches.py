"""Additional branch coverage tests for CGS gateway routes."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from zetherion_ai.cgs_gateway.models import AuthPrincipal
from zetherion_ai.cgs_gateway.routes._utils import fingerprint_payload
from zetherion_ai.cgs_gateway.routes.internal import register_internal_routes
from zetherion_ai.cgs_gateway.routes.reporting import register_reporting_routes
from zetherion_ai.cgs_gateway.routes.runtime import register_runtime_routes
from zetherion_ai.cgs_gateway.server import create_error_middleware


def _runtime_app(
    *,
    principal_tenant_id: str | None = "tenant-a",
    principal_roles: list[str] | None = None,
    principal_scopes: list[str] | None = None,
) -> tuple[web.Application, MagicMock, MagicMock]:
    @web.middleware
    async def inject_context(request: web.Request, handler):
        request["principal"] = AuthPrincipal(
            sub="user-1",
            tenant_id=principal_tenant_id,
            roles=principal_roles or ["operator"],
            scopes=principal_scopes or ["cgs:internal"],
            claims={},
        )
        request["request_id"] = "req_branch_test"
        return await handler(request)

    storage = MagicMock()
    public_client = MagicMock()

    app = web.Application(middlewares=[inject_context, create_error_middleware()])
    app["cgs_storage"] = storage
    app["cgs_public_client"] = public_client
    app["cgs_skills_client"] = MagicMock()
    register_runtime_routes(app)
    register_internal_routes(app)
    register_reporting_routes(app)
    return app, storage, public_client


def _conversation_row() -> dict[str, object]:
    return {
        "conversation_id": "cgs_conv_123",
        "cgs_tenant_id": "tenant-a",
        "zetherion_session_id": "11111111-1111-1111-1111-111111111111",
        "zetherion_session_token": "zt_sess_token",
        "zetherion_api_key": "sk_live_abc",
        "is_active": True,
        "is_closed": False,
    }


class _DummyStreamContent:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def iter_chunked(self, _: int):
        for chunk in self._chunks:
            yield chunk


class _DummyStreamResponse:
    def __init__(
        self,
        *,
        status: int,
        chunks: list[bytes] | None = None,
        json_payload: object | None = None,
        text_payload: str = "",
    ) -> None:
        self.status = status
        self.content = _DummyStreamContent(chunks or [])
        self._json_payload = json_payload
        self._text_payload = text_payload
        self.release = AsyncMock()

    async def json(self) -> object:
        if self._json_payload is None:
            raise ValueError("no json")
        return self._json_payload

    async def text(self) -> str:
        return self._text_payload


@pytest.mark.asyncio
async def test_runtime_create_conversation_validation_error() -> None:
    app, storage, public_client = _runtime_app()
    storage.get_tenant_mapping = AsyncMock()
    public_client.request_json = AsyncMock()

    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/service/ai/v1/conversations", json={"app_user_id": "x"})
        assert resp.status == 400
        body = await resp.json()
        assert body["error"]["code"] == "AI_BAD_REQUEST"


@pytest.mark.asyncio
async def test_runtime_create_conversation_tenant_mismatch_forbidden() -> None:
    app, storage, public_client = _runtime_app(principal_tenant_id="tenant-b")
    storage.get_tenant_mapping = AsyncMock()
    public_client.request_json = AsyncMock()

    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/service/ai/v1/conversations",
            json={"tenant_id": "tenant-a", "metadata": {}},
        )
        assert resp.status == 403
        body = await resp.json()
        assert body["error"]["code"] == "AI_AUTH_FORBIDDEN"


@pytest.mark.asyncio
async def test_runtime_create_conversation_idempotency_conflict() -> None:
    app, storage, public_client = _runtime_app()
    storage.get_tenant_mapping = AsyncMock(
        return_value={
            "cgs_tenant_id": "tenant-a",
            "is_active": True,
            "zetherion_api_key": "sk_live_abc",
        }
    )
    storage.get_idempotency_record = AsyncMock(
        return_value={
            "request_fingerprint": "different-fingerprint",
            "response_status": 200,
            "response_body": {"request_id": "req_old", "data": {}, "error": None},
        }
    )
    public_client.request_json = AsyncMock()

    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/service/ai/v1/conversations",
            headers={"Idempotency-Key": "idem-1"},
            json={"tenant_id": "tenant-a", "metadata": {}},
        )
        assert resp.status == 409
        body = await resp.json()
        assert body["error"]["code"] == "AI_IDEMPOTENCY_CONFLICT"


@pytest.mark.asyncio
async def test_runtime_get_conversation_not_found() -> None:
    app, storage, _ = _runtime_app()
    storage.get_conversation = AsyncMock(return_value=None)

    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/service/ai/v1/conversations/missing")
        assert resp.status == 404
        body = await resp.json()
        assert body["error"]["code"] == "AI_CONVERSATION_NOT_FOUND"


@pytest.mark.asyncio
async def test_runtime_get_conversation_non_dict_upstream_fallback() -> None:
    app, storage, public_client = _runtime_app()
    storage.get_conversation = AsyncMock(return_value=_conversation_row())
    public_client.request_json = AsyncMock(return_value=(200, "ok", {}))

    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/service/ai/v1/conversations/cgs_conv_123")
        assert resp.status == 200
        body = await resp.json()
        assert body["error"] is None
        assert body["data"]["conversation_id"] == "cgs_conv_123"


@pytest.mark.asyncio
async def test_runtime_delete_conversation_accepts_upstream_404() -> None:
    app, storage, public_client = _runtime_app()
    storage.get_conversation = AsyncMock(return_value=_conversation_row())
    storage.get_idempotency_record = AsyncMock(return_value=None)
    storage.save_idempotency_record = AsyncMock()
    storage.close_conversation = AsyncMock(return_value=True)
    public_client.request_json = AsyncMock(return_value=(404, {"detail": "missing"}, {}))

    async with TestClient(TestServer(app)) as client:
        resp = await client.delete("/service/ai/v1/conversations/cgs_conv_123")
        assert resp.status == 200
        body = await resp.json()
        assert body["data"]["closed"] is True
        storage.close_conversation.assert_awaited_once()


@pytest.mark.asyncio
async def test_runtime_delete_conversation_idempotent_replay() -> None:
    app, storage, public_client = _runtime_app()
    storage.get_conversation = AsyncMock(return_value=_conversation_row())
    payload = {"conversation_id": "cgs_conv_123"}
    storage.get_idempotency_record = AsyncMock(
        return_value={
            "request_fingerprint": fingerprint_payload(payload),
            "response_status": 200,
            "response_body": {"request_id": "req_old", "data": {"closed": True}, "error": None},
        }
    )
    public_client.request_json = AsyncMock()

    async with TestClient(TestServer(app)) as client:
        resp = await client.delete(
            "/service/ai/v1/conversations/cgs_conv_123",
            headers={"Idempotency-Key": "idem-delete"},
        )
        assert resp.status == 200
        assert resp.headers["X-Idempotent-Replay"] == "true"
        body = await resp.json()
        assert body["request_id"] == "req_old"
        public_client.request_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_runtime_post_message_maps_upstream_rate_limit() -> None:
    app, storage, public_client = _runtime_app()
    storage.get_conversation = AsyncMock(return_value=_conversation_row())
    storage.get_idempotency_record = AsyncMock(return_value=None)
    public_client.request_json = AsyncMock(return_value=(429, {"detail": "rate"}, {}))

    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/service/ai/v1/conversations/cgs_conv_123/messages",
            json={"message": "hello", "metadata": {}},
        )
        assert resp.status == 429
        body = await resp.json()
        assert body["error"]["code"] == "AI_UPSTREAM_429"


@pytest.mark.asyncio
async def test_runtime_post_message_validation_error() -> None:
    app, storage, public_client = _runtime_app()
    storage.get_conversation = AsyncMock(return_value=_conversation_row())
    public_client.request_json = AsyncMock()

    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/service/ai/v1/conversations/cgs_conv_123/messages",
            json={"message": "", "metadata": {}},
        )
        assert resp.status == 400
        body = await resp.json()
        assert body["error"]["code"] == "AI_BAD_REQUEST"


@pytest.mark.asyncio
async def test_runtime_message_stream_success_and_error_paths() -> None:
    app, storage, public_client = _runtime_app()
    storage.get_conversation = AsyncMock(return_value=_conversation_row())

    success_stream = _DummyStreamResponse(status=200, chunks=[b"data: one\n\n", b"data: two\n\n"])
    public_client.open_stream = AsyncMock(return_value=success_stream)

    async with TestClient(TestServer(app)) as client:
        ok = await client.post(
            "/service/ai/v1/conversations/cgs_conv_123/messages/stream",
            json={"message": "hello", "metadata": {}},
        )
        assert ok.status == 200
        body = await ok.text()
        assert "data: one" in body
        success_stream.release.assert_awaited_once()

    error_stream = _DummyStreamResponse(status=500, text_payload="upstream-failed")
    public_client.open_stream = AsyncMock(return_value=error_stream)

    async with TestClient(TestServer(app)) as client:
        failed = await client.post(
            "/service/ai/v1/conversations/cgs_conv_123/messages/stream",
            json={"message": "hello", "metadata": {}},
        )
        assert failed.status == 503
        failed_body = await failed.json()
        assert failed_body["error"]["code"] == "AI_UPSTREAM_5XX"
        error_stream.release.assert_awaited_once()


@pytest.mark.asyncio
async def test_runtime_get_messages_and_forwarding_endpoints() -> None:
    app, storage, public_client = _runtime_app()
    storage.get_conversation = AsyncMock(return_value=_conversation_row())
    storage.get_idempotency_record = AsyncMock(return_value=None)
    storage.save_idempotency_record = AsyncMock()
    public_client.request_json = AsyncMock(return_value=(200, {"messages": [{"id": "m1"}]}, {}))

    async with TestClient(TestServer(app)) as client:
        messages = await client.get(
            "/service/ai/v1/conversations/cgs_conv_123/messages?limit=10&before=cursor"
        )
        assert messages.status == 200
        msg_body = await messages.json()
        assert msg_body["data"]["messages"][0]["id"] == "m1"

        replay = await client.post(
            "/service/ai/v1/conversations/cgs_conv_123/analytics/replay/chunks",
            json={"sequence_no": 1},
        )
        assert replay.status == 200

        replay_chunk = await client.get(
            "/service/ai/v1/conversations/cgs_conv_123/analytics/replay/chunks/ws1/1"
        )
        assert replay_chunk.status == 200

        analytics_end = await client.post(
            "/service/ai/v1/conversations/cgs_conv_123/analytics/end",
            json={},
        )
        assert analytics_end.status == 200

        feedback = await client.post(
            "/service/ai/v1/conversations/cgs_conv_123/recommendations/r1/feedback",
            json={"feedback_type": "accepted"},
        )
        assert feedback.status == 200


@pytest.mark.asyncio
async def test_runtime_forward_analytics_events_and_recommendations() -> None:
    app, storage, public_client = _runtime_app()
    storage.get_conversation = AsyncMock(return_value=_conversation_row())
    storage.get_idempotency_record = AsyncMock(return_value=None)
    storage.save_idempotency_record = AsyncMock()
    public_client.request_json = AsyncMock(return_value=(200, {"ok": True}, {}))

    async with TestClient(TestServer(app)) as client:
        events = await client.post(
            "/service/ai/v1/conversations/cgs_conv_123/analytics/events",
            json={"events": [{"event_type": "click"}]},
        )
        assert events.status == 200
        events_body = await events.json()
        assert events_body["data"]["ok"] is True

        recs = await client.get("/service/ai/v1/conversations/cgs_conv_123/recommendations?limit=5")
        assert recs.status == 200
        recs_body = await recs.json()
        assert recs_body["data"]["ok"] is True


@pytest.mark.asyncio
async def test_runtime_forwarding_idempotent_replay_and_upstream_error() -> None:
    app, storage, public_client = _runtime_app()
    storage.get_conversation = AsyncMock(return_value=_conversation_row())
    replay_payload = {"a": 1}
    storage.get_idempotency_record = AsyncMock(
        return_value={
            "request_fingerprint": fingerprint_payload(replay_payload),
            "response_status": 200,
            "response_body": {"request_id": "req_old", "data": {"ok": True}, "error": None},
        }
    )
    public_client.request_json = AsyncMock(return_value=(500, {"detail": "bad"}, {}))

    async with TestClient(TestServer(app)) as client:
        replay = await client.post(
            "/service/ai/v1/conversations/cgs_conv_123/analytics/events",
            headers={"Idempotency-Key": "idem-forward"},
            json=replay_payload,
        )
        assert replay.status == 200
        assert replay.headers["X-Idempotent-Replay"] == "true"

        failed = await client.post(
            "/service/ai/v1/conversations/cgs_conv_123/analytics/events",
            json={"events": [{"a": 1}]},
        )
        assert failed.status == 503
        body = await failed.json()
        assert body["error"]["code"] == "AI_UPSTREAM_5XX"


@pytest.mark.asyncio
async def test_runtime_document_routes_success_and_binary_proxy() -> None:
    app, storage, public_client = _runtime_app()
    storage.get_tenant_mapping = AsyncMock(
        return_value={
            "cgs_tenant_id": "tenant-a",
            "is_active": True,
            "zetherion_api_key": "sk_live_abc",
        }
    )
    public_client.request_json = AsyncMock(
        side_effect=[
            (201, {"upload_id": "u1"}, {}),  # create upload
            (201, {"document_id": "d1", "status": "indexed"}, {}),  # complete upload
            (200, {"documents": [{"document_id": "d1"}], "count": 1}, {}),  # list
            (200, {"document_id": "d1", "status": "indexed"}, {}),  # get
            (200, {"document_id": "d1", "status": "indexed"}, {}),  # reindex
            (200, {"answer": "ok", "citations": [], "provider": "groq", "model": "m"}, {}),  # rag
            (200, {"providers": ["groq"], "defaults": {"groq": "m"}, "allowed_models": ["m"]}, {}),
        ]
    )
    public_client.request_raw = AsyncMock(
        side_effect=[
            (
                200,
                b"<html>preview</html>",
                {
                    "Content-Type": "text/html",
                    "Content-Disposition": 'inline; filename="x.html"',
                },
            ),
            (
                200,
                b"file-bytes",
                {
                    "Content-Type": "application/pdf",
                    "Content-Disposition": 'attachment; filename="x.pdf"',
                },
            ),
        ]
    )

    async with TestClient(TestServer(app)) as client:
        created = await client.post(
            "/service/ai/v1/documents/uploads",
            json={
                "tenant_id": "tenant-a",
                "file_name": "proposal.pdf",
                "mime_type": "application/pdf",
                "size_bytes": 10,
            },
        )
        assert created.status == 201

        completed = await client.post(
            "/service/ai/v1/documents/uploads/u1/complete",
            json={"tenant_id": "tenant-a", "file_base64": "aGVsbG8="},
        )
        assert completed.status == 201

        listed = await client.get("/service/ai/v1/documents?tenant_id=tenant-a")
        assert listed.status == 200

        got = await client.get("/service/ai/v1/documents/d1?tenant_id=tenant-a")
        assert got.status == 200

        preview = await client.get("/service/ai/v1/documents/d1/preview?tenant_id=tenant-a")
        assert preview.status == 200
        assert await preview.read() == b"<html>preview</html>"

        downloaded = await client.get("/service/ai/v1/documents/d1/download?tenant_id=tenant-a")
        assert downloaded.status == 200
        assert await downloaded.read() == b"file-bytes"

        reindexed = await client.post(
            "/service/ai/v1/documents/d1/index",
            json={"tenant_id": "tenant-a"},
        )
        assert reindexed.status == 200

        rag = await client.post(
            "/service/ai/v1/rag/query",
            json={"tenant_id": "tenant-a", "query": "hello"},
        )
        assert rag.status == 200

        providers = await client.get("/service/ai/v1/models/providers?tenant_id=tenant-a")
        assert providers.status == 200

    assert public_client.request_raw.await_count == 2


@pytest.mark.asyncio
async def test_runtime_document_routes_require_tenant_query() -> None:
    app, storage, public_client = _runtime_app()
    storage.get_tenant_mapping = AsyncMock()
    public_client.request_json = AsyncMock()

    async with TestClient(TestServer(app)) as client:
        list_resp = await client.get("/service/ai/v1/documents")
        assert list_resp.status == 400
        detail_resp = await client.get("/service/ai/v1/documents/doc-1")
        assert detail_resp.status == 400
        preview_resp = await client.get("/service/ai/v1/documents/doc-1/preview")
        assert preview_resp.status == 400
        providers_resp = await client.get("/service/ai/v1/models/providers")
        assert providers_resp.status == 400


@pytest.mark.asyncio
async def test_internal_forbidden_for_non_operator() -> None:
    app, _, _ = _runtime_app(principal_roles=["viewer"], principal_scopes=["read:all"])
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/service/ai/v1/internal/tenants")
        assert resp.status == 403
        body = await resp.json()
        assert body["error"]["code"] == "AI_AUTH_FORBIDDEN"


@pytest.mark.asyncio
async def test_internal_list_update_deactivate_rotate_and_release_success() -> None:
    app, storage, public_client = _runtime_app()
    storage.list_tenant_mappings = AsyncMock(
        return_value=[{"cgs_tenant_id": "tenant-a"}, {"cgs_tenant_id": "tenant-b"}]
    )
    storage.get_tenant_mapping = AsyncMock(
        return_value={
            "cgs_tenant_id": "tenant-a",
            "is_active": True,
            "zetherion_tenant_id": "11111111-1111-1111-1111-111111111111",
            "zetherion_api_key": "sk_live_abc",
        }
    )
    storage.update_tenant_profile = AsyncMock(
        return_value={"zetherion_tenant_id": "11111111-1111-1111-1111-111111111111"}
    )
    storage.deactivate_tenant_mapping = AsyncMock(return_value=True)
    storage.rotate_tenant_api_key = AsyncMock(return_value={"key_version": 2})
    app["cgs_skills_client"].handle_intent = AsyncMock(
        side_effect=[
            (200, {"success": True}),
            (200, {"success": True}),
            (200, {"data": {"api_key": "sk_rotated"}}),
        ]
    )
    public_client.request_json = AsyncMock(return_value=(201, {"marker_id": "m1"}, {}))

    async with TestClient(TestServer(app)) as client:
        listed = await client.get("/service/ai/v1/internal/tenants?include_inactive=true")
        assert listed.status == 200
        listed_body = await listed.json()
        assert listed_body["data"]["count"] == 2

        updated = await client.patch(
            "/service/ai/v1/internal/tenants/tenant-a",
            json={"name": "Tenant A", "domain": "example.com", "config": {"tier": "gold"}},
        )
        assert updated.status == 200

        deactivated = await client.post("/service/ai/v1/internal/tenants/tenant-a/deactivate")
        assert deactivated.status == 200

        rotated = await client.post("/service/ai/v1/internal/tenants/tenant-a/keys/rotate")
        assert rotated.status == 200
        rotated_body = await rotated.json()
        assert rotated_body["data"]["api_key"] == "sk_rotated"

        released = await client.post(
            "/service/ai/v1/internal/tenants/tenant-a/release-markers",
            json={"source": "deploy"},
        )
        assert released.status == 201
        released_body = await released.json()
        assert released_body["data"]["marker"]["marker_id"] == "m1"


@pytest.mark.asyncio
async def test_internal_release_marker_upstream_failure() -> None:
    app, storage, public_client = _runtime_app()
    storage.get_tenant_mapping = AsyncMock(
        return_value={
            "cgs_tenant_id": "tenant-a",
            "is_active": True,
            "zetherion_api_key": "sk_live_abc",
        }
    )
    public_client.request_json = AsyncMock(return_value=(500, {"detail": "boom"}, {}))

    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/service/ai/v1/internal/tenants/tenant-a/release-markers",
            json={"source": "deploy"},
        )
        assert resp.status == 502
        body = await resp.json()
        assert body["error"]["code"] == "AI_UPSTREAM_ERROR"


@pytest.mark.asyncio
async def test_internal_rotate_key_missing_api_key_is_error() -> None:
    app, storage, _ = _runtime_app()
    storage.get_tenant_mapping = AsyncMock(
        return_value={
            "cgs_tenant_id": "tenant-a",
            "is_active": True,
            "zetherion_tenant_id": "11111111-1111-1111-1111-111111111111",
            "zetherion_api_key": "sk_live_abc",
        }
    )
    app["cgs_skills_client"].handle_intent = AsyncMock(
        return_value=(200, {"success": True, "data": {"tenant_id": "x"}})
    )

    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/service/ai/v1/internal/tenants/tenant-a/keys/rotate")
        assert resp.status == 502
        body = await resp.json()
        assert body["error"]["code"] == "AI_SKILLS_UPSTREAM_ERROR"


@pytest.mark.asyncio
async def test_internal_create_tenant_error_paths() -> None:
    app, storage, _ = _runtime_app()
    app["cgs_skills_client"].handle_intent = AsyncMock(return_value=(500, {"error": True}))
    storage.upsert_tenant_mapping = AsyncMock()

    async with TestClient(TestServer(app)) as client:
        bad_payload = await client.post("/service/ai/v1/internal/tenants", json={})
        assert bad_payload.status == 400

        upstream_fail = await client.post(
            "/service/ai/v1/internal/tenants",
            json={"cgs_tenant_id": "tenant-a", "name": "Tenant A", "config": {}},
        )
        assert upstream_fail.status == 502


@pytest.mark.asyncio
async def test_reporting_forbidden_on_cross_tenant_access() -> None:
    app, storage, _ = _runtime_app(principal_tenant_id="tenant-a")
    storage.get_tenant_mapping = AsyncMock()

    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/service/ai/v1/tenants/tenant-b/crm/contacts")
        assert resp.status == 403
        body = await resp.json()
        assert body["error"]["code"] == "AI_AUTH_FORBIDDEN"


@pytest.mark.asyncio
async def test_reporting_maps_upstream_429_error() -> None:
    app, storage, public_client = _runtime_app(principal_tenant_id="tenant-a")
    storage.get_tenant_mapping = AsyncMock(
        return_value={
            "cgs_tenant_id": "tenant-a",
            "is_active": True,
            "zetherion_api_key": "sk_live_abc",
        }
    )
    public_client.request_json = AsyncMock(return_value=(429, {"detail": "limited"}, {}))

    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/service/ai/v1/tenants/tenant-a/analytics/funnel")
        assert resp.status == 429
        body = await resp.json()
        assert body["error"]["code"] == "AI_UPSTREAM_429"


def test_runtime_now_iso_returns_utc_string() -> None:
    from zetherion_ai.cgs_gateway.routes.runtime import now_iso

    value = now_iso()
    assert "T" in value
    assert value.endswith("+00:00")
