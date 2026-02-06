"""Tiered inference engines for profile extraction.

Cost-conscious approach to profile updates:
- Tier 1 (80%): Regex/keywords - free
- Tier 2 (15%): Local Ollama - free
- Tier 3 (4%): Embedding similarity - ~$0.0001/query
- Tier 4 (1%): Cloud LLM - ~$0.01/query (rare)

Also includes implicit signal detection engines for:
- Urgency and mood inference
- Preference inference from patterns
- Identity context inference
- Relationship signal inference
- Conversation flow inference
"""

import re
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from zetherion_ai.logging import get_logger
from zetherion_ai.profile.models import (
    ProfileCategory,
    ProfileUpdate,
)

if TYPE_CHECKING:
    from zetherion_ai.agent.inference import InferenceBroker

log = get_logger("zetherion_ai.profile.inference")


class InferenceEngine(ABC):
    """Base class for inference engines."""

    @abstractmethod
    async def extract(self, message: str, context: str | None = None) -> list[ProfileUpdate]:
        """Extract profile updates from a message.

        Args:
            message: The user's message.
            context: Optional conversation context.

        Returns:
            List of proposed profile updates.
        """


class Tier1Inference(InferenceEngine):
    """Rule-based extraction - no ML, no cost.

    Handles ~80% of profile updates through:
    - Urgency detection
    - Verbosity feedback
    - Trust grants
    - Explicit preferences via regex
    """

    # Urgency detection keywords
    URGENCY_KEYWORDS = {"asap", "urgent", "hurry", "immediately", "critical", "emergency", "rush"}

    # Verbosity feedback
    VERBOSITY_DECREASE = {"tldr", "too long", "be brief", "shorter", "summarize", "too verbose"}
    VERBOSITY_INCREASE = {"more detail", "explain more", "elaborate", "expand on", "tell me more"}

    # Trust grants
    TRUST_GRANTS = {"just do it", "go ahead", "stop asking", "you decide", "i trust you"}

    # Explicit preferences (regex patterns)
    TIMEZONE_PATTERN = re.compile(
        r"(?:i'm in|my timezone is|i live in|i'm from)\s+([A-Z]{2,4}|[A-Za-z/_-]+)",
        re.IGNORECASE,
    )
    NAME_PATTERN = re.compile(
        r"(?:call me|my name is|i'm|i am)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
        re.IGNORECASE,
    )
    ROLE_PATTERN = re.compile(
        r"(?:i'm a|i work as|my job is|i am a)\s+"
        r"([a-z\s]+(?:developer|engineer|manager|designer|architect|analyst))",
        re.IGNORECASE,
    )
    LANGUAGE_PATTERN = re.compile(
        r"(?:i prefer|i use|i program in|my favorite language is)\s+([A-Za-z+#]+)",
        re.IGNORECASE,
    )

    async def extract(self, message: str, context: str | None = None) -> list[ProfileUpdate]:
        """Extract profile updates using rules and regex."""
        updates: list[ProfileUpdate] = []
        message_lower = message.lower()

        # Check urgency
        if any(kw in message_lower for kw in self.URGENCY_KEYWORDS):
            updates.append(
                ProfileUpdate(
                    profile="user",
                    field_name="current_urgency",
                    value="high",
                    confidence=0.9,
                    requires_confirmation=False,
                    source_tier=1,
                    category=ProfileCategory.HABITS,
                    reason="Urgency keywords detected",
                )
            )

        # Check verbosity feedback
        if any(kw in message_lower for kw in self.VERBOSITY_DECREASE):
            updates.append(
                ProfileUpdate(
                    profile="employment",
                    field_name="verbosity",
                    action="decrease",
                    value=0.2,
                    confidence=0.85,
                    source_tier=1,
                    reason="User requested less verbosity",
                )
            )

        if any(kw in message_lower for kw in self.VERBOSITY_INCREASE):
            updates.append(
                ProfileUpdate(
                    profile="employment",
                    field_name="verbosity",
                    action="increase",
                    value=0.2,
                    confidence=0.85,
                    source_tier=1,
                    reason="User requested more detail",
                )
            )

        # Check trust grants
        if any(phrase in message_lower for phrase in self.TRUST_GRANTS):
            updates.append(
                ProfileUpdate(
                    profile="employment",
                    field_name="trust_level",
                    action="increase",
                    value=0.1,
                    confidence=0.8,
                    source_tier=1,
                    reason="User granted trust",
                )
            )

        # Regex extractions
        if match := self.TIMEZONE_PATTERN.search(message):
            updates.append(
                ProfileUpdate(
                    profile="user",
                    field_name="timezone",
                    value=match.group(1).strip(),
                    confidence=0.95,
                    requires_confirmation=False,
                    source_tier=1,
                    category=ProfileCategory.IDENTITY,
                    reason="Explicit timezone mention",
                )
            )

        if match := self.NAME_PATTERN.search(message):
            name = match.group(1).strip()
            # Filter out common false positives
            if name.lower() not in {"the", "a", "an", "just", "here", "going"}:
                updates.append(
                    ProfileUpdate(
                        profile="user",
                        field_name="name",
                        value=name,
                        confidence=0.9,
                        requires_confirmation=False,
                        source_tier=1,
                        category=ProfileCategory.IDENTITY,
                        reason="Explicit name mention",
                    )
                )

        if match := self.ROLE_PATTERN.search(message):
            updates.append(
                ProfileUpdate(
                    profile="user",
                    field_name="role",
                    value=match.group(1).strip(),
                    confidence=0.9,
                    requires_confirmation=False,
                    source_tier=1,
                    category=ProfileCategory.IDENTITY,
                    reason="Explicit role mention",
                )
            )

        if match := self.LANGUAGE_PATTERN.search(message):
            updates.append(
                ProfileUpdate(
                    profile="user",
                    field_name="preferred_language",
                    value=match.group(1).strip(),
                    confidence=0.75,
                    requires_confirmation=False,
                    source_tier=1,
                    category=ProfileCategory.PREFERENCES,
                    reason="Explicit language preference",
                )
            )

        return updates


