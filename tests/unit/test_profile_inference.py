"""Unit tests for the profile inference engines."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from zetherion_ai.profile.inference import (
    ConversationFlowInference,
    IdentityContextInference,
    PreferenceInference,
    ProfileInferencePipeline,
    RelationshipSignalInference,
    Tier1Inference,
    Tier2Inference,
    Tier3Inference,
    Tier4Inference,
    UrgencyMoodInference,
)
from zetherion_ai.profile.models import ProfileCategory


class TestTier1Inference:
    """Tests for Tier 1 (regex/keyword) inference."""

    @pytest.fixture
    def tier1(self):
        """Create a Tier 1 inference engine."""
        return Tier1Inference()

    @pytest.mark.asyncio
    async def test_urgency_detection(self, tier1):
        """Test urgency keyword detection."""
        updates = await tier1.extract("This is urgent! Please help ASAP")

        urgency_updates = [u for u in updates if u.field_name == "current_urgency"]
        assert len(urgency_updates) == 1
        assert urgency_updates[0].value == "high"
        assert urgency_updates[0].confidence >= 0.8

    @pytest.mark.asyncio
    async def test_verbosity_decrease(self, tier1):
        """Test verbosity decrease detection."""
        updates = await tier1.extract("tldr please, too long")

        verbosity_updates = [u for u in updates if u.field_name == "verbosity"]
        assert len(verbosity_updates) == 1
        assert verbosity_updates[0].action == "decrease"
        assert verbosity_updates[0].profile == "employment"

    @pytest.mark.asyncio
    async def test_verbosity_increase(self, tier1):
        """Test verbosity increase detection."""
        updates = await tier1.extract("Can you explain more? I need more detail")

        verbosity_updates = [u for u in updates if u.field_name == "verbosity"]
        assert len(verbosity_updates) == 1
        assert verbosity_updates[0].action == "increase"

    @pytest.mark.asyncio
    async def test_trust_grants(self, tier1):
        """Test trust grant detection."""
        updates = await tier1.extract("Just do it, I trust you")

        trust_updates = [u for u in updates if u.field_name == "trust_level"]
        assert len(trust_updates) == 1
        assert trust_updates[0].action == "increase"
        assert trust_updates[0].profile == "employment"

    @pytest.mark.asyncio
    async def test_timezone_extraction(self, tier1):
        """Test timezone regex extraction."""
        updates = await tier1.extract("I'm in EST, my timezone is America/New_York")

        tz_updates = [u for u in updates if u.field_name == "timezone"]
        assert len(tz_updates) >= 1
        assert tz_updates[0].category == ProfileCategory.IDENTITY
        assert tz_updates[0].confidence >= 0.9

    @pytest.mark.asyncio
    async def test_name_extraction(self, tier1):
        """Test name regex extraction."""
        updates = await tier1.extract("My name is John")

        name_updates = [u for u in updates if u.field_name == "name"]
        assert len(name_updates) == 1
        assert name_updates[0].value == "John"
        assert name_updates[0].category == ProfileCategory.IDENTITY

    @pytest.mark.asyncio
    async def test_name_extraction_filters_false_positives(self, tier1):
        """Test that common words are filtered from name extraction."""
        updates = await tier1.extract("I'm just here to help")

        name_updates = [u for u in updates if u.field_name == "name"]
        # "just" and "here" should be filtered out
        assert all(u.value.lower() not in ["just", "here", "the", "a"] for u in name_updates)

    @pytest.mark.asyncio
    async def test_role_extraction(self, tier1):
        """Test role regex extraction."""
        updates = await tier1.extract("I'm a software developer at Google")

        role_updates = [u for u in updates if u.field_name == "role"]
        assert len(role_updates) == 1
        assert "developer" in role_updates[0].value.lower()
        assert role_updates[0].category == ProfileCategory.IDENTITY

    @pytest.mark.asyncio
    async def test_language_preference(self, tier1):
        """Test programming language preference extraction."""
        updates = await tier1.extract("I prefer Python for most projects")

        lang_updates = [u for u in updates if u.field_name == "preferred_language"]
        assert len(lang_updates) == 1
        assert lang_updates[0].value == "Python"
        assert lang_updates[0].category == ProfileCategory.PREFERENCES

    @pytest.mark.asyncio
    async def test_no_false_positives_on_neutral_message(self, tier1):
        """Test no updates for neutral messages."""
        updates = await tier1.extract("What's the weather like today?")

        # Should have minimal or no updates
        assert len(updates) <= 1  # Might detect question mark urgency


class TestTier2Inference:
    """Tests for Tier 2 (Ollama) inference."""

    @pytest.fixture
    def tier2(self):
        """Create a Tier 2 inference engine without broker."""
        return Tier2Inference(inference_broker=None)

    @pytest.mark.asyncio
    async def test_worth_analyzing_positive(self, tier2):
        """Test messages that should be analyzed."""
        assert tier2._worth_analyzing("I prefer dark mode") is True
        assert tier2._worth_analyzing("My project deadline is next week") is True
        assert tier2._worth_analyzing("We usually have meetings on Monday") is True

    @pytest.mark.asyncio
    async def test_worth_analyzing_negative(self, tier2):
        """Test messages that should not be analyzed."""
        assert tier2._worth_analyzing("Hello there") is False
        assert tier2._worth_analyzing("What's 2+2?") is False
        assert tier2._worth_analyzing("Thanks!") is False

    @pytest.mark.asyncio
    async def test_extract_without_broker(self, tier2):
        """Test extraction returns empty without broker."""
        updates = await tier2.extract("I prefer working in the morning")
        assert updates == []

    def test_parse_response_valid(self, tier2):
        """Test parsing valid JSON response."""
        response = '{"updates": [{"field": "work_hours", "value": "morning", "confidence": 0.7}]}'
        updates = tier2._parse_response(response)

        assert len(updates) == 1
        assert updates[0].field_name == "work_hours"
        assert updates[0].value == "morning"
        assert updates[0].source_tier == 2

    def test_parse_response_empty(self, tier2):
        """Test parsing empty updates response."""
        response = '{"updates": []}'
        updates = tier2._parse_response(response)
        assert updates == []

    def test_parse_response_invalid_json(self, tier2):
        """Test parsing invalid JSON response."""
        response = "This is not JSON"
        updates = tier2._parse_response(response)
        assert updates == []

    def test_parse_response_missing_field(self, tier2):
        """Test parsing response with missing required fields."""
        response = '{"updates": [{"value": "test"}]}'  # Missing "field"
        updates = tier2._parse_response(response)
        assert updates == []


class TestUrgencyMoodInference:
    """Tests for urgency and mood inference."""

    @pytest.fixture
    def inference(self):
        """Create urgency mood inference engine."""
        return UrgencyMoodInference()

    def test_short_question_urgency(self, inference):
        """Test short question detection."""
        updates = inference.analyze("When?")

        urgency_updates = [u for u in updates if u.field_name == "current_urgency"]
        assert len(urgency_updates) == 1
        assert urgency_updates[0].value == "high"

    def test_multiple_exclamation_emphasis(self, inference):
        """Test emphasis detection from exclamation marks."""
        updates = inference.analyze("This is amazing!!! I love it!!!")

        emphasis_updates = [u for u in updates if u.field_name == "current_emphasis"]
        assert len(emphasis_updates) == 1
        assert emphasis_updates[0].value == "strong"

    def test_ellipsis_uncertainty(self, inference):
        """Test uncertainty detection from ellipsis."""
        updates = inference.analyze("I'm not sure... maybe we should...")

        uncertainty_updates = [u for u in updates if u.field_name == "current_uncertainty"]
        assert len(uncertainty_updates) == 1
        assert uncertainty_updates[0].value is True

    def test_quick_response_engagement(self, inference):
        """Test engagement detection from quick response."""
        updates = inference.analyze("Yes!", response_time_ms=2000)

        engagement_updates = [u for u in updates if u.field_name == "engagement_level"]
        assert len(engagement_updates) == 1
        assert engagement_updates[0].value == "high"

    def test_slow_response_no_engagement_signal(self, inference):
        """Test no engagement signal for slow response."""
        updates = inference.analyze("Yes!", response_time_ms=10000)

        engagement_updates = [u for u in updates if u.field_name == "engagement_level"]
        assert len(engagement_updates) == 0

    def test_all_caps_frustration(self, inference):
        """Test frustration detection from all caps."""
        updates = inference.analyze("WHY ISN'T THIS WORKING?!")

        frustration_updates = [u for u in updates if u.field_name == "possible_frustration"]
        assert len(frustration_updates) == 1
        assert frustration_updates[0].value is True


class TestPreferenceInference:
    """Tests for preference inference from patterns."""

    @pytest.fixture
    def inference(self):
        """Create preference inference engine."""
        return PreferenceInference()

    def test_record_interaction(self, inference):
        """Test recording interactions."""
        now = datetime.now()
        inference.record_interaction(now, topics=["python", "testing"])

        assert len(inference._interaction_times) == 1
        assert len(inference._topics) == 2

    def test_find_peak_hours(self, inference):
        """Test peak hours detection."""
        # Record many interactions at hour 14
        for _ in range(20):
            inference._interaction_times.append(datetime.now().replace(hour=14, minute=0))
        # Record few at other hours
        for _ in range(3):
            inference._interaction_times.append(datetime.now().replace(hour=10, minute=0))

        peak = inference._find_peak_hours()
        assert peak is not None
        assert 14 in peak

    def test_find_dominant_topic(self, inference):
        """Test dominant topic detection."""
        inference._topics = ["python"] * 30 + ["javascript"] * 5 + ["go"] * 2

        dominant = inference._find_dominant_topic()
        assert dominant == "python"

    def test_no_dominant_topic_when_even(self, inference):
        """Test no dominant topic when evenly distributed."""
        # Need 6+ topics so each is < 20% to have no dominant (20% threshold)
        inference._topics = ["python"] + ["javascript"] + ["go"] + ["rust"] + ["cpp"] + ["java"]

        dominant = inference._find_dominant_topic()
        assert dominant is None


class TestIdentityContextInference:
    """Tests for identity context inference."""

    @pytest.fixture
    def inference(self):
        """Create identity context inference engine."""
        return IdentityContextInference()

    def test_infer_role_devops(self, inference):
        """Test DevOps role inference."""
        messages = [
            "I need to deploy this to kubernetes",
            "Let me check the deployment status",
            "Running terraform apply",
        ]

        update = inference.infer_role(messages)
        assert update is not None
        assert "devops" in update.value or "sre" in update.value

    def test_infer_role_frontend(self, inference):
        """Test frontend role inference."""
        messages = [
            "I'm working on the React component",
            "Need to fix this design issue",
            "The UI needs improvement",
        ]

        update = inference.infer_role(messages)
        assert update is not None
        assert "frontend" in update.value or "designer" in update.value

    def test_infer_role_no_signals(self, inference):
        """Test no role inferred without signals."""
        messages = [
            "Hello there",
            "How are you?",
            "Thanks for the help",
        ]

        update = inference.infer_role(messages)
        assert update is None


class TestRelationshipSignalInference:
    """Tests for relationship signal inference."""

    @pytest.fixture
    def inference(self):
        """Create relationship signal inference engine."""
        return RelationshipSignalInference()

    def test_high_correction_rate(self, inference):
        """Test accuracy concern from high correction rate."""
        for _ in range(20):
            inference.record_interaction(was_correction=True)
        for _ in range(30):
            inference.record_interaction(was_correction=False)

        updates = inference.analyze()

        accuracy_updates = [u for u in updates if u.field_name == "accuracy_concern"]
        assert len(accuracy_updates) == 1
        assert accuracy_updates[0].value is True

    def test_high_positive_feedback(self, inference):
        """Test trust building from positive feedback."""
        for _ in range(40):
            inference.record_interaction(was_positive=True)
        for _ in range(60):
            inference.record_interaction(was_positive=False)

        updates = inference.analyze()

        trust_updates = [u for u in updates if u.field_name == "trust_level"]
        assert len(trust_updates) == 1
        assert trust_updates[0].action == "increase"

    def test_many_delegations(self, inference):
        """Test proactivity increase from delegations."""
        for _ in range(15):
            inference.record_interaction(was_delegation=True)
        for _ in range(50):
            inference.record_interaction()

        updates = inference.analyze()

        proactivity_updates = [u for u in updates if u.field_name == "proactivity"]
        assert len(proactivity_updates) == 1
        assert proactivity_updates[0].action == "increase"


class TestConversationFlowInference:
    """Tests for conversation flow inference."""

    @pytest.fixture
    def inference(self):
        """Create conversation flow inference engine."""
        return ConversationFlowInference()

    def test_clarification_needed(self, inference):
        """Test verbosity increase when clarification needed."""
        updates = inference.analyze_turn(
            user_msg="How do I do this?",
            bot_response="You can use the function...",
            next_user_msg="What do you mean? I don't understand",
        )

        verbosity_updates = [u for u in updates if u.field_name == "verbosity"]
        assert len(verbosity_updates) == 1
        assert verbosity_updates[0].action == "increase"

    def test_continues_thread(self, inference):
        """Test helpfulness signal when user continues."""
        updates = inference.analyze_turn(
            user_msg="How do I do this?",
            bot_response="You can use the X function to achieve this.",
            next_user_msg="OK and then?",
        )

        helpful_updates = [u for u in updates if u.field_name == "helpfulness_signal"]
        assert len(helpful_updates) == 1

    def test_no_next_message(self, inference):
        """Test no updates when no next message."""
        updates = inference.analyze_turn(
            user_msg="How do I do this?",
            bot_response="Here's how...",
            next_user_msg=None,
        )

        assert updates == []


class TestProfileInferencePipeline:
    """Tests for the full inference pipeline."""

    @pytest.fixture
    def pipeline(self):
        """Create inference pipeline."""
        return ProfileInferencePipeline(tier1_only=True)

    @pytest.mark.asyncio
    async def test_extract_all_combines_engines(self, pipeline):
        """Test that pipeline combines all engine outputs."""
        updates = await pipeline.extract_all(
            message="This is urgent! I'm a software developer and need help ASAP",
            response_time_ms=1000,
        )

        # Should have updates from both Tier1 and UrgencyMood engines
        assert len(updates) >= 2

        fields = [u.field_name for u in updates]
        assert "current_urgency" in fields

    @pytest.mark.asyncio
    async def test_tier1_only_mode(self, pipeline):
        """Test tier1_only mode skips higher tiers."""
        # This message would trigger Tier 2 normally
        updates = await pipeline.extract_all(
            message="I prefer working on backend projects with Python",
        )

        # Should only have Tier 1 results
        for update in updates:
            assert update.source_tier == 1


class TestTier1InferenceNameFalsePositive:
    """Tests for Tier 1 name extraction false positive branch."""

    @pytest.fixture
    def tier1(self):
        """Create a Tier 1 inference engine."""
        return Tier1Inference()

    @pytest.mark.asyncio
    async def test_name_false_positive_the(self, tier1):
        """Test that 'The' is filtered as a false positive name."""
        # "I'm The Boss" - 'The' matches the name pattern but should be filtered
        updates = await tier1.extract("I'm The one who asked")
        name_updates = [u for u in updates if u.field_name == "name"]
        # "The" should be filtered out as a false positive
        for u in name_updates:
            assert u.value.lower() != "the"

    @pytest.mark.asyncio
    async def test_name_false_positive_going(self, tier1):
        """Test that 'Going' is filtered as a false positive name."""
        updates = await tier1.extract("I'm Going to the store")
        name_updates = [u for u in updates if u.field_name == "name"]
        for u in name_updates:
            assert u.value.lower() != "going"

    @pytest.mark.asyncio
    async def test_role_extraction_engineer(self, tier1):
        """Test role extraction for engineer role."""
        updates = await tier1.extract("I work as a backend engineer")
        role_updates = [u for u in updates if u.field_name == "role"]
        assert len(role_updates) == 1
        assert "engineer" in role_updates[0].value.lower()


class TestTier2InferenceWithBroker:
    """Tests for Tier 2 inference with broker present."""

    @pytest.fixture
    def mock_broker(self):
        """Create a mock inference broker."""
        broker = MagicMock()
        result = MagicMock()
        result.content = (
            '{"updates": [{"field": "work_pattern",' ' "value": "morning", "confidence": 0.7}]}'
        )
        broker.infer = AsyncMock(return_value=result)
        return broker

    @pytest.fixture
    def tier2_with_broker(self, mock_broker):
        """Create a Tier 2 inference engine with broker."""
        return Tier2Inference(inference_broker=mock_broker)

    @pytest.mark.asyncio
    async def test_extract_with_broker_success(self, tier2_with_broker):
        """Test extraction with broker returns parsed updates."""
        updates = await tier2_with_broker.extract("I prefer working in the morning")
        assert len(updates) == 1
        assert updates[0].field_name == "work_pattern"
        assert updates[0].value == "morning"
        assert updates[0].source_tier == 2

    @pytest.mark.asyncio
    async def test_extract_with_broker_failure(self):
        """Test extraction when broker raises exception."""
        broker = MagicMock()
        broker.infer = AsyncMock(side_effect=RuntimeError("Connection failed"))
        tier2 = Tier2Inference(inference_broker=broker)
        updates = await tier2.extract("I prefer working in the morning")
        assert updates == []

    @pytest.mark.asyncio
    async def test_extract_not_worth_analyzing(self):
        """Test extraction returns empty for messages without profile signals."""
        broker = MagicMock()
        tier2 = Tier2Inference(inference_broker=broker)
        updates = await tier2.extract("Hello there")
        assert updates == []
        # Broker should not have been called
        broker.infer.assert_not_called()

    def test_parse_response_json_decode_error(self):
        """Test parsing response with malformed JSON inside braces."""
        tier2 = Tier2Inference()
        response = "{malformed json content}"
        updates = tier2._parse_response(response)
        assert updates == []

    def test_parse_response_confidence_capped_at_0_8(self):
        """Test that parsed confidence is capped at 0.8."""
        tier2 = Tier2Inference()
        response = '{"updates": [{"field": "role", "value": "dev", "confidence": 0.95}]}'
        updates = tier2._parse_response(response)
        assert len(updates) == 1
        assert updates[0].confidence == 0.8


class TestTier3Inference:
    """Tests for Tier 3 (embedding) inference."""

    @pytest.mark.asyncio
    async def test_extract_without_memory(self):
        """Test extraction returns empty when memory is None."""
        tier3 = Tier3Inference(memory=None)
        updates = await tier3.extract("I prefer dark mode")
        assert updates == []

    @pytest.mark.asyncio
    async def test_extract_with_memory(self):
        """Test extraction returns empty with memory (not yet implemented)."""
        mock_memory = MagicMock()
        tier3 = Tier3Inference(memory=mock_memory)
        updates = await tier3.extract("I prefer dark mode")
        assert updates == []


class TestTier4Inference:
    """Tests for Tier 4 (cloud LLM) inference."""

    @pytest.mark.asyncio
    async def test_extract_returns_empty(self):
        """Test extraction always returns empty (rarely used tier)."""
        tier4 = Tier4Inference(inference_broker=None)
        updates = await tier4.extract("Complex multi-turn analysis needed")
        assert updates == []

    @pytest.mark.asyncio
    async def test_extract_with_broker_returns_empty(self):
        """Test extraction returns empty even with broker configured."""
        mock_broker = MagicMock()
        tier4 = Tier4Inference(inference_broker=mock_broker)
        updates = await tier4.extract("I have subtle relationship dynamics")
        assert updates == []


class TestPreferenceInferenceExtended:
    """Extended tests for preference inference patterns."""

    @pytest.fixture
    def inference(self):
        """Create preference inference engine."""
        return PreferenceInference()

    def test_record_interaction_without_topics(self, inference):
        """Test recording interaction without topics."""
        now = datetime.now()
        inference.record_interaction(now, topics=None)
        assert len(inference._interaction_times) == 1
        assert len(inference._topics) == 0

    def test_record_interaction_truncates_times_at_1000(self, inference):
        """Test that interaction times are truncated at 1000."""
        for _ in range(1005):
            inference.record_interaction(datetime.now())
        assert len(inference._interaction_times) == 1000

    def test_record_interaction_truncates_topics_at_1000(self, inference):
        """Test that topics are truncated at 1000."""
        for _ in range(1005):
            inference.record_interaction(datetime.now(), topics=["topic"])
        assert len(inference._topics) == 1000

    @pytest.mark.asyncio
    async def test_analyze_patterns_with_peak_hours(self, inference):
        """Test pattern analysis returns peak hours update."""
        # Record 15 interactions at hour 10
        for _ in range(15):
            inference._interaction_times.append(datetime.now().replace(hour=10, minute=0))
        # Record 2 interactions at hour 22
        for _ in range(2):
            inference._interaction_times.append(datetime.now().replace(hour=22, minute=0))

        updates = await inference.analyze_patterns("user1")
        hour_updates = [u for u in updates if u.field_name == "active_hours"]
        assert len(hour_updates) == 1
        assert 10 in hour_updates[0].value
        assert hour_updates[0].category == ProfileCategory.SCHEDULE

    @pytest.mark.asyncio
    async def test_analyze_patterns_with_dominant_topic(self, inference):
        """Test pattern analysis returns dominant topic update."""
        # Need 10+ interactions for peak hours check
        for _ in range(10):
            inference._interaction_times.append(datetime.now())
        # Need 20+ topics for topic analysis
        inference._topics = ["python"] * 20 + ["go"] * 3

        updates = await inference.analyze_patterns("user1")
        focus_updates = [u for u in updates if u.field_name == "current_focus"]
        assert len(focus_updates) == 1
        assert focus_updates[0].value == "python"
        assert focus_updates[0].category == ProfileCategory.PROJECTS

    @pytest.mark.asyncio
    async def test_analyze_patterns_too_few_interactions(self, inference):
        """Test pattern analysis with fewer than 10 interactions."""
        for _ in range(5):
            inference._interaction_times.append(datetime.now())
        updates = await inference.analyze_patterns("user1")
        hour_updates = [u for u in updates if u.field_name == "active_hours"]
        assert len(hour_updates) == 0

    @pytest.mark.asyncio
    async def test_analyze_patterns_too_few_topics(self, inference):
        """Test pattern analysis with fewer than 20 topics."""
        for _ in range(10):
            inference._interaction_times.append(datetime.now())
        inference._topics = ["python"] * 10
        updates = await inference.analyze_patterns("user1")
        focus_updates = [u for u in updates if u.field_name == "current_focus"]
        assert len(focus_updates) == 0

    def test_find_peak_hours_empty_interactions(self, inference):
        """Test _find_peak_hours with no interaction times."""
        result = inference._find_peak_hours()
        assert result is None

    def test_find_peak_hours_no_peaks_above_threshold(self, inference):
        """Test _find_peak_hours when no hour exceeds 1.5x average."""
        # Distribute evenly so no peak exceeds 1.5x average
        for hour in range(24):
            for _ in range(10):
                inference._interaction_times.append(datetime.now().replace(hour=hour, minute=0))
        result = inference._find_peak_hours()
        assert result is None

    def test_find_dominant_topic_empty(self, inference):
        """Test _find_dominant_topic with no topics."""
        result = inference._find_dominant_topic()
        assert result is None


class TestIdentityContextInferenceExtended:
    """Extended tests for identity context inference."""

    @pytest.fixture
    def inference(self):
        """Create identity context inference engine."""
        return IdentityContextInference()

    def test_infer_role_ml_engineer(self, inference):
        """Test ML engineer role inference."""
        messages = [
            "I need to train the model",
            "Let me update the model parameters",
            "Running model validation",
        ]
        update = inference.infer_role(messages)
        assert update is not None
        assert update.value in ["ml_engineer", "data_scientist"]

    def test_infer_role_backend(self, inference):
        """Test backend role inference from schema keywords."""
        messages = [
            "I need to update the schema",
            "Working on the API endpoints",
            "Database migration is ready",
        ]
        update = inference.infer_role(messages)
        assert update is not None
        assert update.value in ["backend", "database", "dba", "integration"]

    def test_infer_role_confidence_capped(self, inference):
        """Test that inferred role confidence is capped at 0.8."""
        messages = ["deploy"] * 20 + ["kubernetes"] * 20
        update = inference.infer_role(messages)
        assert update is not None
        assert update.confidence <= 0.8

    def test_infer_role_requires_confirmation_at_low_confidence(self, inference):
        """Test that low confidence roles require confirmation."""
        messages = ["test something"]
        update = inference.infer_role(messages)
        assert update is not None
        # 1 match * 0.1 = 0.1 confidence, which is < 0.6 so requires_confirmation
        assert update.requires_confirmation is True


class TestRelationshipSignalInferenceExtended:
    """Extended tests for relationship signal inference."""

    @pytest.fixture
    def inference(self):
        """Create relationship signal inference engine."""
        return RelationshipSignalInference()

    def test_analyze_too_few_interactions(self, inference):
        """Test analyze returns empty with fewer than 10 interactions."""
        for _ in range(5):
            inference.record_interaction()
        updates = inference.analyze()
        assert updates == []

    def test_record_all_interaction_types(self, inference):
        """Test recording all interaction types at once."""
        inference.record_interaction(was_correction=True, was_positive=True, was_delegation=True)
        assert inference._total_interactions == 1
        assert inference._corrections == 1
        assert inference._positive_feedback == 1
        assert inference._delegations == 1


class TestConversationFlowInferenceExtended:
    """Extended tests for conversation flow inference."""

    @pytest.fixture
    def inference(self):
        """Create conversation flow inference engine."""
        return ConversationFlowInference()

    def test_long_follow_up_no_helpfulness(self, inference):
        """Test that long follow-up does not generate helpfulness signal."""
        # next_user_msg >= 50 chars should not trigger helpfulness
        updates = inference.analyze_turn(
            user_msg="Help me",
            bot_response="Here's the solution...",
            next_user_msg=(
                "This is a very long follow up message that"
                " exceeds fifty characters in total length definitely"
            ),
        )
        helpful_updates = [u for u in updates if u.field_name == "helpfulness_signal"]
        assert len(helpful_updates) == 0

    def test_thanks_no_helpfulness(self, inference):
        """Test that 'thanks' in short follow-up does not generate helpfulness."""
        updates = inference.analyze_turn(
            user_msg="Help me",
            bot_response="Here's the solution...",
            next_user_msg="thanks a lot!",
        )
        helpful_updates = [u for u in updates if u.field_name == "helpfulness_signal"]
        assert len(helpful_updates) == 0

    def test_no_clarification_no_verbosity(self, inference):
        """Test no verbosity increase when no clarification signals."""
        updates = inference.analyze_turn(
            user_msg="Do this",
            bot_response="Done.",
            next_user_msg="Great work",
        )
        verbosity_updates = [u for u in updates if u.field_name == "verbosity"]
        assert len(verbosity_updates) == 0

    def test_clarification_with_question_mark(self, inference):
        """Test clarification detected from question mark signal."""
        updates = inference.analyze_turn(
            user_msg="Do this",
            bot_response="Done.",
            next_user_msg="?",
        )
        verbosity_updates = [u for u in updates if u.field_name == "verbosity"]
        assert len(verbosity_updates) == 1


class TestProfileInferencePipelineExtended:
    """Extended tests for the full inference pipeline."""

    @pytest.mark.asyncio
    async def test_pipeline_with_higher_tiers_tier2_finds_nothing(self):
        """Test pipeline runs tier3 when tier2 finds nothing."""
        pipeline = ProfileInferencePipeline(tier1_only=False)

        # Mock tier2 and tier3 to control behavior
        pipeline.tier2.extract = AsyncMock(return_value=[])
        pipeline.tier3.extract = AsyncMock(return_value=[])

        await pipeline.extract_all(
            message="I prefer working on backend projects",
        )

        # Tier2 should have been called
        pipeline.tier2.extract.assert_called_once()
        # Tier3 should have been called since tier2 found nothing
        pipeline.tier3.extract.assert_called_once()

    @pytest.mark.asyncio
    async def test_pipeline_with_higher_tiers_tier2_finds_updates(self):
        """Test pipeline uses tier2 results and skips tier3."""
        from zetherion_ai.profile.models import ProfileUpdate

        pipeline = ProfileInferencePipeline(tier1_only=False)

        tier2_update = ProfileUpdate(
            profile="user",
            field_name="work_pattern",
            value="morning",
            confidence=0.7,
            source_tier=2,
        )
        pipeline.tier2.extract = AsyncMock(return_value=[tier2_update])
        pipeline.tier3.extract = AsyncMock(return_value=[])

        updates = await pipeline.extract_all(
            message="I prefer working in the morning usually",
        )

        # Tier2 should have been called
        pipeline.tier2.extract.assert_called_once()
        # Tier3 should NOT have been called since tier2 found updates
        pipeline.tier3.extract.assert_not_called()
        # Should include tier2 update
        assert any(u.source_tier == 2 for u in updates)

    @pytest.mark.asyncio
    async def test_pipeline_includes_urgency_mood(self):
        """Test pipeline includes urgency mood analysis results."""
        pipeline = ProfileInferencePipeline(tier1_only=True)
        updates = await pipeline.extract_all(
            message="When?",
            response_time_ms=2000,
        )
        fields = [u.field_name for u in updates]
        assert "current_urgency" in fields
        assert "engagement_level" in fields
