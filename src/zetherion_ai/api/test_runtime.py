"""Tenant sandbox runtime for deterministic test-mode chat simulation."""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from zetherion_ai.api.conversation_runtime import TenantConversationContext

_WORD_CHUNK_RE = re.compile(r"\S+\s*")
_DEFAULT_MODEL = "sandbox-simulated"


def _clean(value: str | None) -> str:
    return " ".join(str(value or "").split()).strip()


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value]
    return []


def _style_value(context: TenantConversationContext) -> str | None:
    for memory in context.subject_memories:
        if memory.category == "preference" and memory.memory_key == "response_style":
            return _clean(memory.value).lower()
    return None


def _subject_name(context: TenantConversationContext) -> str | None:
    for memory in context.subject_memories:
        if memory.category == "identity" and memory.memory_key == "name":
            return _clean(memory.value)
    return None


@dataclass(frozen=True)
class SandboxSimulationError(Exception):
    """Structured error raised by the sandbox runtime."""

    status: int
    body: dict[str, Any]


@dataclass(frozen=True)
class SandboxResolution:
    """Resolved sandbox response for one request."""

    profile_id: str | None
    rule_id: str | None
    preset_id: str
    latency_ms: int
    response: dict[str, Any]


@dataclass(frozen=True)
class SandboxChatResult:
    """Resolved non-stream chat response."""

    content: str
    model: str
    profile_id: str | None
    rule_id: str | None
    preset_id: str
    metadata: dict[str, Any] = field(default_factory=dict)


