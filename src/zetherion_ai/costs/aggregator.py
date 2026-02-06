"""Cost aggregation utilities.

Provides functions for aggregating cost data across different dimensions
(time, provider, task type, user) for reporting and analysis.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from zetherion_ai.costs.storage import CostStorage, UsageRecord
from zetherion_ai.logging import get_logger

log = get_logger("zetherion_ai.costs.aggregator")


@dataclass
class CostAggregate:
    """Aggregated cost data."""

    total_cost_usd: float
    total_tokens_input: int
    total_tokens_output: int
    request_count: int
    error_count: int
    estimated_cost_count: int
    rate_limit_count: int
    avg_latency_ms: float | None


class CostAggregator:
    """Aggregates cost data for analysis and reporting."""

    def __init__(self, storage: CostStorage):
        """Initialize the aggregator.

        Args:
            storage: CostStorage instance to read from.
        """
        self._storage = storage

    def aggregate_by_provider(
        self,
        start_date: datetime,
        end_date: datetime,
    ) -> dict[str, CostAggregate]:
        """Aggregate costs by provider.

        Args:
            start_date: Start of the date range.
            end_date: End of the date range.

        Returns:
            Dict mapping provider to CostAggregate.
        """
        records = self._storage.get_usage_by_date_range(start_date, end_date)
        return self._aggregate_records(records, key_fn=lambda r: r.provider)

    def aggregate_by_model(
        self,
        start_date: datetime,
        end_date: datetime,
        provider: str | None = None,
    ) -> dict[str, CostAggregate]:
        """Aggregate costs by model.

        Args:
            start_date: Start of the date range.
            end_date: End of the date range.
            provider: Optional provider filter.

        Returns:
            Dict mapping model to CostAggregate.
        """
        records = self._storage.get_usage_by_date_range(start_date, end_date, provider=provider)
        return self._aggregate_records(records, key_fn=lambda r: r.model)

    def aggregate_by_task_type(
        self,
        start_date: datetime,
        end_date: datetime,
    ) -> dict[str, CostAggregate]:
        """Aggregate costs by task type.

        Args:
            start_date: Start of the date range.
            end_date: End of the date range.

        Returns:
            Dict mapping task type to CostAggregate.
        """
        records = self._storage.get_usage_by_date_range(start_date, end_date)
        return self._aggregate_records(records, key_fn=lambda r: r.task_type or "unknown")

    def aggregate_by_user(
        self,
        start_date: datetime,
        end_date: datetime,
    ) -> dict[str, CostAggregate]:
        """Aggregate costs by user.

        Args:
            start_date: Start of the date range.
            end_date: End of the date range.

        Returns:
            Dict mapping user_id to CostAggregate.
        """
        records = self._storage.get_usage_by_date_range(start_date, end_date)
        return self._aggregate_records(records, key_fn=lambda r: r.user_id or "unknown")

    def aggregate_by_day(
        self,
        start_date: datetime,
        end_date: datetime,
    ) -> dict[str, CostAggregate]:
        """Aggregate costs by day.

        Args:
            start_date: Start of the date range.
            end_date: End of the date range.

        Returns:
            Dict mapping date (YYYY-MM-DD) to CostAggregate.
        """
        records = self._storage.get_usage_by_date_range(start_date, end_date)

        def date_key(r: UsageRecord) -> str:
            if r.timestamp:
                return r.timestamp.strftime("%Y-%m-%d")
            return "unknown"

        return self._aggregate_records(records, key_fn=date_key)

    def aggregate_by_hour(
        self,
        date: datetime,
    ) -> dict[int, CostAggregate]:
        """Aggregate costs by hour for a specific day.

        Args:
            date: The date to analyze.

        Returns:
            Dict mapping hour (0-23) to CostAggregate.
        """
        start_date = date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = start_date + timedelta(days=1)
        records = self._storage.get_usage_by_date_range(start_date, end_date)

        def hour_key(r: UsageRecord) -> int:
            if r.timestamp:
                return r.timestamp.hour
            return 0

        return self._aggregate_records(records, key_fn=hour_key)

    def _aggregate_records(
        self,
        records: list[UsageRecord],
        key_fn: Any,
    ) -> dict[Any, CostAggregate]:
        """Aggregate records by a key function.

        Args:
            records: List of usage records.
            key_fn: Function to extract grouping key from a record.

        Returns:
            Dict mapping keys to CostAggregate.
        """
        groups: dict[Any, list[UsageRecord]] = defaultdict(list)
        for record in records:
            key = key_fn(record)
            groups[key].append(record)

        result: dict[Any, CostAggregate] = {}
        for key, group_records in groups.items():
            result[key] = self._compute_aggregate(group_records)

        return result

    def _compute_aggregate(self, records: list[UsageRecord]) -> CostAggregate:
        """Compute aggregate stats for a group of records.

        Args:
            records: List of usage records.

        Returns:
            CostAggregate with computed stats.
        """
        total_cost = sum(r.cost_usd for r in records)
        total_tokens_input = sum(r.tokens_input for r in records)
        total_tokens_output = sum(r.tokens_output for r in records)
        request_count = len(records)
        error_count = sum(1 for r in records if not r.success)
        estimated_count = sum(1 for r in records if r.cost_estimated)
        rate_limit_count = sum(1 for r in records if r.rate_limit_hit)

        # Calculate average latency (excluding None values)
        latencies = [r.latency_ms for r in records if r.latency_ms is not None]
        avg_latency = sum(latencies) / len(latencies) if latencies else None

        return CostAggregate(
            total_cost_usd=total_cost,
            total_tokens_input=total_tokens_input,
            total_tokens_output=total_tokens_output,
            request_count=request_count,
            error_count=error_count,
            estimated_cost_count=estimated_count,
            rate_limit_count=rate_limit_count,
            avg_latency_ms=avg_latency,
        )

    def get_top_models_by_cost(
        self,
        start_date: datetime,
        end_date: datetime,
        limit: int = 10,
    ) -> list[tuple[str, float]]:
        """Get the top models by cost.

        Args:
            start_date: Start of the date range.
            end_date: End of the date range.
            limit: Maximum number of models to return.

        Returns:
            List of (model_id, total_cost) tuples, sorted by cost descending.
        """
        by_model = self.aggregate_by_model(start_date, end_date)
        sorted_models = sorted(
            by_model.items(),
            key=lambda x: x[1].total_cost_usd,
            reverse=True,
        )
        return [(model, agg.total_cost_usd) for model, agg in sorted_models[:limit]]

    def get_top_task_types_by_cost(
        self,
        start_date: datetime,
        end_date: datetime,
        limit: int = 10,
    ) -> list[tuple[str, float]]:
        """Get the top task types by cost.

        Args:
            start_date: Start of the date range.
            end_date: End of the date range.
            limit: Maximum number of task types to return.

        Returns:
            List of (task_type, total_cost) tuples, sorted by cost descending.
        """
        by_task = self.aggregate_by_task_type(start_date, end_date)
        sorted_tasks = sorted(
            by_task.items(),
            key=lambda x: x[1].total_cost_usd,
            reverse=True,
        )
        return [(task, agg.total_cost_usd) for task, agg in sorted_tasks[:limit]]

    def get_cost_trend(
        self,
        days: int = 30,
    ) -> list[tuple[str, float]]:
        """Get daily cost trend.

        Args:
            days: Number of days to look back.

        Returns:
            List of (date, cost) tuples in chronological order.
        """
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        by_day = self.aggregate_by_day(start_date, end_date)

        # Fill in missing days with zero
        result: list[tuple[str, float]] = []
        current = start_date
        while current <= end_date:
            date_str = current.strftime("%Y-%m-%d")
            if date_str in by_day:
                result.append((date_str, by_day[date_str].total_cost_usd))
            else:
                result.append((date_str, 0.0))
            current += timedelta(days=1)

        return result

    def calculate_projected_monthly_cost(
        self,
        days_to_project: int = 30,
    ) -> float:
        """Calculate projected monthly cost based on recent usage.

        Uses the last 7 days to project the monthly cost.

        Args:
            days_to_project: Number of days to project (default 30).

        Returns:
            Projected cost in USD.
        """
        end_date = datetime.now()
        start_date = end_date - timedelta(days=7)

        total = self._storage.get_total_cost(start_date, end_date)
        daily_avg = total / 7

        return daily_avg * days_to_project
