"""Tenant intelligence skill — entity extraction for tenant CRM.

Runs asynchronously after each client_chat response to extract
structured information from conversations.

Levels:
    L1b (per-message, async): Contact entities, intent, sentiment, purchase signals
    L2  (per-session):        Conversation summary, outcome, customer profile
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from zetherion_ai.logging import get_logger
from zetherion_ai.skills.base import (
    Skill,
    SkillMetadata,
    SkillRequest,
    SkillResponse,
)
from zetherion_ai.skills.permissions import Permission, PermissionSet

if TYPE_CHECKING:
    from zetherion_ai.agent.inference import InferenceBroker
    from zetherion_ai.api.tenant import TenantManager

log = get_logger("zetherion_ai.skills.tenant_intelligence")

# ---------------------------------------------------------------------------
# LLM extraction prompts
# ---------------------------------------------------------------------------

_L1B_EXTRACTION_PROMPT = """\
You are an entity extraction assistant. Analyse the following chat message
from a customer on a business website and extract structured data.

Respond ONLY with valid JSON. No markdown, no commentary.

Message:
{message}

Extract:
{{
  "contact": {{
    "name": "<name or null>",
    "email": "<email or null>",
    "phone": "<phone or null>"
  }},
  "intent": "<one of: enquiry, complaint, booking, support, purchase, feedback, other>",
  "sentiment": "<one of: very_negative, negative, neutral, positive, very_positive>",
  "purchase_signal": <true if the customer shows buying intent, else false>,
  "products_mentioned": ["<product/service mentioned>"],
  "communication_preference": "<email, phone, chat, or null>"
}}
"""

_L2_SESSION_PROMPT = """\
You are a conversation analyst. Summarise the following customer chat session
on a business website.

Respond ONLY with valid JSON. No markdown, no commentary.

Conversation:
{conversation}

