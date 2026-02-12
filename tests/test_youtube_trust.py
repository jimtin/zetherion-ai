"""Tests for the YouTube TrustModel — auto-approval, promotion, demotion, serialization."""

from __future__ import annotations

import pytest

from zetherion_ai.skills.youtube.models import ReplyCategory, TrustLevel
from zetherion_ai.skills.youtube.trust import (
    _AUTO_CATEGORIES,
    _DEMOTION_FROM,
    _DEMOTION_TO,
    _PROMOTION_RULES,
    TrustModel,
)

# =====================================================================
# Fixtures
# =====================================================================


@pytest.fixture()
def supervised() -> TrustModel:
    """Return a TrustModel at SUPERVISED level with zero stats."""
    return TrustModel(level=TrustLevel.SUPERVISED.value)


@pytest.fixture()
def guided() -> TrustModel:
    """Return a TrustModel at GUIDED level with zero stats."""
    return TrustModel(level=TrustLevel.GUIDED.value)


@pytest.fixture()
def autonomous() -> TrustModel:
    """Return a TrustModel at AUTONOMOUS level with zero stats."""
    return TrustModel(level=TrustLevel.AUTONOMOUS.value)


@pytest.fixture()
def full_auto() -> TrustModel:
    """Return a TrustModel at FULL_AUTO level with zero stats."""
    return TrustModel(level=TrustLevel.FULL_AUTO.value)


# =====================================================================
# Initialization
# =====================================================================


class TestInit:
    """TrustModel.__init__ behaviour."""

    def test_default_level_is_supervised(self) -> None:
        tm = TrustModel()
        assert tm.level == TrustLevel.SUPERVISED.value

    def test_default_stats_zeroed(self) -> None:
        tm = TrustModel()
        assert tm.stats == {"total": 0, "approved": 0, "rejected": 0}

    def test_custom_level(self) -> None:
        tm = TrustModel(level=TrustLevel.AUTONOMOUS.value)
        assert tm.level == TrustLevel.AUTONOMOUS.value

    def test_custom_stats(self) -> None:
        stats = {"total": 10, "approved": 8, "rejected": 2}
        tm = TrustModel(stats=stats)
        assert tm.stats == stats

    def test_stats_returns_copy(self) -> None:
        """Mutating the returned dict must not affect the internal state."""
        tm = TrustModel()
        external = tm.stats
        external["total"] = 999
        assert tm.stats["total"] == 0

    @pytest.mark.parametrize(
        "level, expected_label",
        [
            (TrustLevel.SUPERVISED.value, "SUPERVISED"),
            (TrustLevel.GUIDED.value, "GUIDED"),
            (TrustLevel.AUTONOMOUS.value, "AUTONOMOUS"),
            (TrustLevel.FULL_AUTO.value, "FULL_AUTO"),
        ],
    )
    def test_label_matches_trust_level_name(self, level: int, expected_label: str) -> None:
        tm = TrustModel(level=level)
        assert tm.label == expected_label


# =====================================================================
# Approval / rejection rates
# =====================================================================


class TestRates:
    """approval_rate and rejection_rate properties."""

    def test_rates_zero_when_no_activity(self, supervised: TrustModel) -> None:
        assert supervised.approval_rate == 0.0
        assert supervised.rejection_rate == 0.0

    def test_approval_rate_calculation(self) -> None:
        tm = TrustModel(stats={"total": 10, "approved": 7, "rejected": 3})
        assert tm.approval_rate == pytest.approx(0.7)

    def test_rejection_rate_calculation(self) -> None:
        tm = TrustModel(stats={"total": 10, "approved": 7, "rejected": 3})
        assert tm.rejection_rate == pytest.approx(0.3)

    def test_rates_after_approvals(self) -> None:
        tm = TrustModel(level=TrustLevel.FULL_AUTO.value)
        for _ in range(4):
            tm.record_approval()
        assert tm.approval_rate == pytest.approx(1.0)
        assert tm.rejection_rate == pytest.approx(0.0)


