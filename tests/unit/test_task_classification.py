"""Unit tests for task-type classification in agent core.

Tests the keyword frozensets and _classify_task_type method to verify
that messages are routed to the correct TaskType.
"""

from unittest.mock import MagicMock, patch

import pytest

from zetherion_ai.agent.core import (
    CODE_DEBUG_KEYWORDS,
    CODE_KEYWORDS,
    CODE_REVIEW_KEYWORDS,
    CREATIVE_KEYWORDS,
    MATH_KEYWORDS,
    MATH_SPECIFIC_KEYWORDS,
    SUMMARIZATION_KEYWORDS,
)
from zetherion_ai.agent.providers import TaskType


@pytest.fixture
def agent():
    """Create an Agent instance with mocked dependencies."""
    with (
        patch("zetherion_ai.agent.core.create_router_sync") as _mock_router,
        patch("zetherion_ai.agent.core.InferenceBroker") as _mock_broker,
    ):
        mock_memory = MagicMock()
        from zetherion_ai.agent.core import Agent

        return Agent(memory=mock_memory)


class TestKeywordSets:
    """Tests for keyword frozenset definitions."""

    def test_code_keywords_is_frozenset(self) -> None:
        """CODE_KEYWORDS should be a frozenset."""
        assert isinstance(CODE_KEYWORDS, frozenset)

    def test_code_keywords_non_empty(self) -> None:
        """CODE_KEYWORDS should contain entries."""
        assert len(CODE_KEYWORDS) > 0

    def test_code_review_keywords_is_frozenset(self) -> None:
        """CODE_REVIEW_KEYWORDS should be a frozenset."""
        assert isinstance(CODE_REVIEW_KEYWORDS, frozenset)

    def test_code_debug_keywords_is_frozenset(self) -> None:
        """CODE_DEBUG_KEYWORDS should be a frozenset."""
        assert isinstance(CODE_DEBUG_KEYWORDS, frozenset)

    def test_math_keywords_is_frozenset(self) -> None:
        """MATH_KEYWORDS should be a frozenset."""
        assert isinstance(MATH_KEYWORDS, frozenset)

    def test_math_specific_keywords_is_frozenset(self) -> None:
        """MATH_SPECIFIC_KEYWORDS should be a frozenset."""
        assert isinstance(MATH_SPECIFIC_KEYWORDS, frozenset)

    def test_creative_keywords_is_frozenset(self) -> None:
        """CREATIVE_KEYWORDS should be a frozenset."""
        assert isinstance(CREATIVE_KEYWORDS, frozenset)

    def test_summarization_keywords_is_frozenset(self) -> None:
        """SUMMARIZATION_KEYWORDS should be a frozenset."""
        assert isinstance(SUMMARIZATION_KEYWORDS, frozenset)

    def test_math_specific_is_subset_of_math(self) -> None:
        """MATH_SPECIFIC_KEYWORDS should be a subset of MATH_KEYWORDS."""
        assert MATH_SPECIFIC_KEYWORDS.issubset(MATH_KEYWORDS)