Extract:
{{
  "summary": "<2-3 sentence summary of the conversation>",
  "outcome": "<one of: resolved, unresolved, escalated, abandoned>",
  "customer_profile": "<1 sentence describing the customer>",
  "topics": ["<topic discussed>"],
  "unmet_needs": ["<need the business couldn't fulfil, or empty>"],
  "follow_up_needed": <true/false>,
  "follow_up_action": "<what action if follow_up_needed, or null>"
}}
"""


# ---------------------------------------------------------------------------
# TenantIntelligenceSkill
# ---------------------------------------------------------------------------


class TenantIntelligenceSkill(Skill):
    """Async entity extraction for tenant CRM data."""

    def __init__(
        self,
        inference_broker: InferenceBroker | None = None,
        tenant_manager: TenantManager | None = None,
    ) -> None:
        super().__init__(memory=None)
        self._broker = inference_broker
        self._tenant_manager = tenant_manager

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="tenant_intelligence",
            description="Async entity extraction from tenant chat conversations",
            version="0.1.0",
            permissions=PermissionSet(
                {
                    Permission.READ_CONFIG,
                    Permission.WRITE_OWN_COLLECTION,
                }
            ),
            intents=[
                "extract_message_entities",
                "summarise_session",
            ],
        )

    async def initialize(self) -> bool:
        if self._broker is None:
            log.warning("tenant_intelligence_no_broker")
        if self._tenant_manager is None:
            log.warning("tenant_intelligence_no_tenant_manager")
        log.info("tenant_intelligence_initialized")
        return True

    async def handle(self, request: SkillRequest) -> SkillResponse:
        intent = request.intent
        if intent == "extract_message_entities":
            return await self._handle_extract(request)
        elif intent == "summarise_session":
            return await self._handle_summarise(request)
        return SkillResponse.error_response(
            request.id,
            f"Unknown tenant_intelligence intent: {intent}",
        )

    # ------------------------------------------------------------------
    # L1b — Per-message entity extraction
    # ------------------------------------------------------------------

    async def _handle_extract(self, request: SkillRequest) -> SkillResponse:
        ctx = request.context
        message = ctx.get("message", request.message)
        tenant_id = ctx.get("tenant_id", "")
        session_id = ctx.get("session_id")

        if not message:
            return SkillResponse.error_response(request.id, "Message is required.")

        extraction = await self.extract_message_entities(message)

        # Persist to CRM if we have a TenantManager and tenant_id
        contact_id = None
        if self._tenant_manager and tenant_id:
            contact_id = await self._persist_extraction(
                tenant_id=tenant_id,
                session_id=session_id,
                extraction=extraction,
            )

        return SkillResponse(
            request_id=request.id,
            success=True,
            message="Entities extracted.",
            data={
                "extraction": extraction,
                "contact_id": contact_id,
            },
        )

    async def extract_message_entities(self, message: str) -> dict[str, Any]:
        """Extract structured entities from a single message using LLM.

        Returns a dict with keys: contact, intent, sentiment,
        purchase_signal, products_mentioned, communication_preference.

        Falls back to an empty extraction if no broker is configured
        or the LLM response can't be parsed.
        """
        if self._broker is None:
            return self._empty_extraction()

        prompt = _L1B_EXTRACTION_PROMPT.format(message=message)

        try:
            from zetherion_ai.agent.providers import TaskType

            result = await self._broker.infer(
                prompt=prompt,
                task_type=TaskType.DATA_EXTRACTION,
                temperature=0.1,
                max_tokens=500,
            )
            return self._parse_json_response(result.content)
        except Exception:
            log.exception("l1b_extraction_failed")
            return self._empty_extraction()

    # ------------------------------------------------------------------
    # L2 — Per-session summary
    # ------------------------------------------------------------------

    async def _handle_summarise(self, request: SkillRequest) -> SkillResponse:
        ctx = request.context
        messages = ctx.get("messages", [])
        tenant_id = ctx.get("tenant_id", "")
        session_id = ctx.get("session_id")

        if not messages:
            return SkillResponse.error_response(request.id, "Messages are required.")

        summary = await self.summarise_session(messages)

        # Persist as interaction if we have a TenantManager and tenant_id
        if self._tenant_manager and tenant_id:
            await self._tenant_manager.add_interaction(
                tenant_id=tenant_id,
                session_id=session_id,
                interaction_type="session_summary",
                summary=summary.get("summary"),
                entities=summary,
                sentiment=None,
                intent=None,
                outcome=summary.get("outcome"),
            )

        return SkillResponse(
            request_id=request.id,
            success=True,
            message="Session summarised.",
            data={"summary": summary},
        )

    async def summarise_session(
        self,
        messages: list[dict[str, str]],
    ) -> dict[str, Any]:
        """Summarise a conversation session using LLM.

        Args:
            messages: List of {role, content} dicts in chronological order.

        Returns a dict with keys: summary, outcome, customer_profile,
        topics, unmet_needs, follow_up_needed, follow_up_action.
        """
        if self._broker is None:
            return self._empty_summary()

        conversation = "\n".join(f"{m['role'].title()}: {m['content']}" for m in messages)
        prompt = _L2_SESSION_PROMPT.format(conversation=conversation)

        try:
            from zetherion_ai.agent.providers import TaskType

            result = await self._broker.infer(
                prompt=prompt,
                task_type=TaskType.DATA_EXTRACTION,
                temperature=0.1,
                max_tokens=800,
            )
            return self._parse_json_response(result.content)
        except Exception:
            log.exception("l2_session_summary_failed")
            return self._empty_summary()

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    async def _persist_extraction(
        self,
        tenant_id: str,
        session_id: str | None,
        extraction: dict[str, Any],
    ) -> str | None:
        """Persist L1b extraction results to the tenant CRM.

        Returns the contact_id if a contact was created/updated.
        """
        assert self._tenant_manager is not None
        contact = extraction.get("contact", {})
        contact_id = None

        # Upsert contact if we have any contact info
        if any(contact.get(k) for k in ("name", "email", "phone")):
            record = await self._tenant_manager.upsert_contact(
                tenant_id,
                name=contact.get("name"),
                email=contact.get("email"),
                phone=contact.get("phone"),
                tags=extraction.get("products_mentioned", []),
            )
            contact_id = str(record["contact_id"])

        # Record the interaction
        await self._tenant_manager.add_interaction(
            tenant_id=tenant_id,
            contact_id=contact_id,
            session_id=session_id,
            interaction_type="message_extraction",
            entities=extraction,
            sentiment=extraction.get("sentiment"),
            intent=extraction.get("intent"),
        )

        return contact_id

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_json_response(text: str) -> dict[str, Any]:
        """Parse JSON from an LLM response, stripping markdown fences."""
        cleaned = text.strip()
        if cleaned.startswith("```"):
            # Strip ```json ... ``` fences
            lines = cleaned.split("\n")
            lines = [ln for ln in lines if not ln.strip().startswith("```")]
            cleaned = "\n".join(lines)
        return json.loads(cleaned)  # type: ignore[no-any-return]

    @staticmethod
    def _empty_extraction() -> dict[str, Any]:
        return {
            "contact": {"name": None, "email": None, "phone": None},
            "intent": "other",
            "sentiment": "neutral",
            "purchase_signal": False,
            "products_mentioned": [],
            "communication_preference": None,
        }

    @staticmethod
    def _empty_summary() -> dict[str, Any]:
        return {
            "summary": None,
            "outcome": "unresolved",
            "customer_profile": None,
            "topics": [],
            "unmet_needs": [],
            "follow_up_needed": False,
            "follow_up_action": None,
        }
