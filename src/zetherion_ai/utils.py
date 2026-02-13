"""Shared utilities for Zetherion AI."""

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog


@asynccontextmanager
async def timed_operation(
    name: str,
    log: structlog.stdlib.BoundLogger | None = None,
    **extra: Any,
) -> AsyncIterator[dict[str, Any]]:
    """Context manager that measures elapsed time for an async operation.

    Usage::

        async with timed_operation("my_op", log=log) as timing:
            await do_something()
        print(timing["elapsed_ms"])

    Args:
        name: A label for the operation (used in log messages).
        log: Optional structlog logger; if provided, an info-level message
             is emitted on exit.
        **extra: Additional key-value pairs forwarded to the log call.

    Yields:
        A mutable dict that will contain ``elapsed_ms`` after the block exits.
    """
    start = time.perf_counter()
    result: dict[str, Any] = {}
    try:
        yield result
    finally:
        result["elapsed_ms"] = round((time.perf_counter() - start) * 1000, 2)
        if log:
            log.info(name, duration_ms=result["elapsed_ms"], **extra)


def split_text_chunks(content: str, max_length: int) -> list[str]:
    """Split text into chunks that are each <= ``max_length``.

    Prefers splitting at newline boundaries, but hard-splits long lines when
    needed so every chunk always respects Discord's message length limit.
    """
    if max_length <= 0:
        raise ValueError("max_length must be > 0")

    if len(content) <= max_length:
        return [content] if content else []

    chunks: list[str] = []
    remaining = content

    while remaining:
        if len(remaining) <= max_length:
            chunks.append(remaining)
            break

        split_at = remaining.rfind("\n", 0, max_length + 1)
        if split_at > 0:
            chunk = remaining[:split_at]
            if chunk:
                chunks.append(chunk)
            remaining = remaining[split_at + 1 :]
            continue

        chunks.append(remaining[:max_length])
        remaining = remaining[max_length:]

    return [chunk for chunk in chunks if chunk]
