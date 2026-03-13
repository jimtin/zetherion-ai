"""Tests for worker backend configuration persistence."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEV_AGENT_SRC = PROJECT_ROOT / "zetherion-dev-agent" / "src"
if str(DEV_AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(DEV_AGENT_SRC))

import zetherion_dev_agent.config as config_module  # noqa: E402
from zetherion_dev_agent.config import AgentConfig  # noqa: E402


def test_agent_config_save_and_load_preserves_worker_backend_contract(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_dir = tmp_path / "config-home"
    config_file = config_dir / "config.toml"

    monkeypatch.setattr(config_module, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_module, "CONFIG_FILE", config_file)

    config = AgentConfig(
        api_token="token",
        worker_runner="docker",
        worker_execution_backend="wsl_docker",
        worker_workspace_root=r"C:\ZetherionCI\workspaces",
        worker_runtime_root=r"C:\ZetherionCI\agent-runtime",
        worker_docker_backend="wsl_docker",
        worker_wsl_distribution="Ubuntu",
        worker_cleanup_enabled=True,
        worker_cleanup_low_disk_free_bytes=12_345,
        worker_cleanup_target_free_bytes=67_890,
        worker_cleanup_artifact_retention_hours=6,
        worker_cleanup_log_retention_days=3,
        worker_allowed_repo_roots=[r"C:\ZetherionCI\workspaces"],
        worker_denied_repo_roots=[r"C:\ZetherionAI"],
    )

    config.save()
    loaded = AgentConfig.load()

    assert loaded.worker_execution_backend == "wsl_docker"
    assert loaded.worker_workspace_root == r"C:\ZetherionCI\workspaces"
    assert loaded.worker_runtime_root == r"C:\ZetherionCI\agent-runtime"
    assert loaded.worker_docker_backend == "wsl_docker"
    assert loaded.worker_wsl_distribution == "Ubuntu"
    assert loaded.worker_cleanup_enabled is True
    assert loaded.worker_cleanup_low_disk_free_bytes == 12_345
    assert loaded.worker_cleanup_target_free_bytes == 67_890
    assert loaded.worker_cleanup_artifact_retention_hours == 6
    assert loaded.worker_cleanup_log_retention_days == 3
    assert loaded.worker_allowed_repo_roots == [r"C:\ZetherionCI\workspaces"]
    assert loaded.worker_denied_repo_roots == [r"C:\ZetherionAI"]


def test_agent_config_env_overrides_backend_contract(monkeypatch) -> None:
    config = AgentConfig()

    monkeypatch.setenv("DEV_AGENT_WORKER_EXECUTION_BACKEND", "wsl_docker")
    monkeypatch.setenv("DEV_AGENT_WORKER_WORKSPACE_ROOT", r"C:\Worker\workspaces")
    monkeypatch.setenv("DEV_AGENT_WORKER_RUNTIME_ROOT", r"C:\Worker\runtime")
    monkeypatch.setenv("DEV_AGENT_WORKER_DOCKER_BACKEND", "wsl_docker")
    monkeypatch.setenv("DEV_AGENT_WORKER_WSL_DISTRIBUTION", "Ubuntu")
    monkeypatch.setenv("DEV_AGENT_WORKER_CLEANUP_ENABLED", "true")
    monkeypatch.setenv("DEV_AGENT_WORKER_CLEANUP_LOW_DISK_FREE_BYTES", "111")
    monkeypatch.setenv("DEV_AGENT_WORKER_CLEANUP_TARGET_FREE_BYTES", "222")
    monkeypatch.setenv("DEV_AGENT_WORKER_CLEANUP_ARTIFACT_RETENTION_HOURS", "12")
    monkeypatch.setenv("DEV_AGENT_WORKER_CLEANUP_LOG_RETENTION_DAYS", "9")

    loaded = AgentConfig._apply_env_overrides(config)

    assert loaded.worker_execution_backend == "wsl_docker"
    assert loaded.worker_workspace_root == r"C:\Worker\workspaces"
    assert loaded.worker_runtime_root == r"C:\Worker\runtime"
    assert loaded.worker_docker_backend == "wsl_docker"
    assert loaded.worker_wsl_distribution == "Ubuntu"
    assert loaded.worker_cleanup_enabled is True
    assert loaded.worker_cleanup_low_disk_free_bytes == 111
    assert loaded.worker_cleanup_target_free_bytes == 222
    assert loaded.worker_cleanup_artifact_retention_hours == 12
    assert loaded.worker_cleanup_log_retention_days == 9