# =====================================================================
# Auto-approval logic — should_auto_approve
# =====================================================================


class TestShouldAutoApprove:
    """Per-level auto-approval based on reply category."""

    # ------------------------------------------------------------------
    # SUPERVISED — nothing auto-approved
    # ------------------------------------------------------------------

    def test_supervised_no_auto_approval(self, supervised: TrustModel) -> None:
        for cat in ReplyCategory:
            assert supervised.should_auto_approve(cat.value) is False

    # ------------------------------------------------------------------
    # GUIDED — THANK_YOU and FAQ
    # ------------------------------------------------------------------

    def test_guided_thank_you_auto(self, guided: TrustModel) -> None:
        assert guided.should_auto_approve(ReplyCategory.THANK_YOU.value) is True

    def test_guided_faq_auto(self, guided: TrustModel) -> None:
        assert guided.should_auto_approve(ReplyCategory.FAQ.value) is True

    def test_guided_question_not_auto(self, guided: TrustModel) -> None:
        assert guided.should_auto_approve(ReplyCategory.QUESTION.value) is False

    def test_guided_feedback_not_auto(self, guided: TrustModel) -> None:
        assert guided.should_auto_approve(ReplyCategory.FEEDBACK.value) is False

    def test_guided_complaint_not_auto(self, guided: TrustModel) -> None:
        assert guided.should_auto_approve(ReplyCategory.COMPLAINT.value) is False

    def test_guided_spam_not_auto(self, guided: TrustModel) -> None:
        assert guided.should_auto_approve(ReplyCategory.SPAM.value) is False

    # ------------------------------------------------------------------
    # AUTONOMOUS — THANK_YOU, FAQ, QUESTION, FEEDBACK
    # ------------------------------------------------------------------

    def test_autonomous_thank_you_auto(self, autonomous: TrustModel) -> None:
        assert autonomous.should_auto_approve(ReplyCategory.THANK_YOU.value) is True

    def test_autonomous_faq_auto(self, autonomous: TrustModel) -> None:
        assert autonomous.should_auto_approve(ReplyCategory.FAQ.value) is True

    def test_autonomous_question_auto(self, autonomous: TrustModel) -> None:
        assert autonomous.should_auto_approve(ReplyCategory.QUESTION.value) is True

    def test_autonomous_feedback_auto(self, autonomous: TrustModel) -> None:
        assert autonomous.should_auto_approve(ReplyCategory.FEEDBACK.value) is True

    def test_autonomous_complaint_not_auto(self, autonomous: TrustModel) -> None:
        assert autonomous.should_auto_approve(ReplyCategory.COMPLAINT.value) is False

    def test_autonomous_spam_not_auto(self, autonomous: TrustModel) -> None:
        assert autonomous.should_auto_approve(ReplyCategory.SPAM.value) is False

    # ------------------------------------------------------------------
    # FULL_AUTO — everything except SPAM
    # ------------------------------------------------------------------

    def test_full_auto_all_except_spam(self, full_auto: TrustModel) -> None:
        for cat in ReplyCategory:
            if cat == ReplyCategory.SPAM:
                assert full_auto.should_auto_approve(cat.value) is False
            else:
                assert full_auto.should_auto_approve(cat.value) is True

    # ------------------------------------------------------------------
    # Unknown category string
    # ------------------------------------------------------------------

    def test_unknown_category_not_auto(self, full_auto: TrustModel) -> None:
        assert full_auto.should_auto_approve("nonexistent_category") is False


# =====================================================================
# auto_categories / review_categories properties
# =====================================================================