class TenantSandboxRuntime:
    """Evaluate tenant sandbox profiles and simulate deterministic responses."""

    def __init__(self, *, tenant_manager: Any) -> None:
        self._tenant_manager = tenant_manager

    async def preview(
        self,
        *,
        tenant_id: str,
        profile_id: str,
        method: str,
        route_path: str,
        body: dict[str, Any] | None,
        session: dict[str, Any] | None,
        context: TenantConversationContext | None,
        history: list[dict[str, str]] | None,
    ) -> dict[str, Any]:
        """Preview the sandbox rule/preset resolution for one hypothetical request."""
        resolution = await self._resolve(
            tenant_id=tenant_id,
            profile_id=profile_id,
            method=method,
            route_path=route_path,
            body=body or {},
            session=session or {},
            context=context,
            history=history or [],
        )
        preview: dict[str, Any] = {
            "profile_id": resolution.profile_id,
            "matched_rule_id": resolution.rule_id,
            "preset_id": resolution.preset_id,
            "latency_ms": resolution.latency_ms,
            "response": resolution.response,
        }
        try:
            chat_result = self._resolve_chat_result(
                resolution,
                body=body or {},
                context=context,
                history=history or [],
            )
        except SandboxSimulationError as exc:
            preview["error"] = {"status": exc.status, "body": exc.body}
        else:
            preview["chat_result"] = {
                "content": chat_result.content,
                "model": chat_result.model,
                "metadata": chat_result.metadata,
            }
            preview["stream_events"] = self._resolve_stream_events(
                resolution,
                body=body or {},
                context=context,
                history=history or [],
            )[1]
        return preview

    async def simulate_chat(
        self,
        *,
        tenant_id: str,
        profile_id: str | None,
        body: dict[str, Any],
        session: dict[str, Any],
        context: TenantConversationContext,
        history: list[dict[str, str]],
    ) -> SandboxChatResult:
        """Resolve one deterministic sandbox chat response."""
        resolution = await self._resolve(
            tenant_id=tenant_id,
            profile_id=profile_id,
            method="POST",
            route_path="/api/v1/chat",
            body=body,
            session=session,
            context=context,
            history=history,
        )
        if resolution.latency_ms > 0:
            await asyncio.sleep(resolution.latency_ms / 1000)
        return self._resolve_chat_result(resolution, body=body, context=context, history=history)

    async def simulate_stream(
        self,
        *,
        tenant_id: str,
        profile_id: str | None,
        body: dict[str, Any],
        session: dict[str, Any],
        context: TenantConversationContext,
        history: list[dict[str, str]],
    ) -> tuple[SandboxChatResult, list[dict[str, Any]]]:
        """Resolve one deterministic sandbox streaming response."""
        resolution = await self._resolve(
            tenant_id=tenant_id,
            profile_id=profile_id,
            method="POST",
            route_path="/api/v1/chat/stream",
            body=body,
            session=session,
            context=context,
            history=history,
        )
        if resolution.latency_ms > 0:
            await asyncio.sleep(resolution.latency_ms / 1000)
        return self._resolve_stream_events(resolution, body=body, context=context, history=history)

    async def _resolve(
        self,
        *,
        tenant_id: str,
        profile_id: str | None,
        method: str,
        route_path: str,
        body: dict[str, Any],
        session: dict[str, Any],
        context: TenantConversationContext | None,
        history: list[dict[str, str]],
    ) -> SandboxResolution:
        profile = await self._tenant_manager.resolve_test_profile(tenant_id, profile_id)
        if profile is not None:
            rules = await self._tenant_manager.list_test_rules(tenant_id, str(profile["profile_id"]))
            for rule in rules:
                if not bool(rule.get("enabled", True)):
                    continue
                if self._rule_matches(
                    rule=rule,
                    method=method,
                    route_path=route_path,
                    body=body,
                    session=session,
                    context=context,
                    history=history,
                ):
                    return SandboxResolution(
                        profile_id=str(profile["profile_id"]),
                        rule_id=str(rule["rule_id"]),
                        preset_id=_clean((rule.get("response") or {}).get("preset_id")) or "default",
                        latency_ms=max(0, int(rule.get("latency_ms") or 0)),
                        response=dict(rule.get("response") or {}),
                    )

        preset_id = self._default_preset_id(body, context=context, history=history)
        return SandboxResolution(
            profile_id=str(profile["profile_id"]) if isinstance(profile, dict) else None,
            rule_id=None,
            preset_id=preset_id,
            latency_ms=0,
            response={"preset_id": preset_id},
        )

    def _rule_matches(
        self,
        *,
        rule: dict[str, Any],
        method: str,
        route_path: str,
        body: dict[str, Any],
        session: dict[str, Any],
        context: TenantConversationContext | None,
        history: list[dict[str, str]],
    ) -> bool:
        rule_method = _clean(rule.get("method") or "POST").upper()
        if rule_method and rule_method != method.upper():
            return False

        route_pattern = _clean(rule.get("route_pattern") or "")
        if route_pattern and not fnmatch.fnmatch(route_path, route_pattern):
            return False

        match = dict(rule.get("match") or {})
        body_text = json.dumps(body, sort_keys=True).lower()
        metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
        metadata = dict(metadata)

        for needle in _as_list(match.get("body_contains")):
            if needle.lower() not in body_text:
                return False

        expected_metadata = match.get("metadata_contains")
        if isinstance(expected_metadata, dict):
            for key, value in expected_metadata.items():
                if metadata.get(key) != value:
                    return False

        tool_name = _clean(match.get("tool_name"))
        if tool_name:
            actual_tool = _clean(body.get("tool_name") or metadata.get("tool_name"))
            if actual_tool.lower() != tool_name.lower():
                return False

        expected_state = _clean(match.get("conversation_state")).lower()
        if expected_state:
            actual_state = self._conversation_state(session=session, context=context, history=history)
            if actual_state != expected_state:
                return False

        return True

    @staticmethod
    def _conversation_state(
        *,
        session: dict[str, Any],
        context: TenantConversationContext | None,
        history: list[dict[str, str]],
    ) -> str:
        if history:
            return "ongoing"
        if context and (context.session_summary or context.subject_memories):
            return "returning"
        if _clean(session.get("conversation_summary")):
            return "returning"
        return "new"

    def _resolve_chat_result(
        self,
        resolution: SandboxResolution,
        *,
        body: dict[str, Any],
        context: TenantConversationContext | None,
        history: list[dict[str, str]],
    ) -> SandboxChatResult:
        error = resolution.response.get("error")
        if isinstance(error, dict):
            raise SandboxSimulationError(
                status=max(400, int(error.get("status") or 500)),
                body=dict(error.get("body") or {"error": "Simulated sandbox error"}),
            )

        json_body = resolution.response.get("json_body")
        if isinstance(json_body, dict):
            content = _clean(
                json_body.get("content") or json_body.get("message") or json_body.get("text")
            )
            if not content:
                content = self._build_preset_content(
                    resolution.preset_id,
                    message=_clean(body.get("message")),
                    context=context,
                    history=history,
                )
            model = _clean(json_body.get("model")) or _DEFAULT_MODEL
            return SandboxChatResult(
                content=content,
                model=model,
                profile_id=resolution.profile_id,
                rule_id=resolution.rule_id,
                preset_id=resolution.preset_id,
                metadata={
                    "sandbox_profile_id": resolution.profile_id,
                    "sandbox_rule_id": resolution.rule_id,
                    "sandbox_preset_id": resolution.preset_id,
                },
            )

        sse_events = resolution.response.get("sse_events")
        if isinstance(sse_events, list):
            content = "".join(
                str(event.get("content") or "")
                for event in sse_events
                if isinstance(event, dict) and str(event.get("type") or "token") == "token"
            ).strip()
            done_event = next(
                (
                    event
                    for event in reversed(sse_events)
                    if isinstance(event, dict) and str(event.get("type") or "") == "done"
                ),
                {},
            )
            return SandboxChatResult(
                content=content
                or self._build_preset_content(
                    resolution.preset_id,
                    message=_clean(body.get("message")),
                    context=context,
                    history=history,
                ),
                model=_clean(done_event.get("model")) or _DEFAULT_MODEL,
                profile_id=resolution.profile_id,
                rule_id=resolution.rule_id,
                preset_id=resolution.preset_id,
                metadata={
                    "sandbox_profile_id": resolution.profile_id,
                    "sandbox_rule_id": resolution.rule_id,
                    "sandbox_preset_id": resolution.preset_id,
                },
            )

        content = self._build_preset_content(
            resolution.preset_id,
            message=_clean(body.get("message")),
            context=context,
            history=history,
        )
        return SandboxChatResult(
            content=content,
            model=_DEFAULT_MODEL,
            profile_id=resolution.profile_id,
            rule_id=resolution.rule_id,
            preset_id=resolution.preset_id,
            metadata={
                "sandbox_profile_id": resolution.profile_id,
                "sandbox_rule_id": resolution.rule_id,
                "sandbox_preset_id": resolution.preset_id,
            },
        )

    def _resolve_stream_events(
        self,
        resolution: SandboxResolution,
        *,
        body: dict[str, Any],
        context: TenantConversationContext | None,
        history: list[dict[str, str]],
    ) -> tuple[SandboxChatResult, list[dict[str, Any]]]:
        chat = self._resolve_chat_result(resolution, body=body, context=context, history=history)
        configured_events = resolution.response.get("sse_events")
        if isinstance(configured_events, list) and configured_events:
            events = [dict(event) for event in configured_events if isinstance(event, dict)]
            if not any(str(event.get("type") or "") == "done" for event in events):
                events.append(
                    {
                        "type": "done",
                        "model": chat.model,
                    }
                )
            return chat, events

        events = [{"type": "token", "content": chunk} for chunk in self._token_chunks(chat.content)]
        events.append({"type": "done", "model": chat.model})
        return chat, events

    @staticmethod
    def _token_chunks(content: str) -> list[str]:
        chunks = [match.group(0) for match in _WORD_CHUNK_RE.finditer(content)]
        return chunks or [content]

    def _default_preset_id(
        self,
        body: dict[str, Any],
        *,
        context: TenantConversationContext | None,
        history: list[dict[str, str]],
    ) -> str:
        message = _clean(body.get("message")).lower()
        if any(token in message for token in {"price", "pricing", "quote", "cost"}):
            return "pricing"
        if any(token in message for token in {"hours", "availability", "available", "open"}):
            return "availability"
        if any(token in message for token in {"book", "booking", "schedule", "appointment"}):
            return "booking"
        if any(token in message for token in {"urgent", "emergency", "leak", "broken"}):
            return "urgent_support"
        if history or (context and (context.session_summary or context.subject_memories)):
            return "follow_up"
        return "default"

    def _build_preset_content(
        self,
        preset_id: str,
        *,
        message: str,
        context: TenantConversationContext | None,
        history: list[dict[str, str]],
    ) -> str:
        style = _style_value(context) if context else None
        name = _subject_name(context) if context else None
        intro = ""
        if preset_id == "follow_up":
            intro = "Picking up from the earlier context, "
        elif history:
            intro = "Based on the conversation so far, "

        base = {
            "pricing": (
                "pricing usually depends on scope, materials, access, and timing. "
                "We'd normally confirm a couple of details before giving a firm quote."
            ),
            "availability": (
                "availability usually depends on location, urgency, and the size of the job. "
                "We'd normally confirm the postcode and preferred time window next."
            ),
            "booking": (
                "we can help move that toward a booking. "
                "We'd normally confirm the address, contact details, and preferred time window."
            ),
            "urgent_support": (
                "that sounds time-sensitive. "
                "We'd normally prioritise the immediate safety details, location, and the fastest callback route."
            ),
            "follow_up": (
                "the next live-step would usually be to confirm the missing details and keep the thread moving "
                "without re-asking for context you've already given."
            ),
            "default": (
                "the live assistant would usually respond conversationally, reuse the session context, "
                "and ask one focused follow-up if any important detail is missing."
            ),
        }.get(preset_id, "the live assistant would respond here using the tenant sandbox profile.")

        variants = [
            "It would keep the same wire format as production while staying non-billable.",
            "It would avoid external providers while keeping the reply shape realistic.",
            "It would keep the conversation moving without touching live integrations.",
        ]
        digest = hashlib.sha256(f"{preset_id}:{message}".encode("utf-8")).digest()
        tail = variants[digest[0] % len(variants)]

        content = intro + base
        if preset_id in {"default", "follow_up"} or style == "detailed":
            content = f"{content} {tail}"

        if name:
            content = f"{name}, {content[0].lower()}{content[1:]}" if content else name

        if style == "brief":
            sentences = [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", content) if sentence.strip()]
            return sentences[0] if sentences else content
        if style == "formal":
            return content.replace("we'd", "we would").replace("that's", "that is")
        if style == "casual":
            return content.replace("We would", "We'd").replace("would usually", "would typically")
        return content
