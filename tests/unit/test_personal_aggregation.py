"""Unit tests for personality profile aggregation (pure logic)."""

from __future__ import annotations

from datetime import datetime

from zetherion_ai.personal.aggregation import (
    _confidence,
    _ema,
    _merge_string_list,
    _mode_of,
    _running_rate,
    aggregate_signal_into_profile,
)
from zetherion_ai.personal.models import (
    PersonalityProfile,
)
from zetherion_ai.routing.personality import (
    AuthorRole,
    CommunicationProfile,
    CommunicationTrait,
    EmotionalTone,
    Formality,
    PersonalitySignal,
    PowerDynamic,
    RelationshipDynamics,
    SentenceLength,
    VocabularyLevel,
    WritingStyle,
)


def _empty_profile(
    user_id: int = 1,
    subject_email: str = "contact@example.com",
    subject_role: str = "contact",
) -> PersonalityProfile:
    """Create a fresh empty profile."""
    return PersonalityProfile(
        user_id=user_id,
        subject_email=subject_email,
        subject_role=subject_role,
    )


def _signal(
    *,
    author_role: AuthorRole = AuthorRole.CONTACT,
    author_email: str = "contact@example.com",
    formality: Formality = Formality.FORMAL,
    sentence_length: SentenceLength = SentenceLength.MEDIUM,
    vocabulary_level: VocabularyLevel = VocabularyLevel.STANDARD,
    uses_greeting: bool = True,
    greeting_style: str = "Hi",
    uses_signoff: bool = True,
    signoff_style: str = "Best,",
    uses_emoji: bool = False,
    uses_bullet_points: bool = False,
    primary_trait: CommunicationTrait = CommunicationTrait.DIRECT,
    secondary_trait: CommunicationTrait | None = None,
    emotional_tone: EmotionalTone = EmotionalTone.NEUTRAL,
    assertiveness: float = 0.5,
    responsiveness_signal: str = "",
    familiarity: float = 0.5,
    power_dynamic: PowerDynamic = PowerDynamic.PEER,
    trust_level: float = 0.5,
    rapport_indicators: list[str] | None = None,
    preferences_revealed: list[str] | None = None,
    schedule_signals: list[str] | None = None,
    commitments_made: list[str] | None = None,
    expectations_set: list[str] | None = None,
) -> PersonalitySignal:
    """Build a PersonalitySignal with controllable fields."""
    return PersonalitySignal(
        author_role=author_role,
        author_email=author_email,
        writing_style=WritingStyle(
            formality=formality,
            avg_sentence_length=sentence_length,
            uses_greeting=uses_greeting,
            greeting_style=greeting_style,
            uses_signoff=uses_signoff,
            signoff_style=signoff_style,
            uses_emoji=uses_emoji,
            uses_bullet_points=uses_bullet_points,
            vocabulary_level=vocabulary_level,
        ),
        communication=CommunicationProfile(
            primary_trait=primary_trait,
            secondary_trait=secondary_trait,
            emotional_tone=emotional_tone,
            assertiveness=assertiveness,
            responsiveness_signal=responsiveness_signal,
        ),
        relationship=RelationshipDynamics(
            familiarity=familiarity,
            power_dynamic=power_dynamic,
            trust_level=trust_level,
            rapport_indicators=rapport_indicators or [],
        ),
        preferences_revealed=preferences_revealed or [],
        schedule_signals=schedule_signals or [],
        commitments_made=commitments_made or [],
        expectations_set=expectations_set or [],
    )


class TestFirstSignalInitialization:
    """First observation should bootstrap distributions from empty."""

    def test_first_signal_initializes_distributions(self) -> None:
        profile = _empty_profile()
        sig = _signal(formality=Formality.FORMAL)

        result = aggregate_signal_into_profile(profile, sig)

        assert result.observation_count == 1
        assert result.writing_style.formality_distribution == {"formal": 1}
        assert result.writing_style.formality_mode == "formal"
        assert result.communication.primary_trait_distribution == {"direct": 1}
        assert result.relationship.power_dynamic_distribution == {"peer": 1}