class TestCategorySets:
    """auto_categories and review_categories property sets."""

    def test_supervised_auto_categories_empty(self, supervised: TrustModel) -> None:
        assert supervised.auto_categories == set()

    def test_guided_auto_categories(self, guided: TrustModel) -> None:
        assert guided.auto_categories == {"thank_you", "faq"}

    def test_autonomous_auto_categories(self, autonomous: TrustModel) -> None:
        assert autonomous.auto_categories == {"thank_you", "faq", "question", "feedback"}

    def test_full_auto_auto_categories(self, full_auto: TrustModel) -> None:
        assert full_auto.auto_categories == {
            "thank_you",
            "faq",
            "question",
            "feedback",
            "complaint",
        }

    def test_review_categories_excludes_spam(self, supervised: TrustModel) -> None:
        """review_categories is all non-SPAM categories minus auto_categories."""
        all_non_spam = {c.value for c in ReplyCategory if c != ReplyCategory.SPAM}
        assert supervised.review_categories == all_non_spam

    def test_review_plus_auto_equals_all_non_spam(self) -> None:
        """For every level, auto + review should equal all non-SPAM categories."""
        all_non_spam = {c.value for c in ReplyCategory if c != ReplyCategory.SPAM}
        for level in TrustLevel:
            tm = TrustModel(level=level.value)
            assert tm.auto_categories | tm.review_categories == all_non_spam


# =====================================================================
# next_level_at property
# =====================================================================


class TestNextLevelAt:
    """next_level_at property returns the promotion threshold or None."""

    def test_supervised_next_level_at(self, supervised: TrustModel) -> None:
        assert supervised.next_level_at == 50

    def test_guided_next_level_at(self, guided: TrustModel) -> None:
        assert guided.next_level_at == 200

    def test_autonomous_next_level_at_none(self, autonomous: TrustModel) -> None:
        """AUTONOMOUS -> FULL_AUTO is manual only, so no automatic threshold."""
        assert autonomous.next_level_at is None

    def test_full_auto_next_level_at_none(self, full_auto: TrustModel) -> None:
        assert full_auto.next_level_at is None


# =====================================================================
# Promotion logic — record_approval
# =====================================================================


