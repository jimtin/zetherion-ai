#!/usr/bin/env python3
"""Guardrail: production code must use scoped Qdrant helper methods."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path.cwd()
TARGET_PATHS = (Path("src/zetherion_ai"),)
ALLOWED_FILES = {Path("src/zetherion_ai/memory/qdrant.py")}
FORBIDDEN_METHODS = (
    "ensure_collection",
    "store_with_payload",
    "search_collection",
    "filter_by_field",
    "get_by_id",
    "delete_by_field",
    "delete_by_id",
    "delete_by_filters",
)
_PATTERN = re.compile(
    r"\.({methods})\s*\(".format(methods="|".join(re.escape(name) for name in FORBIDDEN_METHODS))
)


def _iter_python_files(root: Path):
    for target in TARGET_PATHS:
        target_path = root / target
        if target_path.is_file() and target_path.suffix == ".py":
            yield target_path
            continue
        if not target_path.exists():
            continue
        yield from sorted(target_path.rglob("*.py"))


def collect_violations(root: Path) -> list[str]:
    violations: list[str] = []
    for path in _iter_python_files(root):
        rel_path = path.relative_to(root)
        if rel_path in ALLOWED_FILES:
            continue
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            match = _PATTERN.search(line)
            if match is None:
                continue
            violations.append(
                f"{rel_path}:{lineno}: direct Qdrant helper "
                f"'{match.group(1)}' is blocked; use scoped accessors"
            )
    return violations


def main() -> int:
    violations = collect_violations(REPO_ROOT)
    if not violations:
        print("Qdrant scope guard check passed.")
        return 0

    print("Qdrant scope guard check failed.")
    print()
    print("Violations:")
    for violation in violations:
        print(f"  - {violation}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
