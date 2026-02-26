"""Recommendation generation for app watcher insights."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from zetherion_ai.api.tenant import TenantManager


@dataclass
class RecommendationCandidate:
    """In-memory recommendation candidate generated from detectors."""

    recommendation_type: str
    title: str
    description: str
    evidence: dict[str, Any]
    risk_class: str = "low"
    confidence: float = 0.6
    expected_impact: float | None = None


class RecommendationEngine:
    """Deterministic detector-based recommendation engine."""

    def __init__(self, tenant_manager: TenantManager) -> None:
        self._tenant_manager = tenant_manager

    def generate_candidates(
        self,
        *,
        session_summary: dict[str, Any],
        funnel_rows: list[dict[str, Any]],
        release_regression: dict[str, Any],
    ) -> list[RecommendationCandidate]:
        """Generate recommendations from behavior and funnel data."""
        out: list[RecommendationCandidate] = []
        by_type = session_summary.get("events_by_type", {})
        friction = session_summary.get("friction", {})

        rage = int(friction.get("rage_clicks", 0))
        if rage >= 3:
            out.append(
                RecommendationCandidate(
                    recommendation_type="ux_friction",
                    title="Reduce rage-click friction on key pages",
                    description=(
                        "Users are repeatedly clicking non-responsive elements. "
                        "Add clearer affordances and actionable hover/disabled states."
                    ),
                    evidence={"rage_clicks": rage},
                    confidence=0.72,
                    expected_impact=0.08,
                )
            )

        dead = int(friction.get("dead_clicks", 0))
        if dead >= 3:
            out.append(
                RecommendationCandidate(
                    recommendation_type="cta_clarity",
                    title="Fix dead-click surfaces",
                    description=(
                        "Dead clicks indicate perceived affordances that do not trigger "
                        "action. Align click targets with user expectations."
                    ),
                    evidence={"dead_clicks": dead},
                    confidence=0.69,
                    expected_impact=0.06,
                )
            )

        form_start = int(by_type.get("form_start", 0))
        form_submit = int(by_type.get("form_submit", 0))
        if form_start >= 5 and form_submit / max(1, form_start) < 0.5:
            out.append(
                RecommendationCandidate(
                    recommendation_type="form_optimization",
                    title="Improve form completion rate",
                    description=(
                        "Many users start forms but do not submit. Reduce fields, improve "
                        "validation hints, and add progressive disclosure."
                    ),
                    evidence={"form_start": form_start, "form_submit": form_submit},
                    confidence=0.75,
                    expected_impact=0.1,
                )
            )

        js_errors = int(friction.get("js_errors", 0))
        api_errors = int(friction.get("api_errors", 0))
        if js_errors + api_errors > 0:
            out.append(
                RecommendationCandidate(
                    recommendation_type="reliability",
                    title="Stabilize client/runtime errors",
                    description=(
                        "Observed javascript/API errors are likely impacting conversion and "
                        "trust. Prioritize error budget reduction."
                    ),
                    evidence={"js_errors": js_errors, "api_errors": api_errors},
                    risk_class="medium",
                    confidence=0.8,
                    expected_impact=0.12,
                )
            )

        conversion_row = next((r for r in funnel_rows if r.get("stage_name") == "conversion"), None)
        if conversion_row and float(conversion_row.get("conversion_rate") or 0.0) < 0.03:
            out.append(
                RecommendationCandidate(
                    recommendation_type="funnel_conversion",
                    title="Run low-risk conversion CTA experiments",
                    description=(
                        "Top-of-funnel activity is not converting. Test headline/CTA copy "
                        "variants and simplify conversion path steps."
                    ),
                    evidence={"conversion_rate": conversion_row.get("conversion_rate")},
                    confidence=0.64,
                    expected_impact=0.09,
                )
            )

        if release_regression.get("regression"):
            out.append(
                RecommendationCandidate(
                    recommendation_type="release_regression",
                    title="Investigate post-release regression",
                    description=(
                        "Error rate increased after latest release marker. Roll back or "
                        "patch high-risk changes before scaling traffic."
                    ),
                    evidence=release_regression,
                    risk_class="high",
                    confidence=0.88,
                    expected_impact=0.2,
                )
            )

        return out

    async def persist_candidates(
        self,
        tenant_id: str,
        candidates: list[RecommendationCandidate],
        *,
        source: str = "detector",
    ) -> list[dict[str, Any]]:
        """Persist recommendation candidates as tenant_recommendations rows."""
        rows: list[dict[str, Any]] = []
        for cand in candidates:
            rows.append(
                await self._tenant_manager.create_recommendation(
                    tenant_id,
                    recommendation_type=cand.recommendation_type,
                    title=cand.title,
                    description=cand.description,
                    evidence=cand.evidence,
                    risk_class=cand.risk_class,
                    confidence=cand.confidence,
                    expected_impact=cand.expected_impact,
                    source=source,
                )
            )
        return rows
