#!/usr/bin/env python3
"""Lightweight Markdown link checker for repository docs."""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = REPO_ROOT / "docs"

# Matches markdown links and images: [text](target) / ![alt](target)
LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")

URL_PREFIXES = (
    "http://",
    "https://",
    "mailto:",
    "tel:",
    "data:",
)


def iter_markdown_files() -> list[Path]:
    files = sorted(DOCS_ROOT.rglob("*.md"))
    readme = REPO_ROOT / "README.md"
    if readme.exists():
        files.append(readme)
    return files


def _strip_target(raw_target: str) -> str:
    target = raw_target.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1].strip()
    # Ignore optional markdown title: [x](path "title")
    if " " in target:
        target = target.split(" ", 1)[0]
    return target


def _is_external_or_anchor(target: str) -> bool:
    if not target:
        return True
    if target.startswith(URL_PREFIXES):
        return True
    if target.startswith("#"):
        return True
    if target.startswith("javascript:"):
        return True
    return False


def _resolve_target(source: Path, target: str) -> Path:
    path_only = target.split("#", 1)[0].split("?", 1)[0]
    return (source.parent / path_only).resolve()


def check_links() -> list[str]:
    issues: list[str] = []

    for md_file in iter_markdown_files():
        text = md_file.read_text(encoding="utf-8")
        in_code_block = False

        for line_no, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("```"):
                in_code_block = not in_code_block
                continue
            if in_code_block:
                continue

            for match in LINK_RE.finditer(line):
                target = _strip_target(match.group(1))
                if _is_external_or_anchor(target):
                    continue

                resolved = _resolve_target(md_file, target)
                if not resolved.exists():
                    rel_source = md_file.relative_to(REPO_ROOT)
                    issues.append(
                        f"{rel_source}:{line_no}: missing target '{target}'"
                    )
                    continue

                # Docs-site links should not escape docs root.
                if md_file.is_relative_to(DOCS_ROOT) and not resolved.is_relative_to(DOCS_ROOT):
                    rel_source = md_file.relative_to(REPO_ROOT)
                    issues.append(
                        f"{rel_source}:{line_no}: link escapes docs root '{target}'"
                    )

    return issues


def main() -> int:
    issues = check_links()
    if issues:
        print("Documentation link check failed:")
        for issue in issues:
            print(f"  - {issue}")
        return 1

    print("Documentation link check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
