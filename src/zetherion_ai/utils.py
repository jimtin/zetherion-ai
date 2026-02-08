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