class TestDistributionMode:
    """Mode should track the most frequent value."""

    def test_enum_distribution_tracks_mode(self) -> None:
        profile = _empty_profile()

        # 3 formal signals
        for _ in range(3):
            profile = aggregate_signal_into_profile(profile, _signal(formality=Formality.FORMAL))

        # 1 casual signal
        profile = aggregate_signal_into_profile(profile, _signal(formality=Formality.CASUAL))

        assert profile.observation_count == 4
        assert profile.writing_style.formality_distribution == {"formal": 3, "casual": 1}
        assert profile.writing_style.formality_mode == "formal"

    def test_mode_changes_when_new_value_dominates(self) -> None:
        profile = _empty_profile()

        # 2 formal
        for _ in range(2):
            profile = aggregate_signal_into_profile(profile, _signal(formality=Formality.FORMAL))

        # 3 casual
        for _ in range(3):
            profile = aggregate_signal_into_profile(profile, _signal(formality=Formality.CASUAL))

        assert profile.writing_style.formality_mode == "casual"


class TestEMAConvergence:
    """EMA fields should converge toward the input value."""

    def test_ema_converges(self) -> None:
        profile = _empty_profile()

        # 20 signals with assertiveness=0.8
        for _ in range(20):
            profile = aggregate_signal_into_profile(profile, _signal(assertiveness=0.8))

        # After 20 observations, should be close to 0.8
        assert abs(profile.communication.assertiveness_ema - 0.8) < 0.05

    def test_ema_helper_function(self) -> None:
        val = 0.5
        for n in range(20):
            val = _ema(val, 0.8, n)
        assert abs(val - 0.8) < 0.05

    def test_familiarity_ema_converges(self) -> None:
        profile = _empty_profile()
        for _ in range(15):
            profile = aggregate_signal_into_profile(profile, _signal(familiarity=0.9))
        assert abs(profile.relationship.familiarity_ema - 0.9) < 0.1

    def test_trust_level_ema_converges(self) -> None:
        profile = _empty_profile()
        for _ in range(15):
            profile = aggregate_signal_into_profile(profile, _signal(trust_level=0.2))
        assert abs(profile.relationship.trust_level_ema - 0.2) < 0.15


class TestBooleanRateTracking:
    """Running rates should accurately track boolean proportions."""

    def test_boolean_rate_tracks_correctly(self) -> None:
        profile = _empty_profile()

        # 7 with greeting, 3 without
        for _ in range(7):
            profile = aggregate_signal_into_profile(profile, _signal(uses_greeting=True))
        for _ in range(3):
            profile = aggregate_signal_into_profile(profile, _signal(uses_greeting=False))

        assert abs(profile.writing_style.greeting_rate - 0.7) < 0.01

    def test_emoji_rate_tracks_correctly(self) -> None:
        profile = _empty_profile()

        for _ in range(3):
            profile = aggregate_signal_into_profile(profile, _signal(uses_emoji=True))
        for _ in range(7):
            profile = aggregate_signal_into_profile(profile, _signal(uses_emoji=False))

        assert abs(profile.writing_style.emoji_rate - 0.3) < 0.01

    def test_running_rate_helper(self) -> None:
        rate = _running_rate(0.7, True, 10)
        expected = (0.7 * 10 + 1.0) / 11
        assert abs(rate - expected) < 1e-9


