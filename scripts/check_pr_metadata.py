#!/usr/bin/env python3
"""Validate pull-request metadata against repo policy."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

REQUIRED_BRANCH_PREFIX = "codex/"
EXEMPT_ACTORS = {"dependabot[bot]"}
EXEMPT_BRANCH_PREFIXES = ("dependabot/",)
SECTION_PATTERN = re.compile(r"^##\s+(?P<title>.+?)\s*$")
BULLET_CODE_PATTERN = re.compile(r"^\s*-\s*`(?P<value>[^`]+)`\s*$")
BULLET_PATTERN = re.compile(r"^\s*-\s+(?P<value>.+?)\s*$")
CHECKBOX_PATTERN = re.compile(r"^\s*-\s*\[(?P<mark>[ xX])\]\s+(?P<value>.+?)\s*$")
PLACEHOLDER_VALUES = {"...", "`...`"}
REQUIRED_SECTIONS = (
    "Summary",
    "Capability IDs",
    "Workflow Scenario IDs",
    "Validation",
    "Receipt / Verification",
)


def _load_event(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _extract_sections(body: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw_line in body.splitlines():
        match = SECTION_PATTERN.match(raw_line.strip())
        if match:
            current = match.group("title")
            sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append(raw_line.rstrip())
    return sections


def _nonempty_lines(lines: list[str]) -> list[str]:
    return [line.strip() for line in lines if line.strip()]


def _extract_code_bullets(lines: list[str]) -> list[str]:
    values: list[str] = []
    for line in _nonempty_lines(lines):
        match = BULLET_CODE_PATTERN.match(line)
        if match:
            values.append(match.group("value").strip())
    return values


def _extract_bullets(lines: list[str]) -> list[str]:
    values: list[str] = []
    for line in _nonempty_lines(lines):
        match = BULLET_PATTERN.match(line)
        if match:
            values.append(match.group("value").strip())
    return values


def validate_pr_metadata(event: dict[str, Any]) -> list[str]:
    pull_request = event.get("pull_request")
    if not isinstance(pull_request, dict):
        return []

    sender = event.get("sender") or {}
    sender_login = str(sender.get("login", "")).strip()
    head = pull_request.get("head") or {}
    head_ref = str(head.get("ref", "")).strip()

    is_exempt_actor = sender_login in EXEMPT_ACTORS
    is_exempt_branch = any(head_ref.startswith(prefix) for prefix in EXEMPT_BRANCH_PREFIXES)
    if is_exempt_actor and is_exempt_branch:
        return []

    errors: list[str] = []
    if not head_ref.startswith(REQUIRED_BRANCH_PREFIX):
        errors.append(
            "PR head branch must start with "
            f"'{REQUIRED_BRANCH_PREFIX}' (got '{head_ref or '<empty>'}')."
        )

    body = str(pull_request.get("body") or "")
    sections = _extract_sections(body)
    for section in REQUIRED_SECTIONS:
        if section not in sections:
            errors.append(f"PR body missing required section: {section}")

    summary_lines = _nonempty_lines(sections.get("Summary", []))
    if not summary_lines or all(line in PLACEHOLDER_VALUES for line in summary_lines):
        errors.append("PR Summary section must contain real content.")

    capability_ids = _extract_code_bullets(sections.get("Capability IDs", []))
    if not capability_ids:
        errors.append("PR body must list at least one non-placeholder capability ID.")
    elif any(value == "..." for value in capability_ids):
        errors.append("PR capability IDs must not use placeholder values.")

    workflow_scenarios = _extract_code_bullets(sections.get("Workflow Scenario IDs", []))
    if not workflow_scenarios:
        errors.append("PR body must list at least one non-placeholder workflow scenario ID.")
    elif any(value == "..." for value in workflow_scenarios):
        errors.append("PR workflow scenario IDs must not use placeholder values.")

    validation_lines = _extract_bullets(sections.get("Validation", []))
    if not validation_lines:
        errors.append("PR Validation section must list deterministic evidence.")
    elif any(value in PLACEHOLDER_VALUES or value == "..." for value in validation_lines):
        errors.append("PR Validation section must not use placeholder values.")

    receipt_lines = _nonempty_lines(sections.get("Receipt / Verification", []))
    checkbox_lines = [CHECKBOX_PATTERN.match(line) for line in receipt_lines]
    checkbox_matches = [match for match in checkbox_lines if match is not None]
    if len(checkbox_matches) < 4:
        errors.append(
            "PR Receipt / Verification section must include the required "
            "checked checklist items."
        )
    else:
        unchecked = [
            match.group("value") for match in checkbox_matches if match.group("mark").lower() != "x"
        ]
        if unchecked:
            errors.append(
                "PR Receipt / Verification checklist items must be explicitly "
                "checked before merge readiness: " + "; ".join(unchecked)
            )

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--event-path",
        default=os.environ.get("GITHUB_EVENT_PATH", ""),
        help="Path to the GitHub event payload JSON.",
    )
    args = parser.parse_args()

    if not args.event_path:
        print("No GitHub event payload provided; skipping PR metadata validation.")
        return 0

    event_path = Path(args.event_path)
    if not event_path.exists():
        print(f"GitHub event payload not found: {event_path}")
        return 1

    errors = validate_pr_metadata(_load_event(event_path))
    if errors:
        print("ERROR: Pull-request metadata validation failed:")
        for error in errors:
            print(f"  - {error}")
        return 1

    print("Pull-request metadata matches policy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
