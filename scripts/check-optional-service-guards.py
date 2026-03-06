#!/usr/bin/env python3
"""Guardrail: optional compose services must be profile-gated."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path.cwd()
COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"

REQUIRED_PROFILE_LINES = {
    "cloudflared": "      - cloudflared",
    "zetherion-ai-whatsapp-bridge": "      - whatsapp-bridge",
}


def _service_block(text: str, service_name: str) -> str:
    lines = text.splitlines()
    marker = f"  {service_name}:"
    start_index: int | None = None
    collected: list[str] = []

    for index, line in enumerate(lines):
        if start_index is None:
            if line == marker:
                start_index = index
                collected.append(line)
            continue

        if line.startswith("  ") and not line.startswith("    ") and line.endswith(":"):
            break
        collected.append(line)

    if start_index is None:
        raise ValueError(f"Service '{service_name}' not found in docker-compose.yml")

    return "\n".join(collected) + "\n"


def collect_violations(compose_path: Path) -> list[str]:
    text = compose_path.read_text(encoding="utf-8")
    violations: list[str] = []
    for service_name, profile_line in REQUIRED_PROFILE_LINES.items():
        block = _service_block(text, service_name)
        if "\n    profiles:\n" not in block:
            violations.append(f"{service_name}: missing profiles block")
            continue
        if profile_line not in block:
            violations.append(
                f"{service_name}: missing required profile line '{profile_line.strip()}'"
            )
    return violations


def main() -> int:
    violations = collect_violations(COMPOSE_PATH)
    if not violations:
        print("Optional service guard check passed.")
        return 0

    print("Optional service guard check failed.")
    print()
    print("Violations:")
    for violation in violations:
        print(f"  - {violation}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
