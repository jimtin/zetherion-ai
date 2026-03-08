"""Unit tests for Windows PowerShell compatibility guard."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "check-windows-powershell-compat.py"
FORBIDDEN = (
    "bad.ps1:1: '??' is forbidden: PowerShell 7 null-coalescing operator "
    "is not supported by Windows PowerShell 5.1"
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "check_windows_powershell_compat_module",
        MODULE_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_collect_violations_accepts_plain_powershell(tmp_path: Path) -> None:
    module = _load_module()
    script_path = tmp_path / "ok.ps1"
    script_path.write_text(
        (
            "# comment with ?? should be ignored\n"
            '$status = "ok"\n'
            "if ($value) { Write-Host $value }\n"
        ),
        encoding="utf-8",
    )

    assert module.collect_violations([script_path]) == []


def test_collect_violations_flags_null_coalescing_operator(tmp_path: Path) -> None:
    module = _load_module()
    script_path = tmp_path / "bad.ps1"
    script_path.write_text('$value = $config["Status"] ?? ""\n', encoding="utf-8")

    violations = module.collect_violations([script_path])
    assert violations == [FORBIDDEN]
