"""High-level cost tracker for LLM API usage.

Provides a simple API for recording usage and getting cost summaries.
Integrates with the pricing module for cost calculation.
"""

import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from zetherion_ai.costs.storage import CostStorage, UsageRecord
from zetherion_ai.logging import get_logger
from zetherion_ai.models.pricing import get_cost

log = get_logger("zetherion_ai.costs.tracker")


@dataclass
class UsageContext:
    """Context for tracking a single API call."""

    provider: str
    model: str
    task_type: str | None = None
    user_id: str | None = None

    # Filled in during tracking
    _start_time: float | None = None
    _tokens_input: int = 0
    _tokens_output: int = 0
    _success: bool = True
    _error_message: str | None = None
    _rate_limit_hit: bool = False


class CostTracker:
    """Tracks LLM API usage and costs.

    Provides both synchronous recording and a context manager for
    tracking calls with automatic latency measurement.
    """

    def __init__(
        self,
        storage: CostStorage | None = None,
        db_path: str | Path = "data/costs.db",
        budget_alert_threshold: float | None = None,
    ):
        """Initialize the cost tracker.

        Args:
            storage: Optional CostStorage instance (creates one if not provided).
            db_path: Path to the SQLite database (if storage not provided).
            budget_alert_threshold: Optional daily budget alert threshold in USD.
        """
        self._storage = storage or CostStorage(db_path)
        self._budget_threshold = budget_alert_threshold

        # In-memory counters for current session
        self._session_start = datetime.now()
        self._session_costs: dict[str, float] = {}
        self._session_requests: dict[str, int] = {}

    @contextmanager
    def track(
        self,
        provider: str,
        model: str,
        task_type: str | None = None,
        user_id: str | None = None,
    ) -> Generator[UsageContext, None, None]:
        """Context manager for tracking an API call.

        Usage:
            with tracker.track("openai", "gpt-4o", task_type="code") as ctx:
                response = await openai_call(...)
                ctx._tokens_input = response.usage.prompt_tokens
                ctx._tokens_output = response.usage.completion_tokens

        Args:
            provider: The provider name.
            model: The model identifier.
            task_type: Optional task type for categorization.
            user_id: Optional user identifier.

        Yields:
            UsageContext to fill in token counts.
        """
        ctx = UsageContext(
            provider=provider,
            model=model,
            task_type=task_type,
            user_id=user_id,
        )
        ctx._start_time = time.perf_counter()

        try:
            yield ctx
        except Exception as e:
            ctx._success = False
            ctx._error_message = str(e)
            # Check if this was a rate limit error
            error_lower = str(e).lower()
            if "rate" in error_lower and "limit" in error_lower:
                ctx._rate_limit_hit = True
            raise
        finally:
            # Calculate latency
            latency_ms = None
            if ctx._start_time:
                latency_ms = int((time.perf_counter() - ctx._start_time) * 1000)

            # Calculate cost
            cost_result = get_cost(
                ctx.model,
                ctx._tokens_input,
                ctx._tokens_output,
            )

            # Record usage
            self.record(
                provider=ctx.provider,
                model=ctx.model,
                tokens_input=ctx._tokens_input,
                tokens_output=ctx._tokens_output,
                cost_usd=cost_result.cost_usd,
                cost_estimated=cost_result.estimated,
                task_type=ctx.task_type,
                user_id=ctx.user_id,
                latency_ms=latency_ms,
                rate_limit_hit=ctx._rate_limit_hit,
                success=ctx._success,
                error_message=ctx._error_message,
            )

    def record(
        self,
        provider: str,
        model: str,
        tokens_input: int,
        tokens_output: int,
        cost_usd: float | None = None,
        cost_estimated: bool = False,
        task_type: str | None = None,
        user_id: str | None = None,
        latency_ms: int | None = None,
        rate_limit_hit: bool = False,
        success: bool = True,
        error_message: str | None = None,
    ) -> int:
        """Record a usage event.

        Args:
            provider: The provider name.
            model: The model identifier.
            tokens_input: Number of input tokens.
            tokens_output: Number of output tokens.
            cost_usd: Optional pre-calculated cost (calculates if not provided).
            cost_estimated: Whether the cost was estimated.
            task_type: Optional task type.
            user_id: Optional user identifier.
            latency_ms: Optional latency in milliseconds.
            rate_limit_hit: Whether a rate limit was hit.
            success: Whether the call succeeded.
            error_message: Optional error message.

        Returns:
            The ID of the recorded usage.
        """
        # Calculate cost if not provided
        if cost_usd is None:
            cost_result = get_cost(model, tokens_input, tokens_output)
            cost_usd = cost_result.cost_usd
            cost_estimated = cost_result.estimated

        record = UsageRecord(
            provider=provider,
            model=model,
            task_type=task_type,
            user_id=user_id,
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            cost_usd=cost_usd,
            cost_estimated=cost_estimated,
            latency_ms=latency_ms,
            rate_limit_hit=rate_limit_hit,
            success=success,
            error_message=error_message,
        )

        record_id = self._storage.record_usage(record)

        # Update session counters
        self._session_costs[provider] = self._session_costs.get(provider, 0.0) + cost_usd
        self._session_requests[provider] = self._session_requests.get(provider, 0) + 1

        # Log the usage
        log.info(
            "usage_recorded",
            provider=provider,
            model=model,
            task_type=task_type,
            tokens_in=tokens_input,
            tokens_out=tokens_output,
            cost_usd=round(cost_usd, 6),
            estimated=cost_estimated,
            latency_ms=latency_ms,
        )

        # Check budget threshold
        if self._budget_threshold:
            self._check_budget_threshold()

        return record_id

    def _check_budget_threshold(self) -> None:
        """Check if daily spending exceeds the budget threshold."""
        today_cost = self.get_today_cost()
        if today_cost >= self._budget_threshold:  # type: ignore
            log.warning(
                "budget_threshold_exceeded",
                today_cost=round(today_cost, 2),
                threshold=self._budget_threshold,
            )

    def get_today_cost(self) -> float:
        """Get total cost for today.

        Returns:
            Total cost in USD for today.
        """
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow = today + timedelta(days=1)
        return self._storage.get_total_cost(today, tomorrow)

    def get_session_costs(self) -> dict[str, float]:
        """Get costs for the current session.

        Returns:
            Dict mapping provider to session cost.
        """
        return dict(self._session_costs)

    def get_session_requests(self) -> dict[str, int]:
        """Get request counts for the current session.

        Returns:
            Dict mapping provider to request count.
        """
        return dict(self._session_requests)

    def get_cost_by_provider(
        self,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> dict[str, float]:
        """Get costs grouped by provider.

        Args:
            start_date: Optional start date (defaults to 30 days ago).
            end_date: Optional end date (defaults to now).

        Returns:
            Dict mapping provider to total cost.
        """
        if start_date is None:
            start_date = datetime.now() - timedelta(days=30)
        if end_date is None:
            end_date = datetime.now()

        return self._storage.get_total_cost_by_provider(start_date, end_date)

    def get_cost_by_task_type(
        self,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> dict[str, float]:
        """Get costs grouped by task type.

        Args:
            start_date: Optional start date (defaults to 30 days ago).
            end_date: Optional end date (defaults to now).

        Returns:
            Dict mapping task type to total cost.
        """
        if start_date is None:
            start_date = datetime.now() - timedelta(days=30)
        if end_date is None:
            end_date = datetime.now()

        records = self._storage.get_usage_by_date_range(start_date, end_date)

        costs: dict[str, float] = {}
        for record in records:
            task_type = record.task_type or "unknown"
            costs[task_type] = costs.get(task_type, 0.0) + record.cost_usd

        return costs

    def get_daily_summary(self, date: str | None = None) -> dict[str, Any]:
        """Get a summary for a specific day.

        Args:
            date: Date in YYYY-MM-DD format (defaults to today).

        Returns:
            Summary dict with totals and breakdowns.
        """
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        summaries = self._storage.get_daily_summary(date)

        total_cost = sum(s["total_cost_usd"] for s in summaries)
        total_tokens_input = sum(s["total_tokens_input"] for s in summaries)
        total_tokens_output = sum(s["total_tokens_output"] for s in summaries)
        total_requests = sum(s["request_count"] for s in summaries)
        total_errors = sum(s["error_count"] for s in summaries)

        by_provider: dict[str, float] = {}
        for s in summaries:
            provider = s["provider"]
            by_provider[provider] = by_provider.get(provider, 0.0) + s["total_cost_usd"]

        return {
            "date": date,
            "total_cost_usd": round(total_cost, 4),
            "total_tokens_input": total_tokens_input,
            "total_tokens_output": total_tokens_output,
            "total_requests": total_requests,
            "total_errors": total_errors,
            "by_provider": {k: round(v, 4) for k, v in by_provider.items()},
            "details": summaries,
        }

    def get_rate_limit_stats(
        self,
        days: int = 7,
    ) -> dict[str, Any]:
        """Get rate limit statistics.

        Args:
            days: Number of days to look back.

        Returns:
            Stats dict with rate limit counts by provider.
        """
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        total = self._storage.get_rate_limit_count(start_date, end_date)

        # Get per-provider counts
        by_provider: dict[str, int] = {}
        for provider in ["openai", "anthropic", "google", "ollama"]:
            count = self._storage.get_rate_limit_count(start_date, end_date, provider)
            if count > 0:
                by_provider[provider] = count

        return {
            "period_days": days,
            "total_rate_limits": total,
            "by_provider": by_provider,
        }

    def get_monthly_report(
        self,
        year: int | None = None,
        month: int | None = None,
    ) -> dict[str, Any]:
        """Get a monthly cost report.

        Args:
            year: Year (defaults to current year).
            month: Month 1-12 (defaults to current month).

        Returns:
            Report dict with totals and daily breakdown.
        """
        if year is None:
            year = datetime.now().year
        if month is None:
            month = datetime.now().month

        # Calculate date range
        start_date = datetime(year, month, 1)
        end_date = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)

        # Get totals
        total_cost = self._storage.get_total_cost(start_date, end_date)
        by_provider = self._storage.get_total_cost_by_provider(start_date, end_date)

        # Get daily breakdown
        daily_costs: dict[str, float] = {}
        current = start_date
        while current < end_date:
            date_str = current.strftime("%Y-%m-%d")
            summaries = self._storage.get_daily_summary(date_str)
            day_cost = sum(s["total_cost_usd"] for s in summaries)
            if day_cost > 0:
                daily_costs[date_str] = round(day_cost, 4)
            current += timedelta(days=1)

        return {
            "year": year,
            "month": month,
            "total_cost_usd": round(total_cost, 2),
            "by_provider": {k: round(v, 2) for k, v in by_provider.items()},
            "daily_costs": daily_costs,
        }
