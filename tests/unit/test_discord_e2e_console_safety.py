from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "tests" / "integration" / "test_discord_e2e.py"


class _Cp1252Stdout:
    encoding = "cp1252"

    def __init__(self) -> None:
        self.chunks: list[str] = []

    def write(self, text: str) -> int:
        text.encode(self.encoding)
        self.chunks.append(text)
        return len(text)

    def flush(self) -> None:
        return None


class _FallbackStdout(_Cp1252Stdout):
    def write(self, text: str) -> int:
        if "✅" in text:
            raise UnicodeEncodeError(self.encoding, text, 0, 1, "character maps to <undefined>")
        return super().write(text)


def _load_module():
    spec = importlib.util.spec_from_file_location("discord_e2e_console_safety", MODULE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_safe_print_falls_back_for_cp1252_console(monkeypatch) -> None:
    module = _load_module()
    fake_stdout = _FallbackStdout()
    monkeypatch.setattr(module.sys, "stdout", fake_stdout)

    module._safe_print("✅ Test client ready")

    assert fake_stdout.chunks == ["? Test client ready\n"]
