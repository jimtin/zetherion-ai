#!/usr/bin/env python3
"""Validate required docs pages are present in MkDocs navigation."""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MKDOCS_CONFIG = REPO_ROOT / "mkdocs.yml"
DOCS_ROOT = REPO_ROOT / "docs"

# Pages we treat as mandatory navigable entry points.
REQUIRED_NAV_PAGES = {
    "index.md",
    "user/getting-started.md",
    "user/auto-update.md",
    "user/commands.md",
    "technical/architecture.md",
    "technical/security.md",
    "technical/configuration.md",
    "technical/api-reference.md",
    "technical/public-api-reference.md",
}

NAV_PATH_RE = re.compile(r"^\s*-\s+[^:]+:\s+([A-Za-z0-9_./-]+\.md)\s*$")


def extract_nav_paths(config_text: str) -> set[str]:
    paths: set[str] = set()
    for line in config_text.splitlines():
        match = NAV_PATH_RE.match(line)
        if match:
            paths.add(match.group(1).strip())
    return paths


def main() -> int:
    if not MKDOCS_CONFIG.exists():
        print(f"ERROR: mkdocs config not found: {MKDOCS_CONFIG}")
        return 1

    nav_paths = extract_nav_paths(MKDOCS_CONFIG.read_text(encoding="utf-8"))

    missing_from_nav = sorted(REQUIRED_NAV_PAGES - nav_paths)
    missing_files = sorted(path for path in REQUIRED_NAV_PAGES if not (DOCS_ROOT / path).exists())

    if missing_from_nav or missing_files:
        print("Documentation navigation validation failed.")
        if missing_from_nav:
            print("\nMissing required pages from mkdocs nav:")
            for path in missing_from_nav:
                print(f"  - {path}")
        if missing_files:
            print("\nRequired pages missing on disk:")
            for path in missing_files:
                print(f"  - docs/{path}")
        return 1

    print("Documentation navigation check passed.")
    print(f"Checked {len(REQUIRED_NAV_PAGES)} required pages.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
