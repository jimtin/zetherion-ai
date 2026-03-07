"""Unit tests for the isolated E2E runtime helper."""

from __future__ import annotations

import importlib
from types import SimpleNamespace


def test_get_runtime_reads_dynamic_ports(monkeypatch) -> None:
    monkeypatch.setenv("E2E_PROJECT_NAME", "proj-123")
    monkeypatch.setenv("E2E_RUN_ID", "run-123")
    monkeypatch.setenv("E2E_STACK_ROOT", "/tmp/e2e-stack")
    monkeypatch.setenv("E2E_SKILLS_HOST_PORT", "28003")
    monkeypatch.setenv("E2E_POSTGRES_HOST_PORT", "28007")
    monkeypatch.setenv("E2E_QDRANT_HOST_PORT", "28008")
    monkeypatch.setenv("E2E_OLLAMA_HOST_PORT", "28006")
    monkeypatch.setenv("E2E_OLLAMA_ROUTER_HOST_PORT", "28005")

    module = importlib.import_module("tests.integration.e2e_runtime")
    module._runtime = None
    runtime = module.get_runtime()

    assert runtime.project_name == "proj-123"
    assert runtime.run_id == "run-123"
    assert runtime.skills_url == "http://localhost:28003"
    assert runtime.postgres_dsn == "postgresql://zetherion:password@localhost:28007/zetherion"
    assert runtime.qdrant_url == "http://localhost:28008"


def test_service_container_id_uses_compose_ps(monkeypatch) -> None:
    module = importlib.import_module("tests.integration.e2e_runtime")
    module._runtime = None
    runtime = module.get_runtime()

    calls: list[list[str]] = []

    def fake_run(command, capture_output=False, text=False, timeout=None):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="container-123\n", stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    container_id = runtime.service_container_id("zetherion-ai-skills")

    assert container_id == "container-123"
    assert calls[0][:7] == [
        "docker",
        "compose",
        "-f",
        runtime.compose_file,
        "-p",
        runtime.project_name,
        "ps",
    ]


def test_service_running_reads_inspect_status(monkeypatch) -> None:
    module = importlib.import_module("tests.integration.e2e_runtime")
    module._runtime = None
    runtime = module.get_runtime()

    def fake_run(command, capture_output=False, text=False, timeout=None):
        if command[:7] == [
            "docker",
            "compose",
            "-f",
            runtime.compose_file,
            "-p",
            runtime.project_name,
            "ps",
        ]:
            return SimpleNamespace(returncode=0, stdout="container-123\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="running\n", stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    assert runtime.service_running("postgres") is True
