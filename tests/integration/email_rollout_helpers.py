"""Test helpers for email personality persistence and inbound rollout tests."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from zetherion_ai.agent.inference import InferenceResult
from zetherion_ai.agent.providers import Provider, TaskType
from zetherion_ai.discord.security.models import ThreatAction, ThreatVerdict
from zetherion_ai.personal.models import (
    PersonalContact,
    PersonalityProfile,
    PersonalLearning,
    PersonalPolicy,
    PersonalProfile,
    Relationship,
)
from zetherion_ai.routing.models import RouteDecision, RouteMode, RouteTag


@dataclass
class _SecurityResult:
    verdict: ThreatVerdict
    payload_hash: str = "payload-1"


class AllowSecurityPipeline:
    """Always-allow security pipeline test stub."""

    async def analyze(
        self,
        content: str,
        *,
        source: str,
        user_id: int,
        context_id: int,
        metadata: dict[str, Any],
    ) -> _SecurityResult:
        del content, source, user_id, context_id, metadata
        return _SecurityResult(ThreatVerdict(action=ThreatAction.ALLOW, score=0.0, tier_reached=1))


class InMemoryIntegrationStorage:
    """Small in-memory storage stub that supports EmailRouter + EmailSkill tests."""

    def __init__(self) -> None:
        self.emails: list[dict[str, Any]] = []
        self.object_links: dict[tuple[int, str, str, str], dict[str, Any]] = {}
        self.routing_decisions: list[RouteDecision] = []
        self.security_events: list[dict[str, Any]] = []

    async def store_email_message(self, **kwargs: Any) -> None:
        self.emails.append(kwargs)

    async def record_security_event(self, **kwargs: Any) -> None:
        self.security_events.append(kwargs)

    async def record_routing_decision(
        self,
        user_id: int,
        provider: str,
        source_type: str,
        decision: RouteDecision,
    ) -> None:
        del user_id, provider, source_type
        self.routing_decisions.append(decision)

    async def upsert_object_link(
        self,
        *,
        user_id: int,
        provider: str,
        object_type: str,
        local_id: str,
        external_id: str,
        destination_id: str,
        metadata: dict[str, Any],
    ) -> None:
        del local_id, destination_id
        self.object_links[(user_id, provider, object_type, external_id)] = {
            "external_id": external_id,
            "metadata": metadata,
        }

    async def get_object_link_by_external(
        self,
        *,
        user_id: int,
        provider: str,
        object_type: str,
        external_id: str,
    ) -> dict[str, Any] | None:
        return self.object_links.get((user_id, provider, object_type, external_id))

    async def enqueue_ingestion_batch(
        self,
        *,
        user_id: int,
        provider: str,
        source_type: str,
        items: list[dict[str, Any]],
        status: str,
        error_code: str,
        error_detail: str | None,
    ) -> tuple[str, int]:
        del user_id, provider, source_type, status, error_code, error_detail
        return ("batch-1", len(items))

    async def claim_ingestion_queue_items(
        self,
        *,
        user_id: int,
        provider: str,
        source_type: str,
        statuses: list[str],
        limit: int,
    ) -> list[Any]:
        del user_id, provider, source_type, statuses, limit
        return []

    async def mark_ingestion_items_done(self, queue_ids: list[int]) -> None:
        del queue_ids

    async def mark_ingestion_items_blocked_unhealthy(
        self,
        *,
        queue_ids: list[int],
        error_code: str,
        error_detail: str,
    ) -> None:
        del queue_ids, error_code, error_detail

    async def move_ingestion_item_to_dead_letter(
        self,
        *,
        queue_id: int,
        error_code: str,
        error_detail: str,
    ) -> None:
        del queue_id, error_code, error_detail

    async def get_ingestion_queue_counts(
        self,
        *,
        user_id: int,
        provider: str,
        source_type: str,
    ) -> dict[str, int]:
        del user_id, provider, source_type
        return {"pending": 0, "blocked_unhealthy": 0, "processing": 0}

    async def get_primary_destination(
        self,
        user_id: int,
        provider: str,
        destination_type: str,
    ) -> dict[str, Any] | None:
        del user_id, provider, destination_type
        return None

    async def set_primary_destination(self, **kwargs: Any) -> bool:
        del kwargs
        return True


class InMemoryPersonalStorage:
    """In-memory PersonalStorage behavior for integration/e2e tests."""

    def __init__(self) -> None:
        self._contacts: dict[tuple[int, str], PersonalContact] = {}
        self._learnings: list[PersonalLearning] = []
        self._policies: list[PersonalPolicy] = []
        self._profiles: dict[int, PersonalProfile] = {}
        self._signals: list[dict[str, Any]] = []
        self._personality_profiles: dict[tuple[int, str, str], PersonalityProfile] = {}

    @property
    def signal_log(self) -> list[dict[str, Any]]:
        return self._signals

    async def upsert_contact(self, contact: PersonalContact) -> int:
        key = (contact.user_id, contact.contact_email)
        existing = self._contacts.get(key)
        if existing is not None:
            contact.interaction_count = max(contact.interaction_count, existing.interaction_count)
        self._contacts[key] = contact
        return 1

    async def increment_contact_interaction(self, user_id: int, contact_email: str) -> bool:
        key = (user_id, contact_email)
        existing = self._contacts.get(key)
        if existing is None:
            existing = PersonalContact(
                user_id=user_id,
                contact_email=contact_email,
                relationship=Relationship.OTHER,
            )
        existing.interaction_count += 1
        existing.last_interaction = datetime.now()
        self._contacts[key] = existing
        return True

    async def get_contact(self, user_id: int, contact_email: str) -> PersonalContact | None:
        return self._contacts.get((user_id, contact_email))

    async def list_contacts(self, user_id: int, limit: int = 10) -> list[PersonalContact]:
        contacts = [c for (uid, _), c in self._contacts.items() if uid == user_id]
        contacts.sort(key=lambda c: c.interaction_count, reverse=True)
        return contacts[:limit]

    async def add_learning(self, learning: PersonalLearning) -> int:
        self._learnings.append(learning)
        return len(self._learnings)

    async def list_learnings(self, user_id: int, limit: int = 10) -> list[PersonalLearning]:
        rows = [lr for lr in self._learnings if lr.user_id == user_id]
        rows.sort(key=lambda lr: lr.created_at, reverse=True)
        return rows[:limit]

    async def list_policies(self, user_id: int) -> list[PersonalPolicy]:
        return [p for p in self._policies if p.user_id == user_id]

    async def log_personality_signal(self, **kwargs: Any) -> int:
        self._signals.append(kwargs)
        return len(self._signals)

    async def get_personality_profile(
        self,
        user_id: int,
        subject_email: str,
        subject_role: str,
    ) -> PersonalityProfile | None:
        return self._personality_profiles.get((user_id, subject_email, subject_role))

    async def upsert_personality_profile(self, profile: PersonalityProfile) -> int:
        key = (profile.user_id, profile.subject_email, profile.subject_role)
        self._personality_profiles[key] = profile
        return profile.id or 1

    async def list_personality_profiles(
        self,
        user_id: int,
        *,
        subject_role: str | None = None,
        min_observations: int = 1,
        limit: int = 10,
    ) -> list[PersonalityProfile]:
        rows = [p for (uid, _, _), p in self._personality_profiles.items() if uid == user_id]
        if subject_role is not None:
            rows = [p for p in rows if p.subject_role == subject_role]
        rows = [p for p in rows if p.observation_count >= min_observations]
        rows.sort(key=lambda p: p.observation_count, reverse=True)
        return rows[:limit]

    async def get_profile(self, user_id: int) -> PersonalProfile | None:
        return self._profiles.get(user_id)

    async def upsert_profile(self, profile: PersonalProfile) -> int:
        self._profiles[profile.user_id] = profile
        return 1


class InferenceBrokerStub:
    """Inference broker stub with Groq-first classification/profile behavior."""

    def __init__(
        self,
        *,
        extraction_provider: Provider = Provider.GROQ,
        available_providers: set[Provider] | None = None,
    ) -> None:
        self.extraction_provider = extraction_provider
        self.available_providers = available_providers or {
            Provider.GROQ,
            Provider.GEMINI,
            Provider.CLAUDE,
            Provider.OPENAI,
        }
        self.calls: list[tuple[TaskType, str]] = []
        self.profile_prompts: list[str] = []

    async def infer(
        self,
        prompt: str,
        task_type: TaskType,
        system_prompt: str | None = None,
        messages: list[dict[str, str]] | None = None,
        max_tokens: int = 500,
        temperature: float = 0.1,
    ) -> InferenceResult:
        del system_prompt, messages, max_tokens, temperature
        self.calls.append((task_type, prompt))

        if task_type == TaskType.CLASSIFICATION:
            from_email = self._extract_field(prompt, "From")
            content = json.dumps(
                {
                    "category": "work_colleague",
                    "action": "read_only",
                    "urgency": 0.2,
                    "confidence": 0.94,
                    "sentiment": "neutral",
                    "topics": ["status"],
                    "contact": {"email": from_email},
                    "reasoning": "status update",
                }
            )
            return InferenceResult(
                content=content,
                provider=Provider.GROQ,
                task_type=task_type,
                model="llama-3.3-70b-versatile",
                latency_ms=25.0,
            )

        if task_type == TaskType.PROFILE_EXTRACTION:
            self.profile_prompts.append(prompt)
            author_role = "owner" if '"author_role": "owner"' in prompt else "contact"
            from_email = self._extract_field(prompt, "From")
            content = json.dumps(
                {
                    "author_role": author_role,
                    "author_name": "Sender",
                    "author_email": from_email,
                    "writing_style": {
                        "formality": "formal",
                        "avg_sentence_length": "medium",
                        "uses_greeting": True,
                        "greeting_style": "Hi,",
                        "uses_signoff": True,
                        "signoff_style": "Thanks,",
                        "uses_emoji": False,
                        "uses_bullet_points": False,
                        "vocabulary_level": "standard",
                    },
                    "communication": {
                        "primary_trait": "direct",
                        "secondary_trait": None,
                        "emotional_tone": "neutral",
                        "assertiveness": 0.6,
                        "responsiveness_signal": "",
                    },
                    "relationship": {
                        "familiarity": 0.5,
                        "power_dynamic": "peer",
                        "trust_level": 0.6,
                        "rapport_indicators": [],
                    },
                    "preferences_revealed": ["prefers concise updates"],
                    "schedule_signals": ["responds in mornings"],
                    "commitments_made": ["send recap"],
                    "expectations_set": ["review before Friday"],
                    "confidence": 0.88,
                    "reasoning": "clear structure",
                }
            )
            return InferenceResult(
                content=content,
                provider=Provider.GROQ,
                task_type=task_type,
                model="llama-3.3-70b-versatile",
                latency_ms=33.0,
            )

        if task_type == TaskType.DATA_EXTRACTION:
            model = (
                "llama-3.3-70b-versatile"
                if self.extraction_provider == Provider.GROQ
                else "gemini-2.5-flash"
            )
            return InferenceResult(
                content='{"kind":"none"}',
                provider=self.extraction_provider,
                task_type=task_type,
                model=model,
                latency_ms=18.0,
            )

        raise RuntimeError(f"unsupported task type in stub: {task_type}")

    async def health_check(self, provider: Provider) -> bool:
        return provider in self.available_providers

    @staticmethod
    def _extract_field(prompt: str, field: str) -> str:
        match = re.search(rf"^{field}:\\s*(.+)$", prompt, flags=re.MULTILINE)
        if match:
            return match.group(1).strip()
        return ""


class ChatInferenceStub:
    """Minimal inference stub for Groq router e2e tests."""

    def __init__(self) -> None:
        self.calls: list[tuple[TaskType, str]] = []

    async def infer(
        self,
        prompt: str,
        task_type: TaskType,
        system_prompt: str | None = None,
        messages: list[dict[str, str]] | None = None,
        max_tokens: int = 500,
        temperature: float = 0.1,
    ) -> InferenceResult:
        del system_prompt, messages, max_tokens, temperature
        self.calls.append((task_type, prompt))
        if task_type == TaskType.CLASSIFICATION:
            return InferenceResult(
                content='{"intent":"simple_query","confidence":0.95,"reasoning":"greeting"}',
                provider=Provider.GROQ,
                task_type=task_type,
                model="llama-3.3-70b-versatile",
                latency_ms=9.0,
            )
        if task_type == TaskType.SIMPLE_QA:
            return InferenceResult(
                content="Hello!",
                provider=Provider.GROQ,
                task_type=task_type,
                model="llama-3.3-70b-versatile",
                latency_ms=12.0,
            )
        raise RuntimeError(f"unsupported task type in chat stub: {task_type}")

    async def health_check(self, provider: Provider) -> bool:
        return provider == Provider.GROQ


class TaskCalendarRouterStub:
    """Task/calendar routing stub."""

    def __init__(self) -> None:
        self.route_task = AsyncMock(
            return_value=RouteDecision(
                mode=RouteMode.AUTO,
                route_tag=RouteTag.TASK_CANDIDATE,
                reason="task routed",
                provider="google",
            )
        )
        self.route_event = AsyncMock(
            return_value=RouteDecision(
                mode=RouteMode.AUTO,
                route_tag=RouteTag.CALENDAR_CANDIDATE,
                reason="event routed",
                provider="google",
            )
        )


class ProvidersWithEmailStub:
    """Provider registry stub with unread message support."""

    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self._adapter = SimpleNamespace()
        self._adapter.email = SimpleNamespace(list_unread=AsyncMock(return_value=messages))

    def adapters(self, provider: str) -> Any:
        if provider == "google":
            return self._adapter
        return None


def make_unread_message(
    *,
    account_ref: str,
    account_email: str,
    external_id: str,
    from_email: str,
    subject: str,
    body_preview: str,
) -> dict[str, Any]:
    """Build a provider unread message payload."""
    return {
        "account_ref": account_ref,
        "account_email": account_email,
        "external_id": external_id,
        "thread_id": f"thread-{external_id}",
        "subject": subject,
        "from_email": from_email,
        "to_emails": [account_email],
        "body_preview": body_preview,
        "received_at": datetime.now().isoformat(),
    }
