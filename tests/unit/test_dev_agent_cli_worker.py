"""Regression tests for dev-agent worker CLI invocation."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEV_AGENT_SRC = PROJECT_ROOT / "zetherion-dev-agent" / "src"
if str(DEV_AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(DEV_AGENT_SRC))

from zetherion_dev_agent import cli  # noqa: E402
from zetherion_dev_agent.config import AgentConfig  # noqa: E402


class _FakeRuntime:
    def __init__(self) -> None:
        self.closed = False

    async def run_once(self) -> SimpleNamespace:
        return SimpleNamespace(
            claimed_job=True,
            job_id="job-1",
            status="succeeded",
            poll_after_seconds=5,
        )

    async def close(self) -> None:
        self.closed = True


class _LoopBoundRuntime(_FakeRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.run_loop: asyncio.AbstractEventLoop | None = None

    async def run_once(self) -> SimpleNamespace:
        self.run_loop = asyncio.get_running_loop()
        return await super().run_once()

    async def close(self) -> None:
        assert asyncio.get_running_loop() is self.run_loop
        await super().close()


def test_worker_cli_accepts_default_configured_runner(monkeypatch) -> None:
    config = AgentConfig(worker_runner="docker")
    runtime = _FakeRuntime()

    monkeypatch.setattr(cli.AgentConfig, "load", staticmethod(lambda: config))
    monkeypatch.setattr(cli, "WorkerRuntime", lambda loaded: runtime)

    result = CliRunner().invoke(cli.main, ["worker", "--once"])

    assert result.exit_code == 0
    assert "claimed_job=True" in result.output
    assert runtime.closed is True


def test_worker_cli_accepts_docker_runner_override(monkeypatch) -> None:
    config = AgentConfig(worker_runner="noop")
    runtime = _FakeRuntime()

    def _runtime_factory(loaded: AgentConfig) -> _FakeRuntime:
        assert loaded.worker_runner == "docker"
        return runtime

    monkeypatch.setattr(cli.AgentConfig, "load", staticmethod(lambda: config))
    monkeypatch.setattr(cli, "WorkerRuntime", _runtime_factory)

    result = CliRunner().invoke(cli.main, ["worker", "--once", "--runner", "docker"])

    assert result.exit_code == 0
    assert "status=succeeded" in result.output
    assert runtime.closed is True


def test_worker_cli_closes_runtime_on_same_event_loop(monkeypatch) -> None:
    config = AgentConfig(worker_runner="docker")
    runtime = _LoopBoundRuntime()

    monkeypatch.setattr(cli.AgentConfig, "load", staticmethod(lambda: config))
    monkeypatch.setattr(cli, "WorkerRuntime", lambda loaded: runtime)

    result = CliRunner().invoke(cli.main, ["worker", "--once"])

    assert result.exit_code == 0
    assert "claimed_job=True" in result.output
    assert runtime.closed is True
