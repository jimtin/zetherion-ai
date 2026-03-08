#!/usr/bin/env python3
"""Guardrail: Windows host-facing PowerShell scripts must stay PowerShell 5 compatible."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path.cwd()
SCRIPT_GLOBS = [
    "scripts/windows/*.ps1",
    "scripts/*.ps1",
]
BANNED_TOKENS = {
    "??": "PowerShell 7 null-coalescing operator is not supported by Windows PowerShell 5.1",
}


def iter_script_paths() -> list[Path]:
    paths: set[Path] = set()
    for pattern in SCRIPT_GLOBS:
        paths.update(REPO_ROOT.glob(pattern))
    return sorted(path for path in paths if path.is_file())


def collect_violations(paths: list[Path]) -> list[str]:
    violations: list[str] = []
    for path in paths:
        lines = path.read_text(encoding="utf-8").splitlines()
        for line_number, line in enumerate(lines, start=1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            for token, reason in BANNED_TOKENS.items():
                if token in line:
                    try:
                        display_path = path.relative_to(REPO_ROOT)
                    except ValueError:
                        display_path = path.name
                    violations.append(
                        f"{display_path}:{line_number}: '{token}' is forbidden: {reason}"
                    )
    return violations


def main() -> int:
    violations = collect_violations(iter_script_paths())
    if not violations:
        print("Windows PowerShell compatibility check passed.")
        return 0

    print("Windows PowerShell compatibility check failed.")
    print()
    print("Violations:")
    for violation in violations:
        print(f"  - {violation}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