class TestPromotion:
    """record_approval should trigger level-up at correct thresholds."""

    def test_supervised_promotes_to_guided_at_50_with_low_rejection(self) -> None:
        """50 approvals, 0 rejections -> rejection rate 0% < 5% -> promote."""
        tm = TrustModel(level=TrustLevel.SUPERVISED.value)
        for _ in range(50):
            tm.record_approval()
        assert tm.level == TrustLevel.GUIDED.value

    def test_supervised_no_promote_at_49(self) -> None:
        """49 approvals is below the 50 threshold -> stays SUPERVISED."""
        tm = TrustModel(level=TrustLevel.SUPERVISED.value)
        for _ in range(49):
            tm.record_approval()
        assert tm.level == TrustLevel.SUPERVISED.value

    def test_supervised_no_promote_when_rejection_rate_too_high(self) -> None:
        """50 total but 5% rejected exactly -> NOT < 5%, so no promotion."""
        # 3 rejected + 47 approved = 50 total, rejection_rate = 3/50 = 0.06 > 0.05
        tm = TrustModel(level=TrustLevel.SUPERVISED.value)
        for _ in range(3):
            tm.record_rejection()
        for _ in range(47):
            tm.record_approval()
        assert tm.level == TrustLevel.SUPERVISED.value

    def test_supervised_promote_with_exactly_at_boundary_rejection(self) -> None:
        """Rejection rate must be strictly < 5%. At exactly 5% no promotion."""
        # 5 rejected + 95 approved = 100 total, rejection_rate = 5/100 = 0.05
        # 0.05 is NOT < 0.05 so no promotion.
        tm = TrustModel(level=TrustLevel.SUPERVISED.value)
        for _ in range(5):
            tm.record_rejection()
        for _ in range(95):
            tm.record_approval()
        # After 100 total with 5% rejection, should still be SUPERVISED
        assert tm.level == TrustLevel.SUPERVISED.value

    def test_supervised_promote_just_under_boundary(self) -> None:
        """2 rejections out of 50 = 4% < 5% -> promotes."""
        tm = TrustModel(level=TrustLevel.SUPERVISED.value)
        for _ in range(2):
            tm.record_rejection()
        for _ in range(48):
            tm.record_approval()
        assert tm.level == TrustLevel.GUIDED.value

    def test_guided_promotes_to_autonomous_at_200_low_rejection(self) -> None:
        """200 approvals, 0 rejections -> promote to AUTONOMOUS."""
        tm = TrustModel(level=TrustLevel.GUIDED.value)
        for _ in range(200):
            tm.record_approval()
        assert tm.level == TrustLevel.AUTONOMOUS.value

    def test_guided_no_promote_at_199(self) -> None:
        """199 total is below the 200 threshold -> stays GUIDED."""
        tm = TrustModel(level=TrustLevel.GUIDED.value)
        for _ in range(199):
            tm.record_approval()
        assert tm.level == TrustLevel.GUIDED.value

    def test_guided_no_promote_high_rejection(self) -> None:
        """200 total but rejection >= 3% -> stays GUIDED."""
        # 6 rejected + 194 approved = 200 total, rejection_rate = 6/200 = 3%
        tm = TrustModel(level=TrustLevel.GUIDED.value)
        for _ in range(6):
            tm.record_rejection()
        for _ in range(194):
            tm.record_approval()
        assert tm.level == TrustLevel.GUIDED.value

    def test_guided_promote_just_under_rejection_threshold(self) -> None:
        """5 rejections out of 200 = 2.5% < 3% -> promotes to AUTONOMOUS."""
        tm = TrustModel(level=TrustLevel.GUIDED.value)
        for _ in range(5):
            tm.record_rejection()
        for _ in range(195):
            tm.record_approval()
        assert tm.level == TrustLevel.AUTONOMOUS.value

    def test_autonomous_does_not_auto_promote_to_full_auto(self) -> None:
        """AUTONOMOUS -> FULL_AUTO has no auto-promotion rule."""
        tm = TrustModel(level=TrustLevel.AUTONOMOUS.value)
        for _ in range(500):
            tm.record_approval()
        assert tm.level == TrustLevel.AUTONOMOUS.value

    def test_full_auto_stays_full_auto_on_approval(self) -> None:
        """FULL_AUTO has no further promotion level."""
        tm = TrustModel(level=TrustLevel.FULL_AUTO.value)
        for _ in range(100):
            tm.record_approval()
        assert tm.level == TrustLevel.FULL_AUTO.value

    def test_promotion_increments_stats(self) -> None:
        """Stats should be correctly incremented after promotion."""
        tm = TrustModel(level=TrustLevel.SUPERVISED.value)
        for _ in range(50):
            tm.record_approval()
        assert tm.stats["total"] == 50
        assert tm.stats["approved"] == 50
        assert tm.stats["rejected"] == 0


# =====================================================================
# Demotion logic — record_rejection
# =====================================================================


class TestDemotion:
    """record_rejection should trigger demotion from AUTONOMOUS to GUIDED."""

    def test_autonomous_demotes_on_single_rejection(self) -> None:
        """Any rejection at AUTONOMOUS triggers demotion to GUIDED."""
        tm = TrustModel(level=TrustLevel.AUTONOMOUS.value)
        tm.record_rejection()
        assert tm.level == TrustLevel.GUIDED.value

    def test_supervised_no_demotion_on_rejection(self) -> None:
        """Rejection at SUPERVISED should not change level."""
        tm = TrustModel(level=TrustLevel.SUPERVISED.value)
        tm.record_rejection()
        assert tm.level == TrustLevel.SUPERVISED.value

    def test_guided_no_demotion_on_rejection(self) -> None:
        """Rejection at GUIDED should not demote (only AUTONOMOUS demotes)."""
        tm = TrustModel(level=TrustLevel.GUIDED.value)
        tm.record_rejection()
        assert tm.level == TrustLevel.GUIDED.value

    def test_full_auto_no_demotion_on_rejection(self) -> None:
        """Rejection at FULL_AUTO should not demote."""
        tm = TrustModel(level=TrustLevel.FULL_AUTO.value)
        tm.record_rejection()
        assert tm.level == TrustLevel.FULL_AUTO.value

    def test_rejection_increments_stats(self) -> None:
        """Stats must be updated even when demotion occurs."""
        tm = TrustModel(level=TrustLevel.AUTONOMOUS.value)
        tm.record_rejection()
        assert tm.stats["total"] == 1
        assert tm.stats["rejected"] == 1
        assert tm.stats["approved"] == 0

    def test_demotion_constants_match(self) -> None:
        """Verify module-level demotion constants are correct."""
        assert TrustLevel.AUTONOMOUS.value == _DEMOTION_FROM
        assert TrustLevel.GUIDED.value == _DEMOTION_TO

    def test_multiple_rejections_at_supervised(self) -> None:
        """Multiple rejections at SUPERVISED only accumulate stats."""
        tm = TrustModel(level=TrustLevel.SUPERVISED.value)
        for _ in range(10):
            tm.record_rejection()
        assert tm.level == TrustLevel.SUPERVISED.value
        assert tm.stats["rejected"] == 10
        assert tm.stats["total"] == 10