class TestClassifyTaskType:
    """Tests for Agent._classify_task_type method."""

    def test_code_keyword_returns_code_generation(self, agent) -> None:
        """A message with a code keyword should return CODE_GENERATION."""
        result = agent._classify_task_type("Can you write a python script for me?")
        assert result == TaskType.CODE_GENERATION

    def test_code_with_review_returns_code_review(self, agent) -> None:
        """A message with both code and review keywords should return CODE_REVIEW."""
        result = agent._classify_task_type("Please review my code implementation")
        assert result == TaskType.CODE_REVIEW

    def test_code_with_debug_returns_code_debugging(self, agent) -> None:
        """A message with both code and debug keywords should return CODE_DEBUGGING."""
        result = agent._classify_task_type("Can you debug this python error?")
        assert result == TaskType.CODE_DEBUGGING

    def test_code_with_fix_returns_code_debugging(self, agent) -> None:
        """A message asking to fix code should return CODE_DEBUGGING."""
        result = agent._classify_task_type("Fix this javascript bug please")
        assert result == TaskType.CODE_DEBUGGING

    def test_math_specific_keyword_returns_math_analysis(self, agent) -> None:
        """A message with a math-specific keyword should return MATH_ANALYSIS."""
        result = agent._classify_task_type("Calculate the derivative of this equation")
        assert result == TaskType.MATH_ANALYSIS

    def test_math_general_keyword_returns_complex_reasoning(self, agent) -> None:
        """A message with a general math keyword (not math-specific) returns COMPLEX_REASONING."""
        result = agent._classify_task_type("Can you analyze why this approach works?")
        assert result == TaskType.COMPLEX_REASONING

    def test_reasoning_keyword_returns_complex_reasoning(self, agent) -> None:
        """A reasoning keyword without math-specific terms returns COMPLEX_REASONING."""
        result = agent._classify_task_type("I need help with logical reasoning")
        assert result == TaskType.COMPLEX_REASONING

    def test_creative_keyword_returns_creative_writing(self, agent) -> None:
        """A message with a creative keyword should return CREATIVE_WRITING."""
        result = agent._classify_task_type("Write me a short story about cats")
        assert result == TaskType.CREATIVE_WRITING

    def test_poem_returns_creative_writing(self, agent) -> None:
        """Asking for a poem should return CREATIVE_WRITING."""
        result = agent._classify_task_type("I want a poem about the sunset")
        assert result == TaskType.CREATIVE_WRITING

    def test_summarize_returns_summarization(self, agent) -> None:
        """A summarize keyword should return SUMMARIZATION."""
        result = agent._classify_task_type("Can you summarize this article for me?")
        assert result == TaskType.SUMMARIZATION

    def test_tldr_returns_summarization(self, agent) -> None:
        """TLDR should return SUMMARIZATION."""
        result = agent._classify_task_type("Give me a tldr of this document")
        assert result == TaskType.SUMMARIZATION

    def test_generic_message_returns_conversation(self, agent) -> None:
        """A generic message with no special keywords should return CONVERSATION."""
        result = agent._classify_task_type("Hello, how are you today?")
        assert result == TaskType.CONVERSATION

    def test_empty_message_returns_conversation(self, agent) -> None:
        """An empty message should return CONVERSATION (default)."""
        result = agent._classify_task_type("")
        assert result == TaskType.CONVERSATION

    def test_classification_is_case_insensitive(self, agent) -> None:
        """Classification should be case insensitive."""
        result = agent._classify_task_type("PYTHON PROGRAMMING help")
        assert result == TaskType.CODE_GENERATION

    def test_code_keywords_take_priority_over_creative(self, agent) -> None:
        """Code keywords should take priority over creative keywords (checked first)."""
        # "write" is a CREATIVE keyword, "python" is a CODE keyword
        # Code is checked first, so code wins
        result = agent._classify_task_type("Write a python function")
        assert result == TaskType.CODE_GENERATION

    def test_condense_returns_summarization(self, agent) -> None:
        """The condense keyword should return SUMMARIZATION."""
        result = agent._classify_task_type("Condense this into a few bullet points")
        assert result == TaskType.SUMMARIZATION

    def test_each_code_keyword_triggers_code(self, agent) -> None:
        """Every keyword in CODE_KEYWORDS should trigger a code-related TaskType."""
        code_types = {TaskType.CODE_GENERATION, TaskType.CODE_REVIEW, TaskType.CODE_DEBUGGING}
        for kw in CODE_KEYWORDS:
            result = agent._classify_task_type(f"Help me with {kw}")
            assert result in code_types, f"Keyword '{kw}' did not trigger a code TaskType"

    def test_each_creative_keyword_triggers_creative(self, agent) -> None:
        """Every keyword in CREATIVE_KEYWORDS should trigger CREATIVE_WRITING (if not code)."""
        # Only test keywords that are not also in CODE_KEYWORDS
        non_code_creative = CREATIVE_KEYWORDS - CODE_KEYWORDS
        for kw in non_code_creative:
            result = agent._classify_task_type(f"I want {kw}")
            assert result == TaskType.CREATIVE_WRITING, (
                f"Keyword '{kw}' did not trigger CREATIVE_WRITING"
            )

    def test_each_summarization_keyword_triggers_summarization(self, agent) -> None:
        """Every keyword in SUMMARIZATION_KEYWORDS should trigger SUMMARIZATION."""
        for kw in SUMMARIZATION_KEYWORDS:
            result = agent._classify_task_type(f"Please {kw} this")
            assert result == TaskType.SUMMARIZATION, f"Keyword '{kw}' did not trigger SUMMARIZATION"
