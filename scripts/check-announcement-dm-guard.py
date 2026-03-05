#!/usr/bin/env python3
"""Guardrail: block direct `user.send(...)` in announcement-producing paths."""

from __future__ import annotations

import re
import sys
from pathlib import Path

USER_SEND_PATTERN = re.compile(r"\buser\.send\(")

TARGET_PATHS = (
    Path("src/zetherion_ai/agent/inference.py"),
    Path("src/zetherion_ai/discord/bot.py"),
    Path("src/zetherion_ai/scheduler/actions.py"),
    Path("src/zetherion_ai/announcements"),
)

ALLOWED_USER_SEND_FILES = {
    Path("src/zetherion_ai/announcements/discord_adapter.py"),
}


def iter_target_files(repo_root: Path) -> list[Path]:
    files: list[Path] = []
    for rel_path in TARGET_PATHS:
        abs_path = repo_root / rel_path
        if abs_path.is_dir():
            files.extend(sorted(p for p in abs_path.rglob("*.py") if p.is_file()))
        elif abs_path.is_file():
            files.append(abs_path)
    return files


def scan_user_send_occurrences(file_path: Path) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    for lineno, line in enumerate(file_path.read_text(encoding="utf-8").splitlines(), start=1):
        if USER_SEND_PATTERN.search(line):
            hits.append((lineno, line.strip()))
    return hits


def collect_violations(repo_root: Path) -> list[str]:
    violations: list[str] = []
    for file_path in iter_target_files(repo_root):
        rel_path = file_path.relative_to(repo_root)
        hits = scan_user_send_occurrences(file_path)
        if not hits:
            continue
        if rel_path in ALLOWED_USER_SEND_FILES:
            continue
        for lineno, snippet in hits:
            violations.append(f"{rel_path}:{lineno}: {snippet}")
    return violations


def main() -> int:
    repo_root = Path.cwd()
    violations = collect_violations(repo_root)
    if not violations:
        print("Announcement DM guard check passed.")
        return 0

    print("Announcement DM guard check failed.")
    print()
    print("Direct `user.send(...)` is only allowed in:")
    for allowed in sorted(ALLOWED_USER_SEND_FILES):
        print(f"  - {allowed}")
    print()
    print("Violations:")
    for violation in violations:
        print(f"  - {violation}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