# =====================================================================
# set_level — manual override
# =====================================================================


class TestSetLevel:
    """Manual trust level override via set_level."""

    def test_set_level_to_full_auto(self, supervised: TrustModel) -> None:
        supervised.set_level(TrustLevel.FULL_AUTO.value)
        assert supervised.level == TrustLevel.FULL_AUTO.value

    def test_set_level_to_supervised(self, full_auto: TrustModel) -> None:
        full_auto.set_level(TrustLevel.SUPERVISED.value)
        assert full_auto.level == TrustLevel.SUPERVISED.value

    def test_set_level_rejects_negative(self, supervised: TrustModel) -> None:
        supervised.set_level(-1)
        assert supervised.level == TrustLevel.SUPERVISED.value  # unchanged

    def test_set_level_rejects_too_high(self, supervised: TrustModel) -> None:
        supervised.set_level(TrustLevel.FULL_AUTO.value + 1)
        assert supervised.level == TrustLevel.SUPERVISED.value  # unchanged

    def test_set_level_boundary_zero(self, guided: TrustModel) -> None:
        guided.set_level(0)
        assert guided.level == 0

    def test_set_level_boundary_max(self, supervised: TrustModel) -> None:
        supervised.set_level(TrustLevel.FULL_AUTO.value)
        assert supervised.level == TrustLevel.FULL_AUTO.value


# =====================================================================
# from_channel factory
# =====================================================================


class TestFromChannel:
    """TrustModel.from_channel() class method."""

    def test_from_channel_with_level_and_stats(self) -> None:
        channel = {
            "trust_level": 2,
            "trust_stats": {"total": 100, "approved": 95, "rejected": 5},
        }
        tm = TrustModel.from_channel(channel)
        assert tm.level == TrustLevel.AUTONOMOUS.value
        assert tm.stats == {"total": 100, "approved": 95, "rejected": 5}

    def test_from_channel_defaults_when_missing(self) -> None:
        tm = TrustModel.from_channel({})
        assert tm.level == TrustLevel.SUPERVISED.value
        assert tm.stats == {"total": 0, "approved": 0, "rejected": 0}

    def test_from_channel_with_none_trust_stats(self) -> None:
        channel = {"trust_level": 1, "trust_stats": None}
        tm = TrustModel.from_channel(channel)
        assert tm.level == TrustLevel.GUIDED.value
        assert tm.stats == {"total": 0, "approved": 0, "rejected": 0}

    def test_from_channel_coerces_level_to_int(self) -> None:
        """trust_level might arrive as a string from a DB row."""
        channel = {"trust_level": "1"}
        tm = TrustModel.from_channel(channel)
        assert tm.level == 1

    def test_from_channel_full_auto(self) -> None:
        channel = {
            "trust_level": TrustLevel.FULL_AUTO.value,
            "trust_stats": {"total": 500, "approved": 490, "rejected": 10},
        }
        tm = TrustModel.from_channel(channel)
        assert tm.level == TrustLevel.FULL_AUTO.value
        assert tm.stats["total"] == 500


