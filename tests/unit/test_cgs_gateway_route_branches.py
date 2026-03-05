"""Additional branch coverage tests for CGS gateway routes."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer

from zetherion_ai.cgs_gateway.models import AuthPrincipal
from zetherion_ai.cgs_gateway.routes._utils import fingerprint_payload
from zetherion_ai.cgs_gateway.routes.internal import register_internal_routes
from zetherion_ai.cgs_gateway.routes.internal_admin import register_internal_admin_routes
from zetherion_ai.cgs_gateway.routes.reporting import register_reporting_routes
from zetherion_ai.cgs_gateway.routes.runtime import register_runtime_routes
from zetherion_ai.cgs_gateway.server import create_error_middleware
from zetherion_ai.security.trust_policy import (
    TrustActionClass,
    TrustDecisionOutcome,
    TrustPolicyDecision,
)


def _runtime_app(
    *,
    principal_tenant_id: str | None = "tenant-a",
    principal_roles: list[str] | None = None,
    principal_scopes: list[str] | None = None,
    principal_claims: dict[str, object] | None = None,
) -> tuple[web.Application, MagicMock, MagicMock]:
    @web.middleware
    async def inject_context(request: web.Request, handler):
        request["principal"] = AuthPrincipal(
            sub="user-1",
            tenant_id=principal_tenant_id,
            roles=principal_roles or ["operator"],
            scopes=principal_scopes or ["cgs:internal"],
            claims=principal_claims or {},
        )
        request["request_id"] = "req_branch_test"
        return await handler(request)

    storage = MagicMock()
    public_client = MagicMock()

    app = web.Application(middlewares=[inject_context, create_error_middleware()])
    app["cgs_storage"] = storage
    app["cgs_public_client"] = public_client
    app["cgs_skills_client"] = MagicMock()
    app["cgs_blog_publish_token"] = "blog-token"
    register_runtime_routes(app)
    register_internal_routes(app)
    register_internal_admin_routes(app)
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
async def test_runtime_document_complete_upload_supports_multipart_passthrough() -> None:
    app, storage, public_client = _runtime_app()
    storage.get_tenant_mapping = AsyncMock(
        return_value={
            "cgs_tenant_id": "tenant-a",
            "is_active": True,
            "zetherion_api_key": "sk_live_abc",
        }
    )
    public_client.request_json = AsyncMock(return_value=(201, {"document_id": "d1"}, {}))

    form = FormData()
    form.add_field("file", b"hello-world", filename="note.txt", content_type="text/plain")
    form.add_field("metadata", '{"source":"portal"}')

    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/service/ai/v1/documents/uploads/u1/complete?tenant_id=tenant-a",
            data=form,
        )
        assert response.status == 201
        body = await response.json()
        assert body["data"]["document_id"] == "d1"

    kwargs = public_client.request_json.await_args.kwargs
    assert isinstance(kwargs["data"], bytes | bytearray)
    assert kwargs["headers"]["Content-Type"].startswith("multipart/form-data")


@pytest.mark.asyncio
async def test_runtime_document_complete_upload_multipart_requires_tenant_query() -> None:
    app, storage, public_client = _runtime_app()
    storage.get_tenant_mapping = AsyncMock()
    public_client.request_json = AsyncMock()

    form = FormData()
    form.add_field("file", b"hello-world", filename="note.txt", content_type="text/plain")

    async with TestClient(TestServer(app)) as client:
        response = await client.post("/service/ai/v1/documents/uploads/u1/complete", data=form)
        assert response.status == 400
        body = await response.json()
        assert body["error"]["code"] == "AI_BAD_REQUEST"
        assert "tenant_id" in body["error"]["message"]


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
async def test_internal_admin_mutation_requires_step_up_claim() -> None:
    app, storage, _ = _runtime_app(
        principal_scopes=["cgs:internal", "cgs:zetherion-admin"],
        principal_claims={},
    )
    storage.get_tenant_mapping = AsyncMock(
        return_value={
            "cgs_tenant_id": "tenant-a",
            "is_active": True,
            "zetherion_tenant_id": "11111111-1111-1111-1111-111111111111",
        }
    )
    app["cgs_skills_client"].request_admin_json = AsyncMock(return_value=(200, {"ok": True}))

    async with TestClient(TestServer(app)) as client:
        resp = await client.put(
            "/service/ai/v1/internal/admin/tenants/tenant-a/settings/models/default_provider",
            json={"value": "groq"},
        )
        assert resp.status == 403
        body = await resp.json()
        assert body["error"]["code"] == "AI_AUTH_STEP_UP_REQUIRED"


@pytest.mark.asyncio
async def test_internal_admin_secret_put_requires_approval_ticket() -> None:
    app, storage, _ = _runtime_app(
        principal_scopes=["cgs:internal", "cgs:zetherion-admin", "cgs:zetherion-secrets-admin"],
        principal_claims={"step_up": True},
    )
    storage.get_tenant_mapping = AsyncMock(
        return_value={
            "cgs_tenant_id": "tenant-a",
            "is_active": True,
            "zetherion_tenant_id": "11111111-1111-1111-1111-111111111111",
        }
    )
    storage.create_admin_change = AsyncMock(
        return_value={"change_id": "chg_1", "status": "pending"}
    )
    app["cgs_skills_client"].request_admin_json = AsyncMock(return_value=(200, {"ok": True}))

    async with TestClient(TestServer(app)) as client:
        resp = await client.put(
            "/service/ai/v1/internal/admin/tenants/tenant-a/secrets/OPENAI_API_KEY",
            json={"value": "sk-live"},
        )
        assert resp.status == 409
        body = await resp.json()
        assert body["error"]["code"] == "AI_APPROVAL_REQUIRED"
        assert body["error"]["details"]["change_ticket_id"] == "chg_1"
        app["cgs_skills_client"].request_admin_json.assert_not_called()


@pytest.mark.asyncio
async def test_internal_admin_secret_put_with_approved_ticket_applies() -> None:
    app, storage, _ = _runtime_app(
        principal_scopes=["cgs:internal", "cgs:zetherion-admin", "cgs:zetherion-secrets-admin"],
        principal_claims={"step_up": True},
    )
    storage.get_tenant_mapping = AsyncMock(
        return_value={
            "cgs_tenant_id": "tenant-a",
            "is_active": True,
            "zetherion_tenant_id": "11111111-1111-1111-1111-111111111111",
        }
    )
    storage.get_admin_change = AsyncMock(
        return_value={
            "change_id": "chg_approved",
            "cgs_tenant_id": "tenant-a",
            "action": "secret.put",
            "status": "approved",
        }
    )
    storage.mark_admin_change_applied = AsyncMock(return_value=None)
    storage.mark_admin_change_failed = AsyncMock(return_value=None)
    app["cgs_skills_client"].request_admin_json = AsyncMock(return_value=(200, {"ok": True}))

    async with TestClient(TestServer(app)) as client:
        resp = await client.put(
            "/service/ai/v1/internal/admin/tenants/tenant-a/secrets/OPENAI_API_KEY",
            params={"change_ticket_id": "chg_approved"},
            json={"value": "sk-live"},
        )
        assert resp.status == 200
        body = await resp.json()
        assert body["data"]["ok"] is True
        storage.mark_admin_change_applied.assert_awaited_once()
        app["cgs_skills_client"].request_admin_json.assert_awaited_once()


@pytest.mark.asyncio
async def test_internal_admin_route_matrix_success_paths() -> None:
    app, storage, _ = _runtime_app(
        principal_scopes=["cgs:internal", "cgs:zetherion-admin", "cgs:zetherion-secrets-admin"],
        principal_claims={
            "step_up": True,
            "allowed_tenants": ["tenant-a"],
            "email": "ops@example.com",
        },
    )
    storage.get_tenant_mapping = AsyncMock(
        return_value={
            "cgs_tenant_id": "tenant-a",
            "is_active": True,
            "zetherion_tenant_id": "11111111-1111-1111-1111-111111111111",
        }
    )
    storage.get_admin_change = AsyncMock(
        return_value={
            "change_id": "chg_delete",
            "cgs_tenant_id": "tenant-a",
            "action": "secret.delete",
            "status": "approved",
        }
    )
    storage.mark_admin_change_applied = AsyncMock(return_value=None)
    storage.mark_admin_change_failed = AsyncMock(return_value=None)
    app["cgs_skills_client"].request_admin_json = AsyncMock(return_value=(200, {"ok": True}))

    async with TestClient(TestServer(app)) as client:
        assert (
            await client.get("/service/ai/v1/internal/admin/tenants/tenant-a/discord-users")
        ).status == 200
        assert (
            await client.post(
                "/service/ai/v1/internal/admin/tenants/tenant-a/discord-users",
                json={"discord_user_id": 5, "role": "user"},
            )
        ).status == 201
        assert (
            await client.delete("/service/ai/v1/internal/admin/tenants/tenant-a/discord-users/5")
        ).status == 200
        assert (
            await client.patch(
                "/service/ai/v1/internal/admin/tenants/tenant-a/discord-users/5/role",
                json={"role": "admin"},
            )
        ).status == 200
        assert (
            await client.get("/service/ai/v1/internal/admin/tenants/tenant-a/discord-bindings")
        ).status == 200
        assert (
            await client.put(
                "/service/ai/v1/internal/admin/tenants/tenant-a/discord-bindings/guilds/10",
                json={"priority": 10, "is_active": True},
            )
        ).status == 200
        assert (
            await client.put(
                "/service/ai/v1/internal/admin/tenants/tenant-a/discord-bindings/channels/20",
                json={"guild_id": 10, "priority": 1, "is_active": True},
            )
        ).status == 200
        assert (
            await client.delete(
                "/service/ai/v1/internal/admin/tenants/tenant-a/discord-bindings/channels/20"
            )
        ).status == 200
        assert (
            await client.get("/service/ai/v1/internal/admin/tenants/tenant-a/settings")
        ).status == 200
        assert (
            await client.put(
                "/service/ai/v1/internal/admin/tenants/tenant-a/settings/models/default_provider",
                json={"value": "groq"},
            )
        ).status == 200
        assert (
            await client.delete(
                "/service/ai/v1/internal/admin/tenants/tenant-a/settings/models/default_provider"
            )
        ).status == 200
        assert (
            await client.get("/service/ai/v1/internal/admin/tenants/tenant-a/secrets")
        ).status == 200
        assert (
            await client.delete(
                "/service/ai/v1/internal/admin/tenants/tenant-a/secrets/OPENAI_API_KEY",
                params={"change_ticket_id": "chg_delete"},
            )
        ).status == 200
        assert (
            await client.get("/service/ai/v1/internal/admin/tenants/tenant-a/audit")
        ).status == 200

    assert app["cgs_skills_client"].request_admin_json.await_count >= 13
    storage.mark_admin_change_applied.assert_awaited_once()


@pytest.mark.asyncio
async def test_internal_admin_owner_role_patch_with_approved_ticket() -> None:
    app, storage, _ = _runtime_app(
        principal_scopes=["cgs:internal", "cgs:zetherion-admin"],
        principal_claims={"step_up": True},
    )
    storage.get_tenant_mapping = AsyncMock(
        return_value={
            "cgs_tenant_id": "tenant-a",
            "is_active": True,
            "zetherion_tenant_id": "11111111-1111-1111-1111-111111111111",
        }
    )
    storage.get_admin_change = AsyncMock(
        return_value={
            "change_id": "chg_owner",
            "cgs_tenant_id": "tenant-a",
            "action": "discord.role.owner",
            "status": "approved",
        }
    )
    storage.mark_admin_change_applied = AsyncMock(return_value=None)
    app["cgs_skills_client"].request_admin_json = AsyncMock(return_value=(200, {"ok": True}))

    async with TestClient(TestServer(app)) as client:
        resp = await client.patch(
            "/service/ai/v1/internal/admin/tenants/tenant-a/discord-users/99/role",
            params={"change_ticket_id": "chg_owner"},
            json={"role": "owner"},
        )
        assert resp.status == 200
        body = await resp.json()
        assert body["data"]["ok"] is True

    storage.mark_admin_change_applied.assert_awaited_once()


@pytest.mark.asyncio
async def test_internal_admin_list_secrets_requires_secrets_scope() -> None:
    app, storage, _ = _runtime_app(
        principal_scopes=["cgs:internal", "cgs:zetherion-admin"],
        principal_claims={"step_up": True},
    )
    storage.get_tenant_mapping = AsyncMock()
    app["cgs_skills_client"].request_admin_json = AsyncMock()

    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/service/ai/v1/internal/admin/tenants/tenant-a/secrets")
        assert resp.status == 403
        body = await resp.json()
        assert body["error"]["code"] == "AI_AUTH_FORBIDDEN"


@pytest.mark.asyncio
async def test_internal_admin_denies_operator_without_tenant_allowance() -> None:
    app, storage, _ = _runtime_app(
        principal_scopes=["cgs:internal", "cgs:zetherion-admin"],
        principal_claims={"allowed_tenants": ["tenant-b"]},
    )
    storage.get_tenant_mapping = AsyncMock()
    app["cgs_skills_client"].request_admin_json = AsyncMock()

    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/service/ai/v1/internal/admin/tenants/tenant-a/settings")
        assert resp.status == 403
        body = await resp.json()
        assert body["error"]["code"] == "AI_AUTH_FORBIDDEN"


@pytest.mark.asyncio
async def test_internal_admin_secret_delete_approval_ticket_error_paths() -> None:
    app, storage, _ = _runtime_app(
        principal_scopes=["cgs:internal", "cgs:zetherion-admin", "cgs:zetherion-secrets-admin"],
        principal_claims={"step_up": True},
    )
    storage.get_tenant_mapping = AsyncMock(
        return_value={
            "cgs_tenant_id": "tenant-a",
            "is_active": True,
            "zetherion_tenant_id": "11111111-1111-1111-1111-111111111111",
        }
    )
    app["cgs_skills_client"].request_admin_json = AsyncMock(return_value=(200, {"ok": True}))

    async with TestClient(TestServer(app)) as client:
        storage.get_admin_change = AsyncMock(return_value=None)
        not_found = await client.delete(
            "/service/ai/v1/internal/admin/tenants/tenant-a/secrets/OPENAI_API_KEY",
            params={"change_ticket_id": "missing"},
        )
        assert not_found.status == 404

        storage.get_admin_change = AsyncMock(
            return_value={
                "change_id": "chg1",
                "cgs_tenant_id": "tenant-b",
                "action": "secret.delete",
                "status": "approved",
            }
        )
        tenant_mismatch = await client.delete(
            "/service/ai/v1/internal/admin/tenants/tenant-a/secrets/OPENAI_API_KEY",
            params={"change_ticket_id": "chg1"},
        )
        assert tenant_mismatch.status == 403

        storage.get_admin_change = AsyncMock(
            return_value={
                "change_id": "chg2",
                "cgs_tenant_id": "tenant-a",
                "action": "secret.put",
                "status": "approved",
            }
        )
        action_mismatch = await client.delete(
            "/service/ai/v1/internal/admin/tenants/tenant-a/secrets/OPENAI_API_KEY",
            params={"change_ticket_id": "chg2"},
        )
        assert action_mismatch.status == 409

        storage.get_admin_change = AsyncMock(
            return_value={
                "change_id": "chg3",
                "cgs_tenant_id": "tenant-a",
                "action": "secret.delete",
                "status": "pending",
            }
        )
        pending = await client.delete(
            "/service/ai/v1/internal/admin/tenants/tenant-a/secrets/OPENAI_API_KEY",
            params={"change_ticket_id": "chg3"},
        )
        assert pending.status == 409


@pytest.mark.asyncio
async def test_internal_admin_change_workflow_endpoints() -> None:
    app, storage, _ = _runtime_app(
        principal_scopes=["cgs:internal", "cgs:zetherion-admin"],
        principal_claims={"step_up": True},
    )
    storage.create_admin_change = AsyncMock(
        return_value={"change_id": "chg_1", "status": "pending"}
    )
    storage.list_admin_changes = AsyncMock(return_value=[{"change_id": "chg_1"}])
    storage.get_admin_change = AsyncMock(
        side_effect=[
            None,
            {"change_id": "chg_self", "cgs_tenant_id": "tenant-a", "requested_by": "user-1"},
            {"change_id": "chg_invalid", "cgs_tenant_id": "tenant-a", "requested_by": "another"},
            {"change_id": "chg_reject", "cgs_tenant_id": "tenant-a", "requested_by": "another"},
        ]
    )
    storage.approve_admin_change = AsyncMock(return_value=None)
    storage.reject_admin_change = AsyncMock(
        return_value={"change_id": "chg_reject", "status": "rejected"}
    )

    async with TestClient(TestServer(app)) as client:
        created = await client.post(
            "/service/ai/v1/internal/admin/tenants/tenant-a/changes",
            json={"action": "setting.put", "payload": {"k": "v"}},
        )
        assert created.status == 201

        listed = await client.get(
            "/service/ai/v1/internal/admin/tenants/tenant-a/changes",
            params={"status": "pending"},
        )
        assert listed.status == 200

        missing = await client.post(
            "/service/ai/v1/internal/admin/tenants/tenant-a/changes/chg_missing/approve",
            json={"reason": "approve"},
        )
        assert missing.status == 404

        self_approve = await client.post(
            "/service/ai/v1/internal/admin/tenants/tenant-a/changes/chg_self/approve",
            json={"reason": "approve"},
        )
        assert self_approve.status == 409

        invalid_state = await client.post(
            "/service/ai/v1/internal/admin/tenants/tenant-a/changes/chg_invalid/approve",
            json={"reason": "approve"},
        )
        assert invalid_state.status == 409

        rejected = await client.post(
            "/service/ai/v1/internal/admin/tenants/tenant-a/changes/chg_reject/reject",
            json={"reason": "reject"},
        )
        assert rejected.status == 200


@pytest.mark.asyncio
async def test_internal_admin_email_route_matrix_and_high_risk_controls() -> None:
    app, storage, _ = _runtime_app(
        principal_scopes=["cgs:internal", "cgs:zetherion-admin", "cgs:zetherion-secrets-admin"],
        principal_claims={"step_up": True, "allowed_tenants": ["tenant-a"]},
    )
    storage.get_tenant_mapping = AsyncMock(
        return_value={
            "cgs_tenant_id": "tenant-a",
            "is_active": True,
            "zetherion_tenant_id": "11111111-1111-1111-1111-111111111111",
        }
    )
    storage.create_admin_change = AsyncMock(
        return_value={"change_id": "chg_email", "status": "pending"}
    )
    storage.get_admin_change = AsyncMock(
        return_value={
            "change_id": "chg_email_approved",
            "cgs_tenant_id": "tenant-a",
            "action": "email.oauth_app.put",
            "status": "approved",
            "requested_by": "another-operator",
        }
    )
    storage.mark_admin_change_applied = AsyncMock(return_value=None)
    storage.mark_admin_change_failed = AsyncMock(return_value=None)

    app["cgs_skills_client"].request_tenant_admin_json = AsyncMock(
        return_value=(200, {"ok": True, "accounts": [], "insights": []})
    )
    app["cgs_skills_client"].request_admin_json = AsyncMock(return_value=(200, {"ok": True}))

    async with TestClient(TestServer(app)) as client:
        oauth_get = await client.get(
            "/service/ai/v1/internal/admin/tenants/tenant-a/email/providers/google/oauth-app"
        )
        assert oauth_get.status == 200

        oauth_put_requires_ticket = await client.put(
            "/service/ai/v1/internal/admin/tenants/tenant-a/email/providers/google/oauth-app",
            json={
                "redirect_uri": "https://cgs.example.com/oauth/callback",
                "client_id": "client-id",
                "client_secret": "client-secret",
                "enabled": True,
            },
        )
        assert oauth_put_requires_ticket.status == 409
        body = await oauth_put_requires_ticket.json()
        assert body["error"]["code"] == "AI_APPROVAL_REQUIRED"

        storage.get_admin_change = AsyncMock(
            return_value={
                "change_id": "chg_email_approved",
                "cgs_tenant_id": "tenant-a",
                "action": "email.oauth_app.put",
                "status": "approved",
                "requested_by": "another-operator",
            }
        )
        oauth_put = await client.put(
            "/service/ai/v1/internal/admin/tenants/tenant-a/email/providers/google/oauth-app",
            params={"change_ticket_id": "chg_email_approved"},
            json={
                "redirect_uri": "https://cgs.example.com/oauth/callback",
                "client_id": "client-id",
                "client_secret": "client-secret",
                "enabled": True,
            },
        )
        assert oauth_put.status == 200

        connect_start = await client.post(
            "/service/ai/v1/internal/admin/tenants/tenant-a/email/mailboxes/connect/start",
            json={"provider": "google", "account_hint": "ops@example.com"},
        )
        assert connect_start.status == 201

        callback = await client.get(
            "/service/ai/v1/internal/admin/tenants/tenant-a/email/mailboxes/connect/callback",
            params={"provider": "google", "code": "abc", "state": "state-1"},
        )
        assert callback.status == 200

        listed = await client.get(
            "/service/ai/v1/internal/admin/tenants/tenant-a/email/mailboxes?provider=google"
        )
        assert listed.status == 200

        patched = await client.patch(
            "/service/ai/v1/internal/admin/tenants/tenant-a/email/mailboxes/mailbox-1",
            json={"status": "connected"},
        )
        assert patched.status == 200

        sync = await client.post(
            "/service/ai/v1/internal/admin/tenants/tenant-a/email/mailboxes/mailbox-1/sync",
            json={"direction": "bi_directional", "max_results": 10},
        )
        assert sync.status == 200

        critical = await client.get(
            "/service/ai/v1/internal/admin/tenants/tenant-a/email/critical/messages?status=open"
        )
        assert critical.status == 200

        calendars_missing = await client.get(
            "/service/ai/v1/internal/admin/tenants/tenant-a/email/calendars"
        )
        assert calendars_missing.status == 400

        calendars = await client.get(
            "/service/ai/v1/internal/admin/tenants/tenant-a/email/calendars?mailbox_id=mailbox-1"
        )
        assert calendars.status == 200

        primary_calendar = await client.put(
            "/service/ai/v1/internal/admin/tenants/tenant-a/email/mailboxes/mailbox-1/calendar-primary",
            json={"calendar_id": "primary"},
        )
        assert primary_calendar.status == 200

        insights = await client.get(
            "/service/ai/v1/internal/admin/tenants/tenant-a/email/insights?limit=50"
        )
        assert insights.status == 200

        reindex = await client.post(
            "/service/ai/v1/internal/admin/tenants/tenant-a/email/insights/reindex",
            json={"insight_type": "critical_email"},
        )
        assert reindex.status == 200

        storage.get_admin_change = AsyncMock(
            return_value={
                "change_id": "chg_mailbox_delete",
                "cgs_tenant_id": "tenant-a",
                "action": "email.mailbox.delete",
                "status": "approved",
                "requested_by": "another-operator",
            }
        )
        deleted = await client.delete(
            "/service/ai/v1/internal/admin/tenants/tenant-a/email/mailboxes/mailbox-1",
            params={"change_ticket_id": "chg_mailbox_delete"},
        )
        assert deleted.status == 200

    assert app["cgs_skills_client"].request_tenant_admin_json.await_count >= 12
    assert storage.mark_admin_change_applied.await_count >= 2


@pytest.mark.asyncio
async def test_internal_admin_messaging_route_matrix_success_paths() -> None:
    app, storage, _ = _runtime_app(
        principal_scopes=["cgs:internal", "cgs:zetherion-admin"],
        principal_claims={"step_up": True, "allowed_tenants": ["tenant-a"]},
    )
    storage.get_tenant_mapping = AsyncMock(
        return_value={
            "cgs_tenant_id": "tenant-a",
            "is_active": True,
            "zetherion_tenant_id": "11111111-1111-1111-1111-111111111111",
        }
    )
    app["cgs_skills_client"].request_tenant_admin_json = AsyncMock(
        return_value=(200, {"ok": True, "chats": [], "messages": []})
    )
    app["cgs_skills_client"].request_admin_json = AsyncMock(return_value=(200, {"ok": True}))

    async with TestClient(TestServer(app)) as client:
        provider_get = await client.get(
            "/service/ai/v1/internal/admin/tenants/tenant-a/messaging/providers/whatsapp/config"
        )
        assert provider_get.status == 200

        provider_put = await client.put(
            "/service/ai/v1/internal/admin/tenants/tenant-a/messaging/providers/whatsapp/config",
            json={"enabled": True, "bridge_mode": "local_sidecar"},
        )
        assert provider_put.status == 200

        policy_put = await client.put(
            "/service/ai/v1/internal/admin/tenants/tenant-a/messaging/chats/chat-1/policy",
            json={"provider": "whatsapp", "read_enabled": True, "send_enabled": True},
        )
        assert policy_put.status == 200

        policy_get = await client.get(
            "/service/ai/v1/internal/admin/tenants/tenant-a/messaging/chats/chat-1/policy",
            params={"provider": "whatsapp"},
        )
        assert policy_get.status == 200

        chats = await client.get(
            "/service/ai/v1/internal/admin/tenants/tenant-a/messaging/chats",
            params={"provider": "whatsapp", "limit": "50"},
        )
        assert chats.status == 200

        messages = await client.get(
            "/service/ai/v1/internal/admin/tenants/tenant-a/messaging/messages",
            params={"provider": "whatsapp", "chat_id": "chat-1", "limit": "50"},
        )
        assert messages.status == 200

    assert app["cgs_skills_client"].request_tenant_admin_json.await_count >= 6


@pytest.mark.asyncio
async def test_internal_admin_messaging_send_returns_approval_required_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, storage, _ = _runtime_app(
        principal_scopes=["cgs:internal", "cgs:zetherion-admin"],
        principal_claims={"step_up": True, "allowed_tenants": ["tenant-a"]},
    )
    storage.get_tenant_mapping = AsyncMock(
        return_value={
            "cgs_tenant_id": "tenant-a",
            "is_active": True,
            "zetherion_tenant_id": "11111111-1111-1111-1111-111111111111",
        }
    )
    storage.create_admin_change = AsyncMock(
        return_value={"change_id": "chg_msg_send", "status": "pending"}
    )
    approval_required = TrustPolicyDecision(
        action="messaging.send",
        action_class=TrustActionClass.CRITICAL,
        outcome=TrustDecisionOutcome.APPROVAL_REQUIRED,
        status=409,
        code="AI_APPROVAL_REQUIRED",
        message="This action requires approval before apply",
        details={},
        requires_two_person=True,
    )
    monkeypatch.setattr(
        "zetherion_ai.cgs_gateway.routes.internal_admin._TRUST_POLICY_EVALUATOR",
        SimpleNamespace(evaluate=lambda **_: approval_required),
    )

    app["cgs_skills_client"].request_tenant_admin_json = AsyncMock(return_value=(202, {"ok": True}))
    app["cgs_skills_client"].request_admin_json = AsyncMock(return_value=(202, {"ok": True}))

    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/service/ai/v1/internal/admin/tenants/tenant-a/messaging/messages/chat-1/send",
            json={"provider": "whatsapp", "text": "hello"},
        )
        assert response.status == 409
        body = await response.json()
        assert body["error"]["code"] == "AI_APPROVAL_REQUIRED"
        assert body["error"]["details"]["change_ticket_id"] == "chg_msg_send"

    app["cgs_skills_client"].request_tenant_admin_json.assert_not_awaited()


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
        assert resp.status == 503
        body = await resp.json()
        assert body["error"]["code"] == "AI_UPSTREAM_5XX"


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


@pytest.mark.asyncio
async def test_internal_blog_publish_success_and_duplicate() -> None:
    app, storage, _ = _runtime_app()
    payload = {
        "idempotency_key": "blog-abcdef1",
        "source": "zetherion-windows-post-deploy",
        "sha": "abcdef1",
        "repo": "owner/repo",
        "release_tag": "v1.2.3",
        "title": "Release v1.2.3",
        "slug": "release-v1-2-3",
        "meta_description": "Meta description",
        "excerpt": "Excerpt",
        "primary_keyword": "release notes",
        "content_markdown": "# Release Notes",
        "json_ld": {"blog_posting": {}, "faq_page": {}},
        "models": {"draft": "gpt-5.2", "refine": "claude-sonnet-4-6"},
        "published_at": "2026-03-03T00:00:00Z",
    }
    payload_fp = fingerprint_payload(payload)
    storage.find_blog_publish_receipt = AsyncMock(
        side_effect=[None, {"payload_fingerprint": payload_fp}]
    )
    storage.create_blog_publish_receipt = AsyncMock(
        return_value={
            "receipt_id": "blog_1",
            "idempotency_key": "blog-abcdef1",
            "sha": "abcdef1",
            "published_at": "2026-03-03T00:00:00Z",
        }
    )

    async with TestClient(TestServer(app)) as client:
        created = await client.post(
            "/service/ai/v1/internal/blog/publish",
            headers={
                "Authorization": "Bearer blog-token",
                "Idempotency-Key": "blog-abcdef1",
            },
            json=payload,
        )
        assert created.status == 201
        created_body = await created.json()
        assert created_body["data"]["status"] == "published"

        duplicated = await client.post(
            "/service/ai/v1/internal/blog/publish",
            headers={
                "Authorization": "Bearer blog-token",
                "Idempotency-Key": "blog-abcdef1",
            },
            json=payload,
        )
        assert duplicated.status == 409
        dup_body = await duplicated.json()
        assert dup_body["data"]["status"] == "duplicate"


@pytest.mark.asyncio
async def test_internal_blog_publish_rejects_invalid_token_and_idempotency() -> None:
    app, storage, _ = _runtime_app()
    storage.find_blog_publish_receipt = AsyncMock(return_value=None)
    storage.create_blog_publish_receipt = AsyncMock()
    payload = {
        "idempotency_key": "blog-abcdef1",
        "source": "zetherion-windows-post-deploy",
        "sha": "abcdef1",
        "repo": "owner/repo",
        "release_tag": "v1.2.3",
        "title": "Release v1.2.3",
        "slug": "release-v1-2-3",
        "meta_description": "Meta description",
        "excerpt": "Excerpt",
        "primary_keyword": "release notes",
        "content_markdown": "# Release Notes",
        "json_ld": {"blog_posting": {}, "faq_page": {}},
        "models": {"draft": "gpt-5.2", "refine": "claude-sonnet-4-6"},
        "published_at": "2026-03-03T00:00:00Z",
    }

    async with TestClient(TestServer(app)) as client:
        invalid_token = await client.post(
            "/service/ai/v1/internal/blog/publish",
            headers={
                "Authorization": "Bearer wrong-token",
                "Idempotency-Key": "blog-abcdef1",
            },
            json=payload,
        )
        assert invalid_token.status == 403

        mismatch = await client.post(
            "/service/ai/v1/internal/blog/publish",
            headers={
                "Authorization": "Bearer blog-token",
                "Idempotency-Key": "blog-abcdef1",
            },
            json={**payload, "sha": "bbbbbbb"},
        )
        assert mismatch.status == 400
