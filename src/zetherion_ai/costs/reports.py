"""Cost report generation.

Generates formatted reports for display in Discord or logs.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from zetherion_ai.costs.aggregator import CostAggregator
from zetherion_ai.costs.storage import CostStorage
from zetherion_ai.costs.tracker import CostTracker
from zetherion_ai.logging import get_logger

log = get_logger("zetherion_ai.costs.reports")


@dataclass
class DailyReport:
    """Daily cost report."""

    date: str
    total_cost_usd: float
    by_provider: dict[str, float]
    by_task_type: dict[str, float]
    top_models: list[tuple[str, float]]
    request_count: int
    error_count: int
    rate_limit_count: int
    estimated_cost_count: int


@dataclass
class MonthlyReport:
    """Monthly cost report."""

    year: int
    month: int
    total_cost_usd: float
    by_provider: dict[str, float]
    daily_costs: list[tuple[str, float]]
    top_models: list[tuple[str, float]]
    top_task_types: list[tuple[str, float]]
    projected_cost: float
    avg_daily_cost: float


class CostReportGenerator:
    """Generates formatted cost reports."""

    def __init__(
        self,
        storage: CostStorage,
        tracker: CostTracker | None = None,
    ):
        """Initialize the report generator.

        Args:
            storage: CostStorage instance.
            tracker: Optional CostTracker for session data.
        """
        self._storage = storage
        self._tracker = tracker
        self._aggregator = CostAggregator(storage)

    def generate_daily_report(
        self,
        date: datetime | None = None,
    ) -> DailyReport:
        """Generate a daily cost report.

        Args:
            date: Date for the report (defaults to today).

        Returns:
            DailyReport with all metrics.
        """
        if date is None:
            date = datetime.now()

        start_date = date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = start_date + timedelta(days=1)
        date_str = start_date.strftime("%Y-%m-%d")

        # Get aggregations
        by_provider = self._aggregator.aggregate_by_provider(start_date, end_date)
        by_task = self._aggregator.aggregate_by_task_type(start_date, end_date)
        top_models = self._aggregator.get_top_models_by_cost(start_date, end_date, 5)

        # Calculate totals
        total_cost = sum(agg.total_cost_usd for agg in by_provider.values())
        request_count = sum(agg.request_count for agg in by_provider.values())
        error_count = sum(agg.error_count for agg in by_provider.values())
        rate_limit_count = sum(agg.rate_limit_count for agg in by_provider.values())
        estimated_count = sum(agg.estimated_cost_count for agg in by_provider.values())

        return DailyReport(
            date=date_str,
            total_cost_usd=round(total_cost, 4),
            by_provider={k: round(v.total_cost_usd, 4) for k, v in by_provider.items()},
            by_task_type={k: round(v.total_cost_usd, 4) for k, v in by_task.items()},
            top_models=[(m, round(c, 4)) for m, c in top_models],
            request_count=request_count,
            error_count=error_count,
            rate_limit_count=rate_limit_count,
            estimated_cost_count=estimated_count,
        )

    def generate_monthly_report(
        self,
        year: int | None = None,
        month: int | None = None,
    ) -> MonthlyReport:
        """Generate a monthly cost report.

        Args:
            year: Year (defaults to current year).
            month: Month 1-12 (defaults to current month).

        Returns:
            MonthlyReport with all metrics.
        """
        if year is None:
            year = datetime.now().year
        if month is None:
            month = datetime.now().month

        # Calculate date range
        start_date = datetime(year, month, 1)
        end_date = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)

        # Get aggregations
        by_provider = self._aggregator.aggregate_by_provider(start_date, end_date)
        by_day = self._aggregator.aggregate_by_day(start_date, end_date)
        top_models = self._aggregator.get_top_models_by_cost(start_date, end_date, 10)
        top_tasks = self._aggregator.get_top_task_types_by_cost(start_date, end_date, 10)

        # Calculate totals
        total_cost = sum(agg.total_cost_usd for agg in by_provider.values())

        # Daily costs (sorted by date)
        daily_costs = sorted(
            [(date, agg.total_cost_usd) for date, agg in by_day.items()],
            key=lambda x: x[0],
        )

        # Average daily cost and projection
        days_with_data = len(daily_costs)
        avg_daily = total_cost / days_with_data if days_with_data > 0 else 0

        # Project for full month
        days_in_month = (end_date - start_date).days
        projected = avg_daily * days_in_month

        return MonthlyReport(
            year=year,
            month=month,
            total_cost_usd=round(total_cost, 2),
            by_provider={k: round(v.total_cost_usd, 2) for k, v in by_provider.items()},
            daily_costs=[(d, round(c, 4)) for d, c in daily_costs],
            top_models=[(m, round(c, 4)) for m, c in top_models],
            top_task_types=[(t, round(c, 4)) for t, c in top_tasks],
            projected_cost=round(projected, 2),
            avg_daily_cost=round(avg_daily, 2),
        )

    def format_daily_report_discord(
        self,
        report: DailyReport,
    ) -> str:
        """Format a daily report for Discord.

        Args:
            report: The DailyReport to format.

        Returns:
            Formatted string for Discord message.
        """
        lines = [
            f"**Daily Cost Report - {report.date}**",
            "",
            f"Total Cost: **${report.total_cost_usd:.4f}**",
            f"Requests: {report.request_count}",
        ]

        if report.error_count > 0:
            lines.append(f"Errors: {report.error_count}")

        if report.rate_limit_count > 0:
            lines.append(f"Rate Limits: {report.rate_limit_count}")

        if report.estimated_cost_count > 0:
            lines.append(f"Estimated Costs: {report.estimated_cost_count}")

        # By provider
        if report.by_provider:
            lines.append("")
            lines.append("**By Provider:**")
            for provider, cost in sorted(
                report.by_provider.items(), key=lambda x: x[1], reverse=True
            ):
                lines.append(f"  {provider}: ${cost:.4f}")

        # Top models
        if report.top_models:
            lines.append("")
            lines.append("**Top Models:**")
            for model, cost in report.top_models[:5]:
                lines.append(f"  {model}: ${cost:.4f}")

        return "\n".join(lines)

    def format_monthly_report_discord(
        self,
        report: MonthlyReport,
    ) -> str:
        """Format a monthly report for Discord.

        Args:
            report: The MonthlyReport to format.

        Returns:
            Formatted string for Discord message.
        """
        month_name = datetime(report.year, report.month, 1).strftime("%B %Y")

        lines = [
            f"**Monthly Cost Report - {month_name}**",
            "",
            f"Total Cost: **${report.total_cost_usd:.2f}**",
            f"Average Daily: ${report.avg_daily_cost:.2f}",
            f"Projected: ${report.projected_cost:.2f}",
        ]

        # By provider
        if report.by_provider:
            lines.append("")
            lines.append("**By Provider:**")
            for provider, cost in sorted(
                report.by_provider.items(), key=lambda x: x[1], reverse=True
            ):
                lines.append(f"  {provider}: ${cost:.2f}")

        # Top models
        if report.top_models:
            lines.append("")
            lines.append("**Top 5 Models by Cost:**")
            for model, cost in report.top_models[:5]:
                lines.append(f"  {model}: ${cost:.2f}")

        # Top task types
        if report.top_task_types:
            lines.append("")
            lines.append("**Top Task Types:**")
            for task, cost in report.top_task_types[:5]:
                lines.append(f"  {task}: ${cost:.2f}")

        return "\n".join(lines)

    def format_session_summary(self) -> str:
        """Format a summary of the current session.

        Returns:
            Formatted string for session summary.
        """
        if not self._tracker:
            return "No session tracker available."

        costs = self._tracker.get_session_costs()
        request_counts = self._tracker.get_session_requests()

        if not costs:
            return "No usage recorded this session."

        total_cost = sum(costs.values())
        total_requests = sum(request_counts.values())

        lines = [
            "**Session Summary**",
            "",
            f"Total Cost: **${total_cost:.4f}**",
            f"Total Requests: {total_requests}",
        ]

        if len(costs) > 1:
            lines.append("")
            lines.append("**By Provider:**")
            for provider, cost in sorted(costs.items(), key=lambda x: x[1], reverse=True):
                req_count = request_counts.get(provider, 0)
                lines.append(f"  {provider}: ${cost:.4f} ({req_count} requests)")

        return "\n".join(lines)

    def generate_budget_alert(
        self,
        threshold: float,
        current: float,
    ) -> str:
        """Generate a budget alert message.

        Args:
            threshold: The budget threshold.
            current: Current spending.

        Returns:
            Alert message string.
        """
        pct = (current / threshold) * 100
        return (
            f"**Budget Alert**\n\n"
            f"Daily spending has reached **${current:.2f}** "
            f"({pct:.0f}% of ${threshold:.2f} threshold)."
        )