class Tier2Inference(InferenceEngine):
    """Local Ollama inference for ambiguous cases.

    Handles ~15% of profile updates for cases where Tier 1
    doesn't match but the message seems to contain profile info.
    """

    EXTRACTION_PROMPT = """Analyze this message for profile updates. Return JSON only.
Message: {message}

Extract any of: preferences, work patterns, project mentions, relationship info.
Return: {{"updates": [{{"field": "...", "value": "...", "confidence": 0.0-1.0}}]}}
If nothing found, return: {{"updates": []}}"""

    # Signals that a message might contain profile info
    PROFILE_SIGNALS = [
        "i ",
        "my ",
        "we ",
        "our ",
        "prefer",
        "usually",
        "always",
        "never",
        "working on",
        "deadline",
        "meeting",
        "project",
        "team",
        "colleague",
        "boss",
        "client",
    ]

    def __init__(self, inference_broker: "InferenceBroker | None" = None):
        """Initialize with optional inference broker.

        Args:
            inference_broker: The inference broker for LLM calls.
        """
        self._broker = inference_broker

    async def extract(self, message: str, context: str | None = None) -> list[ProfileUpdate]:
        """Extract profile updates using local Ollama.

        Only invokes LLM if message appears to contain profile info.
        """
        # Quick heuristic check first
        if not self._worth_analyzing(message):
            return []

        if self._broker is None:
            log.warning("tier2_no_broker", message="No inference broker configured")
            return []

        try:
            # Import here to avoid circular imports
            from zetherion_ai.agent.providers import TaskType

            result = await self._broker.infer(
                prompt=self.EXTRACTION_PROMPT.format(message=message),
                task_type=TaskType.PROFILE_EXTRACTION,
                max_tokens=300,
            )

            return self._parse_response(result.content)
        except Exception as e:
            log.warning("tier2_extraction_failed", error=str(e))
            return []

    def _worth_analyzing(self, message: str) -> bool:
        """Quick heuristic: does this message likely contain profile info?"""
        message_lower = message.lower()
        return any(signal in message_lower for signal in self.PROFILE_SIGNALS)

    def _parse_response(self, response: str) -> list[ProfileUpdate]:
        """Parse LLM response into profile updates."""
        import json

        updates: list[ProfileUpdate] = []

        try:
            # Try to extract JSON from response
            json_match = re.search(r"\{.*\}", response, re.DOTALL)
            if not json_match:
                return []

            data = json.loads(json_match.group())
            for update_data in data.get("updates", []):
                if "field" in update_data and "value" in update_data:
                    updates.append(
                        ProfileUpdate(
                            profile="user",
                            field_name=update_data["field"],
                            value=update_data["value"],
                            confidence=min(0.8, update_data.get("confidence", 0.6)),
                            source_tier=2,
                            reason="Extracted by local LLM",
                        )
                    )
        except (json.JSONDecodeError, KeyError) as e:
            log.debug("tier2_parse_error", error=str(e))

        return updates


