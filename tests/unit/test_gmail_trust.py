"""Comprehensive unit tests for Gmail trust scoring module."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from zetherion_ai.skills.gmail.replies import TRUST_CEILINGS, ReplyType
from zetherion_ai.skills.gmail.trust import (
    APPROVAL_DELTA,
    GLOBAL_CAP,
    MAJOR_EDIT_DELTA,
    MINOR_EDIT_DELTA,
    REJECTION_DELTA,
    TrustManager,
    TrustScore,
    _outcome_delta,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_pool():
    """Mock asyncpg connection pool with async context manager support."""
    pool = MagicMock()
    conn = AsyncMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    return pool, conn


@pytest.fixture
def trust_manager(mock_pool):
    """Create a TrustManager wired to the mock pool."""
    pool, _ = mock_pool
    return TrustManager(pool)


# ---------------------------------------------------------------------------
# 1. TrustScore dataclass
# ---------------------------------------------------------------------------


class TestTrustScore:
    """Tests for the TrustScore dataclass."""

    def test_default_values(self):
        ts = TrustScore(score=0.5)
        assert ts.score == 0.5
        assert ts.approvals == 0
        assert ts.rejections == 0
        assert ts.edits == 0
        assert ts.total_interactions == 0

    def test_approval_rate_with_interactions(self):
        ts = TrustScore(score=0.8, approvals=7, total_interactions=10)
        assert ts.approval_rate == pytest.approx(0.7)

    def test_approval_rate_zero_interactions(self):
        ts = TrustScore(score=0.0)
        assert ts.approval_rate == 0.0

    def test_approval_rate_all_approvals(self):
        ts = TrustScore(score=0.9, approvals=5, total_interactions=5)
        assert ts.approval_rate == pytest.approx(1.0)

    def test_to_dict_serialization(self):
        ts = TrustScore(
            score=0.12345,
            approvals=3,
            rejections=1,
            edits=2,
            total_interactions=6,
        )
        d = ts.to_dict()
        assert d == {
            "score": 0.1235,  # rounded to 4 decimal places
            "approvals": 3,
            "rejections": 1,
            "edits": 2,
            "total_interactions": 6,
            "approval_rate": 0.5,  # 3/6
        }

    def test_to_dict_zero_interactions(self):
        ts = TrustScore(score=0.0)
        d = ts.to_dict()
        assert d["approval_rate"] == 0.0
        assert d["score"] == 0.0


# ---------------------------------------------------------------------------
# 2. _outcome_delta function
# ---------------------------------------------------------------------------


class TestOutcomeDelta:
    """Tests for the _outcome_delta helper function."""

    def test_approved(self):
        assert _outcome_delta("approved") == APPROVAL_DELTA

    def test_minor_edit(self):
        assert _outcome_delta("minor_edit") == MINOR_EDIT_DELTA

    def test_major_edit(self):
        assert _outcome_delta("major_edit") == MAJOR_EDIT_DELTA

    def test_rejected(self):
        assert _outcome_delta("rejected") == REJECTION_DELTA

    def test_unknown_outcome_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown outcome"):
            _outcome_delta("unknown")

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown outcome"):
            _outcome_delta("")


# ---------------------------------------------------------------------------
# 3. TrustManager.get_effective_trust
# ---------------------------------------------------------------------------


class TestGetEffectiveTrust:
    """Tests for TrustManager.get_effective_trust."""

    async def test_min_of_type_contact_ceiling(self, trust_manager, mock_pool):
        """Both type and contact trust present -- effective = min(all three)."""
        _, conn = mock_pool
        # type_trust returns 0.6, contact_trust returns 0.7
        conn.fetchrow.side_effect = [
            {"score": 0.6, "approvals": 5, "rejections": 0, "edits": 0, "total_interactions": 5},
            {"score": 0.7, "approvals": 5, "rejections": 0, "edits": 0, "total_interactions": 5},
        ]
        reply_type = ReplyType.ACKNOWLEDGMENT  # ceiling = 0.95
        result = await trust_manager.get_effective_trust(1, "a@b.com", reply_type)
        assert result == 0.6  # min(0.6, 0.7, 0.95)

    async def test_type_trust_lower(self, trust_manager, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.side_effect = [
            {"score": 0.3, "approvals": 2, "rejections": 0, "edits": 0, "total_interactions": 2},
            {"score": 0.8, "approvals": 8, "rejections": 0, "edits": 0, "total_interactions": 8},
        ]
        result = await trust_manager.get_effective_trust(1, "a@b.com", ReplyType.ACKNOWLEDGMENT)
        assert result == 0.3

    async def test_contact_trust_lower(self, trust_manager, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.side_effect = [
            {"score": 0.8, "approvals": 8, "rejections": 0, "edits": 0, "total_interactions": 8},
            {"score": 0.2, "approvals": 1, "rejections": 0, "edits": 0, "total_interactions": 5},
        ]
        result = await trust_manager.get_effective_trust(1, "a@b.com", ReplyType.ACKNOWLEDGMENT)
        assert result == 0.2

    async def test_ceiling_lower_than_both(self, trust_manager, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.side_effect = [
            {"score": 0.9, "approvals": 9, "rejections": 0, "edits": 0, "total_interactions": 9},
            {"score": 0.9, "approvals": 9, "rejections": 0, "edits": 0, "total_interactions": 9},
        ]
        # SENSITIVE ceiling = 0.30
        result = await trust_manager.get_effective_trust(1, "a@b.com", ReplyType.SENSITIVE)
        assert result == TRUST_CEILINGS[ReplyType.SENSITIVE]

    async def test_missing_type_trust_returns_zero(self, trust_manager, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.side_effect = [
            None,  # no type trust row
            {"score": 0.5, "approvals": 3, "rejections": 0, "edits": 0, "total_interactions": 3},
        ]
        result = await trust_manager.get_effective_trust(1, "a@b.com", ReplyType.GENERAL)
        assert result == 0.0

    async def test_missing_contact_trust_returns_zero(self, trust_manager, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.side_effect = [
            {"score": 0.5, "approvals": 3, "rejections": 0, "edits": 0, "total_interactions": 3},
            None,  # no contact trust row
        ]
        result = await trust_manager.get_effective_trust(1, "a@b.com", ReplyType.GENERAL)
        assert result == 0.0


# ---------------------------------------------------------------------------
# 4. TrustManager.should_auto_send
# ---------------------------------------------------------------------------


class TestShouldAutoSend:
    """Tests for TrustManager.should_auto_send."""

    async def test_both_above_threshold(self, trust_manager, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.side_effect = [
            {"score": 0.9, "approvals": 9, "rejections": 0, "edits": 0, "total_interactions": 9},
            {"score": 0.9, "approvals": 9, "rejections": 0, "edits": 0, "total_interactions": 9},
        ]
        result = await trust_manager.should_auto_send(
            1, "a@b.com", ReplyType.ACKNOWLEDGMENT, confidence=0.9
        )
        assert result is True

    async def test_trust_below_threshold(self, trust_manager, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.side_effect = [
            {"score": 0.5, "approvals": 3, "rejections": 0, "edits": 0, "total_interactions": 3},
            {"score": 0.5, "approvals": 3, "rejections": 0, "edits": 0, "total_interactions": 3},
        ]
        result = await trust_manager.should_auto_send(
            1, "a@b.com", ReplyType.ACKNOWLEDGMENT, confidence=0.9
        )
        assert result is False

    async def test_confidence_below_threshold(self, trust_manager, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.side_effect = [
            {"score": 0.9, "approvals": 9, "rejections": 0, "edits": 0, "total_interactions": 9},
            {"score": 0.9, "approvals": 9, "rejections": 0, "edits": 0, "total_interactions": 9},
        ]
        result = await trust_manager.should_auto_send(
            1, "a@b.com", ReplyType.ACKNOWLEDGMENT, confidence=0.5
        )
        assert result is False

    async def test_both_below_threshold(self, trust_manager, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.side_effect = [
            {"score": 0.3, "approvals": 2, "rejections": 0, "edits": 0, "total_interactions": 2},
            {"score": 0.3, "approvals": 2, "rejections": 0, "edits": 0, "total_interactions": 2},
        ]
        result = await trust_manager.should_auto_send(
            1, "a@b.com", ReplyType.ACKNOWLEDGMENT, confidence=0.3
        )
        assert result is False

    async def test_custom_auto_threshold(self, trust_manager, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.side_effect = [
            {"score": 0.6, "approvals": 5, "rejections": 0, "edits": 0, "total_interactions": 5},
            {"score": 0.6, "approvals": 5, "rejections": 0, "edits": 0, "total_interactions": 5},
        ]
        # Use a lower threshold of 0.5
        result = await trust_manager.should_auto_send(
            1, "a@b.com", ReplyType.ACKNOWLEDGMENT, confidence=0.6, auto_threshold=0.5
        )
        assert result is True


# ---------------------------------------------------------------------------
# 5. TrustManager.record_feedback
# ---------------------------------------------------------------------------


class TestRecordFeedback:
    """Tests for TrustManager.record_feedback."""

    async def test_approved_feedback(self, trust_manager, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.side_effect = [
            {"score": 0.55},  # _update_type_trust
            {"score": 0.55},  # _update_contact_trust
        ]
        new_type, new_contact = await trust_manager.record_feedback(
            1, "a@b.com", ReplyType.GENERAL, "approved"
        )
        assert new_type == 0.55
        assert new_contact == 0.55

    async def test_rejected_feedback(self, trust_manager, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.side_effect = [
            {"score": 0.30},
            {"score": 0.30},
        ]
        new_type, new_contact = await trust_manager.record_feedback(
            1, "a@b.com", ReplyType.GENERAL, "rejected"
        )
        assert new_type == 0.30
        assert new_contact == 0.30

    async def test_minor_edit_feedback(self, trust_manager, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.side_effect = [
            {"score": 0.48},
            {"score": 0.48},
        ]
        new_type, new_contact = await trust_manager.record_feedback(
            1, "a@b.com", ReplyType.GENERAL, "minor_edit"
        )
        assert new_type == 0.48
        assert new_contact == 0.48

    async def test_major_edit_feedback(self, trust_manager, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.side_effect = [
            {"score": 0.40},
            {"score": 0.40},
        ]
        new_type, new_contact = await trust_manager.record_feedback(
            1, "a@b.com", ReplyType.GENERAL, "major_edit"
        )
        assert new_type == 0.40
        assert new_contact == 0.40

    async def test_returns_tuple(self, trust_manager, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.side_effect = [
            {"score": 0.10},
            {"score": 0.20},
        ]
        result = await trust_manager.record_feedback(1, "a@b.com", ReplyType.GENERAL, "approved")
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert result == (0.10, 0.20)

    async def test_unknown_outcome_raises(self, trust_manager):
        with pytest.raises(ValueError, match="Unknown outcome"):
            await trust_manager.record_feedback(1, "a@b.com", ReplyType.GENERAL, "invalid")


# ---------------------------------------------------------------------------
# 6. TrustManager.get_type_trust
# ---------------------------------------------------------------------------


class TestGetTypeTrust:
    """Tests for TrustManager.get_type_trust."""

    async def test_row_found(self, trust_manager, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.return_value = {
            "score": 0.75,
            "approvals": 10,
            "rejections": 2,
            "edits": 3,
            "total_interactions": 15,
        }
        ts = await trust_manager.get_type_trust(1, ReplyType.GENERAL)
        assert ts.score == 0.75
        assert ts.approvals == 10
        assert ts.rejections == 2
        assert ts.edits == 3
        assert ts.total_interactions == 15

    async def test_row_not_found(self, trust_manager, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.return_value = None
        ts = await trust_manager.get_type_trust(1, ReplyType.GENERAL)
        assert ts.score == 0.0
        assert ts.approvals == 0
        assert ts.rejections == 0
        assert ts.edits == 0
        assert ts.total_interactions == 0


# ---------------------------------------------------------------------------
# 7. TrustManager.get_contact_trust
# ---------------------------------------------------------------------------


class TestGetContactTrust:
    """Tests for TrustManager.get_contact_trust."""

    async def test_row_found(self, trust_manager, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.return_value = {
            "score": 0.65,
            "approvals": 8,
            "rejections": 1,
            "edits": 4,
            "total_interactions": 13,
        }
        ts = await trust_manager.get_contact_trust(1, "alice@example.com")
        assert ts.score == 0.65
        assert ts.approvals == 8
        assert ts.rejections == 1
        assert ts.edits == 4
        assert ts.total_interactions == 13

    async def test_row_not_found(self, trust_manager, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.return_value = None
        ts = await trust_manager.get_contact_trust(1, "unknown@example.com")
        assert ts.score == 0.0
        assert ts.approvals == 0


# ---------------------------------------------------------------------------
# 8. TrustManager.list_type_trusts
# ---------------------------------------------------------------------------


class TestListTypeTrusts:
    """Tests for TrustManager.list_type_trusts."""

    async def test_returns_dict_keyed_by_reply_type(self, trust_manager, mock_pool):
        _, conn = mock_pool
        conn.fetch.return_value = [
            {
                "reply_type": "general",
                "score": 0.5,
                "approvals": 3,
                "rejections": 1,
                "edits": 0,
                "total_interactions": 4,
            },
            {
                "reply_type": "acknowledgment",
                "score": 0.8,
                "approvals": 8,
                "rejections": 0,
                "edits": 0,
                "total_interactions": 8,
            },
        ]
        result = await trust_manager.list_type_trusts(1)
        assert "general" in result
        assert "acknowledgment" in result
        assert result["general"].score == 0.5
        assert result["acknowledgment"].score == 0.8

    async def test_empty_result(self, trust_manager, mock_pool):
        _, conn = mock_pool
        conn.fetch.return_value = []
        result = await trust_manager.list_type_trusts(1)
        assert result == {}


# ---------------------------------------------------------------------------
# 9. TrustManager.list_contact_trusts
# ---------------------------------------------------------------------------


class TestListContactTrusts:
    """Tests for TrustManager.list_contact_trusts."""

    async def test_returns_dict_keyed_by_contact_email(self, trust_manager, mock_pool):
        _, conn = mock_pool
        conn.fetch.return_value = [
            {
                "contact_email": "alice@example.com",
                "score": 0.7,
                "approvals": 5,
                "rejections": 0,
                "edits": 1,
                "total_interactions": 6,
            },
        ]
        result = await trust_manager.list_contact_trusts(1)
        assert "alice@example.com" in result
        assert result["alice@example.com"].score == 0.7

    async def test_respects_limit_parameter(self, trust_manager, mock_pool):
        _, conn = mock_pool
        conn.fetch.return_value = []
        await trust_manager.list_contact_trusts(1, limit=5)
        # Verify the limit parameter was passed to the query
        call_args = conn.fetch.call_args
        assert call_args[0][1] == 1  # user_id
        assert call_args[0][2] == 5  # limit

    async def test_default_limit_is_20(self, trust_manager, mock_pool):
        _, conn = mock_pool
        conn.fetch.return_value = []
        await trust_manager.list_contact_trusts(1)
        call_args = conn.fetch.call_args
        assert call_args[0][2] == 20  # default limit

    async def test_empty_result(self, trust_manager, mock_pool):
        _, conn = mock_pool
        conn.fetch.return_value = []
        result = await trust_manager.list_contact_trusts(1)
        assert result == {}


# ---------------------------------------------------------------------------
# 10. TrustManager.reset_type_trust
# ---------------------------------------------------------------------------


class TestResetTypeTrust:
    """Tests for TrustManager.reset_type_trust."""

    async def test_returns_true_when_row_updated(self, trust_manager, mock_pool):
        _, conn = mock_pool
        conn.execute.return_value = "UPDATE 1"
        result = await trust_manager.reset_type_trust(1, ReplyType.GENERAL)
        assert result is True

    async def test_returns_false_when_no_row(self, trust_manager, mock_pool):
        _, conn = mock_pool
        conn.execute.return_value = "UPDATE 0"
        result = await trust_manager.reset_type_trust(1, ReplyType.GENERAL)
        assert result is False


# ---------------------------------------------------------------------------
# 11. TrustManager.reset_contact_trust
# ---------------------------------------------------------------------------


class TestResetContactTrust:
    """Tests for TrustManager.reset_contact_trust."""

    async def test_returns_true_when_row_updated(self, trust_manager, mock_pool):
        _, conn = mock_pool
        conn.execute.return_value = "UPDATE 1"
        result = await trust_manager.reset_contact_trust(1, "alice@example.com")
        assert result is True

    async def test_returns_false_when_no_row(self, trust_manager, mock_pool):
        _, conn = mock_pool
        conn.execute.return_value = "UPDATE 0"
        result = await trust_manager.reset_contact_trust(1, "nobody@example.com")
        assert result is False


# ---------------------------------------------------------------------------
# 12. TrustManager._update_type_trust  -- SQL param verification
# ---------------------------------------------------------------------------


class TestUpdateTypeTrust:
    """Tests for TrustManager._update_type_trust."""

    async def test_approved_params(self, trust_manager, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.return_value = {"score": 0.05}
        await trust_manager._update_type_trust(1, ReplyType.GENERAL, APPROVAL_DELTA, "approved")
        args = conn.fetchrow.call_args[0]
        # $1=user_id, $2=reply_type, $3=delta, $4=cap, $5=approved, $6=rejected, $7=edits
        assert args[1] == 1  # user_id
        assert args[2] == ReplyType.GENERAL.value  # reply_type
        assert args[3] == APPROVAL_DELTA  # delta
        assert args[5] == 1  # approved increment
        assert args[6] == 0  # rejected increment
        assert args[7] == 0  # edits increment

    async def test_rejected_params(self, trust_manager, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.return_value = {"score": 0.0}
        await trust_manager._update_type_trust(1, ReplyType.GENERAL, REJECTION_DELTA, "rejected")
        args = conn.fetchrow.call_args[0]
        assert args[3] == REJECTION_DELTA
        assert args[5] == 0  # approved
        assert args[6] == 1  # rejected
        assert args[7] == 0  # edits

    async def test_minor_edit_params(self, trust_manager, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.return_value = {"score": 0.48}
        await trust_manager._update_type_trust(1, ReplyType.GENERAL, MINOR_EDIT_DELTA, "minor_edit")
        args = conn.fetchrow.call_args[0]
        assert args[3] == MINOR_EDIT_DELTA
        assert args[5] == 0  # approved
        assert args[6] == 0  # rejected
        assert args[7] == 1  # edits

    async def test_major_edit_params(self, trust_manager, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.return_value = {"score": 0.40}
        await trust_manager._update_type_trust(1, ReplyType.GENERAL, MAJOR_EDIT_DELTA, "major_edit")
        args = conn.fetchrow.call_args[0]
        assert args[3] == MAJOR_EDIT_DELTA
        assert args[5] == 0  # approved
        assert args[6] == 0  # rejected
        assert args[7] == 1  # edits

    async def test_cap_uses_min_of_ceiling_and_global_cap(self, trust_manager, mock_pool):
        """For SENSITIVE (ceiling=0.30), cap = min(0.30, 0.95) = 0.30."""
        _, conn = mock_pool
        conn.fetchrow.return_value = {"score": 0.05}
        await trust_manager._update_type_trust(1, ReplyType.SENSITIVE, APPROVAL_DELTA, "approved")
        args = conn.fetchrow.call_args[0]
        expected_cap = min(TRUST_CEILINGS[ReplyType.SENSITIVE], GLOBAL_CAP)
        assert args[4] == expected_cap  # $4 = cap

    async def test_cap_for_acknowledgment(self, trust_manager, mock_pool):
        """For ACKNOWLEDGMENT (ceiling=0.95), cap = min(0.95, 0.95) = 0.95."""
        _, conn = mock_pool
        conn.fetchrow.return_value = {"score": 0.05}
        await trust_manager._update_type_trust(
            1, ReplyType.ACKNOWLEDGMENT, APPROVAL_DELTA, "approved"
        )
        args = conn.fetchrow.call_args[0]
        assert args[4] == GLOBAL_CAP

    async def test_returns_new_score(self, trust_manager, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.return_value = {"score": 0.75}
        result = await trust_manager._update_type_trust(
            1, ReplyType.GENERAL, APPROVAL_DELTA, "approved"
        )
        assert result == 0.75


# ---------------------------------------------------------------------------
# 13. TrustManager._update_contact_trust  -- SQL param verification
# ---------------------------------------------------------------------------


class TestUpdateContactTrust:
    """Tests for TrustManager._update_contact_trust."""

    async def test_approved_params(self, trust_manager, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.return_value = {"score": 0.05}
        await trust_manager._update_contact_trust(1, "a@b.com", APPROVAL_DELTA, "approved")
        args = conn.fetchrow.call_args[0]
        # $1=user_id, $2=contact_email, $3=delta, $4=GLOBAL_CAP, $5=approved, $6=rejected, $7=edits
        assert args[1] == 1  # user_id
        assert args[2] == "a@b.com"  # contact_email
        assert args[3] == APPROVAL_DELTA
        assert args[4] == GLOBAL_CAP  # always GLOBAL_CAP for contact trust
        assert args[5] == 1  # approved
        assert args[6] == 0  # rejected
        assert args[7] == 0  # edits

    async def test_rejected_params(self, trust_manager, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.return_value = {"score": 0.0}
        await trust_manager._update_contact_trust(1, "a@b.com", REJECTION_DELTA, "rejected")
        args = conn.fetchrow.call_args[0]
        assert args[3] == REJECTION_DELTA
        assert args[5] == 0  # approved
        assert args[6] == 1  # rejected
        assert args[7] == 0  # edits

    async def test_minor_edit_params(self, trust_manager, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.return_value = {"score": 0.48}
        await trust_manager._update_contact_trust(1, "a@b.com", MINOR_EDIT_DELTA, "minor_edit")
        args = conn.fetchrow.call_args[0]
        assert args[3] == MINOR_EDIT_DELTA
        assert args[5] == 0  # approved
        assert args[6] == 0  # rejected
        assert args[7] == 1  # edits

    async def test_major_edit_params(self, trust_manager, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.return_value = {"score": 0.40}
        await trust_manager._update_contact_trust(1, "a@b.com", MAJOR_EDIT_DELTA, "major_edit")
        args = conn.fetchrow.call_args[0]
        assert args[3] == MAJOR_EDIT_DELTA
        assert args[5] == 0  # approved
        assert args[6] == 0  # rejected
        assert args[7] == 1  # edits

    async def test_cap_always_global_cap(self, trust_manager, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.return_value = {"score": 0.05}
        await trust_manager._update_contact_trust(1, "a@b.com", APPROVAL_DELTA, "approved")
        args = conn.fetchrow.call_args[0]
        assert args[4] == GLOBAL_CAP

    async def test_returns_new_score(self, trust_manager, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.return_value = {"score": 0.92}
        result = await trust_manager._update_contact_trust(1, "a@b.com", APPROVAL_DELTA, "approved")
        assert result == 0.92


# ---------------------------------------------------------------------------
# Module-level constants sanity checks
# ---------------------------------------------------------------------------


class TestModuleConstants:
    """Verify the module constants have expected values."""

    def test_approval_delta(self):
        assert APPROVAL_DELTA == 0.05

    def test_minor_edit_delta(self):
        assert MINOR_EDIT_DELTA == -0.02

    def test_major_edit_delta(self):
        assert MAJOR_EDIT_DELTA == -0.10

    def test_rejection_delta(self):
        assert REJECTION_DELTA == -0.20

    def test_global_cap(self):
        assert GLOBAL_CAP == 0.95
