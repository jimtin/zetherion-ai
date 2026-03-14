"""Coverage artifact generation for canonical owner-CI gates."""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CoverageMetric:
    """One normalized coverage metric."""

    name: str
    covered: int
    total: int
    threshold: float

    @property
    def actual(self) -> float:
        if self.total <= 0:
            return 100.0
        return round((self.covered / self.total) * 100.0, 2)

    @property
    def passed(self) -> bool:
        return self.actual >= self.threshold

    @property
    def delta_to_threshold(self) -> float:
        return round(max(0.0, self.threshold - self.actual), 2)

    def to_payload(self) -> dict[str, Any]:
        return {
            "covered": self.covered,
            "total": self.total,
            "actual": self.actual,
            "threshold": self.threshold,
            "passed": self.passed,
            "delta_to_threshold": self.delta_to_threshold,
        }


@dataclass(frozen=True)
class FunctionCoverageTarget:
    """One Python function/method tracked for custom function coverage."""

    file_path: str
    qualified_name: str
    start_line: int
    end_line: int
    executable_lines: tuple[int, ...]
    covered_lines: tuple[int, ...]

    @property
    def covered(self) -> bool:
        return bool(self.covered_lines)

    @property
    def missing_lines(self) -> tuple[int, ...]:
        return tuple(line for line in self.executable_lines if line not in set(self.covered_lines))


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _relative_path(path: Path, repo_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(repo_root.resolve()))
    except ValueError:
        return str(path.resolve())


def _is_docstring_expr(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(getattr(node, "value", None), ast.Constant)
        and isinstance(node.value.value, str)
    )


def _is_ellipsis_expr(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(getattr(node, "value", None), ast.Constant)
        and node.value.value is Ellipsis
    )


def _body_is_stub(body: list[ast.stmt]) -> bool:
    statements = [node for node in body if not _is_docstring_expr(node)]
    if not statements:
        return True
    return all(isinstance(node, ast.Pass) or _is_ellipsis_expr(node) for node in statements)


class _ExecutableLineCollector(ast.NodeVisitor):
    """Collect executable line numbers without descending into nested defs/classes."""

    def __init__(self) -> None:
        self.lines: set[int] = set()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        return None

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        return None

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        return None

    def generic_visit(self, node: ast.AST) -> None:
        lineno = getattr(node, "lineno", None)
        if isinstance(lineno, int):
            end_lineno = getattr(node, "end_lineno", lineno)
            if isinstance(end_lineno, int):
                for value in range(lineno, end_lineno + 1):
                    self.lines.add(value)
        super().generic_visit(node)