class Tier3Inference(InferenceEngine):
    """Embedding-based pattern matching.

    Handles ~4% of profile updates by matching against known patterns.
    Low cost (~$0.0001/query).
    """

    def __init__(self, memory: "object | None" = None):
        """Initialize with optional Qdrant memory.

        Args:
            memory: The QdrantMemory instance for similarity search.
        """
        self._memory = memory

    async def extract(self, message: str, context: str | None = None) -> list[ProfileUpdate]:
        """Extract profile updates using embedding similarity."""
        if self._memory is None:
            return []

        # This would search against a "preference_patterns" collection
        # For now, return empty - will be implemented when Qdrant collections are set up
        return []


class Tier4Inference(InferenceEngine):
    """Cloud LLM for complex cases - use sparingly.

    Handles ~1% of profile updates for:
    - Multi-turn patterns spanning several messages
    - Subtle relationship dynamics
    - Complex project/goal inference
    """

    def __init__(self, inference_broker: "InferenceBroker | None" = None):
        """Initialize with optional inference broker.

        Args:
            inference_broker: The inference broker for LLM calls.
        """
        self._broker = inference_broker

    async def extract(self, message: str, context: str | None = None) -> list[ProfileUpdate]:
        """Extract profile updates using cloud LLM.

        Only for complex, high-value inferences.
        """
        # This tier is rarely used - only for complex analysis
        # Will be invoked explicitly when needed
        return []


# === Implicit Signal Detection Engines ===


@dataclass
class MessageMetadata:
    """Metadata about a message for implicit signal detection."""

    length: int
    response_time_ms: int | None
    punctuation_count: dict[str, int]
    is_all_caps: bool
    contains_question: bool


class UrgencyMoodInference:
    """Detect urgency and mood from message patterns."""

    def analyze(self, message: str, response_time_ms: int | None = None) -> list[ProfileUpdate]:
        """Analyze message for urgency and mood signals.

        Args:
            message: The user's message.
            response_time_ms: Time since last bot response.

        Returns:
            List of profile updates based on detected signals.
        """
        updates: list[ProfileUpdate] = []

        # Short, direct questions = urgent
        if len(message) < 30 and message.strip().endswith("?"):
            updates.append(
                ProfileUpdate(
                    profile="user",
                    field_name="current_urgency",
                    value="high",
                    confidence=0.6,
                    source_tier=1,
                    category=ProfileCategory.HABITS,
                    reason="Short direct question",
                )
            )

        # Multiple exclamation marks = emphasis
        if message.count("!") > 2:
            updates.append(
                ProfileUpdate(
                    profile="user",
                    field_name="current_emphasis",
                    value="strong",
                    confidence=0.7,
                    source_tier=1,
                    category=ProfileCategory.HABITS,
                    reason="Multiple exclamation marks",
                )
            )

        # Ellipsis = uncertainty
        if "..." in message:
            updates.append(
                ProfileUpdate(
                    profile="user",
                    field_name="current_uncertainty",
                    value=True,
                    confidence=0.5,
                    source_tier=1,
                    category=ProfileCategory.HABITS,
                    reason="Ellipsis detected",
                )
            )

        # Quick response = high engagement
        if response_time_ms is not None and response_time_ms < 5000:
            updates.append(
                ProfileUpdate(
                    profile="user",
                    field_name="engagement_level",
                    value="high",
                    confidence=0.65,
                    source_tier=1,
                    category=ProfileCategory.HABITS,
                    reason="Quick response time",
                )
            )

        # All caps = possible frustration
        if message.isupper() and len(message) > 10:
            updates.append(
                ProfileUpdate(
                    profile="user",
                    field_name="possible_frustration",
                    value=True,
                    confidence=0.6,
                    source_tier=1,
                    category=ProfileCategory.HABITS,
                    reason="All caps message",
                )
            )

        return updates


