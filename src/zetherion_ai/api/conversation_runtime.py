"""Tenant-scoped conversation runtime helpers for the public API."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_WHITESPACE_RE = re.compile(r"\s+")
_FAVORITE_RE = re.compile(
    r"(?i)\b(?:my\s+)?favou?rite\s+(?P<item>[a-z0-9 _-]{2,40})\s+is\s+(?P<value>[^.!?\n]{1,120})"
)
_NAME_RE = re.compile(r"(?i)\b(?:my\s+name\s+is|call\s+me)\s+(?P<value>[^.!?\n]{1,80})")
_ROLE_RE = re.compile(r"(?i)\bi\s+work\s+as\s+(?P<value>[^.!?\n]{1,120})")
_STYLE_PATTERNS = (
    (re.compile(r"(?i)\b(?:keep|make)\s+(?:the\s+)?responses?\s+(?:more\s+)?brief\b"), "brief"),
    (re.compile(r"(?i)\b(?:please\s+)?be\s+brief\b"), "brief"),
    (re.compile(r"(?i)\b(?:please\s+)?be\s+concise\b"), "brief"),
    (
        re.compile(r"(?i)\b(?:keep|make)\s+(?:the\s+)?responses?\s+(?:more\s+)?detailed\b"),
        "detailed",
    ),
    (re.compile(r"(?i)\b(?:keep|make)\s+(?:the\s+)?responses?\s+(?:more\s+)?formal\b"), "formal"),
    (re.compile(r"(?i)\b(?:keep|make)\s+(?:the\s+)?responses?\s+(?:more\s+)?casual\b"), "casual"),
    (
        re.compile(r"(?i)\b(?:keep|make)\s+(?:the\s+)?responses?\s+(?:more\s+)?friendly\b"),
        "friendly",
    ),
    (
        re.compile(r"(?i)\b(?:keep|make)\s+(?:the\s+)?responses?\s+(?:more\s+)?conversational\b"),
        "conversational",
    ),
    (
        re.compile(r"(?i)\b(?:keep|make)\s+(?:the\s+)?responses?\s+(?:more\s+)?professional\b"),
        "professional",
    ),
)


@dataclass(frozen=True)
class TenantSubjectMemory:
    """One tenant-scoped durable memory for an API chat subject."""

    category: str
    memory_key: str
    value: str
    confidence: float = 0.8

    @property
    def prompt_line(self) -> str:
        label = self.memory_key.replace("_", " ").strip()
        return f"- {label}: {self.value}"


@dataclass(frozen=True)
class MemoryCandidate:
    """A durable memory extracted from a tenant user message."""

    category: str
    memory_key: str
    value: str
    confidence: float = 0.8


@dataclass(frozen=True)
class TenantConversationContext:
    """Prompt context assembled for one tenant chat turn."""

    history: list[dict[str, str]]
    memory_subject_id: str | None
    session_summary: str
    subject_memories: list[TenantSubjectMemory]
    context_notes: str | None


def _clean_text(value: str | None) -> str:
    if value is None:
        return ""
    collapsed = _WHITESPACE_RE.sub(" ", value).strip()
    return collapsed.strip(" .")


def _truncate_text(value: str, *, limit: int) -> str:
    cleaned = _clean_text(value)
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip() + "..."


class TenantConversationRuntime:
    """Tenant-scoped runtime memory and context assembly for API chat."""

    def __init__(
        self,
        *,
        tenant_manager: Any,
        subject_memory_limit: int = 6,
    ) -> None:
        self._tenant_manager = tenant_manager
        self._subject_memory_limit = subject_memory_limit

    @staticmethod
    def resolve_memory_subject_id(session: dict[str, Any]) -> str | None:
        """Return the stable tenant-local subject ID for a session."""
        explicit = _clean_text(session.get("memory_subject_id"))
        if explicit:
            return explicit
        external_user_id = _clean_text(session.get("external_user_id"))
        if external_user_id:
            return external_user_id
        return None

    async def build_context(
        self,
        *,
        tenant_id: str,
        session: dict[str, Any],
        history: list[dict[str, str]],
    ) -> TenantConversationContext:
        """Assemble tenant-scoped prompt context for one chat request."""
        memory_subject_id = self.resolve_memory_subject_id(session)
        session_summary = _clean_text(session.get("conversation_summary"))
        subject_memories: list[TenantSubjectMemory] = []

        if memory_subject_id:
            rows = await self._tenant_manager.list_subject_memories(
                tenant_id=tenant_id,
                memory_subject_id=memory_subject_id,
                limit=self._subject_memory_limit,
            )
            subject_memories = [
                TenantSubjectMemory(
                    category=str(row.get("category") or "memory"),
                    memory_key=str(row.get("memory_key") or "memory"),
                    value=_clean_text(str(row.get("value") or "")),
                    confidence=float(row.get("confidence") or 0.8),
                )
                for row in rows
                if _clean_text(str(row.get("value") or ""))
            ]

        context_notes = self._build_context_notes(
            session_summary=session_summary,
            subject_memories=subject_memories,
        )
        return TenantConversationContext(
            history=history,
            memory_subject_id=memory_subject_id,
            session_summary=session_summary,
            subject_memories=subject_memories,
            context_notes=context_notes,
        )

    async def record_turn(
        self,
        *,
        tenant_id: str,
        session: dict[str, Any],
        session_id: str,
        user_message: str,
        assistant_message: str,
        history: list[dict[str, Any]],
    ) -> None:
        """Persist tenant-scoped conversation state after a response."""
        memory_subject_id = self.resolve_memory_subject_id(session)
        if memory_subject_id:
            for candidate in self.extract_memory_candidates(user_message):
                await self._tenant_manager.upsert_subject_memory(
                    tenant_id=tenant_id,
                    memory_subject_id=memory_subject_id,
                    category=candidate.category,
                    memory_key=candidate.memory_key,
                    value=candidate.value,
                    source_session_id=session_id,
                    confidence=candidate.confidence,
                )

        summary = self.build_session_summary(history)
        await self._tenant_manager.persist_session_context(
            session_id=session_id,
            tenant_id=tenant_id,
            memory_subject_id=memory_subject_id,
            conversation_summary=summary,
        )

    @staticmethod
    def extract_memory_candidates(message: str) -> list[MemoryCandidate]:
        """Extract durable tenant-scoped memory candidates from one user turn."""
        cleaned = _clean_text(message)
        if not cleaned:
            return []

        candidates: list[MemoryCandidate] = []

        for pattern, style_value in _STYLE_PATTERNS:
            if pattern.search(cleaned):
                candidates.append(
                    MemoryCandidate(
                        category="preference",
                        memory_key="response_style",
                        value=style_value,
                        confidence=0.88,
                    )
                )
                break

        favorite_match = _FAVORITE_RE.search(cleaned)
        if favorite_match:
            item = _clean_text(favorite_match.group("item")).lower().replace(" ", "_")
            value = _truncate_text(favorite_match.group("value"), limit=120)
            if item and value:
                candidates.append(
                    MemoryCandidate(
                        category="preference",
                        memory_key=f"favorite_{item}",
                        value=value,
                        confidence=0.84,
                    )
                )

        name_match = _NAME_RE.search(cleaned)
        if name_match:
            value = _truncate_text(name_match.group("value"), limit=80)
            if value:
                candidates.append(
                    MemoryCandidate(
                        category="identity",
                        memory_key="name",
                        value=value,
                        confidence=0.92,
                    )
                )

        role_match = _ROLE_RE.search(cleaned)
        if role_match:
            value = _truncate_text(role_match.group("value"), limit=120)
            if value:
                candidates.append(
                    MemoryCandidate(
                        category="identity",
                        memory_key="role",
                        value=value,
                        confidence=0.82,
                    )
                )

        deduped: dict[tuple[str, str], MemoryCandidate] = {}
        for candidate in candidates:
            deduped[(candidate.category, candidate.memory_key)] = candidate
        return list(deduped.values())

    @staticmethod
    def build_session_summary(history: list[dict[str, Any]]) -> str:
        """Build a compact rolling summary from recent tenant chat turns."""
        user_turns = [
            _truncate_text(str(entry.get("content") or ""), limit=140)
            for entry in history
            if str(entry.get("role") or "") == "user"
            and _clean_text(str(entry.get("content") or ""))
        ]
        assistant_turns = [
            _truncate_text(str(entry.get("content") or ""), limit=180)
            for entry in history
            if str(entry.get("role") or "") == "assistant"
            and _clean_text(str(entry.get("content") or ""))
        ]

        sections: list[str] = []
        if user_turns:
            sections.append("Recent user requests: " + " | ".join(user_turns[-3:]))
        if assistant_turns:
            sections.append("Latest assistant response: " + assistant_turns[-1])
        return "\n".join(sections)

    @staticmethod
    def _build_context_notes(
        *,
        session_summary: str,
        subject_memories: list[TenantSubjectMemory],
    ) -> str | None:
        sections: list[str] = []
        if session_summary:
            sections.append("Current conversation summary:\n" + session_summary)
        if subject_memories:
            memory_lines = "\n".join(memory.prompt_line for memory in subject_memories)
            sections.append("Known tenant user context:\n" + memory_lines)
        if not sections:
            return None
        return "\n\n".join(sections)
