"""Internal CGS operator routes for tenant admin control-plane actions."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from aiohttp import web
from pydantic import ValidationError

from zetherion_ai.cgs_gateway.errors import GatewayError, map_upstream_error, success_response
from zetherion_ai.cgs_gateway.middleware import principal_is_operator
from zetherion_ai.cgs_gateway.models import (
    TenantAdminAutomergeExecuteRequest,
    TenantAdminChangeCreateRequest,
    TenantAdminChangeDecisionRequest,
    TenantAdminChannelBindingRequest,
    TenantAdminDiscordRolePatchRequest,
    TenantAdminDiscordUserCreateRequest,
    TenantAdminEmailOAuthAppPutRequest,
    TenantAdminGuildBindingRequest,
    TenantAdminInsightsReindexRequest,
    TenantAdminMailboxConnectStartRequest,
    TenantAdminMailboxPatchRequest,
    TenantAdminMailboxSetPrimaryCalendarRequest,
    TenantAdminMailboxSyncRequest,
    TenantAdminMessagingChatPolicyPutRequest,
    TenantAdminMessagingDeleteRequest,
    TenantAdminMessagingProviderPutRequest,
    TenantAdminMessagingSendRequest,
    TenantAdminSecretPutRequest,
    TenantAdminSettingPutRequest,
)
from zetherion_ai.cgs_gateway.routes._utils import (
    enforce_mutation_rate_limit,
    enforce_tenant_access,
    fingerprint_payload,
    json_object,
    principal,
    request_id,
    resolve_active_mapping,
)
from zetherion_ai.security.trust_policy import TrustPolicyDecision, TrustPolicyEvaluator

_ADMIN_SCOPE = "cgs:zetherion-admin"
_SECRETS_SCOPE = "cgs:zetherion-secrets-admin"
_STEP_UP_ACR_VALUES = {"mfa", "aal2", "aal3", "urn:mfa"}
_TRUST_POLICY_EVALUATOR = TrustPolicyEvaluator()


def _scope_set(values: list[str]) -> set[str]:
    return {scope.strip().lower() for scope in values if scope.strip()}


def _claim_scopes(claims: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    scope = claims.get("scope")
    if isinstance(scope, str):
        out.update(_scope_set(scope.split()))
    scopes = claims.get("scopes")
    if isinstance(scopes, list):
        out.update(_scope_set([str(s) for s in scopes]))
    return out


def _has_step_up(claims: dict[str, Any]) -> bool:
    if bool(claims.get("step_up")):
        return True
    acr = claims.get("acr")
    if isinstance(acr, str) and acr.strip().lower() in _STEP_UP_ACR_VALUES:
        return True
    amr = claims.get("amr")
    return isinstance(amr, list) and any(
        str(method).strip().lower() in {"mfa", "otp", "hwk"} for method in amr
    )


def _ensure_internal_admin_access(
    request: web.Request,
    *,
    mutating: bool,
    requires_secrets_scope: bool = False,
) -> None:
    p = principal(request)
    if not principal_is_operator(p):
        raise GatewayError(
            code="AI_AUTH_FORBIDDEN",
            message="Operator scope is required for internal admin endpoints",
            status=403,
        )

    scopes = _scope_set(p.scopes) | _claim_scopes(p.claims)
    if _ADMIN_SCOPE not in scopes and "cgs:admin" not in scopes and "cgs:internal" not in scopes:
        raise GatewayError(
            code="AI_AUTH_FORBIDDEN",
            message="Missing cgs:zetherion-admin scope",
            status=403,
        )
    if requires_secrets_scope and _SECRETS_SCOPE not in scopes:
        raise GatewayError(
            code="AI_AUTH_FORBIDDEN",
            message="Missing cgs:zetherion-secrets-admin scope",
            status=403,
        )
    if mutating and not _has_step_up(p.claims):
        raise GatewayError(
            code="AI_AUTH_STEP_UP_REQUIRED",
            message="Step-up authentication is required for mutating admin actions",
            status=403,
        )


def _ensure_operator_tenant_access(request: web.Request, cgs_tenant_id: str) -> None:
    p = principal(request)
    enforce_tenant_access(p, cgs_tenant_id)
    allowed = p.claims.get("allowed_tenants")
    if isinstance(allowed, list) and allowed:
        normalized = {str(item) for item in allowed}
        if cgs_tenant_id not in normalized:
            raise GatewayError(
                code="AI_AUTH_FORBIDDEN",
                message="Operator is not authorized for this tenant",
                status=403,
            )


def _build_actor_context(
    request: web.Request,
    *,
    change_ticket_id: str | None = None,
) -> dict[str, Any]:
    p = principal(request)
    claims = p.claims if isinstance(p.claims, dict) else {}
    actor_email = claims.get("email")
    return {
        "actor_sub": p.sub,
        "actor_roles": p.roles,
        "actor_email": str(actor_email) if actor_email else None,
        "request_id": request_id(request),
        "change_ticket_id": change_ticket_id,
        "timestamp": datetime.now(UTC).isoformat(),
        "nonce": uuid4().hex,
    }


def _change_ticket_from_request(
    request: web.Request,
    payload_change_ticket_id: str | None = None,
) -> str | None:
    from_query = request.query.get("change_ticket_id")
    if isinstance(from_query, str) and from_query.strip():
        return from_query.strip()
    if isinstance(payload_change_ticket_id, str) and payload_change_ticket_id.strip():
        return payload_change_ticket_id.strip()
    return None


def _derive_policy_action(method: str, subpath: str, payload: dict[str, Any] | None) -> str:
    method_upper = method.upper()
    normalized = subpath.lower()

    if normalized.startswith("/secrets/"):
        if method_upper == "PUT":
            return "secret.put"
        if method_upper == "DELETE":
            return "secret.delete"
    if (
        normalized.startswith("/email/providers/")
        and normalized.endswith("/oauth-app")
        and method_upper == "PUT"
    ):
        return "email.oauth_app.put"
    if normalized.startswith("/email/mailboxes/") and method_upper == "DELETE":
        return "email.mailbox.delete"
    if normalized.startswith("/discord-users/") and normalized.endswith("/role"):
        role = str((payload or {}).get("role", "")).strip().lower()
        if role == "owner":
            return "discord.role.owner"

    if normalized.startswith("/messaging/"):
        if method_upper == "POST" and normalized.endswith("/ingest"):
            return "messaging.ingest"
        if method_upper == "POST" and normalized.endswith("/send"):
            return "messaging.send"
        if method_upper == "DELETE" and normalized == "/messaging/messages":
            return "messaging.delete"
        return "tenant_admin.read" if method_upper == "GET" else "tenant_admin.mutate"

    if normalized.startswith("/automerge/") and method_upper == "POST":
        return "automerge.execute"

    if method_upper == "GET":
        return "tenant_admin.read"
    return "tenant_admin.mutate"


def _derive_policy_target(subpath: str, payload: dict[str, Any] | None) -> str | None:
    if payload:
        for key in ("chat_id", "mailbox_id", "target", "name"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    parts = [part for part in subpath.split("/") if part]
    if not parts:
        return None
    if parts[-1] == "send" and len(parts) >= 2:
        return parts[-2]
    return parts[-1]


def _derive_policy_context(
    method: str,
    subpath: str,
    payload: dict[str, Any] | None,
    query: dict[str, str] | None,
) -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "method": method.upper(),
        "subpath": subpath,
    }
    normalized = subpath.lower()
    if normalized.startswith("/messaging/"):
        query_chat_id = str((query or {}).get("chat_id") or "").strip()
        target = _derive_policy_target(subpath, payload)
        if query_chat_id:
            ctx["chat_id"] = query_chat_id
        elif (
            target
            and not normalized.endswith("/messages")
            and not normalized.endswith("/messages/export")
        ):
            ctx["chat_id"] = target
        if method.upper() == "POST" and normalized.endswith("/send"):
            body = payload or {}
            ctx["explicitly_elevated"] = bool(body.get("explicitly_elevated"))
        if method.upper() == "DELETE" and normalized == "/messaging/messages":
            body = payload or {}
            raw_chat_id = str(body.get("chat_id") or "").strip()
            if raw_chat_id:
                ctx["chat_id"] = raw_chat_id
            ctx["explicitly_elevated"] = bool(body.get("explicitly_elevated"))
    if normalized.startswith("/automerge/"):
        body = payload or {}
        ctx["branch_guard_passed"] = bool(body.get("branch_guard_passed"))
        ctx["risk_guard_passed"] = bool(body.get("risk_guard_passed"))
        ctx["explicitly_elevated"] = bool(body.get("explicitly_elevated"))
    return ctx


async def _enforce_trust_policy(
    request: web.Request,
    *,
    cgs_tenant_id: str,
    zetherion_tenant_id: str | None,
    method: str,
    subpath: str,
    payload: dict[str, Any] | None,
    query: dict[str, str] | None,
    change_ticket_id: str | None,
) -> str | None:
    action = _derive_policy_action(method, subpath, payload)
    context = _derive_policy_context(method, subpath, payload, query)
    decision: TrustPolicyDecision = _TRUST_POLICY_EVALUATOR.evaluate(
        tenant_id=zetherion_tenant_id,
        action=action,
        context=context,
    )
    if decision.allowed:
        return change_ticket_id

    if decision.approval_required:
        approved_change = await _ensure_high_risk_approval(
            request,
            cgs_tenant_id=cgs_tenant_id,
            action=action,
            target=_derive_policy_target(subpath, payload),
            payload=payload or {},
            change_ticket_id=change_ticket_id,
        )
        return str(approved_change["change_id"])

    details = dict(decision.details)
    if decision.requires_two_person:
        details.setdefault("requires_two_person", True)
    raise GatewayError(
        code=decision.code,
        message=decision.message,
        status=decision.status,
        details=details,
    )


async def _admin_idempotency_check(
    request: web.Request,
    *,
    cgs_tenant_id: str,
    payload: dict[str, Any],
) -> tuple[str | None, str | None, dict[str, Any] | None]:
    key = request.headers.get("Idempotency-Key", "").strip()
    if not key:
        return None, None, None

    storage = request.app["cgs_storage"]
    fp = fingerprint_payload(payload)
    record = await storage.get_idempotency_record(
        cgs_tenant_id=cgs_tenant_id,
        endpoint=request.path,
        method=request.method.upper(),
        idempotency_key=key,
    )
    if record is None:
        return key, fp, None

    if str(record.get("request_fingerprint", "")) != fp:
        raise GatewayError(
            code="AI_IDEMPOTENCY_CONFLICT",
            message="Idempotency key already used with different payload",
            status=409,
        )

    cached_body = record.get("response_body")
    if not isinstance(cached_body, dict):
        cached_body = {"request_id": request_id(request), "data": None, "error": None}
    return (
        key,
        fp,
        {
            "status": int(record.get("response_status", 200)),
            "body": cached_body,
        },
    )


async def _admin_save_idempotency(
    request: web.Request,
    *,
    cgs_tenant_id: str,
    idempotency_key: str | None,
    request_fingerprint: str | None,
    response_status: int,
    response_body: dict[str, Any],
) -> None:
    if not idempotency_key or not request_fingerprint:
        return
    await request.app["cgs_storage"].save_idempotency_record(
        cgs_tenant_id=cgs_tenant_id,
        endpoint=request.path,
        method=request.method.upper(),
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
        response_status=response_status,
        response_body=response_body,
    )


async def _ensure_high_risk_approval(
    request: web.Request,
    *,
    cgs_tenant_id: str,
    action: str,
    target: str | None,
    payload: dict[str, Any],
    change_ticket_id: str | None,
) -> dict[str, Any]:
    storage = request.app["cgs_storage"]
    p = principal(request)
    rid = request_id(request)

    if not change_ticket_id:
        created = await storage.create_admin_change(
            cgs_tenant_id=cgs_tenant_id,
            action=action,
            target=target,
            payload=payload,
            requested_by=p.sub,
            request_id=rid,
            reason="Automatically created for high-risk action",
        )
        raise GatewayError(
            code="AI_APPROVAL_REQUIRED",
            message="This action requires approval before apply",
            status=409,
            details={
                "change_ticket_id": created["change_id"],
                "status": created["status"],
                "duplicate": bool(created.get("duplicate", False)),
            },
        )

    change = await storage.get_admin_change(change_ticket_id)
    if change is None:
        raise GatewayError(
            code="AI_APPROVAL_NOT_FOUND",
            message="Approval ticket not found",
            status=404,
        )
    if str(change.get("cgs_tenant_id")) != cgs_tenant_id:
        raise GatewayError(
            code="AI_AUTH_FORBIDDEN",
            message="Approval ticket tenant mismatch",
            status=403,
        )
    if str(change.get("action")) != action:
        raise GatewayError(
            code="AI_APPROVAL_INVALID",
            message="Approval ticket action mismatch",
            status=409,
            details={"expected_action": action, "actual_action": change.get("action")},
        )
    if str(change.get("status")) != "approved":
        raise GatewayError(
            code="AI_APPROVAL_REQUIRED",
            message="Approval ticket is not approved",
            status=409,
            details={"change_ticket_id": change_ticket_id, "status": change.get("status")},
        )
    if not isinstance(change, dict):
        raise GatewayError(
            code="AI_APPROVAL_INVALID",
            message="Approval ticket payload is invalid",
            status=409,
        )
    requested_by = str(change.get("requested_by", "") or "")
    approved_by = str(change.get("approved_by", "") or "")
    if requested_by and approved_by and requested_by == approved_by:
        raise GatewayError(
            code="AI_APPROVAL_TWO_PERSON_REQUIRED",
            message="Requester and approver must be different operators",
            status=409,
        )
    if requested_by and requested_by == p.sub:
        raise GatewayError(
            code="AI_APPROVAL_TWO_PERSON_REQUIRED",
            message="Requester cannot apply their own high-risk change",
            status=409,
        )
    request["change_ticket_id"] = change_ticket_id
    return change


async def _call_admin_upstream(
    request: web.Request,
    *,
    cgs_tenant_id: str,
    method: str,
    subpath: str,
    payload: dict[str, Any] | None = None,
    query: dict[str, str] | None = None,
    change_ticket_id: str | None = None,
) -> Any:
    mapping = await resolve_active_mapping(request.app["cgs_storage"], cgs_tenant_id)
    zetherion_tenant_id = str(mapping["zetherion_tenant_id"])
    effective_change_ticket_id = await _enforce_trust_policy(
        request,
        cgs_tenant_id=cgs_tenant_id,
        zetherion_tenant_id=zetherion_tenant_id,
        method=method,
        subpath=subpath,
        payload=payload,
        query=query,
        change_ticket_id=change_ticket_id,
    )
    actor = _build_actor_context(request, change_ticket_id=effective_change_ticket_id)
    skills_client = request.app["cgs_skills_client"]
    tenant_method = getattr(skills_client, "request_tenant_admin_json", None)
    if callable(tenant_method) and inspect.iscoroutinefunction(tenant_method):
        status, upstream = await skills_client.request_tenant_admin_json(
            method,
            tenant_id=zetherion_tenant_id,
            subpath=subpath,
            actor=actor,
            json_body=payload,
            query=query,
        )
    else:  # pragma: no cover - compatibility shim for legacy mocks
        upstream_path = f"/admin/tenants/{mapping['zetherion_tenant_id']}{subpath}"
        status, upstream = await skills_client.request_admin_json(
            method,
            upstream_path,
            actor=actor,
            json_body=payload,
            query=query,
        )
    if effective_change_ticket_id:
        request["change_ticket_id"] = effective_change_ticket_id
    request["cgs_tenant_id"] = cgs_tenant_id
    if isinstance(upstream, dict):
        upstream_request_id = upstream.get("request_id") or upstream.get("upstream_request_id")
        if isinstance(upstream_request_id, str) and upstream_request_id:
            request["upstream_request_id"] = upstream_request_id
    if status >= 400:
        raise map_upstream_error(status=status, payload=upstream, source="skills")
    return upstream


async def _admin_mutation_response(
    request: web.Request,
    *,
    cgs_tenant_id: str,
    method: str,
    subpath: str,
    payload: dict[str, Any] | None = None,
    query: dict[str, str] | None = None,
    change_ticket_id: str | None = None,
    response_status: int = 200,
) -> web.Response:
    enforce_mutation_rate_limit(request, cgs_tenant_id=cgs_tenant_id, family="admin")
    payload_for_idempotency: dict[str, Any] = {
        "subpath": subpath,
        "payload": payload or {},
        "change_ticket_id": change_ticket_id,
    }
    idem_key, idem_fp, cached = await _admin_idempotency_check(
        request,
        cgs_tenant_id=cgs_tenant_id,
        payload=payload_for_idempotency,
    )
    if cached is not None:
        response = web.json_response(cached["body"], status=cached["status"])
        response.headers["X-Idempotent-Replay"] = "true"
        return response

    data = await _call_admin_upstream(
        request,
        cgs_tenant_id=cgs_tenant_id,
        method=method,
        subpath=subpath,
        payload=payload,
        query=query,
        change_ticket_id=change_ticket_id,
    )
    envelope = {
        "request_id": request_id(request),
        "data": data,
        "error": None,
    }
    await _admin_save_idempotency(
        request,
        cgs_tenant_id=cgs_tenant_id,
        idempotency_key=idem_key,
        request_fingerprint=idem_fp,
        response_status=response_status,
        response_body=envelope,
    )
    return web.json_response(envelope, status=response_status)


async def handle_admin_list_discord_users(request: web.Request) -> web.Response:
    _ensure_internal_admin_access(request, mutating=False)
    cgs_tenant_id = request.match_info["tenant_id"]
    _ensure_operator_tenant_access(request, cgs_tenant_id)
    data = await _call_admin_upstream(
        request,
        cgs_tenant_id=cgs_tenant_id,
        method="GET",
        subpath="/discord-users",
    )
    return success_response(request_id(request), data)


async def handle_admin_add_discord_user(request: web.Request) -> web.Response:
    _ensure_internal_admin_access(request, mutating=True)
    cgs_tenant_id = request.match_info["tenant_id"]
    _ensure_operator_tenant_access(request, cgs_tenant_id)
    raw = await json_object(request)
    try:
        body = TenantAdminDiscordUserCreateRequest.model_validate(raw)
    except ValidationError as exc:
        raise GatewayError(
            code="AI_BAD_REQUEST",
            message="Validation failed",
            status=400,
            details={"errors": exc.errors()},
        ) from exc
    payload = body.model_dump(mode="json", exclude_none=True)
    return await _admin_mutation_response(
        request,
        cgs_tenant_id=cgs_tenant_id,
        method="POST",
        subpath="/discord-users",
        payload=payload,
        change_ticket_id=_change_ticket_from_request(request, body.change_ticket_id),
        response_status=201,
    )


async def handle_admin_delete_discord_user(request: web.Request) -> web.Response:
    _ensure_internal_admin_access(request, mutating=True)
    cgs_tenant_id = request.match_info["tenant_id"]
    _ensure_operator_tenant_access(request, cgs_tenant_id)
    return await _admin_mutation_response(
        request,
        cgs_tenant_id=cgs_tenant_id,
        method="DELETE",
        subpath=f"/discord-users/{request.match_info['discord_user_id']}",
        change_ticket_id=_change_ticket_from_request(request),
    )


async def handle_admin_patch_discord_user_role(request: web.Request) -> web.Response:
    _ensure_internal_admin_access(request, mutating=True)
    cgs_tenant_id = request.match_info["tenant_id"]
    _ensure_operator_tenant_access(request, cgs_tenant_id)
    raw = await json_object(request)
    try:
        body = TenantAdminDiscordRolePatchRequest.model_validate(raw)
    except ValidationError as exc:
        raise GatewayError(
            code="AI_BAD_REQUEST",
            message="Validation failed",
            status=400,
            details={"errors": exc.errors()},
        ) from exc

    change_ticket_id = _change_ticket_from_request(request, body.change_ticket_id)
    if body.role.lower() == "owner":
        change = await _ensure_high_risk_approval(
            request,
            cgs_tenant_id=cgs_tenant_id,
            action="discord.role.owner",
            target=request.match_info["discord_user_id"],
            payload=body.model_dump(mode="json", exclude_none=True),
            change_ticket_id=change_ticket_id,
        )
        change_ticket_id = str(change["change_id"])

    payload = body.model_dump(mode="json", exclude_none=True)
    response = await _admin_mutation_response(
        request,
        cgs_tenant_id=cgs_tenant_id,
        method="PATCH",
        subpath=f"/discord-users/{request.match_info['discord_user_id']}/role",
        payload=payload,
        change_ticket_id=change_ticket_id,
    )
    if body.role.lower() == "owner" and change_ticket_id:
        await request.app["cgs_storage"].mark_admin_change_applied(
            change_id=change_ticket_id,
            result={"status": "applied", "operation": "discord.role.owner"},
        )
    return response


async def handle_admin_list_discord_bindings(request: web.Request) -> web.Response:
    _ensure_internal_admin_access(request, mutating=False)
    cgs_tenant_id = request.match_info["tenant_id"]
    _ensure_operator_tenant_access(request, cgs_tenant_id)
    data = await _call_admin_upstream(
        request,
        cgs_tenant_id=cgs_tenant_id,
        method="GET",
        subpath="/discord-bindings",
    )
    return success_response(request_id(request), data)


async def handle_admin_put_guild_binding(request: web.Request) -> web.Response:
    _ensure_internal_admin_access(request, mutating=True)
    cgs_tenant_id = request.match_info["tenant_id"]
    _ensure_operator_tenant_access(request, cgs_tenant_id)
    raw = await json_object(request)
    try:
        body = TenantAdminGuildBindingRequest.model_validate(raw)
    except ValidationError as exc:
        raise GatewayError(
            code="AI_BAD_REQUEST",
            message="Validation failed",
            status=400,
            details={"errors": exc.errors()},
        ) from exc
    payload = body.model_dump(mode="json", exclude_none=True)
    return await _admin_mutation_response(
        request,
        cgs_tenant_id=cgs_tenant_id,
        method="PUT",
        subpath=f"/discord-bindings/guilds/{request.match_info['guild_id']}",
        payload=payload,
        change_ticket_id=_change_ticket_from_request(request, body.change_ticket_id),
    )


async def handle_admin_put_channel_binding(request: web.Request) -> web.Response:
    _ensure_internal_admin_access(request, mutating=True)
    cgs_tenant_id = request.match_info["tenant_id"]
    _ensure_operator_tenant_access(request, cgs_tenant_id)
    raw = await json_object(request)
    try:
        body = TenantAdminChannelBindingRequest.model_validate(raw)
    except ValidationError as exc:
        raise GatewayError(
            code="AI_BAD_REQUEST",
            message="Validation failed",
            status=400,
            details={"errors": exc.errors()},
        ) from exc
    payload = body.model_dump(mode="json", exclude_none=True)
    return await _admin_mutation_response(
        request,
        cgs_tenant_id=cgs_tenant_id,
        method="PUT",
        subpath=f"/discord-bindings/channels/{request.match_info['channel_id']}",
        payload=payload,
        change_ticket_id=_change_ticket_from_request(request, body.change_ticket_id),
    )


async def handle_admin_delete_channel_binding(request: web.Request) -> web.Response:
    _ensure_internal_admin_access(request, mutating=True)
    cgs_tenant_id = request.match_info["tenant_id"]
    _ensure_operator_tenant_access(request, cgs_tenant_id)
    return await _admin_mutation_response(
        request,
        cgs_tenant_id=cgs_tenant_id,
        method="DELETE",
        subpath=f"/discord-bindings/channels/{request.match_info['channel_id']}",
        change_ticket_id=_change_ticket_from_request(request),
    )


async def handle_admin_list_settings(request: web.Request) -> web.Response:
    _ensure_internal_admin_access(request, mutating=False)
    cgs_tenant_id = request.match_info["tenant_id"]
    _ensure_operator_tenant_access(request, cgs_tenant_id)
    data = await _call_admin_upstream(
        request,
        cgs_tenant_id=cgs_tenant_id,
        method="GET",
        subpath="/settings",
    )
    return success_response(request_id(request), data)


async def handle_admin_put_setting(request: web.Request) -> web.Response:
    _ensure_internal_admin_access(request, mutating=True)
    cgs_tenant_id = request.match_info["tenant_id"]
    _ensure_operator_tenant_access(request, cgs_tenant_id)
    raw = await json_object(request)
    try:
        body = TenantAdminSettingPutRequest.model_validate(raw)
    except ValidationError as exc:
        raise GatewayError(
            code="AI_BAD_REQUEST",
            message="Validation failed",
            status=400,
            details={"errors": exc.errors()},
        ) from exc
    payload = body.model_dump(mode="json", exclude_none=True)
    return await _admin_mutation_response(
        request,
        cgs_tenant_id=cgs_tenant_id,
        method="PUT",
        subpath=f"/settings/{request.match_info['namespace']}/{request.match_info['key']}",
        payload=payload,
        change_ticket_id=_change_ticket_from_request(request, body.change_ticket_id),
    )


async def handle_admin_delete_setting(request: web.Request) -> web.Response:
    _ensure_internal_admin_access(request, mutating=True)
    cgs_tenant_id = request.match_info["tenant_id"]
    _ensure_operator_tenant_access(request, cgs_tenant_id)
    return await _admin_mutation_response(
        request,
        cgs_tenant_id=cgs_tenant_id,
        method="DELETE",
        subpath=f"/settings/{request.match_info['namespace']}/{request.match_info['key']}",
        change_ticket_id=_change_ticket_from_request(request),
    )


async def handle_admin_list_secrets(request: web.Request) -> web.Response:
    _ensure_internal_admin_access(request, mutating=False, requires_secrets_scope=True)
    cgs_tenant_id = request.match_info["tenant_id"]
    _ensure_operator_tenant_access(request, cgs_tenant_id)
    data = await _call_admin_upstream(
        request,
        cgs_tenant_id=cgs_tenant_id,
        method="GET",
        subpath="/secrets",
    )
    return success_response(request_id(request), data)


async def handle_admin_put_secret(request: web.Request) -> web.Response:
    _ensure_internal_admin_access(request, mutating=True, requires_secrets_scope=True)
    cgs_tenant_id = request.match_info["tenant_id"]
    _ensure_operator_tenant_access(request, cgs_tenant_id)
    raw = await json_object(request)
    try:
        body = TenantAdminSecretPutRequest.model_validate(raw)
    except ValidationError as exc:
        raise GatewayError(
            code="AI_BAD_REQUEST",
            message="Validation failed",
            status=400,
            details={"errors": exc.errors()},
        ) from exc

    change_ticket_id = _change_ticket_from_request(request, body.change_ticket_id)
    change = await _ensure_high_risk_approval(
        request,
        cgs_tenant_id=cgs_tenant_id,
        action="secret.put",
        target=request.match_info["name"],
        payload={"description": body.description},
        change_ticket_id=change_ticket_id,
    )
    applied_change_id = str(change["change_id"])
    try:
        response = await _admin_mutation_response(
            request,
            cgs_tenant_id=cgs_tenant_id,
            method="PUT",
            subpath=f"/secrets/{request.match_info['name']}",
            payload=body.model_dump(mode="json", exclude_none=True),
            change_ticket_id=applied_change_id,
        )
    except Exception:
        await request.app["cgs_storage"].mark_admin_change_failed(
            change_id=applied_change_id,
            result={"status": "failed", "operation": "secret.put"},
        )
        raise
    await request.app["cgs_storage"].mark_admin_change_applied(
        change_id=applied_change_id,
        result={"status": "applied", "operation": "secret.put"},
    )
    return response


async def handle_admin_delete_secret(request: web.Request) -> web.Response:
    _ensure_internal_admin_access(request, mutating=True, requires_secrets_scope=True)
    cgs_tenant_id = request.match_info["tenant_id"]
    _ensure_operator_tenant_access(request, cgs_tenant_id)

    change_ticket_id = _change_ticket_from_request(request)
    change = await _ensure_high_risk_approval(
        request,
        cgs_tenant_id=cgs_tenant_id,
        action="secret.delete",
        target=request.match_info["name"],
        payload={},
        change_ticket_id=change_ticket_id,
    )
    applied_change_id = str(change["change_id"])
    try:
        response = await _admin_mutation_response(
            request,
            cgs_tenant_id=cgs_tenant_id,
            method="DELETE",
            subpath=f"/secrets/{request.match_info['name']}",
            change_ticket_id=applied_change_id,
        )
    except Exception:
        await request.app["cgs_storage"].mark_admin_change_failed(
            change_id=applied_change_id,
            result={"status": "failed", "operation": "secret.delete"},
        )
        raise
    await request.app["cgs_storage"].mark_admin_change_applied(
        change_id=applied_change_id,
        result={"status": "applied", "operation": "secret.delete"},
    )
    return response


async def handle_admin_list_audit(request: web.Request) -> web.Response:
    _ensure_internal_admin_access(request, mutating=False)
    cgs_tenant_id = request.match_info["tenant_id"]
    _ensure_operator_tenant_access(request, cgs_tenant_id)
    data = await _call_admin_upstream(
        request,
        cgs_tenant_id=cgs_tenant_id,
        method="GET",
        subpath="/audit",
    )
    return success_response(request_id(request), data)


async def handle_admin_get_email_oauth_app(request: web.Request) -> web.Response:
    _ensure_internal_admin_access(request, mutating=False, requires_secrets_scope=True)
    cgs_tenant_id = request.match_info["tenant_id"]
    _ensure_operator_tenant_access(request, cgs_tenant_id)
    provider = request.match_info.get("provider", "google")
    data = await _call_admin_upstream(
        request,
        cgs_tenant_id=cgs_tenant_id,
        method="GET",
        subpath=f"/email/providers/{provider}/oauth-app",
    )
    return success_response(request_id(request), data)


async def handle_admin_put_email_oauth_app(request: web.Request) -> web.Response:
    _ensure_internal_admin_access(request, mutating=True, requires_secrets_scope=True)
    cgs_tenant_id = request.match_info["tenant_id"]
    _ensure_operator_tenant_access(request, cgs_tenant_id)
    provider = request.match_info.get("provider", "google")
    raw = await json_object(request)
    try:
        body = TenantAdminEmailOAuthAppPutRequest.model_validate(raw)
    except ValidationError as exc:
        raise GatewayError(
            code="AI_BAD_REQUEST",
            message="Validation failed",
            status=400,
            details={"errors": exc.errors()},
        ) from exc

    change_ticket_id = _change_ticket_from_request(request, body.change_ticket_id)
    payload = body.model_dump(mode="json", exclude_none=True)
    approval = await _ensure_high_risk_approval(
        request,
        cgs_tenant_id=cgs_tenant_id,
        action="email.oauth_app.put",
        target=provider,
        payload={"redirect_uri": body.redirect_uri, "enabled": body.enabled},
        change_ticket_id=change_ticket_id,
    )
    applied_change_id = str(approval["change_id"])
    try:
        response = await _admin_mutation_response(
            request,
            cgs_tenant_id=cgs_tenant_id,
            method="PUT",
            subpath=f"/email/providers/{provider}/oauth-app",
            payload=payload,
            change_ticket_id=applied_change_id,
        )
    except Exception:
        await request.app["cgs_storage"].mark_admin_change_failed(
            change_id=applied_change_id,
            result={"status": "failed", "operation": "email.oauth_app.put"},
        )
        raise
    await request.app["cgs_storage"].mark_admin_change_applied(
        change_id=applied_change_id,
        result={"status": "applied", "operation": "email.oauth_app.put"},
    )
    return response


async def handle_admin_start_mailbox_connect(request: web.Request) -> web.Response:
    _ensure_internal_admin_access(request, mutating=True)
    cgs_tenant_id = request.match_info["tenant_id"]
    _ensure_operator_tenant_access(request, cgs_tenant_id)
    raw = await json_object(request, required=False)
    try:
        body = TenantAdminMailboxConnectStartRequest.model_validate(raw)
    except ValidationError as exc:
        raise GatewayError(
            code="AI_BAD_REQUEST",
            message="Validation failed",
            status=400,
            details={"errors": exc.errors()},
        ) from exc
    payload = body.model_dump(mode="json", exclude_none=True)
    return await _admin_mutation_response(
        request,
        cgs_tenant_id=cgs_tenant_id,
        method="POST",
        subpath=f"/email/oauth/{body.provider}/start",
        payload=payload,
        change_ticket_id=_change_ticket_from_request(request, body.change_ticket_id),
        response_status=201,
    )


async def handle_admin_mailbox_connect_callback(request: web.Request) -> web.Response:
    _ensure_internal_admin_access(request, mutating=True)
    cgs_tenant_id = request.match_info["tenant_id"]
    _ensure_operator_tenant_access(request, cgs_tenant_id)
    code = request.query.get("code", "").strip()
    state = request.query.get("state", "").strip()
    provider = request.query.get("provider", "google").strip().lower() or "google"
    if not code or not state:
        raise GatewayError(
            code="AI_BAD_REQUEST",
            message="Missing OAuth callback code/state",
            status=400,
        )
    payload = {"code": code, "state": state}
    return await _admin_mutation_response(
        request,
        cgs_tenant_id=cgs_tenant_id,
        method="POST",
        subpath=f"/email/oauth/{provider}/exchange",
        payload=payload,
    )


async def handle_admin_list_mailboxes(request: web.Request) -> web.Response:
    _ensure_internal_admin_access(request, mutating=False)
    cgs_tenant_id = request.match_info["tenant_id"]
    _ensure_operator_tenant_access(request, cgs_tenant_id)
    provider = request.query.get("provider", "google").strip() or "google"
    data = await _call_admin_upstream(
        request,
        cgs_tenant_id=cgs_tenant_id,
        method="GET",
        subpath="/email/accounts",
        query={"provider": provider},
    )
    return success_response(request_id(request), data)


async def handle_admin_patch_mailbox(request: web.Request) -> web.Response:
    _ensure_internal_admin_access(request, mutating=True)
    cgs_tenant_id = request.match_info["tenant_id"]
    _ensure_operator_tenant_access(request, cgs_tenant_id)
    raw = await json_object(request)
    try:
        body = TenantAdminMailboxPatchRequest.model_validate(raw)
    except ValidationError as exc:
        raise GatewayError(
            code="AI_BAD_REQUEST",
            message="Validation failed",
            status=400,
            details={"errors": exc.errors()},
        ) from exc
    payload = body.model_dump(mode="json", exclude_none=True)
    return await _admin_mutation_response(
        request,
        cgs_tenant_id=cgs_tenant_id,
        method="PATCH",
        subpath=f"/email/accounts/{request.match_info['mailbox_id']}",
        payload=payload,
        change_ticket_id=_change_ticket_from_request(request, body.change_ticket_id),
    )


async def handle_admin_delete_mailbox(request: web.Request) -> web.Response:
    _ensure_internal_admin_access(request, mutating=True)
    cgs_tenant_id = request.match_info["tenant_id"]
    _ensure_operator_tenant_access(request, cgs_tenant_id)
    change_ticket_id = _change_ticket_from_request(request)
    change = await _ensure_high_risk_approval(
        request,
        cgs_tenant_id=cgs_tenant_id,
        action="email.mailbox.delete",
        target=request.match_info["mailbox_id"],
        payload={},
        change_ticket_id=change_ticket_id,
    )
    applied_change_id = str(change["change_id"])
    try:
        response = await _admin_mutation_response(
            request,
            cgs_tenant_id=cgs_tenant_id,
            method="DELETE",
            subpath=f"/email/accounts/{request.match_info['mailbox_id']}",
            change_ticket_id=applied_change_id,
        )
    except Exception:
        await request.app["cgs_storage"].mark_admin_change_failed(
            change_id=applied_change_id,
            result={"status": "failed", "operation": "email.mailbox.delete"},
        )
        raise
    await request.app["cgs_storage"].mark_admin_change_applied(
        change_id=applied_change_id,
        result={"status": "applied", "operation": "email.mailbox.delete"},
    )
    return response


async def handle_admin_sync_mailbox(request: web.Request) -> web.Response:
    _ensure_internal_admin_access(request, mutating=True)
    cgs_tenant_id = request.match_info["tenant_id"]
    _ensure_operator_tenant_access(request, cgs_tenant_id)
    raw = await json_object(request, required=False)
    try:
        body = TenantAdminMailboxSyncRequest.model_validate(raw)
    except ValidationError as exc:
        raise GatewayError(
            code="AI_BAD_REQUEST",
            message="Validation failed",
            status=400,
            details={"errors": exc.errors()},
        ) from exc
    payload = body.model_dump(mode="json", exclude_none=True)
    return await _admin_mutation_response(
        request,
        cgs_tenant_id=cgs_tenant_id,
        method="POST",
        subpath=f"/email/accounts/{request.match_info['mailbox_id']}/sync",
        payload=payload,
        change_ticket_id=_change_ticket_from_request(request, body.change_ticket_id),
    )


async def handle_admin_list_critical_messages(request: web.Request) -> web.Response:
    _ensure_internal_admin_access(request, mutating=False)
    cgs_tenant_id = request.match_info["tenant_id"]
    _ensure_operator_tenant_access(request, cgs_tenant_id)
    query: dict[str, str] = {}
    for key in ("status", "severity", "limit"):
        value = request.query.get(key)
        if isinstance(value, str) and value.strip():
            query[key] = value.strip()
    data = await _call_admin_upstream(
        request,
        cgs_tenant_id=cgs_tenant_id,
        method="GET",
        subpath="/email/critical",
        query=query or None,
    )
    return success_response(request_id(request), data)


async def handle_admin_list_calendars(request: web.Request) -> web.Response:
    _ensure_internal_admin_access(request, mutating=False)
    cgs_tenant_id = request.match_info["tenant_id"]
    _ensure_operator_tenant_access(request, cgs_tenant_id)
    account_id = request.query.get("mailbox_id", "").strip()
    if not account_id:
        raise GatewayError(
            code="AI_BAD_REQUEST",
            message="Missing mailbox_id query parameter",
            status=400,
        )
    data = await _call_admin_upstream(
        request,
        cgs_tenant_id=cgs_tenant_id,
        method="GET",
        subpath="/email/calendars",
        query={"account_id": account_id},
    )
    return success_response(request_id(request), data)


async def handle_admin_set_mailbox_primary_calendar(request: web.Request) -> web.Response:
    _ensure_internal_admin_access(request, mutating=True)
    cgs_tenant_id = request.match_info["tenant_id"]
    _ensure_operator_tenant_access(request, cgs_tenant_id)
    raw = await json_object(request)
    try:
        body = TenantAdminMailboxSetPrimaryCalendarRequest.model_validate(raw)
    except ValidationError as exc:
        raise GatewayError(
            code="AI_BAD_REQUEST",
            message="Validation failed",
            status=400,
            details={"errors": exc.errors()},
        ) from exc
    payload = body.model_dump(mode="json", exclude_none=True)
    return await _admin_mutation_response(
        request,
        cgs_tenant_id=cgs_tenant_id,
        method="PUT",
        subpath=f"/email/accounts/{request.match_info['mailbox_id']}/calendar-primary",
        payload=payload,
        change_ticket_id=_change_ticket_from_request(request, body.change_ticket_id),
    )


async def handle_admin_list_email_insights(request: web.Request) -> web.Response:
    _ensure_internal_admin_access(request, mutating=False)
    cgs_tenant_id = request.match_info["tenant_id"]
    _ensure_operator_tenant_access(request, cgs_tenant_id)
    query: dict[str, str] = {}
    for key in ("insight_type", "min_confidence", "limit"):
        value = request.query.get(key)
        if isinstance(value, str) and value.strip():
            query[key] = value.strip()
    data = await _call_admin_upstream(
        request,
        cgs_tenant_id=cgs_tenant_id,
        method="GET",
        subpath="/email/insights",
        query=query or None,
    )
    return success_response(request_id(request), data)


async def handle_admin_reindex_email_insights(request: web.Request) -> web.Response:
    _ensure_internal_admin_access(request, mutating=True)
    cgs_tenant_id = request.match_info["tenant_id"]
    _ensure_operator_tenant_access(request, cgs_tenant_id)
    raw = await json_object(request, required=False)
    try:
        body = TenantAdminInsightsReindexRequest.model_validate(raw)
    except ValidationError as exc:
        raise GatewayError(
            code="AI_BAD_REQUEST",
            message="Validation failed",
            status=400,
            details={"errors": exc.errors()},
        ) from exc
    payload = body.model_dump(mode="json", exclude_none=True)
    return await _admin_mutation_response(
        request,
        cgs_tenant_id=cgs_tenant_id,
        method="POST",
        subpath="/email/insights/reindex",
        payload=payload,
        change_ticket_id=_change_ticket_from_request(request, body.change_ticket_id),
    )


async def handle_admin_get_messaging_provider_config(request: web.Request) -> web.Response:
    _ensure_internal_admin_access(request, mutating=False)
    cgs_tenant_id = request.match_info["tenant_id"]
    _ensure_operator_tenant_access(request, cgs_tenant_id)
    provider = request.match_info.get("provider", "whatsapp")
    data = await _call_admin_upstream(
        request,
        cgs_tenant_id=cgs_tenant_id,
        method="GET",
        subpath=f"/messaging/providers/{provider}/config",
    )
    return success_response(request_id(request), data)


async def handle_admin_put_messaging_provider_config(request: web.Request) -> web.Response:
    _ensure_internal_admin_access(request, mutating=True)
    cgs_tenant_id = request.match_info["tenant_id"]
    _ensure_operator_tenant_access(request, cgs_tenant_id)
    provider = request.match_info.get("provider", "whatsapp")
    raw = await json_object(request)
    try:
        body = TenantAdminMessagingProviderPutRequest.model_validate(raw)
    except ValidationError as exc:
        raise GatewayError(
            code="AI_BAD_REQUEST",
            message="Validation failed",
            status=400,
            details={"errors": exc.errors()},
        ) from exc

    payload = body.model_dump(mode="json", exclude_none=True)
    return await _admin_mutation_response(
        request,
        cgs_tenant_id=cgs_tenant_id,
        method="PUT",
        subpath=f"/messaging/providers/{provider}/config",
        payload=payload,
        change_ticket_id=_change_ticket_from_request(request, body.change_ticket_id),
    )


async def handle_admin_get_messaging_chat_policy(request: web.Request) -> web.Response:
    _ensure_internal_admin_access(request, mutating=False)
    cgs_tenant_id = request.match_info["tenant_id"]
    _ensure_operator_tenant_access(request, cgs_tenant_id)
    chat_id = request.match_info["chat_id"]
    provider = request.query.get("provider", "whatsapp").strip() or "whatsapp"
    data = await _call_admin_upstream(
        request,
        cgs_tenant_id=cgs_tenant_id,
        method="GET",
        subpath=f"/messaging/chats/{chat_id}/policy",
        query={"provider": provider},
    )
    return success_response(request_id(request), data)


async def handle_admin_put_messaging_chat_policy(request: web.Request) -> web.Response:
    _ensure_internal_admin_access(request, mutating=True)
    cgs_tenant_id = request.match_info["tenant_id"]
    _ensure_operator_tenant_access(request, cgs_tenant_id)
    chat_id = request.match_info["chat_id"]
    raw = await json_object(request)
    try:
        body = TenantAdminMessagingChatPolicyPutRequest.model_validate(raw)
    except ValidationError as exc:
        raise GatewayError(
            code="AI_BAD_REQUEST",
            message="Validation failed",
            status=400,
            details={"errors": exc.errors()},
        ) from exc

    payload = body.model_dump(mode="json", exclude_none=True)
    return await _admin_mutation_response(
        request,
        cgs_tenant_id=cgs_tenant_id,
        method="PUT",
        subpath=f"/messaging/chats/{chat_id}/policy",
        payload=payload,
        change_ticket_id=_change_ticket_from_request(request, body.change_ticket_id),
    )


async def handle_admin_list_messaging_chats(request: web.Request) -> web.Response:
    _ensure_internal_admin_access(request, mutating=False)
    cgs_tenant_id = request.match_info["tenant_id"]
    _ensure_operator_tenant_access(request, cgs_tenant_id)
    query: dict[str, str] = {}
    for key in ("provider", "include_inactive", "limit"):
        value = request.query.get(key)
        if isinstance(value, str) and value.strip():
            query[key] = value.strip()
    data = await _call_admin_upstream(
        request,
        cgs_tenant_id=cgs_tenant_id,
        method="GET",
        subpath="/messaging/chats",
        query=query or None,
    )
    return success_response(request_id(request), data)


async def handle_admin_list_messaging_messages(request: web.Request) -> web.Response:
    _ensure_internal_admin_access(request, mutating=False)
    cgs_tenant_id = request.match_info["tenant_id"]
    _ensure_operator_tenant_access(request, cgs_tenant_id)
    chat_id = request.query.get("chat_id", "").strip()
    if not chat_id:
        raise GatewayError(
            code="AI_BAD_REQUEST",
            message="Missing chat_id query parameter",
            status=400,
        )
    query: dict[str, str] = {"chat_id": chat_id}
    for key in ("provider", "direction", "limit"):
        value = request.query.get(key)
        if isinstance(value, str) and value.strip():
            query[key] = value.strip()
    data = await _call_admin_upstream(
        request,
        cgs_tenant_id=cgs_tenant_id,
        method="GET",
        subpath="/messaging/messages",
        query=query,
    )
    return success_response(request_id(request), data)


async def handle_admin_export_messaging_messages(request: web.Request) -> web.Response:
    _ensure_internal_admin_access(request, mutating=False)
    cgs_tenant_id = request.match_info["tenant_id"]
    _ensure_operator_tenant_access(request, cgs_tenant_id)
    query: dict[str, str] = {}
    for key in ("provider", "chat_id", "sender_id", "direction", "include_expired", "limit"):
        value = request.query.get(key)
        if isinstance(value, str) and value.strip():
            query[key] = value.strip()
    data = await _call_admin_upstream(
        request,
        cgs_tenant_id=cgs_tenant_id,
        method="GET",
        subpath="/messaging/messages/export",
        query=query or None,
    )
    return success_response(request_id(request), data)


async def handle_admin_delete_messaging_messages(request: web.Request) -> web.Response:
    _ensure_internal_admin_access(request, mutating=True)
    cgs_tenant_id = request.match_info["tenant_id"]
    _ensure_operator_tenant_access(request, cgs_tenant_id)
    raw = await json_object(request, required=False)
    try:
        body = TenantAdminMessagingDeleteRequest.model_validate(raw)
    except ValidationError as exc:
        raise GatewayError(
            code="AI_BAD_REQUEST",
            message="Validation failed",
            status=400,
            details={"errors": exc.errors()},
        ) from exc
    payload = body.model_dump(mode="json", exclude_none=True)
    return await _admin_mutation_response(
        request,
        cgs_tenant_id=cgs_tenant_id,
        method="DELETE",
        subpath="/messaging/messages",
        payload=payload,
        change_ticket_id=_change_ticket_from_request(request, body.change_ticket_id),
    )


async def handle_admin_send_messaging_message(request: web.Request) -> web.Response:
    _ensure_internal_admin_access(request, mutating=True)
    cgs_tenant_id = request.match_info["tenant_id"]
    _ensure_operator_tenant_access(request, cgs_tenant_id)
    chat_id = request.match_info["chat_id"]
    raw = await json_object(request)
    try:
        body = TenantAdminMessagingSendRequest.model_validate(raw)
    except ValidationError as exc:
        raise GatewayError(
            code="AI_BAD_REQUEST",
            message="Validation failed",
            status=400,
            details={"errors": exc.errors()},
        ) from exc

    payload = body.model_dump(mode="json", exclude_none=True)
    return await _admin_mutation_response(
        request,
        cgs_tenant_id=cgs_tenant_id,
        method="POST",
        subpath=f"/messaging/messages/{chat_id}/send",
        payload=payload,
        change_ticket_id=_change_ticket_from_request(request, body.change_ticket_id),
        response_status=202,
    )


async def handle_admin_list_security_events(request: web.Request) -> web.Response:
    _ensure_internal_admin_access(request, mutating=False)
    cgs_tenant_id = request.match_info["tenant_id"]
    _ensure_operator_tenant_access(request, cgs_tenant_id)
    query: dict[str, str] = {}
    for key in ("event_type", "severity", "action", "limit"):
        value = request.query.get(key)
        if isinstance(value, str) and value.strip():
            query[key] = value.strip()
    data = await _call_admin_upstream(
        request,
        cgs_tenant_id=cgs_tenant_id,
        method="GET",
        subpath="/security/events",
        query=query or None,
    )
    return success_response(request_id(request), data)


async def handle_admin_get_security_dashboard(request: web.Request) -> web.Response:
    _ensure_internal_admin_access(request, mutating=False)
    cgs_tenant_id = request.match_info["tenant_id"]
    _ensure_operator_tenant_access(request, cgs_tenant_id)
    query: dict[str, str] = {}
    for key in ("window_hours", "recent_limit"):
        value = request.query.get(key)
        if isinstance(value, str) and value.strip():
            query[key] = value.strip()
    data = await _call_admin_upstream(
        request,
        cgs_tenant_id=cgs_tenant_id,
        method="GET",
        subpath="/security/dashboard",
        query=query or None,
    )
    return success_response(request_id(request), data)


async def handle_admin_execute_automerge(request: web.Request) -> web.Response:
    _ensure_internal_admin_access(request, mutating=True)
    cgs_tenant_id = request.match_info["tenant_id"]
    _ensure_operator_tenant_access(request, cgs_tenant_id)
    raw = await json_object(request)
    try:
        body = TenantAdminAutomergeExecuteRequest.model_validate(raw)
    except ValidationError as exc:
        raise GatewayError(
            code="AI_BAD_REQUEST",
            message="Validation failed",
            status=400,
            details={"errors": exc.errors(include_context=False)},
        ) from exc

    payload = body.model_dump(mode="json", exclude_none=True)
    applied_change_id = _change_ticket_from_request(request, body.change_ticket_id)
    try:
        response = await _admin_mutation_response(
            request,
            cgs_tenant_id=cgs_tenant_id,
            method="POST",
            subpath="/automerge/execute",
            payload=payload,
            change_ticket_id=applied_change_id,
        )
    except Exception:
        if applied_change_id:
            await request.app["cgs_storage"].mark_admin_change_failed(
                change_id=applied_change_id,
                result={"status": "failed", "operation": "automerge.execute"},
            )
        raise

    if applied_change_id:
        await request.app["cgs_storage"].mark_admin_change_applied(
            change_id=applied_change_id,
            result={"status": "applied", "operation": "automerge.execute"},
        )
    return response


async def handle_admin_submit_change(request: web.Request) -> web.Response:
    _ensure_internal_admin_access(request, mutating=True)
    rid = request_id(request)
    cgs_tenant_id = request.match_info["tenant_id"]
    _ensure_operator_tenant_access(request, cgs_tenant_id)
    enforce_mutation_rate_limit(request, cgs_tenant_id=cgs_tenant_id, family="admin")
    raw = await json_object(request)
    try:
        body = TenantAdminChangeCreateRequest.model_validate(raw)
    except ValidationError as exc:
        raise GatewayError(
            code="AI_BAD_REQUEST",
            message="Validation failed",
            status=400,
            details={"errors": exc.errors()},
        ) from exc

    policy_decision = _TRUST_POLICY_EVALUATOR.evaluate(
        tenant_id=None,
        action=body.action,
        context={"method": "POST", "subpath": "/changes"},
    )
    if not policy_decision.allowed and not policy_decision.approval_required:
        raise GatewayError(
            code=policy_decision.code,
            message=policy_decision.message,
            status=policy_decision.status,
            details=policy_decision.details,
        )

    idem_key, idem_fp, cached = await _admin_idempotency_check(
        request,
        cgs_tenant_id=cgs_tenant_id,
        payload=body.model_dump(mode="json"),
    )
    if cached is not None:
        response = web.json_response(cached["body"], status=cached["status"])
        response.headers["X-Idempotent-Replay"] = "true"
        return response

    created = await request.app["cgs_storage"].create_admin_change(
        cgs_tenant_id=cgs_tenant_id,
        action=body.action,
        target=body.target,
        payload=body.payload,
        requested_by=principal(request).sub,
        request_id=rid,
        reason=body.reason,
    )
    request["change_ticket_id"] = str(created["change_id"])
    status_code = 409 if bool(created.get("duplicate", False)) else 201
    envelope = {
        "request_id": rid,
        "data": created,
        "error": None,
    }
    await _admin_save_idempotency(
        request,
        cgs_tenant_id=cgs_tenant_id,
        idempotency_key=idem_key,
        request_fingerprint=idem_fp,
        response_status=status_code,
        response_body=envelope,
    )
    return web.json_response(envelope, status=status_code)


async def handle_admin_list_changes(request: web.Request) -> web.Response:
    _ensure_internal_admin_access(request, mutating=False)
    cgs_tenant_id = request.match_info["tenant_id"]
    _ensure_operator_tenant_access(request, cgs_tenant_id)
    decision = _TRUST_POLICY_EVALUATOR.evaluate(
        tenant_id=None,
        action="admin.change.list",
        context={"method": "GET", "subpath": "/changes"},
    )
    if not decision.allowed:
        raise GatewayError(
            code=decision.code,
            message=decision.message,
            status=decision.status,
            details=decision.details,
        )
    status = request.query.get("status")
    changes = await request.app["cgs_storage"].list_admin_changes(
        cgs_tenant_id=cgs_tenant_id,
        status=status,
    )
    return success_response(request_id(request), {"changes": changes, "count": len(changes)})


async def handle_admin_approve_change(request: web.Request) -> web.Response:
    _ensure_internal_admin_access(request, mutating=True)
    cgs_tenant_id = request.match_info["tenant_id"]
    _ensure_operator_tenant_access(request, cgs_tenant_id)
    decision = _TRUST_POLICY_EVALUATOR.evaluate(
        tenant_id=None,
        action="admin.change.approve",
        context={"method": "POST", "subpath": "/changes/approve"},
    )
    if not decision.allowed:
        raise GatewayError(
            code=decision.code,
            message=decision.message,
            status=decision.status,
            details=decision.details,
        )
    enforce_mutation_rate_limit(request, cgs_tenant_id=cgs_tenant_id, family="admin")
    rid = request_id(request)
    raw = await json_object(request, required=False)
    try:
        body = TenantAdminChangeDecisionRequest.model_validate(raw)
    except ValidationError as exc:
        raise GatewayError(
            code="AI_BAD_REQUEST",
            message="Validation failed",
            status=400,
            details={"errors": exc.errors()},
        ) from exc
    request["change_ticket_id"] = request.match_info["change_id"]
    idem_key, idem_fp, cached = await _admin_idempotency_check(
        request,
        cgs_tenant_id=cgs_tenant_id,
        payload={
            "change_id": request.match_info["change_id"],
            "reason": body.reason,
            "decision": "approve",
        },
    )
    if cached is not None:
        response = web.json_response(cached["body"], status=cached["status"])
        response.headers["X-Idempotent-Replay"] = "true"
        return response
    change = await request.app["cgs_storage"].get_admin_change(request.match_info["change_id"])
    if change is None or str(change.get("cgs_tenant_id")) != cgs_tenant_id:
        raise GatewayError(
            code="AI_APPROVAL_NOT_FOUND",
            message="Approval ticket not found",
            status=404,
        )
    if str(change.get("requested_by")) == principal(request).sub:
        raise GatewayError(
            code="AI_APPROVAL_TWO_PERSON_REQUIRED",
            message="Requester cannot approve their own change",
            status=409,
        )
    approved = await request.app["cgs_storage"].approve_admin_change(
        change_id=request.match_info["change_id"],
        approved_by=principal(request).sub,
        reason=body.reason,
    )
    if approved is None:
        raise GatewayError(
            code="AI_APPROVAL_INVALID",
            message="Change is not in pending state",
            status=409,
        )
    envelope = {
        "request_id": rid,
        "data": approved,
        "error": None,
    }
    await _admin_save_idempotency(
        request,
        cgs_tenant_id=cgs_tenant_id,
        idempotency_key=idem_key,
        request_fingerprint=idem_fp,
        response_status=200,
        response_body=envelope,
    )
    return web.json_response(envelope)


async def handle_admin_reject_change(request: web.Request) -> web.Response:
    _ensure_internal_admin_access(request, mutating=True)
    cgs_tenant_id = request.match_info["tenant_id"]
    _ensure_operator_tenant_access(request, cgs_tenant_id)
    decision = _TRUST_POLICY_EVALUATOR.evaluate(
        tenant_id=None,
        action="admin.change.reject",
        context={"method": "POST", "subpath": "/changes/reject"},
    )
    if not decision.allowed:
        raise GatewayError(
            code=decision.code,
            message=decision.message,
            status=decision.status,
            details=decision.details,
        )
    enforce_mutation_rate_limit(request, cgs_tenant_id=cgs_tenant_id, family="admin")
    rid = request_id(request)
    raw = await json_object(request, required=False)
    try:
        body = TenantAdminChangeDecisionRequest.model_validate(raw)
    except ValidationError as exc:
        raise GatewayError(
            code="AI_BAD_REQUEST",
            message="Validation failed",
            status=400,
            details={"errors": exc.errors()},
        ) from exc
    request["change_ticket_id"] = request.match_info["change_id"]
    idem_key, idem_fp, cached = await _admin_idempotency_check(
        request,
        cgs_tenant_id=cgs_tenant_id,
        payload={
            "change_id": request.match_info["change_id"],
            "reason": body.reason,
            "decision": "reject",
        },
    )
    if cached is not None:
        response = web.json_response(cached["body"], status=cached["status"])
        response.headers["X-Idempotent-Replay"] = "true"
        return response
    change = await request.app["cgs_storage"].get_admin_change(request.match_info["change_id"])
    if change is None or str(change.get("cgs_tenant_id")) != cgs_tenant_id:
        raise GatewayError(
            code="AI_APPROVAL_NOT_FOUND",
            message="Approval ticket not found",
            status=404,
        )
    rejected = await request.app["cgs_storage"].reject_admin_change(
        change_id=request.match_info["change_id"],
        approved_by=principal(request).sub,
        reason=body.reason,
    )
    if rejected is None:
        raise GatewayError(
            code="AI_APPROVAL_INVALID",
            message="Change is not in pending state",
            status=409,
        )
    envelope = {
        "request_id": rid,
        "data": rejected,
        "error": None,
    }
    await _admin_save_idempotency(
        request,
        cgs_tenant_id=cgs_tenant_id,
        idempotency_key=idem_key,
        request_fingerprint=idem_fp,
        response_status=200,
        response_body=envelope,
    )
    return web.json_response(envelope)


def register_internal_admin_routes(app: web.Application) -> None:
    """Register CGS internal tenant-admin routes."""
    prefix = "/service/ai/v1/internal/admin/tenants/{tenant_id}"

    app.router.add_get(prefix + "/discord-users", handle_admin_list_discord_users)
    app.router.add_post(prefix + "/discord-users", handle_admin_add_discord_user)
    app.router.add_delete(
        prefix + "/discord-users/{discord_user_id}",
        handle_admin_delete_discord_user,
    )
    app.router.add_patch(
        prefix + "/discord-users/{discord_user_id}/role",
        handle_admin_patch_discord_user_role,
    )
    app.router.add_get(prefix + "/discord-bindings", handle_admin_list_discord_bindings)
    app.router.add_put(
        prefix + "/discord-bindings/guilds/{guild_id}",
        handle_admin_put_guild_binding,
    )
    app.router.add_put(
        prefix + "/discord-bindings/channels/{channel_id}",
        handle_admin_put_channel_binding,
    )
    app.router.add_delete(
        prefix + "/discord-bindings/channels/{channel_id}",
        handle_admin_delete_channel_binding,
    )
    app.router.add_get(prefix + "/settings", handle_admin_list_settings)
    app.router.add_put(prefix + "/settings/{namespace}/{key}", handle_admin_put_setting)
    app.router.add_delete(prefix + "/settings/{namespace}/{key}", handle_admin_delete_setting)
    app.router.add_get(prefix + "/secrets", handle_admin_list_secrets)
    app.router.add_put(prefix + "/secrets/{name}", handle_admin_put_secret)
    app.router.add_delete(prefix + "/secrets/{name}", handle_admin_delete_secret)
    app.router.add_get(prefix + "/audit", handle_admin_list_audit)
    app.router.add_get(
        prefix + "/email/providers/{provider}/oauth-app",
        handle_admin_get_email_oauth_app,
    )
    app.router.add_put(
        prefix + "/email/providers/{provider}/oauth-app",
        handle_admin_put_email_oauth_app,
    )
    app.router.add_post(
        prefix + "/email/mailboxes/connect/start",
        handle_admin_start_mailbox_connect,
    )
    app.router.add_get(
        prefix + "/email/mailboxes/connect/callback",
        handle_admin_mailbox_connect_callback,
    )
    app.router.add_get(prefix + "/email/mailboxes", handle_admin_list_mailboxes)
    app.router.add_patch(prefix + "/email/mailboxes/{mailbox_id}", handle_admin_patch_mailbox)
    app.router.add_delete(prefix + "/email/mailboxes/{mailbox_id}", handle_admin_delete_mailbox)
    app.router.add_post(
        prefix + "/email/mailboxes/{mailbox_id}/sync",
        handle_admin_sync_mailbox,
    )
    app.router.add_get(
        prefix + "/email/critical/messages",
        handle_admin_list_critical_messages,
    )
    app.router.add_get(prefix + "/email/calendars", handle_admin_list_calendars)
    app.router.add_put(
        prefix + "/email/mailboxes/{mailbox_id}/calendar-primary",
        handle_admin_set_mailbox_primary_calendar,
    )
    app.router.add_get(prefix + "/email/insights", handle_admin_list_email_insights)
    app.router.add_post(
        prefix + "/email/insights/reindex",
        handle_admin_reindex_email_insights,
    )
    app.router.add_get(
        prefix + "/messaging/providers/{provider}/config",
        handle_admin_get_messaging_provider_config,
    )
    app.router.add_put(
        prefix + "/messaging/providers/{provider}/config",
        handle_admin_put_messaging_provider_config,
    )
    app.router.add_get(
        prefix + "/messaging/chats/{chat_id}/policy",
        handle_admin_get_messaging_chat_policy,
    )
    app.router.add_put(
        prefix + "/messaging/chats/{chat_id}/policy",
        handle_admin_put_messaging_chat_policy,
    )
    app.router.add_get(prefix + "/messaging/chats", handle_admin_list_messaging_chats)
    app.router.add_get(
        prefix + "/messaging/messages",
        handle_admin_list_messaging_messages,
    )
    app.router.add_get(
        prefix + "/messaging/messages/export",
        handle_admin_export_messaging_messages,
    )
    app.router.add_delete(
        prefix + "/messaging/messages",
        handle_admin_delete_messaging_messages,
    )
    app.router.add_post(
        prefix + "/messaging/messages/{chat_id}/send",
        handle_admin_send_messaging_message,
    )
    app.router.add_get(prefix + "/security/events", handle_admin_list_security_events)
    app.router.add_get(prefix + "/security/dashboard", handle_admin_get_security_dashboard)
    app.router.add_post(
        prefix + "/automerge/execute",
        handle_admin_execute_automerge,
    )

    app.router.add_post(prefix + "/changes", handle_admin_submit_change)
    app.router.add_get(prefix + "/changes", handle_admin_list_changes)
    app.router.add_post(
        prefix + "/changes/{change_id}/approve",
        handle_admin_approve_change,
    )
    app.router.add_post(
        prefix + "/changes/{change_id}/reject",
        handle_admin_reject_change,
    )
