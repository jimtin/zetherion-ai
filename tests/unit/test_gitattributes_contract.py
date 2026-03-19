from __future__ import annotations

from pathlib import Path


def test_gitattributes_forces_lf_for_shell_and_python_scripts() -> None:
    gitattributes = (Path(__file__).resolve().parents[2] / ".gitattributes").read_text(
        encoding="utf-8"
    )

    assert "*.sh text eol=lf" in gitattributes
    assert "*.py text eol=lf" in gitattributes
