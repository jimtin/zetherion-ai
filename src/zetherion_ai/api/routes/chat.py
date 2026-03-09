"""Chat endpoints for the public API.

All endpoints require session token (Bearer) authentication,
which is handled by the auth middleware.

Chat logic (L1a signal detection, system prompt construction, inference)
is delegated to ``ClientChatSkill``.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from aiohttp import web

from zetherion_ai.api.conversation_runtime import TenantConversationRuntime
from zetherion_ai.api.test_runtime import SandboxSimulationError, TenantSandboxRuntime
from zetherion_ai.logging import get_logger
from zetherion_ai.skills.base import SkillRequest
from zetherion_ai.skills.client_chat import ClientChatSkill
from zetherion_ai.skills.tenant_intelligence import TenantIntelligenceSkill

log = get_logger("zetherion_ai.api.routes.chat")

# Maximum conversation turns to include as context for the LLM.
_CONTEXT_WINDOW = 20


def _format_messages_for_llm(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Convert stored messages to the format expected by InferenceBroker."""
    return [{"role": m["role"], "content": m["content"]} for m in messages]


def _serialise(record: dict[str, Any]) -> dict[str, Any]:
    """Convert datetime/uuid fields to strings for JSON."""
    out = {}
    for k, v in record.items():
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        elif hasattr(v, "hex"):
            out[k] = str(v)
        else:
            out[k] = v
    return out


def _get_chat_skill(request: web.Request) -> ClientChatSkill:
    """Get or lazily create a ClientChatSkill from the app's inference broker."""
    skill = request.app.get("client_chat_skill")
    if skill is not None:
        return skill  # type: ignore[no-any-return]
    broker = request.app.get("inference_broker")
    return ClientChatSkill(inference_broker=broker)


def _get_conversation_runtime(request: web.Request) -> TenantConversationRuntime:
    """Get or lazily create the tenant conversation runtime."""
    runtime = request.app.get("tenant_conversation_runtime")
    if isinstance(runtime, TenantConversationRuntime):
        return runtime
    runtime = TenantConversationRuntime(tenant_manager=request.app["tenant_manager"])
    request.app["tenant_conversation_runtime"] = runtime
    return runtime


def _get_sandbox_runtime(request: web.Request) -> TenantSandboxRuntime:
    """Get or lazily create the tenant sandbox runtime."""
    runtime = request.app.get("tenant_sandbox_runtime")
    if isinstance(runtime, TenantSandboxRuntime):
        return runtime
    runtime = TenantSandboxRuntime(tenant_manager=request.app["tenant_manager"])
    request.app["tenant_sandbox_runtime"] = runtime
    return runtime


async def _get_tenant_intelligence_skill(request: web.Request) -> TenantIntelligenceSkill:
    """Get or lazily create a TenantIntelligenceSkill from app dependencies."""
    cached_skill = request.app.get("tenant_intelligence_skill")
    if isinstance(cached_skill, TenantIntelligenceSkill):
        return cached_skill

    broker = request.app.get("inference_broker")
    tenant_manager = request.app.get("tenant_manager")
    skill = TenantIntelligenceSkill(
        inference_broker=broker,
        tenant_manager=tenant_manager,
    )
    await skill.safe_initialize()
    request.app["tenant_intelligence_skill"] = skill
    return skill


def _schedule_background(coro: Any, *, label: str) -> None:
    """Run a background task and log exceptions safely."""
    import asyncio

    task = asyncio.create_task(coro)

    def _done_callback(t: asyncio.Task[Any]) -> None:
        try:
            t.result()
        except Exception:
            log.exception("chat_background_task_failed", label=label)

    task.add_done_callback(_done_callback)


async def _dispatch_message_extraction(
    request: web.Request,
    *,
    tenant_id: str,
    session_id: str,
    message: str,
) -> None:
    """Dispatch async L1b message extraction for CRM enrichment."""
    skill = await _get_tenant_intelligence_skill(request)
    extraction_request = SkillRequest(
        id=uuid.uuid4(),
        user_id=f"tenant:{tenant_id}",
        intent="extract_message_entities",
        message=message,
        context={
            "tenant_id": tenant_id,
            "session_id": session_id,
            "message": message,
        },
    )
    response = await skill.safe_handle(extraction_request)
    if not response.success:
        log.warning(
            "tenant_intelligence_extract_failed",
            tenant_id=tenant_id,
            session_id=session_id,
            error=response.error,
        )