class PreferenceInference:
    """Infer preferences from behavior patterns."""

    def __init__(self) -> None:
        """Initialize the preference inference engine."""
        self._interaction_times: list[datetime] = []
        self._topics: list[str] = []

    def record_interaction(self, timestamp: datetime, topics: list[str] | None = None) -> None:
        """Record an interaction for pattern analysis.

        Args:
            timestamp: When the interaction occurred.
            topics: Topics discussed in the interaction.
        """
        self._interaction_times.append(timestamp)
        if topics:
            self._topics.extend(topics)

        # Keep only last 1000 interactions
        if len(self._interaction_times) > 1000:
            self._interaction_times = self._interaction_times[-1000:]
        if len(self._topics) > 1000:
            self._topics = self._topics[-1000:]

    async def analyze_patterns(self, user_id: str) -> list[ProfileUpdate]:
        """Analyze interaction patterns to infer preferences.

        Args:
            user_id: The user's ID.

        Returns:
            List of profile updates based on patterns.
        """
        updates: list[ProfileUpdate] = []

        # Analyze time-of-day patterns
        if len(self._interaction_times) >= 10:
            peak_hours = self._find_peak_hours()
            if peak_hours:
                updates.append(
                    ProfileUpdate(
                        profile="user",
                        field_name="active_hours",
                        value=peak_hours,
                        confidence=0.7,
                        source_tier=1,
                        category=ProfileCategory.SCHEDULE,
                        reason="Derived from interaction patterns",
                    )
                )

        # Analyze topic frequency
        if len(self._topics) >= 20:
            dominant_topic = self._find_dominant_topic()
            if dominant_topic:
                updates.append(
                    ProfileUpdate(
                        profile="user",
                        field_name="current_focus",
                        value=dominant_topic,
                        confidence=0.6,
                        source_tier=1,
                        category=ProfileCategory.PROJECTS,
                        reason="Frequently discussed topic",
                    )
                )

        return updates

    def _find_peak_hours(self) -> list[int] | None:
        """Find peak interaction hours."""
        if not self._interaction_times:
            return None

        hour_counts: dict[int, int] = defaultdict(int)
        for dt in self._interaction_times:
            hour_counts[dt.hour] += 1

        if not hour_counts:
            return None

        # Find hours with above-average activity
        avg_count = sum(hour_counts.values()) / len(hour_counts)
        peak_hours = [h for h, c in hour_counts.items() if c > avg_count * 1.5]

        return sorted(peak_hours) if peak_hours else None

    def _find_dominant_topic(self) -> str | None:
        """Find the most frequently discussed topic."""
        if not self._topics:
            return None

        topic_counts: dict[str, int] = defaultdict(int)
        for topic in self._topics:
            topic_counts[topic] += 1

        if not topic_counts:
            return None

        dominant = max(topic_counts.items(), key=lambda x: x[1])
        # Only return if it's significantly more common
        if dominant[1] >= len(self._topics) * 0.2:
            return dominant[0]
        return None


class IdentityContextInference:
    """Infer identity details from context clues."""

    ROLE_INDICATORS: dict[str, list[str]] = {
        "deploy": ["devops", "sre", "backend"],
        "design": ["designer", "ux", "frontend"],
        "sprint": ["scrum_master", "agile", "project_manager"],
        "model": ["ml_engineer", "data_scientist"],
        "schema": ["backend", "database", "dba"],
        "test": ["qa", "tester", "sdet"],
        "kubernetes": ["devops", "sre", "platform"],
        "react": ["frontend", "ui_developer"],
        "api": ["backend", "integration"],
    }

    def infer_role(self, messages: list[str]) -> ProfileUpdate | None:
        """Infer likely role from message patterns.

        Args:
            messages: Recent messages from the user.

        Returns:
            Profile update with inferred role, or None.
        """
        role_scores: dict[str, int] = defaultdict(int)

        for message in messages:
            message_lower = message.lower()
            for keyword, roles in self.ROLE_INDICATORS.items():
                if keyword in message_lower:
                    for role in roles:
                        role_scores[role] += 1

        if not role_scores:
            return None

        top_role = max(role_scores, key=lambda r: role_scores[r])
        confidence = min(0.8, role_scores[top_role] * 0.1)

        return ProfileUpdate(
            profile="user",
            field_name="likely_role",
            value=top_role,
            confidence=confidence,
            requires_confirmation=confidence < 0.6,
            source_tier=1,
            category=ProfileCategory.IDENTITY,
            reason=f"Inferred from keyword patterns ({role_scores[top_role]} matches)",
        )


