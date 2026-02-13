"""Unit tests for the utils module.

Tests the timed_operation async context manager for timing measurement
and optional structured logging.
"""

import asyncio
from unittest.mock import MagicMock

import pytest

from zetherion_ai.utils import split_text_chunks, timed_operation


class TestTimedOperation:
    """Tests for the timed_operation async context manager."""

    async def test_yields_dict_with_elapsed_ms(self) -> None:
        """timed_operation should yield a dict that gets populated with elapsed_ms."""
        async with timed_operation("test_op") as timing:
            await asyncio.sleep(0.01)

        assert "elapsed_ms" in timing
        assert isinstance(timing["elapsed_ms"], float)

    async def test_elapsed_ms_is_positive(self) -> None:
        """elapsed_ms should be greater than zero after the block exits."""
        async with timed_operation("test_op") as timing:
            await asyncio.sleep(0.01)

        assert timing["elapsed_ms"] > 0

    async def test_dict_is_empty_inside_context(self) -> None:
        """The yielded dict should be empty while inside the context block."""
        async with timed_operation("test_op") as timing:
            assert "elapsed_ms" not in timing

    async def test_log_info_called_when_log_provided(self) -> None:
        """When a log is provided, log.info should be called with timing data."""
        mock_log = MagicMock()

        async with timed_operation("my_operation", log=mock_log) as timing:
            await asyncio.sleep(0.01)

        mock_log.info.assert_called_once()
        call_args = mock_log.info.call_args
        assert call_args[0][0] == "my_operation"
        assert "duration_ms" in call_args[1]
        assert call_args[1]["duration_ms"] == timing["elapsed_ms"]

    async def test_no_logging_when_log_is_none(self) -> None:
        """When log is None, no logging should happen."""
        async with timed_operation("test_op", log=None) as timing:
            await asyncio.sleep(0.01)

        # No exception should be raised and elapsed_ms should be set
        assert timing["elapsed_ms"] > 0

    async def test_extra_kwargs_forwarded_to_log(self) -> None:
        """Extra keyword arguments should be forwarded to the log.info call."""
        mock_log = MagicMock()

        async with timed_operation(
            "my_op", log=mock_log, intent="complex_task", user_id=42
        ) as timing:
            pass

        call_kwargs = mock_log.info.call_args[1]
        assert call_kwargs["intent"] == "complex_task"
        assert call_kwargs["user_id"] == 42
        assert call_kwargs["duration_ms"] == timing["elapsed_ms"]

    async def test_timing_recorded_on_exception(self) -> None:
        """elapsed_ms should still be recorded even when the block raises."""
        timing = {}
        with pytest.raises(ValueError, match="test error"):
            async with timed_operation("failing_op") as timing:
                await asyncio.sleep(0.01)
                raise ValueError("test error")

        assert "elapsed_ms" in timing
        assert timing["elapsed_ms"] > 0

    async def test_log_called_on_exception(self) -> None:
        """log.info should still be called when the block raises an exception."""
        mock_log = MagicMock()

        with pytest.raises(RuntimeError):
            async with timed_operation("failing_op", log=mock_log):
                raise RuntimeError("boom")

        mock_log.info.assert_called_once()
        assert "duration_ms" in mock_log.info.call_args[1]

    async def test_elapsed_ms_is_rounded(self) -> None:
        """elapsed_ms should be rounded to 2 decimal places."""
        async with timed_operation("test_op") as timing:
            await asyncio.sleep(0.01)

        # Verify rounding: the string representation should have at most 2 decimal places
        decimal_str = str(timing["elapsed_ms"])
        if "." in decimal_str:
            decimal_places = len(decimal_str.split(".")[1])
            assert decimal_places <= 2


class TestSplitTextChunks:
    """Tests for split_text_chunks helper."""

    def test_returns_single_chunk_when_under_limit(self) -> None:
        assert split_text_chunks("hello", max_length=10) == ["hello"]

    def test_splits_on_newline_when_possible(self) -> None:
        content = "a" * 5 + "\n" + "b" * 5
        chunks = split_text_chunks(content, max_length=8)
        assert chunks == ["aaaaa", "bbbbb"]

    def test_hard_splits_long_line(self) -> None:
        content = "x" * 25
        chunks = split_text_chunks(content, max_length=10)
        assert chunks == ["x" * 10, "x" * 10, "x" * 5]

    def test_raises_for_non_positive_limit(self) -> None:
        with pytest.raises(ValueError, match="max_length"):
            split_text_chunks("hello", max_length=0)
