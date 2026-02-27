"""Tests for dev-agent autopilot daemon orchestration."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEV_AGENT_SRC = PROJECT_ROOT / "zetherion-dev-agent" / "src"
if str(DEV_AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(DEV_AGENT_SRC))

from zetherion_dev_agent.config import AgentConfig
from zetherion_dev_agent.daemon import DevAutopilotDaemon
from zetherion_dev_agent.docker_monitor import CleanupResult, ContainerSnapshot, ProjectSnapshot


class _FakeMonitor:
    def __init__(self, project: ProjectSnapshot, result: CleanupResult) -> None:
        self._project = project
        self._result = result

    def discover_projects(self) -> dict[str, ProjectSnapshot]:
        return {self._project.project_id: self._project}

    def run_cleanup(
        self,
        project: ProjectSnapshot,
        *,
        dry_run: bool,
        exited_older_than_hours: int,
    ) -> CleanupResult:
        assert project.project_id == self._project.project_id
        assert exited_older_than_hours == 24
        self._result.dry_run = dry_run
        return self._result


@pytest.mark.asyncio
async def test_discovery_creates_promptable_pending(tmp_path: Path) -> None:
    project = ProjectSnapshot(
        project_id="proj-a",
        containers=[
            ContainerSnapshot(
                container_id="abc",
                name="proj-a-web-1",
                image="img:latest",
                state="running",
                status="Up 10 minutes",
                project_id="proj-a",
                labels={},
            )
        ],
    )
    fake_result = CleanupResult(project_id="proj-a", dry_run=True)
    config = AgentConfig(
        webhook_url="https://discord.test/webhook",
        repos=[],
        database_path=str(tmp_path / "daemon.db"),
        api_token="token",
    )
    daemon = DevAutopilotDaemon(config, monitor=_FakeMonitor(project, fake_result))
    with patch("zetherion_dev_agent.daemon.send_event", new_callable=AsyncMock) as send_mock:
        send_mock.return_value = True
        await daemon.discovery_cycle()

    pending = daemon.store.list_pending_approvals()
    assert len(pending) == 1
    assert pending[0].project_id == "proj-a"
    assert pending[0].prompt_count == 1
    await daemon.close()


@pytest.mark.asyncio
async def test_cleanup_cycle_runs_auto_clean_projects(tmp_path: Path) -> None:
    project = ProjectSnapshot(
        project_id="proj-b",
        containers=[
            ContainerSnapshot(
                container_id="abc",
                name="proj-b-web-1",
                image="img:latest",
                state="exited",
                status="Exited (0) 2 days ago",
                project_id="proj-b",
                labels={},
            )
        ],
    )
    fake_result = CleanupResult(project_id="proj-b", dry_run=False, success=True)
    config = AgentConfig(
        webhook_url="https://discord.test/webhook",
        repos=[],
        database_path=str(tmp_path / "daemon.db"),
        api_token="token",
    )
    daemon = DevAutopilotDaemon(config, monitor=_FakeMonitor(project, fake_result))
    daemon.store.set_policy("proj-b", "auto_clean", source="test")

    with patch("zetherion_dev_agent.daemon.send_event", new_callable=AsyncMock) as send_mock:
        send_mock.return_value = True
        summary = await daemon.run_cleanup_cycle(dry_run=False)

    assert summary.project_count == 1
    assert summary.success_count == 1
    history = daemon.store.list_cleanup_runs(limit=5)
    assert len(history) == 1
    assert history[0]["project_id"] == "proj-b"
    await daemon.close()