async def handle_chat(request: web.Request) -> web.Response:
    """POST /api/v1/chat — send a message and get an AI response.

    Requires Bearer session token. The middleware attaches ``request["tenant"]``
    and ``request["session"]``.
    """
    tenant = request["tenant"]
    session = request["session"]
    tenant_manager = request.app["tenant_manager"]

    tenant_id = str(tenant["tenant_id"])
    session_id = str(session["session_id"])
    execution_mode = str(session.get("execution_mode") or request.get("execution_mode") or "live")

    # Parse request body
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    message = data.get("message", "").strip()
    if not message:
        return web.json_response({"error": "Message is required"}, status=400)

    if len(message) > 10000:
        return web.json_response({"error": "Message too long (max 10000 chars)"}, status=400)

    # Store the user's message
    await tenant_manager.add_message(
        session_id=session_id,
        tenant_id=tenant_id,
        execution_mode=execution_mode,
        role="user",
        content=message,
        metadata=data.get("metadata"),
    )

    conversation_runtime = _get_conversation_runtime(request)
    history: list[dict[str, Any]] = []
    try:
        history = await tenant_manager.get_messages(
            session_id=session_id,
            tenant_id=tenant_id,
            limit=_CONTEXT_WINDOW,
        )
        context = await conversation_runtime.build_context(
            tenant_id=tenant_id,
            session=session,
            history=_format_messages_for_llm(history[:-1]),
        )
        if execution_mode == "test":
            result = await _get_sandbox_runtime(request).simulate_chat(
                tenant_id=tenant_id,
                profile_id=(
                    str(session.get("test_profile_id"))
                    if session.get("test_profile_id") is not None
                    else None
                ),
                body={"message": message, "metadata": data.get("metadata") or {}},
                session=session,
                context=context,
                history=context.history,
            )
            assistant_content = result.content
            model_used = result.model
            assistant_metadata = result.metadata
        else:
            chat_skill = _get_chat_skill(request)
            result = await chat_skill.generate_response(
                tenant=tenant,
                message=message,
                history=context.history,
                context_notes=context.context_notes,
            )
            assistant_content = result.content
            model_used = result.model
            assistant_metadata = None
    except SandboxSimulationError as exc:
        return web.json_response(exc.body, status=exc.status)
    except Exception:
        log.exception("chat_inference_failed", tenant_id=tenant_id, session_id=session_id)
        assistant_content = "I'm sorry, I encountered an error. Please try again."
        model_used = None
        assistant_metadata = None

    # Store the assistant's response
    assistant_msg = await tenant_manager.add_message(
        session_id=session_id,
        tenant_id=tenant_id,
        execution_mode=execution_mode,
        role="assistant",
        content=assistant_content,
        metadata=assistant_metadata,
    )
    try:
        await conversation_runtime.record_turn(
            tenant_id=tenant_id,
            session=session,
            session_id=session_id,
            user_message=message,
            assistant_message=assistant_content,
            history=[
                *(history or [{"role": "user", "content": message}]),
                {"role": "assistant", "content": assistant_content},
            ],
        )
    except Exception:
        log.exception("chat_runtime_record_turn_failed", tenant_id=tenant_id, session_id=session_id)

    response = _serialise(assistant_msg)
    if model_used:
        response["model"] = model_used

    # Async post-response extraction for tenant CRM (L1b).
    if execution_mode != "test":
        _schedule_background(
            _dispatch_message_extraction(
                request,
                tenant_id=tenant_id,
                session_id=session_id,
                message=message,
            ),
            label="tenant_intelligence_extract",
        )

    return web.json_response(response, status=200)


async def handle_chat_history(request: web.Request) -> web.Response:
    """GET /api/v1/chat/history — get conversation history for the session.

    Requires Bearer session token. Returns messages in chronological order.

    Query params:
        limit: Max messages to return (default 50, max 100).
        before: Message ID cursor for pagination.
    """
    tenant = request["tenant"]
    session = request["session"]
    tenant_manager = request.app["tenant_manager"]

    tenant_id = str(tenant["tenant_id"])
    session_id = str(session["session_id"])

    # Parse query params
    try:
        limit = min(int(request.query.get("limit", "50")), 100)
    except (ValueError, TypeError):
        limit = 50

    before_id = request.query.get("before")

    messages = await tenant_manager.get_messages(
        session_id=session_id,
        tenant_id=tenant_id,
        limit=limit,
        before_id=before_id,
    )

    return web.json_response(
        {
            "session_id": session_id,
            "messages": [_serialise(m) for m in messages],
        }
    )


