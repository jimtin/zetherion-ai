from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_coverage_gate_module():
    module_path = Path(__file__).resolve().parents[2] / "scripts" / "testing" / "coverage_gate.py"
    spec = importlib.util.spec_from_file_location("coverage_gate_script", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_display_path_maps_container_workspace_to_repo_relative(monkeypatch):
    module = _load_coverage_gate_module()
    repo_root = Path("/Users/jameshinton/Developer/zetherion-ai")
    artifact_path = Path("/workspace/.artifacts/coverage/coverage.json")

    monkeypatch.setenv("ZETHERION_WORKSPACE_ROOT", "/workspace")
    monkeypatch.setenv("ZETHERION_HOST_WORKSPACE_ROOT", str(repo_root))

    assert module._display_path(artifact_path, repo_root) == ".artifacts/coverage/coverage.json"


def test_display_path_falls_back_to_absolute_path_when_no_mapping(monkeypatch):
    module = _load_coverage_gate_module()
    repo_root = Path("/Users/jameshinton/Developer/zetherion-ai")
    external_path = Path("/tmp/coverage.json")

    monkeypatch.delenv("ZETHERION_WORKSPACE_ROOT", raising=False)
    monkeypatch.delenv("ZETHERION_HOST_WORKSPACE_ROOT", raising=False)

    assert module._display_path(external_path, repo_root) == str(external_path.resolve())
