"""Pure aggregation functions for building personality profiles.

Merges individual PersonalitySignal observations into evolving
PersonalityProfile records.  Zero I/O — all state is passed in/out.
"""

from __future__ import annotations

from datetime import datetime

from zetherion_ai.personal.models import (
    MAX_LIST_ITEMS,
    AggregatedCommunication,
    AggregatedRelationship,
    AggregatedWritingStyle,
    PersonalityProfile,
)
from zetherion_ai.routing.personality import PersonalitySignal

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _increment_distribution(dist: dict[str, int], key: str) -> dict[str, int]:
    """Return a new distribution dict with *key* incremented by 1."""
    out = dict(dist)
    out[key] = out.get(key, 0) + 1
    return out


def _mode_of(dist: dict[str, int], fallback: str = "") -> str:
    """Return the key with the highest count, or *fallback*."""
    if not dist:
        return fallback
    return max(dist, key=lambda k: dist[k])


def _ema(old: float, new: float, n: int) -> float:
    """Exponential moving average with adaptive alpha.

    alpha = min(0.3, 2 / (n + 1))  — first observations have full weight,
    converging toward 0.3 as *n* grows.
    """
    alpha = min(0.3, 2.0 / (n + 1))
    return old * (1 - alpha) + new * alpha


def _running_rate(old_rate: float, new_value: bool, n: int) -> float:
    """Update a running boolean rate: (old * n + new) / (n + 1)."""
    return (old_rate * n + float(new_value)) / (n + 1)


def _merge_string_list(existing: list[str], new_items: list[str]) -> list[str]:
    """Union + case-insensitive dedup, capped at MAX_LIST_ITEMS."""
    seen: set[str] = set()
    result: list[str] = []
    for item in [*existing, *new_items]:
        key = item.strip().lower()
        if key and key not in seen:
            seen.add(key)
            result.append(item.strip())
    return result[:MAX_LIST_ITEMS]


def _confidence(n: int) -> float:
    """Confidence curve: grows with observations, asymptotes at 0.95.

    min(0.95, 1 - 1 / (1 + 0.3 * n))
    """
    return min(0.95, 1.0 - 1.0 / (1.0 + 0.3 * n))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def aggregate_signal_into_profile(
    existing: PersonalityProfile,
    signal: PersonalitySignal,
) -> PersonalityProfile:
    """Merge one PersonalitySignal into an existing aggregated profile.

    Returns a **new** PersonalityProfile — the input is not mutated.
    """
    n = existing.observation_count
    now = datetime.now()

    # -- Writing style --
    ws = signal.writing_style
    old_ws = existing.writing_style

    formality_dist = _increment_distribution(
        old_ws.formality_distribution,
        ws.formality.value,
    )
    sentence_dist = _increment_distribution(
        old_ws.avg_sentence_length_distribution,
        ws.avg_sentence_length.value,
    )
    vocab_dist = _increment_distribution(
        old_ws.vocabulary_level_distribution,
        ws.vocabulary_level.value,
    )

    greeting_styles = _merge_string_list(
        old_ws.greeting_styles,
        [ws.greeting_style] if ws.greeting_style else [],
    )
    signoff_styles = _merge_string_list(
        old_ws.signoff_styles,
        [ws.signoff_style] if ws.signoff_style else [],
    )

    new_writing_style = AggregatedWritingStyle(
        formality_distribution=formality_dist,
        formality_mode=_mode_of(formality_dist, old_ws.formality_mode),
        avg_sentence_length_distribution=sentence_dist,
        avg_sentence_length_mode=_mode_of(sentence_dist, old_ws.avg_sentence_length_mode),
        greeting_rate=_running_rate(old_ws.greeting_rate, ws.uses_greeting, n),
        greeting_styles=greeting_styles,
        signoff_rate=_running_rate(old_ws.signoff_rate, ws.uses_signoff, n),
        signoff_styles=signoff_styles,
        emoji_rate=_running_rate(old_ws.emoji_rate, ws.uses_emoji, n),
        bullet_point_rate=_running_rate(old_ws.bullet_point_rate, ws.uses_bullet_points, n),
        vocabulary_level_distribution=vocab_dist,
        vocabulary_level_mode=_mode_of(vocab_dist, old_ws.vocabulary_level_mode),
    )

    # -- Communication profile --
    comm = signal.communication
    old_comm = existing.communication

    primary_dist = _increment_distribution(
        old_comm.primary_trait_distribution,
        comm.primary_trait.value,
    )
    secondary_dist = dict(old_comm.secondary_trait_distribution)
    if comm.secondary_trait is not None:
        secondary_dist = _increment_distribution(secondary_dist, comm.secondary_trait.value)
    tone_dist = _increment_distribution(
        old_comm.emotional_tone_distribution,
        comm.emotional_tone.value,
    )
    resp_signals = _merge_string_list(
        old_comm.responsiveness_signals,
        [comm.responsiveness_signal] if comm.responsiveness_signal else [],
    )

    new_communication = AggregatedCommunication(
        primary_trait_distribution=primary_dist,
        primary_trait_mode=_mode_of(primary_dist, old_comm.primary_trait_mode),
        secondary_trait_distribution=secondary_dist,
        emotional_tone_distribution=tone_dist,
        emotional_tone_mode=_mode_of(tone_dist, old_comm.emotional_tone_mode),
        assertiveness_ema=_ema(old_comm.assertiveness_ema, comm.assertiveness, n),
        responsiveness_signals=resp_signals,
    )

    # -- Relationship dynamics --
    rel = signal.relationship
    old_rel = existing.relationship

    power_dist = _increment_distribution(
        old_rel.power_dynamic_distribution,
        rel.power_dynamic.value,
    )
    rapport = _merge_string_list(old_rel.rapport_indicators, rel.rapport_indicators)

    new_relationship = AggregatedRelationship(
        familiarity_ema=_ema(old_rel.familiarity_ema, rel.familiarity, n),
        power_dynamic_distribution=power_dist,
        power_dynamic_mode=_mode_of(power_dist, old_rel.power_dynamic_mode),
        trust_level_ema=_ema(old_rel.trust_level_ema, rel.trust_level, n),
        rapport_indicators=rapport,
    )

    # -- Lists (commitments, expectations, preferences, schedule) --
    new_commitments = _merge_string_list(existing.commitments, signal.commitments_made)
    new_expectations = _merge_string_list(existing.expectations, signal.expectations_set)
    new_preferences = _merge_string_list(existing.preferences, signal.preferences_revealed)
    new_schedule = _merge_string_list(existing.schedule_signals, signal.schedule_signals)

    new_n = n + 1

    return PersonalityProfile(
        id=existing.id,
        user_id=existing.user_id,
        subject_email=existing.subject_email,
        subject_role=existing.subject_role,
        observation_count=new_n,
        writing_style=new_writing_style,
        communication=new_communication,
        relationship=new_relationship,
        commitments=new_commitments,
        expectations=new_expectations,
        preferences=new_preferences,
        schedule_signals=new_schedule,
        confidence=_confidence(new_n),
        first_observed=existing.first_observed,
        last_observed=now,
        updated_at=now,
    )
