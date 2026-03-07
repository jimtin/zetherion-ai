"""Unit tests for YouTube trust shadow-mode integration."""

from zetherion_ai.skills.youtube.models import ReplyCategory, TrustLevel
from zetherion_ai.skills.youtube.trust import TrustModel


def test_should_auto_approve_records_shadow_decision(monkeypatch) -> None:
    recorded: list[dict[str, object]] = []
    model = TrustModel(level=TrustLevel.GUIDED.value)

    monkeypatch.setattr(
        "zetherion_ai.skills.youtube.trust._record_youtube_trust_decision",
        lambda **kwargs: recorded.append(kwargs),
    )

    approved = model.should_auto_approve(ReplyCategory.THANK_YOU.value)

    assert approved is True
    assert recorded == [
        {
            "category": ReplyCategory.THANK_YOU.value,
            "auto_approved": True,
            "level": TrustLevel.GUIDED.value,
        }
    ]


def test_should_auto_approve_keeps_complaints_in_review_before_full_auto() -> None:
    model = TrustModel(level=TrustLevel.AUTONOMOUS.value)

    assert model.should_auto_approve(ReplyCategory.COMPLAINT.value) is False