# =====================================================================
# to_dict serialization
# =====================================================================


class TestToDict:
    """to_dict() serialization output."""

    def test_to_dict_keys(self, supervised: TrustModel) -> None:
        d = supervised.to_dict()
        expected_keys = {
            "level",
            "label",
            "stats",
            "approval_rate",
            "rejection_rate",
            "next_level_at",
        }
        assert set(d.keys()) == expected_keys

    def test_to_dict_level_and_label(self) -> None:
        tm = TrustModel(level=TrustLevel.GUIDED.value)
        d = tm.to_dict()
        assert d["level"] == TrustLevel.GUIDED.value
        assert d["label"] == "GUIDED"

    def test_to_dict_stats_match(self) -> None:
        stats = {"total": 30, "approved": 28, "rejected": 2}
        tm = TrustModel(stats=stats)
        d = tm.to_dict()
        assert d["stats"] == stats

    def test_to_dict_rates_rounded(self) -> None:
        tm = TrustModel(stats={"total": 3, "approved": 1, "rejected": 2})
        d = tm.to_dict()
        # 1/3 = 0.333..., rounded to 4 decimal places
        assert d["approval_rate"] == round(1 / 3, 4)
        assert d["rejection_rate"] == round(2 / 3, 4)

    def test_to_dict_rates_zero_when_empty(self, supervised: TrustModel) -> None:
        d = supervised.to_dict()
        assert d["approval_rate"] == 0.0
        assert d["rejection_rate"] == 0.0

    def test_to_dict_next_level_at_supervised(self, supervised: TrustModel) -> None:
        d = supervised.to_dict()
        assert d["next_level_at"] == 50

    def test_to_dict_next_level_at_none_for_autonomous(self, autonomous: TrustModel) -> None:
        d = autonomous.to_dict()
        assert d["next_level_at"] is None


# =====================================================================
# Edge cases & combined scenarios
# =====================================================================