class TestStringListDedup:
    """String lists should be deduped and capped."""

    def test_string_lists_dedup_and_cap(self) -> None:
        profile = _empty_profile()

        # Same greeting repeated many times
        for _ in range(5):
            profile = aggregate_signal_into_profile(profile, _signal(greeting_style="Hi there"))

        assert profile.writing_style.greeting_styles == ["Hi there"]

    def test_different_styles_accumulate(self) -> None:
        profile = _empty_profile()
        greetings = ["Hi", "Hello", "Hey", "Dear Sir"]

        for g in greetings:
            profile = aggregate_signal_into_profile(profile, _signal(greeting_style=g))

        assert len(profile.writing_style.greeting_styles) == 4

    def test_merge_string_list_caps_at_max(self) -> None:
        existing = [f"item-{i}" for i in range(18)]
        new_items = ["new-1", "new-2", "new-3", "new-4"]
        result = _merge_string_list(existing, new_items)
        assert len(result) == 20  # MAX_LIST_ITEMS

    def test_merge_string_list_case_insensitive_dedup(self) -> None:
        result = _merge_string_list(["Hello"], ["hello", "HELLO"])
        assert len(result) == 1

    def test_rapport_indicators_accumulate(self) -> None:
        profile = _empty_profile()

        profile = aggregate_signal_into_profile(
            profile, _signal(rapport_indicators=["uses first name"])
        )
        profile = aggregate_signal_into_profile(
            profile, _signal(rapport_indicators=["shared context", "uses first name"])
        )

        assert len(profile.relationship.rapport_indicators) == 2
        assert "uses first name" in profile.relationship.rapport_indicators
        assert "shared context" in profile.relationship.rapport_indicators


class TestConfidenceGrowth:
    """Confidence should grow with observations and asymptote at 0.95."""

    def test_confidence_growth_curve(self) -> None:
        # 1 obs → ~0.23, 5 → ~0.60, 10 → ~0.75, 20 → ~0.86
        assert abs(_confidence(1) - 0.2307) < 0.01
        assert abs(_confidence(5) - 0.6) < 0.01
        assert abs(_confidence(10) - 0.75) < 0.01
        assert abs(_confidence(20) - 0.857) < 0.01

    def test_confidence_never_exceeds_095(self) -> None:
        assert _confidence(1000) == 0.95
        assert _confidence(10000) == 0.95

    def test_confidence_at_zero(self) -> None:
        assert _confidence(0) == 0.0

    def test_profile_confidence_grows(self) -> None:
        profile = _empty_profile()
        prev_confidence = 0.0
        for _ in range(10):
            profile = aggregate_signal_into_profile(profile, _signal())
            assert profile.confidence > prev_confidence
            prev_confidence = profile.confidence


class TestPurity:
    """aggregate_signal_into_profile should NOT mutate its inputs."""

    def test_aggregate_is_pure(self) -> None:
        profile = _empty_profile()
        original_count = profile.observation_count

        _ = aggregate_signal_into_profile(profile, _signal())

        assert profile.observation_count == original_count
        assert profile.writing_style.formality_distribution == {}


class TestRolePreservation:
    """Aggregation should preserve subject_role and user_id."""

    def test_owner_role_preserved(self) -> None:
        profile = _empty_profile(subject_role="owner", subject_email="me@example.com")
        result = aggregate_signal_into_profile(
            profile,
            _signal(author_role=AuthorRole.OWNER, author_email="me@example.com"),
        )
        assert result.subject_role == "owner"
        assert result.subject_email == "me@example.com"
        assert result.user_id == 1

    def test_contact_role_preserved(self) -> None:
        profile = _empty_profile(subject_role="contact")
        result = aggregate_signal_into_profile(profile, _signal())
        assert result.subject_role == "contact"

    def test_id_preserved(self) -> None:
        profile = _empty_profile()
        profile.id = 42
        result = aggregate_signal_into_profile(profile, _signal())
        assert result.id == 42