async def handle_chat_stream(request: web.Request) -> web.StreamResponse:
    """POST /api/v1/chat/stream — send a message and stream the AI response via SSE.

    SSE event format::

        data: {"type": "token", "content": "Hello"}
        data: {"type": "token", "content": " there"}
        data: {"type": "done", "message_id": "...", "model": "..."}

    Requires Bearer session token.
    """
    tenant = request["tenant"]
    session = request["session"]
    tenant_manager = request.app["tenant_manager"]

    tenant_id = str(tenant["tenant_id"])
    session_id = str(session["session_id"])
    execution_mode = str(session.get("execution_mode") or request.get("execution_mode") or "live")

    # Parse request body
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    message = data.get("message", "").strip()
    if not message:
        return web.json_response({"error": "Message is required"}, status=400)

    if len(message) > 10000:
        return web.json_response({"error": "Message too long (max 10000 chars)"}, status=400)

    # Store the user's message
    await tenant_manager.add_message(
        session_id=session_id,
        tenant_id=tenant_id,
        execution_mode=execution_mode,
        role="user",
        content=message,
        metadata=data.get("metadata"),
    )

    assistant_content = ""
    model_used = None
    history: list[dict[str, Any]] = []
    assistant_metadata: dict[str, Any] | None = None

    conversation_runtime = _get_conversation_runtime(request)
    broker = request.app.get("inference_broker")
    stream_events: list[dict[str, Any]] = []

    try:
        history = await tenant_manager.get_messages(
            session_id=session_id,
            tenant_id=tenant_id,
            limit=_CONTEXT_WINDOW,
        )
        context = await conversation_runtime.build_context(
            tenant_id=tenant_id,
            session=session,
            history=_format_messages_for_llm(history[:-1]),
        )

        if execution_mode == "test":
            chat_result, stream_events = await _get_sandbox_runtime(request).simulate_stream(
                tenant_id=tenant_id,
                profile_id=(
                    str(session.get("test_profile_id"))
                    if session.get("test_profile_id") is not None
                    else None
                ),
                body={"message": message, "metadata": data.get("metadata") or {}},
                session=session,
                context=context,
                history=context.history,
            )
            assistant_content = chat_result.content
            model_used = chat_result.model
            assistant_metadata = chat_result.metadata
        elif broker is None:
            assistant_content = "Chat is not configured. Please contact the administrator."
            stream_events = [{"type": "token", "content": assistant_content}]
        else:
            chat_skill = _get_chat_skill(request)
            _, stream = await chat_skill.generate_stream(
                tenant=tenant,
                message=message,
                history=context.history,
                context_notes=context.context_notes,
            )

            async for chunk in stream:
                if chunk.done:
                    model_used = chunk.model
                else:
                    assistant_content += chunk.content
                    stream_events.append({"type": "token", "content": chunk.content})
    except SandboxSimulationError as exc:
        return web.json_response(exc.body, status=exc.status)
    except Exception:
        log.exception("chat_stream_failed", tenant_id=tenant_id, session_id=session_id)
        if not assistant_content:
            assistant_content = "I'm sorry, I encountered an error. Please try again."
            stream_events = [{"type": "token", "content": assistant_content}]

    # Prepare SSE response after sandbox/live resolution so simulated errors can return JSON.
    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
    await response.prepare(request)

    for event_payload in stream_events:
        if str(event_payload.get("type") or "") == "done":
            if not model_used:
                model_used = str(event_payload.get("model") or "") or None
            continue
        await response.write(f"data: {json.dumps(event_payload)}\n\n".encode())

    # Store the assistant's full response
    assistant_msg = await tenant_manager.add_message(
        session_id=session_id,
        tenant_id=tenant_id,
        execution_mode=execution_mode,
        role="assistant",
        content=assistant_content,
        metadata=assistant_metadata,
    )
    try:
        await conversation_runtime.record_turn(
            tenant_id=tenant_id,
            session=session,
            session_id=session_id,
            user_message=message,
            assistant_message=assistant_content,
            history=[
                *(history or [{"role": "user", "content": message}]),
                {"role": "assistant", "content": assistant_content},
            ],
        )
    except Exception:
        log.exception("chat_stream_record_turn_failed", tenant_id=tenant_id, session_id=session_id)

    # Send the done event
    done_payload: dict[str, Any] = {
        "type": "done",
        "message_id": str(assistant_msg.get("message_id", "")),
    }
    if model_used:
        done_payload["model"] = model_used
    await response.write(f"data: {json.dumps(done_payload)}\n\n".encode())

    # Async post-response extraction for tenant CRM (L1b).
    if execution_mode != "test":
        _schedule_background(
            _dispatch_message_extraction(
                request,
                tenant_id=tenant_id,
                session_id=session_id,
                message=message,
            ),
            label="tenant_intelligence_extract_stream",
        )

    await response.write_eof()
    return response
