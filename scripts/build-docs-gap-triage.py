#!/usr/bin/env python3
"""Generate a weekly triage markdown report from docs knowledge gap logs."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass
class GapStat:
    count: int
    latest_ts: str
    intents: set[str]
    reasons: Counter[str]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        default="data/docs_unknown_questions.jsonl",
        help="Path to docs gap JSONL log",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional output markdown path (defaults to stdout)",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=20,
        help="Number of top questions to include",
    )
    return parser.parse_args()


def _load_entries(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []

    entries: list[dict[str, object]] = []
    for idx, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue
        question = raw.get("question")
        if not isinstance(question, str) or not question.strip():
            continue
        entries.append(raw)
    return entries


def _to_markdown(entries: list[dict[str, object]], source_path: Path, top_n: int) -> str:
    now = datetime.now(UTC).strftime("%Y-%m-%d")
    header = [
        f"# Weekly Docs Knowledge Gap Triage ({now})",
        "",
        "This issue is generated from unresolved docs-backed Q&A queries.",
        f"Source file: `{source_path}`",
        "",
    ]

    if not entries:
        header.extend(
            [
                "## Summary",
                "",
                "No gap log entries were found in this repository snapshot.",
                "",
                "## Triage Checklist",
                "",
                "- [ ] Confirm `DOCS_KNOWLEDGE_ENABLED=true` in active deployments.",
                "- [ ] Export or attach this week's `data/docs_unknown_questions.jsonl` from runtime environments.",
                "- [ ] Add or update docs for repeated unanswered questions.",
                "- [ ] Close this issue once updates ship.",
            ]
        )
        return "\n".join(header)

    grouped: dict[str, GapStat] = {}
    for entry in entries:
        question = str(entry.get("question", "")).strip()
        if question not in grouped:
            grouped[question] = GapStat(
                count=0,
                latest_ts="",
                intents=set(),
                reasons=Counter(),
            )
        stat = grouped[question]
        stat.count += 1

        timestamp = str(entry.get("timestamp", ""))
        if timestamp and timestamp > stat.latest_ts:
            stat.latest_ts = timestamp

        intent = str(entry.get("intent", "")).strip()
        if intent:
            stat.intents.add(intent)

        reason = str(entry.get("reason", "")).strip()
        if reason:
            stat.reasons[reason] += 1

    ordered = sorted(grouped.items(), key=lambda item: (-item[1].count, item[0].lower()))[:top_n]

    lines = header
    lines.extend(
        [
            "## Summary",
            "",
            f"- Total unresolved entries: **{len(entries)}**",
            f"- Unique unanswered questions: **{len(grouped)}**",
            "",
            "## Top Questions",
            "",
            "| Count | Last Seen (UTC) | Intent(s) | Question |",
            "|---:|---|---|---|",
        ]
    )

    for question, stat in ordered:
        intents = ", ".join(sorted(stat.intents)) if stat.intents else "-"
        latest = stat.latest_ts or "-"
        q = question.replace("|", "\\|")
        lines.append(f"| {stat.count} | {latest} | {intents} | {q} |")

    reason_counts = Counter()
    for stat in grouped.values():
        reason_counts.update(stat.reasons)

    if reason_counts:
        lines.extend(["", "## Gap Reasons", ""])
        for reason, count in reason_counts.most_common():
            lines.append(f"- `{reason}`: {count}")

    lines.extend(
        [
            "",
            "## Triage Checklist",
            "",
            "- [ ] Add or update docs for top repeated questions.",
            "- [ ] Link merged PRs in this issue.",
            "- [ ] Add regression checks if the drift came from API/config changes.",
            "- [ ] Close once docs are published and synced to wiki.",
        ]
    )

    return "\n".join(lines)


def main() -> int:
    args = _parse_args()
    source = Path(args.input)
    entries = _load_entries(source)
    markdown = _to_markdown(entries, source, args.top)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(markdown, encoding="utf-8")
    else:
        print(markdown)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