class RelationshipSignalInference:
    """Infer relationship dynamics from communication patterns."""

    def __init__(self) -> None:
        """Initialize the relationship signal inference engine."""
        self._corrections: int = 0
        self._positive_feedback: int = 0
        self._delegations: int = 0
        self._total_interactions: int = 0

    def record_interaction(
        self,
        was_correction: bool = False,
        was_positive: bool = False,
        was_delegation: bool = False,
    ) -> None:
        """Record an interaction's characteristics.

        Args:
            was_correction: Whether user corrected the bot.
            was_positive: Whether user gave positive feedback.
            was_delegation: Whether user delegated a task.
        """
        self._total_interactions += 1
        if was_correction:
            self._corrections += 1
        if was_positive:
            self._positive_feedback += 1
        if was_delegation:
            self._delegations += 1

    def analyze(self) -> list[ProfileUpdate]:
        """Analyze relationship signals.

        Returns:
            List of profile updates based on relationship dynamics.
        """
        updates: list[ProfileUpdate] = []

        if self._total_interactions < 10:
            return updates

        # High correction rate = accuracy concern
        correction_rate = self._corrections / self._total_interactions
        if correction_rate > 0.2:
            updates.append(
                ProfileUpdate(
                    profile="employment",
                    field_name="accuracy_concern",
                    value=True,
                    confidence=0.7,
                    source_tier=1,
                    reason=f"High correction rate: {correction_rate:.1%}",
                )
            )

        # Positive feedback = trust building
        positive_rate = self._positive_feedback / self._total_interactions
        if positive_rate > 0.3:
            updates.append(
                ProfileUpdate(
                    profile="employment",
                    field_name="trust_level",
                    action="increase",
                    value=0.05,
                    confidence=0.6,
                    source_tier=1,
                    reason=f"High positive feedback: {positive_rate:.1%}",
                )
            )

        # Many delegations = increase proactivity
        if self._delegations > 10:
            updates.append(
                ProfileUpdate(
                    profile="employment",
                    field_name="proactivity",
                    action="increase",
                    value=0.1,
                    confidence=0.65,
                    source_tier=1,
                    reason=f"User delegated {self._delegations} tasks",
                )
            )

        return updates


class ConversationFlowInference:
    """Infer from how the conversation flows."""

    # Clarification signals
    CLARIFICATION_SIGNALS = [
        "what do you mean",
        "?",
        "huh",
        "explain",
        "i don't understand",
        "unclear",
        "confused",
    ]

    def analyze_turn(
        self,
        user_msg: str,
        bot_response: str,
        next_user_msg: str | None,
    ) -> list[ProfileUpdate]:
        """Analyze a conversation turn for signals.

        Args:
            user_msg: The user's message.
            bot_response: The bot's response.
            next_user_msg: The user's next message, if any.

        Returns:
            List of profile updates based on conversation flow.
        """
        updates: list[ProfileUpdate] = []

        if next_user_msg is None:
            return updates

        next_lower = next_user_msg.lower()

        # Did user ask for clarification?
        if any(s in next_lower for s in self.CLARIFICATION_SIGNALS):
            updates.append(
                ProfileUpdate(
                    profile="employment",
                    field_name="verbosity",
                    action="increase",
                    value=0.1,
                    confidence=0.5,
                    source_tier=1,
                    reason="User needed clarification",
                )
            )

        # Did user build on the response? (short follow-up likely means continuing)
        if len(next_user_msg) < 50 and "thanks" not in next_lower:
            updates.append(
                ProfileUpdate(
                    profile="employment",
                    field_name="helpfulness_signal",
                    action="increment",
                    value=1,
                    confidence=0.7,
                    source_tier=1,
                    reason="User continued conversation thread",
                )
            )

        return updates


class ProfileInferencePipeline:
    """Orchestrates all inference engines in a tiered manner."""

    def __init__(
        self,
        inference_broker: "InferenceBroker | None" = None,
        memory: "object | None" = None,
        tier1_only: bool = False,
    ):
        """Initialize the inference pipeline.

        Args:
            inference_broker: The inference broker for LLM calls.
            memory: The QdrantMemory instance for similarity search.
            tier1_only: If True, only use Tier 1 (free) inference.
        """
        self.tier1 = Tier1Inference()
        self.tier2 = Tier2Inference(inference_broker)
        self.tier3 = Tier3Inference(memory)
        self.tier4 = Tier4Inference(inference_broker)

        self.urgency_mood = UrgencyMoodInference()
        self.preference = PreferenceInference()
        self.identity = IdentityContextInference()
        self.relationship = RelationshipSignalInference()
        self.conversation_flow = ConversationFlowInference()

        self._tier1_only = tier1_only

    async def extract_all(
        self,
        message: str,
        context: str | None = None,
        response_time_ms: int | None = None,
    ) -> list[ProfileUpdate]:
        """Run all applicable inference engines.

        Args:
            message: The user's message.
            context: Optional conversation context.
            response_time_ms: Time since last bot response.

        Returns:
            Combined list of profile updates from all engines.
        """
        updates: list[ProfileUpdate] = []

        # Always run Tier 1 (free)
        updates.extend(await self.tier1.extract(message, context))

        # Run implicit signal engines (all free)
        updates.extend(self.urgency_mood.analyze(message, response_time_ms))

        # Run higher tiers if not in tier1-only mode
        if not self._tier1_only:
            tier2_updates = await self.tier2.extract(message, context)
            if not tier2_updates:
                # Only try Tier 3 if Tier 2 found nothing
                tier3_updates = await self.tier3.extract(message, context)
                updates.extend(tier3_updates)
            else:
                updates.extend(tier2_updates)

        return updates