class TestEdgeCases:
    """Boundary values, combined promotion/demotion flows, and corner cases."""

    def test_promote_then_demote_cycle(self) -> None:
        """Promote SUPERVISED -> GUIDED -> AUTONOMOUS then demote back."""
        tm = TrustModel(level=TrustLevel.SUPERVISED.value)
        # Promote to GUIDED
        for _ in range(50):
            tm.record_approval()
        assert tm.level == TrustLevel.GUIDED.value

        # Promote to AUTONOMOUS
        for _ in range(200):
            tm.record_approval()
        assert tm.level == TrustLevel.AUTONOMOUS.value

        # Demote back to GUIDED
        tm.record_rejection()
        assert tm.level == TrustLevel.GUIDED.value

    def test_stats_accumulate_across_promotion(self) -> None:
        """Stats carry over through promotions and demotions."""
        tm = TrustModel(level=TrustLevel.SUPERVISED.value)
        for _ in range(50):
            tm.record_approval()
        # Now at GUIDED with 50 total
        assert tm.stats["total"] == 50

        for _ in range(200):
            tm.record_approval()
        # Now at AUTONOMOUS with 250 total
        assert tm.stats["total"] == 250

        tm.record_rejection()
        # Demoted to GUIDED, stats still accumulate
        assert tm.stats["total"] == 251
        assert tm.stats["rejected"] == 1

    def test_promotion_does_not_skip_levels(self) -> None:
        """Even with massive approval counts, promotion goes one step at a time."""
        tm = TrustModel(level=TrustLevel.SUPERVISED.value)
        # Record enough for both SUPERVISED and GUIDED thresholds
        for _ in range(300):
            tm.record_approval()
        # Should be GUIDED first (promoted at 50), then at 250 total
        # the _try_promote runs each time -- at approval 50 it promotes to GUIDED.
        # Subsequent approvals check GUIDED rules (200 total, <3% rejection).
        # At approval 250 the 200 threshold is met -> promotes to AUTONOMOUS.
        assert tm.level == TrustLevel.AUTONOMOUS.value

    def test_auto_categories_returns_fresh_set(self, guided: TrustModel) -> None:
        """auto_categories must return a new set each call (defensive copy)."""
        s1 = guided.auto_categories
        s2 = guided.auto_categories
        assert s1 == s2
        assert s1 is not s2

    def test_unknown_level_auto_categories_empty(self) -> None:
        """If level is somehow outside known values, auto_categories returns empty."""
        tm = TrustModel(level=99)
        assert tm.auto_categories == set()

    def test_record_approval_and_rejection_interleaved(self) -> None:
        """Interleaved approvals/rejections maintain correct stats."""
        tm = TrustModel(level=TrustLevel.FULL_AUTO.value)
        tm.record_approval()
        tm.record_rejection()
        tm.record_approval()
        tm.record_approval()
        tm.record_rejection()
        assert tm.stats["total"] == 5
        assert tm.stats["approved"] == 3
        assert tm.stats["rejected"] == 2
        assert tm.approval_rate == pytest.approx(0.6)
        assert tm.rejection_rate == pytest.approx(0.4)

    def test_promotion_rules_module_constant_integrity(self) -> None:
        """Verify _PROMOTION_RULES has expected structure and values."""
        assert TrustLevel.SUPERVISED.value in _PROMOTION_RULES
        assert TrustLevel.GUIDED.value in _PROMOTION_RULES
        assert TrustLevel.AUTONOMOUS.value not in _PROMOTION_RULES
        assert TrustLevel.FULL_AUTO.value not in _PROMOTION_RULES

        sup_min, sup_rate = _PROMOTION_RULES[TrustLevel.SUPERVISED.value]
        assert sup_min == 50
        assert sup_rate == pytest.approx(0.05)

        gui_min, gui_rate = _PROMOTION_RULES[TrustLevel.GUIDED.value]
        assert gui_min == 200
        assert gui_rate == pytest.approx(0.03)

    def test_auto_categories_module_constant_integrity(self) -> None:
        """Verify _AUTO_CATEGORIES has an entry for every TrustLevel."""
        for level in TrustLevel:
            assert level.value in _AUTO_CATEGORIES

    def test_pre_loaded_stats_affect_promotion(self) -> None:
        """A model initialized with existing stats can promote immediately."""
        # 49 approvals already recorded; one more should trigger promotion.
        tm = TrustModel(
            level=TrustLevel.SUPERVISED.value,
            stats={"total": 49, "approved": 49, "rejected": 0},
        )
        tm.record_approval()
        assert tm.level == TrustLevel.GUIDED.value

    def test_pre_loaded_stats_block_promotion_with_rejections(self) -> None:
        """High historical rejection rate prevents promotion even at threshold."""
        # 47 approved + 3 rejected = 50 total, but rejection = 3/50 = 6% >= 5%
        tm = TrustModel(
            level=TrustLevel.SUPERVISED.value,
            stats={"total": 49, "approved": 46, "rejected": 3},
        )
        tm.record_approval()  # total=50, rejected=3 -> 6% > 5%
        assert tm.level == TrustLevel.SUPERVISED.value

    def test_demotion_after_manual_set_to_autonomous(self) -> None:
        """Manually setting level to AUTONOMOUS still allows demotion."""
        tm = TrustModel(level=TrustLevel.SUPERVISED.value)
        tm.set_level(TrustLevel.AUTONOMOUS.value)
        tm.record_rejection()
        assert tm.level == TrustLevel.GUIDED.value

    def test_stats_preserved_after_set_level(self) -> None:
        """set_level does not reset stats."""
        tm = TrustModel(stats={"total": 100, "approved": 90, "rejected": 10})
        tm.set_level(TrustLevel.FULL_AUTO.value)
        assert tm.stats["total"] == 100
