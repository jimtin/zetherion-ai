# ruff: noqa: E402
"""Tests for dev-agent Docker discovery and cleanup planning helpers."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEV_AGENT_SRC = PROJECT_ROOT / "zetherion-dev-agent" / "src"
if str(DEV_AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(DEV_AGENT_SRC))

from zetherion_dev_agent.docker_monitor import (
    CleanupAction,
    ContainerSnapshot,
    DockerMonitor,
    ProjectSnapshot,
    _infer_project_id_from_name,
    _status_age_hours,
)


def test_status_age_hours_parses_expected_units() -> None:
    assert _status_age_hours("Exited (0) 2 hours ago") == 2.0
    assert _status_age_hours("Exited (0) 3 days ago") == 72.0
    assert _status_age_hours("Exited (0) less than a second ago") == 0.0
    assert _status_age_hours("Up 4 minutes") is None


def test_infer_project_id_from_name_variants() -> None:
    assert _infer_project_id_from_name("myproj_web_1") == "myproj"
    assert _infer_project_id_from_name("myproj-web-1") == "myproj"
    assert _infer_project_id_from_name("single") == "single"


def test_plan_cleanup_removes_old_exited_and_networks(monkeypatch: object) -> None:
    monitor = DockerMonitor()
    container = ContainerSnapshot(
        container_id="abc123",
        name="myproj-web-1",
        image="img:latest",
        state="exited",
        status="Exited (0) 2 days ago",
        project_id="myproj",
        labels={"com.docker.compose.project": "myproj"},
    )
    running = ContainerSnapshot(
        container_id="def456",
        name="myproj-db-1",
        image="postgres:16",
        state="running",
        status="Up 2 hours",
        project_id="myproj",
        labels={"com.docker.compose.project": "myproj"},
    )
    project = ProjectSnapshot(project_id="myproj", containers=[container, running])

    monkeypatch.setattr(
        monitor,
        "_list_project_networks",
        lambda _project_id: [("net123", "myproj_default")],
    )

    actions = monitor.plan_cleanup(project, exited_older_than_hours=24)
    assert isinstance(actions[0], CleanupAction)
    assert actions[0].action_type == "remove_containers"
    assert "abc123" in actions[0].command
    assert all("def456" not in action.command for action in actions)
    assert actions[1].action_type == "remove_network"
