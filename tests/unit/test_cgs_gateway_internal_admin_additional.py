"""Additional branch coverage for internal admin CGS routes."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import zetherion_ai.cgs_gateway.routes.internal_admin as internal_admin_routes
from zetherion_ai.cgs_gateway.models import AuthPrincipal
from zetherion_ai.cgs_gateway.routes._utils import fingerprint_payload
from zetherion_ai.cgs_gateway.routes.internal_admin import (
    _claim_scopes,
    _has_step_up,
    register_internal_admin_routes,
)
from zetherion_ai.cgs_gateway.server import create_error_middleware
from zetherion_ai.security.trust_policy import (
    TrustActionClass,
    TrustDecisionOutcome,
    TrustPolicyDecision,
)


def _admin_app(
    *,
    roles: list[str] | None = None,
    scopes: list[str] | None = None,
    claims: dict[str, object] | None = None,
) -> tuple[web.Application, MagicMock, MagicMock]:
    @web.middleware
    async def inject_context(request: web.Request, handler):
        request["principal"] = AuthPrincipal(
            sub="operator-1",
            tenant_id="tenant-a",
            roles=roles or ["operator"],
            scopes=scopes or ["cgs:internal", "cgs:zetherion-admin"],
            claims=claims or {},
        )
        request["request_id"] = "req_admin_extra"
        return await handler(request)

    storage = MagicMock()
    skills = MagicMock()

    app = web.Application(middlewares=[inject_context, create_error_middleware()])
    app["cgs_storage"] = storage
    app["cgs_skills_client"] = skills
    register_internal_admin_routes(app)
    return app, storage, skills


def _active_mapping() -> dict[str, object]:
    return {
        "cgs_tenant_id": "tenant-a",
        "is_active": True,
        "zetherion_tenant_id": "11111111-1111-1111-1111-111111111111",
    }


def _denied_policy(action: str) -> TrustPolicyDecision:
    return TrustPolicyDecision(
        action=action,
        action_class=TrustActionClass.CRITICAL,
        outcome=TrustDecisionOutcome.DENY,
        status=403,
        code="AI_TRUST_POLICY_DENIED",
        message="Blocked by policy",
        details={"action": action},
    )


class _MappingLikeChange:
    def __init__(self, values: dict[str, object]) -> None:
        self._values = values

    def get(self, key: str, default: object | None = None) -> object | None:
        return self._values.get(key, default)


def test_internal_admin_scope_claims_and_step_up_helpers() -> None:
    scope_values = _claim_scopes({"scope": "cgs:internal cgs:zetherion-admin"})
    assert "cgs:internal" in scope_values
    assert "cgs:zetherion-admin" in scope_values

    scope_values = _claim_scopes({"scopes": ["cgs:zetherion-admin", " cgs:internal "]})
    assert scope_values == {"cgs:zetherion-admin", "cgs:internal"}

    assert _has_step_up({"acr": "mfa"}) is True
    assert _has_step_up({"amr": ["otp"]}) is True
    assert _has_step_up({"amr": ["pwd"]}) is False


@pytest.mark.asyncio
async def test_internal_admin_validation_error_matrix() -> None:
    app, storage, skills = _admin_app(
        scopes=["cgs:internal", "cgs:zetherion-admin", "cgs:zetherion-secrets-admin"],
        claims={"step_up": True, "allowed_tenants": ["tenant-a"]},
    )
    storage.get_tenant_mapping = AsyncMock(return_value=_active_mapping())
    skills.request_tenant_admin_json = AsyncMock(return_value=(200, {"ok": True}))

    cases = [
        (
            "post",
            "/service/ai/v1/internal/admin/tenants/tenant-a/discord-users",
            {"discord_user_id": 0},
        ),
        (
            "patch",
            "/service/ai/v1/internal/admin/tenants/tenant-a/discord-users/5/role",
            {"role": ""},
        ),
        (
            "put",
            "/service/ai/v1/internal/admin/tenants/tenant-a/discord-bindings/guilds/10",
            {"priority": -1},
        ),
        (
            "put",
            "/service/ai/v1/internal/admin/tenants/tenant-a/discord-bindings/channels/20",
            {"guild_id": 0},
        ),
        (
            "put",
            "/service/ai/v1/internal/admin/tenants/tenant-a/settings/models/default_provider",
            {"data_type": ""},
        ),
        (
            "put",
            "/service/ai/v1/internal/admin/tenants/tenant-a/secrets/OPENAI_API_KEY",
            {"value": ""},
        ),
        (
            "put",
            "/service/ai/v1/internal/admin/tenants/tenant-a/email/providers/google/oauth-app",
            {"redirect_uri": ""},
        ),
        (
            "post",
            "/service/ai/v1/internal/admin/tenants/tenant-a/email/mailboxes/connect/start",
            {"provider": ""},
        ),
        (
            "patch",
            "/service/ai/v1/internal/admin/tenants/tenant-a/email/mailboxes/mailbox-1",
            {"status": ""},
        ),
        (
            "post",
            "/service/ai/v1/internal/admin/tenants/tenant-a/email/mailboxes/mailbox-1/sync",
            {"max_results": 0},
        ),
        (
            "put",
            "/service/ai/v1/internal/admin/tenants/tenant-a/email/mailboxes/mailbox-1/calendar-primary",
            {"calendar_id": ""},
        ),
        (
            "post",
            "/service/ai/v1/internal/admin/tenants/tenant-a/email/insights/reindex",
            {"insight_type": "x" * 121},
        ),
        (
            "post",
            "/service/ai/v1/internal/admin/tenants/tenant-a/automerge/execute",
            {"repository": "not-a-repo", "branch_guard_passed": True, "risk_guard_passed": True},
        ),
        (
            "delete",
            "/service/ai/v1/internal/admin/tenants/tenant-a/messaging/messages",
            {"message_ids": "not-an-array"},
        ),
        (
            "put",
            "/service/ai/v1/internal/admin/tenants/tenant-a/workers/nodes/node-1/capabilities",
            {"capabilities": "not-an-array"},
        ),
        (
            "post",
            "/service/ai/v1/internal/admin/tenants/tenant-a/workers/nodes/node-1/quarantine",
            {"metadata": "not-an-object"},
        ),
        (
            "post",
            "/service/ai/v1/internal/admin/tenants/tenant-a/workers/nodes/node-1/unquarantine",
            {"health_status": ""},
        ),
        (
            "post",
            "/service/ai/v1/internal/admin/tenants/tenant-a/workers/jobs/job-1/retry",
            {"reason": {"invalid": True}},
        ),
        (
            "post",
            "/service/ai/v1/internal/admin/tenants/tenant-a/workers/jobs/job-1/cancel",
            {"reason": {"invalid": True}},
        ),
        ("post", "/service/ai/v1/internal/admin/tenants/tenant-a/changes", {"action": ""}),
        (
            "post",
            "/service/ai/v1/internal/admin/tenants/tenant-a/changes/chg-1/approve",
            {"reason": {"invalid": True}},
        ),
        (
            "post",
            "/service/ai/v1/internal/admin/tenants/tenant-a/changes/chg-1/reject",
            {"reason": {"invalid": True}},
        ),
    ]

    async with TestClient(TestServer(app)) as client:
        for method, path, payload in cases:
            resp = await getattr(client, method)(path, json=payload)
            assert resp.status == 400
            body = await resp.json()
            assert body["error"]["code"] == "AI_BAD_REQUEST"


@pytest.mark.asyncio
async def test_internal_admin_replay_paths_for_change_workflow() -> None:
    app, storage, _ = _admin_app(
        scopes=["cgs:internal", "cgs:zetherion-admin"],
        claims={"step_up": True, "allowed_tenants": ["tenant-a"]},
    )
    create_payload = {
        "action": "setting.put",
        "target": None,
        "payload": {"k": "v"},
        "reason": None,
    }
    approve_payload = {"change_id": "chg-1", "reason": "ship", "decision": "approve"}
    reject_payload = {"change_id": "chg-1", "reason": "stop", "decision": "reject"}
    storage.get_idempotency_record = AsyncMock(
        side_effect=[
            {
                "request_fingerprint": fingerprint_payload(create_payload),
                "response_status": 201,
                "response_body": "cached-create",
            },
            {
                "request_fingerprint": fingerprint_payload(approve_payload),
                "response_status": 200,
                "response_body": {
                    "request_id": "req_old",
                    "data": {"approved": True},
                    "error": None,
                },
            },
            {
                "request_fingerprint": fingerprint_payload(reject_payload),
                "response_status": 200,
                "response_body": {
                    "request_id": "req_old",
                    "data": {"rejected": True},
                    "error": None,
                },
            },
        ]
    )

    async with TestClient(TestServer(app)) as client:
        created = await client.post(
            "/service/ai/v1/internal/admin/tenants/tenant-a/changes",
            headers={"Idempotency-Key": "idem-create"},
            json={"action": "setting.put", "payload": {"k": "v"}},
        )
        assert created.status == 201
        assert created.headers["X-Idempotent-Replay"] == "true"
        created_body = await created.json()
        assert created_body["request_id"] == "req_admin_extra"
        assert created_body["data"] is None

        approved = await client.post(
            "/service/ai/v1/internal/admin/tenants/tenant-a/changes/chg-1/approve",
            headers={"Idempotency-Key": "idem-approve"},
            json={"reason": "ship"},
        )
        assert approved.status == 200
        assert approved.headers["X-Idempotent-Replay"] == "true"

        rejected = await client.post(
            "/service/ai/v1/internal/admin/tenants/tenant-a/changes/chg-1/reject",
            headers={"Idempotency-Key": "idem-reject"},
            json={"reason": "stop"},
        )
        assert rejected.status == 200
        assert rejected.headers["X-Idempotent-Replay"] == "true"


@pytest.mark.asyncio
async def test_internal_admin_submit_change_blocks_denied_trust_policy_actions() -> None:
    app, storage, _ = _admin_app(
        scopes=["cgs:internal", "cgs:zetherion-admin"],
        claims={"step_up": True, "allowed_tenants": ["tenant-a"]},
    )
    storage.create_admin_change = AsyncMock()

    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/service/ai/v1/internal/admin/tenants/tenant-a/changes",
            json={
                "action": "automerge.execute",
                "payload": {"branch_guard_passed": True, "risk_guard_passed": True},
            },
        )
        assert resp.status == 403
        body = await resp.json()
        assert body["error"]["code"] == "AI_TRUST_POLICY_DENIED"

    storage.create_admin_change.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "json_body", "action"),
    [
        ("/service/ai/v1/internal/admin/tenants/tenant-a/changes", None, "admin.change.list"),
        (
            "/service/ai/v1/internal/admin/tenants/tenant-a/changes/chg-1/approve",
            {"reason": "ship"},
            "admin.change.approve",
        ),
        (
            "/service/ai/v1/internal/admin/tenants/tenant-a/changes/chg-1/reject",
            {"reason": "stop"},
            "admin.change.reject",
        ),
    ],
)
async def test_internal_admin_change_workflow_policy_denials(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    json_body: dict[str, str] | None,
    action: str,
) -> None:
    app, storage, _ = _admin_app(
        scopes=["cgs:internal", "cgs:zetherion-admin"],
        claims={"step_up": True, "allowed_tenants": ["tenant-a"]},
    )

    monkeypatch.setattr(
        internal_admin_routes._TRUST_POLICY_EVALUATOR,
        "evaluate",
        lambda tenant_id, action, context: _denied_policy(action),
    )

    async with TestClient(TestServer(app)) as client:
        if json_body is None:
            resp = await client.get(path)
        else:
            resp = await client.post(path, json=json_body)
        assert resp.status == 403
        body = await resp.json()
        assert body["error"]["code"] == "AI_TRUST_POLICY_DENIED"

    storage.list_admin_changes.assert_not_called()
    storage.get_admin_change.assert_not_called()


@pytest.mark.asyncio
async def test_internal_admin_body_ticket_and_upstream_request_id_capture() -> None:
    app, storage, skills = _admin_app(
        scopes=["cgs:internal", "cgs:zetherion-admin"],
        claims={"step_up": True, "allowed_tenants": ["tenant-a"], "email": "ops@example.com"},
    )
    storage.get_tenant_mapping = AsyncMock(return_value=_active_mapping())
    skills.request_tenant_admin_json = AsyncMock(
        return_value=(200, {"request_id": "upstream-123", "ok": True})
    )

    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/service/ai/v1/internal/admin/tenants/tenant-a/discord-users",
            json={
                "discord_user_id": 42,
                "role": "user",
                "change_ticket_id": "chg-body-ticket",
            },
        )
        assert resp.status == 201
        body = await resp.json()
        assert body["data"]["ok"] is True

    actor = skills.request_tenant_admin_json.await_args.kwargs["actor"]
    assert actor["change_ticket_id"] == "chg-body-ticket"
    assert actor["actor_email"] == "ops@example.com"


@pytest.mark.asyncio
async def test_internal_admin_high_risk_guard_edge_cases() -> None:
    app, storage, _ = _admin_app(
        scopes=["cgs:internal", "cgs:zetherion-admin", "cgs:zetherion-secrets-admin"],
        claims={"step_up": True, "allowed_tenants": ["tenant-a"]},
    )
    storage.get_admin_change = AsyncMock(
        side_effect=[
            _MappingLikeChange(
                {
                    "cgs_tenant_id": "tenant-a",
                    "action": "secret.delete",
                    "status": "approved",
                    "requested_by": "requester",
                    "approved_by": "approver",
                }
            ),
            {
                "cgs_tenant_id": "tenant-a",
                "action": "secret.delete",
                "status": "approved",
                "requested_by": "same-person",
                "approved_by": "same-person",
            },
            {
                "cgs_tenant_id": "tenant-a",
                "action": "secret.delete",
                "status": "approved",
                "requested_by": "operator-1",
                "approved_by": "other-approver",
            },
        ]
    )

    async with TestClient(TestServer(app)) as client:
        invalid_payload = await client.delete(
            "/service/ai/v1/internal/admin/tenants/tenant-a/secrets/OPENAI_API_KEY",
            params={"change_ticket_id": "chg-invalid"},
        )
        assert invalid_payload.status == 409
        invalid_body = await invalid_payload.json()
        assert invalid_body["error"]["code"] == "AI_APPROVAL_INVALID"

        two_person = await client.delete(
            "/service/ai/v1/internal/admin/tenants/tenant-a/secrets/OPENAI_API_KEY",
            params={"change_ticket_id": "chg-two-person"},
        )
        assert two_person.status == 409
        two_person_body = await two_person.json()
        assert two_person_body["error"]["code"] == "AI_APPROVAL_TWO_PERSON_REQUIRED"

        requester_apply = await client.delete(
            "/service/ai/v1/internal/admin/tenants/tenant-a/secrets/OPENAI_API_KEY",
            params={"change_ticket_id": "chg-self-apply"},
        )
        assert requester_apply.status == 409
        requester_apply_body = await requester_apply.json()
        assert requester_apply_body["error"]["code"] == "AI_APPROVAL_TWO_PERSON_REQUIRED"


@pytest.mark.asyncio
async def test_internal_admin_upstream_error_mapping_and_mutation_replay() -> None:
    app, storage, skills = _admin_app(
        scopes=["cgs:internal", "cgs:zetherion-admin"],
        claims={"step_up": True, "allowed_tenants": ["tenant-a"]},
    )
    storage.get_tenant_mapping = AsyncMock(return_value=_active_mapping())
    skills.request_tenant_admin_json = AsyncMock(return_value=(500, {"detail": "boom"}))

    async with TestClient(TestServer(app)) as client:
        failed = await client.get("/service/ai/v1/internal/admin/tenants/tenant-a/settings")
        assert failed.status == 502
        failed_body = await failed.json()
        assert failed_body["error"]["code"] == "AI_SKILLS_UPSTREAM_ERROR"

    replay_payload = {
        "subpath": "/settings/ns/key",
        "payload": {"value": "v", "data_type": "string"},
        "change_ticket_id": None,
    }
    storage.get_idempotency_record = AsyncMock(
        return_value={
            "request_fingerprint": fingerprint_payload(replay_payload),
            "response_status": 200,
            "response_body": {"request_id": "req_cached", "data": {"ok": True}, "error": None},
        }
    )
    skills.request_tenant_admin_json = AsyncMock(return_value=(200, {"ok": True}))

    async with TestClient(TestServer(app)) as client:
        replay = await client.put(
            "/service/ai/v1/internal/admin/tenants/tenant-a/settings/ns/key",
            headers={"Idempotency-Key": "idem-setting"},
            json={"value": "v"},
        )
        assert replay.status == 200
        assert replay.headers["X-Idempotent-Replay"] == "true"
        replay_body = await replay.json()
        assert replay_body["request_id"] == "req_cached"

    skills.request_tenant_admin_json.assert_not_awaited()
