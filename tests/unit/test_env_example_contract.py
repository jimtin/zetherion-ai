from __future__ import annotations

from pathlib import Path


def test_env_example_assignment_lines_do_not_embed_inline_comments() -> None:
    env_example = Path(__file__).resolve().parents[2] / ".env.example"

    offenders: list[str] = []
    for line_number, line in enumerate(
        env_example.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        _, value = stripped.split("=", 1)
        if value.startswith(("'", '"')):
            continue
        if " #" in value:
            offenders.append(f"{line_number}: {line}")

    assert offenders == [], "Inline comments must not share .env assignment lines:\n" + "\n".join(
        offenders
    )