class TestSecondaryTraits:
    """Secondary traits should accumulate when present."""

    def test_secondary_trait_accumulated(self) -> None:
        profile = _empty_profile()

        profile = aggregate_signal_into_profile(
            profile,
            _signal(secondary_trait=CommunicationTrait.ANALYTICAL),
        )
        profile = aggregate_signal_into_profile(
            profile,
            _signal(secondary_trait=CommunicationTrait.ANALYTICAL),
        )
        profile = aggregate_signal_into_profile(
            profile,
            _signal(secondary_trait=CommunicationTrait.DIPLOMATIC),
        )

        assert profile.communication.secondary_trait_distribution == {
            "analytical": 2,
            "diplomatic": 1,
        }

    def test_none_secondary_trait_not_accumulated(self) -> None:
        profile = _empty_profile()
        profile = aggregate_signal_into_profile(profile, _signal(secondary_trait=None))
        assert profile.communication.secondary_trait_distribution == {}


class TestCommitmentsAndExpectations:
    """Commitments and expectations should accumulate."""

    def test_commitments_accumulate(self) -> None:
        profile = _empty_profile()
        profile = aggregate_signal_into_profile(
            profile, _signal(commitments_made=["deliver by Friday"])
        )
        profile = aggregate_signal_into_profile(
            profile, _signal(commitments_made=["review PR", "deliver by Friday"])
        )
        assert len(profile.commitments) == 2

    def test_expectations_accumulate(self) -> None:
        profile = _empty_profile()
        profile = aggregate_signal_into_profile(
            profile, _signal(expectations_set=["send report by Monday"])
        )
        assert "send report by Monday" in profile.expectations


class TestPreferencesAndSchedule:
    """Preferences and schedule signals should accumulate."""

    def test_preferences_accumulate(self) -> None:
        profile = _empty_profile()
        profile = aggregate_signal_into_profile(
            profile, _signal(preferences_revealed=["prefers morning meetings"])
        )
        profile = aggregate_signal_into_profile(
            profile, _signal(preferences_revealed=["likes dark roast coffee"])
        )
        assert len(profile.preferences) == 2

    def test_schedule_signals_accumulate(self) -> None:
        profile = _empty_profile()
        profile = aggregate_signal_into_profile(
            profile, _signal(schedule_signals=["works late evenings"])
        )
        assert "works late evenings" in profile.schedule_signals


class TestHelperFunctions:
    """Test helper functions directly."""

    def test_mode_of_empty_returns_fallback(self) -> None:
        assert _mode_of({}, "default") == "default"

    def test_mode_of_single_entry(self) -> None:
        assert _mode_of({"a": 1}, "default") == "a"

    def test_mode_of_tie_returns_max(self) -> None:
        result = _mode_of({"a": 2, "b": 2}, "default")
        assert result in ("a", "b")

    def test_running_rate_first_observation(self) -> None:
        rate = _running_rate(0.0, True, 0)
        assert rate == 1.0

    def test_running_rate_second_observation(self) -> None:
        rate = _running_rate(1.0, False, 1)
        assert abs(rate - 0.5) < 1e-9


class TestTimestamps:
    """Profile timestamps should be tracked correctly."""

    def test_first_observed_stays_from_initial(self) -> None:
        profile = _empty_profile()
        original_first = profile.first_observed

        result = aggregate_signal_into_profile(profile, _signal())

        assert result.first_observed == original_first

    def test_last_observed_updates(self) -> None:
        profile = _empty_profile()
        profile.last_observed = datetime(2020, 1, 1)

        result = aggregate_signal_into_profile(profile, _signal())

        # last_observed should be updated to now
        assert result.last_observed > datetime(2020, 1, 1)


class TestResponsivenessSignals:
    """Responsiveness signals should accumulate as string list."""

    def test_responsiveness_signals_accumulate(self) -> None:
        profile = _empty_profile()

        profile = aggregate_signal_into_profile(
            profile, _signal(responsiveness_signal="expects quick reply")
        )
        profile = aggregate_signal_into_profile(
            profile, _signal(responsiveness_signal="prefers async")
        )

        assert len(profile.communication.responsiveness_signals) == 2

    def test_empty_responsiveness_signal_not_added(self) -> None:
        profile = _empty_profile()
        profile = aggregate_signal_into_profile(profile, _signal(responsiveness_signal=""))
        assert profile.communication.responsiveness_signals == []