def _function_line_candidates(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[int]:
    collector = _ExecutableLineCollector()
    for statement in node.body:
        if _is_docstring_expr(statement):
            continue
        collector.visit(statement)
    return collector.lines


def _iter_function_nodes(
    node: ast.AST,
    *,
    parents: tuple[str, ...] = (),
) -> list[tuple[tuple[str, ...], ast.FunctionDef | ast.AsyncFunctionDef]]:
    collected: list[tuple[tuple[str, ...], ast.FunctionDef | ast.AsyncFunctionDef]] = []
    body = list(getattr(node, "body", []) or [])
    for child in body:
        if isinstance(child, ast.ClassDef):
            collected.extend(_iter_function_nodes(child, parents=(*parents, child.name)))
            continue
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
            collected.append((parents, child))
            collected.extend(_iter_function_nodes(child, parents=(*parents, child.name)))
    return collected


def _load_coverage_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _function_targets_for_file(
    *,
    file_path: Path,
    repo_root: Path,
    coverage_entry: dict[str, Any],
) -> list[FunctionCoverageTarget]:
    try:
        parsed = ast.parse(file_path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []

    executable_lines = set(coverage_entry.get("executed_lines") or []) | set(
        coverage_entry.get("missing_lines") or []
    )
    executed_lines = set(coverage_entry.get("executed_lines") or [])
    targets: list[FunctionCoverageTarget] = []
    for parents, node in _iter_function_nodes(parsed):
        if _body_is_stub(list(node.body or [])):
            continue
        candidate_lines = sorted(
            line for line in _function_line_candidates(node) if line in executable_lines
        )
        if not candidate_lines:
            continue
        qualified_name = ".".join((*parents, node.name)) if parents else node.name
        covered_lines = tuple(line for line in candidate_lines if line in executed_lines)
        targets.append(
            FunctionCoverageTarget(
                file_path=_relative_path(file_path, repo_root),
                qualified_name=qualified_name,
                start_line=int(getattr(node, "lineno", 0) or 0),
                end_line=int(getattr(node, "end_lineno", getattr(node, "lineno", 0)) or 0),
                executable_lines=tuple(candidate_lines),
                covered_lines=covered_lines,
            )
        )
    return targets


def build_function_coverage(
    *,
    coverage_payload: dict[str, Any],
    repo_root: Path,
    source_root: Path,
) -> tuple[CoverageMetric, list[dict[str, Any]]]:
    files = dict(coverage_payload.get("files") or {})
    all_targets: list[FunctionCoverageTarget] = []
    for raw_file_path, entry in files.items():
        file_path = Path(raw_file_path)
        if not file_path.is_absolute():
            file_path = repo_root / file_path
        resolved = file_path.resolve()
        try:
            resolved.relative_to(source_root.resolve())
        except ValueError:
            continue
        all_targets.extend(
            _function_targets_for_file(
                file_path=resolved,
                repo_root=repo_root,
                coverage_entry=dict(entry or {}),
            )
        )

    covered = sum(1 for target in all_targets if target.covered)
    metric = CoverageMetric(
        name="functions",
        covered=covered,
        total=len(all_targets),
        threshold=0,
    )
    gaps = [
        {
            "file": target.file_path,
            "metric": "functions",
            "identifier": target.qualified_name,
            "line_numbers": list(target.missing_lines or target.executable_lines),
            "missing_hit_count": max(1, len(target.executable_lines)),
            "projected_threshold_impact": round(
                100.0 / max(1, metric.total),
                4,
            ),
            "priority": "high" if len(target.executable_lines) <= 3 else "medium",
            "summary": (
                f"Function `{target.qualified_name}` is not covered."
                if not target.covered
                else ""
            ),
        }
        for target in all_targets
        if not target.covered
    ]
    return metric, gaps


def build_coverage_artifacts(
    *,
    coverage_payload: dict[str, Any],
    repo_root: Path,
    source_root: Path,
    thresholds: dict[str, float],
    coverage_json_path: str,
    coverage_report_path: str | None = None,
    html_index_path: str | None = None,
    repo_sha: str | None = None,
    run_id: str | None = None,
    lane_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], int]:
    totals = dict(coverage_payload.get("totals") or {})
    statement_total = int(totals.get("num_statements") or 0)
    covered_lines = int(totals.get("covered_lines") or 0)
    branch_total = int(totals.get("num_branches") or 0)
    covered_branches = int(totals.get("covered_branches") or 0)

    metrics = {
        "statements": CoverageMetric(
            name="statements",
            covered=covered_lines,
            total=statement_total,
            threshold=float(thresholds["statements"]),
        ),
        "lines": CoverageMetric(
            name="lines",
            covered=covered_lines,
            total=statement_total,
            threshold=float(thresholds["lines"]),
        ),
        "branches": CoverageMetric(
            name="branches",
            covered=covered_branches,
            total=branch_total,
            threshold=float(thresholds["branches"]),
        ),
    }
    function_metric, function_gaps = build_function_coverage(
        coverage_payload=coverage_payload,
        repo_root=repo_root,
        source_root=source_root,
    )
    metrics["functions"] = CoverageMetric(
        name="functions",
        covered=function_metric.covered,
        total=function_metric.total,
        threshold=float(thresholds["functions"]),
    )

    coverage_gaps: list[dict[str, Any]] = []
    for raw_file_path, entry in dict(coverage_payload.get("files") or {}).items():
        file_path = Path(raw_file_path)
        if not file_path.is_absolute():
            file_path = repo_root / file_path
        normalized_path = _relative_path(file_path, repo_root)
        missing_lines = sorted(
            int(line)
            for line in list(dict(entry or {}).get("missing_lines") or [])
            if isinstance(line, int)
        )
        if missing_lines:
            coverage_gaps.append(
                {
                    "file": normalized_path,
                    "metric": "statements",
                    "identifier": f"{normalized_path}:missing_lines",
                    "line_numbers": missing_lines[:25],
                    "missing_hit_count": len(missing_lines),
                    "projected_threshold_impact": round(
                        (len(missing_lines) / max(1, statement_total)) * 100.0,
                        4,
                    ),
                    "priority": "high" if len(missing_lines) >= 10 else "medium",
                    "summary": f"{len(missing_lines)} statement line(s) are not covered.",
                }
            )
        missing_branches = list(dict(entry or {}).get("missing_branches") or [])
        if missing_branches:
            branch_lines = sorted(
                {
                    int(branch[0])
                    for branch in missing_branches
                    if isinstance(branch, list | tuple) and branch and isinstance(branch[0], int)
                }
            )
            coverage_gaps.append(
                {
                    "file": normalized_path,
                    "metric": "branches",
                    "identifier": f"{normalized_path}:missing_branches",
                    "line_numbers": branch_lines[:25],
                    "missing_hit_count": len(missing_branches),
                    "projected_threshold_impact": round(
                        (len(missing_branches) / max(1, branch_total)) * 100.0,
                        4,
                    ),
                    "priority": "high" if len(missing_branches) >= 5 else "medium",
                    "summary": f"{len(missing_branches)} branch path(s) are not covered.",
                }
            )
    coverage_gaps.extend(function_gaps)
    coverage_gaps.sort(
        key=lambda gap: (
            0 if gap["priority"] == "high" else 1,
            -float(gap.get("projected_threshold_impact") or 0.0),
            -int(gap.get("missing_hit_count") or 0),
            str(gap.get("file") or ""),
        )
    )

    artifacts = {
        "coverage_json": coverage_json_path,
        "coverage_report": coverage_report_path,
        "html_index": html_index_path,
    }
    summary = {
        "generated_at": _utc_now_iso(),
        "repo_sha": repo_sha,
        "run_id": run_id,
        "lane_id": lane_id,
        "artifacts": artifacts,
        "metrics": {name: metric.to_payload() for name, metric in metrics.items()},
        "passed": all(metric.passed for metric in metrics.values()),
    }
    gaps = {
        "generated_at": _utc_now_iso(),
        "repo_sha": repo_sha,
        "run_id": run_id,
        "lane_id": lane_id,
        "artifacts": artifacts,
        "gaps": coverage_gaps,
    }
    exit_code = 0 if summary["passed"] else 1
    return summary, gaps, exit_code
