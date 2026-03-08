from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from zetherion_ai.discord.e2e_lease import (
    TOPIC_PREFIX,
    DiscordE2ELease,
    _parse_datetime,
)


def _lease() -> DiscordE2ELease:
    return DiscordE2ELease(
        run_id="discord-run",
        mode="windows_prod_canary",
        target_bot_id=2222,
        author_id=1111,
        created_at=datetime.now(tz=UTC),
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        guild_id=123,
        category_id=456,
        channel_prefix="zeth-e2e",
    )


def test_from_topic_returns_none_for_invalid_json() -> None:
    assert DiscordE2ELease.from_topic(f"{TOPIC_PREFIX}{{not-json") is None


def test_from_topic_returns_none_for_non_dict_payload() -> None:
    assert DiscordE2ELease.from_topic(f"{TOPIC_PREFIX}[1,2,3]") is None


def test_from_topic_returns_none_for_invalid_payload_shape() -> None:
    assert DiscordE2ELease.from_topic(f'{TOPIC_PREFIX}{"{\"run_id\": \"x\"}"}') is None


@pytest.mark.parametrize(
    ("name", "channel_prefix"),
    [
        (None, "zeth-e2e"),
        ("other-prefix-run", "zeth-e2e"),
        ("zeth-e2e-not-a-lease", "zeth-e2e"),
        ("zeth-e2e-run", ""),
    ],
)
def test_from_thread_name_returns_none_for_missing_or_mismatched_inputs(
    name: str | None, channel_prefix: str
) -> None:
    assert (
        DiscordE2ELease.from_thread_name(name, channel_prefix=channel_prefix, guild_id=123) is None
    )


def test_from_thread_name_returns_none_for_invalid_timestamp() -> None:
    name = "zeth-e2e-m-lr-r-run-a-1111-t-2222-e-999999999999999999999999"
    assert DiscordE2ELease.from_thread_name(name, channel_prefix="zeth-e2e", guild_id=123) is None


def test_parse_datetime_treats_naive_values_as_utc() -> None:
    parsed = _parse_datetime("2026-03-08T12:34:56")
    assert parsed.tzinfo == UTC
    assert parsed == datetime(2026, 3, 8, 12, 34, 56, tzinfo=UTC)


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_parse_datetime_rejects_empty_values(raw: object) -> None:
    with pytest.raises(ValueError, match="timestamp must be a non-empty string"):
        _parse_datetime(raw)


def test_from_channel_metadata_prefers_topic_over_thread_name() -> None:
    lease = _lease()
    parsed = DiscordE2ELease.from_channel_metadata(
        topic=lease.to_topic(),
        name="zeth-e2e-m-lr-r-other-a-9999-t-8888-e-1",
        channel_prefix="zeth-e2e",
        guild_id=123,
    )

    assert parsed == lease
