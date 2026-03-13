"""Tests for dev-agent watcher loop behavior."""

from __future__ import annotations

import inspect
import importlib
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEV_AGENT_SRC = PROJECT_ROOT / "zetherion-dev-agent" / "src"
if str(DEV_AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(DEV_AGENT_SRC))

from zetherion_dev_agent.cli import _watch_loop  # noqa: E402
from zetherion_dev_agent.config import AgentConfig  # noqa: E402
from zetherion_dev_agent.state import ScanState  # noqa: E402
from zetherion_dev_agent.watchers.git import CommitInfo, TagInfo  # noqa: E402


def test_cli_import_does_not_require_daemon_module(monkeypatch: pytest.MonkeyPatch) -> None:
    cli_name = "zetherion_dev_agent.cli"
    sys.modules.pop(cli_name, None)

    original_import = __import__

    def guarded_import(name: str, globals=None, locals=None, fromlist=(), level: int = 0):
        if name == "zetherion_dev_agent.daemon":
            raise ModuleNotFoundError("daemon import blocked for worker bootstrap regression")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", guarded_import)
    module = importlib.import_module(cli_name)

    assert hasattr(module, "worker_command")
    assert callable(module.worker_command)


def test_watch_loop_uses_asyncio_sleep() -> None:
    source = inspect.getsource(_watch_loop)
    assert "await asyncio.sleep" in source
    assert "time.sleep(" not in source


@pytest.mark.asyncio
async def test_main_branch_commit_emits_deploy_marker() -> None:
    config = AgentConfig(
        webhook_url="https://discord.test/webhook",
        agent_name="zetherion-dev-agent",
        repos=["/tmp/repo"],
        scan_interval=1,
        claude_code_enabled=False,
        annotations_enabled=False,
        git_enabled=True,
    )
    state = ScanState()

    commit = CommitInfo(
        sha="abcdef123456",
        message="feat: release candidate",
        author="dev",
        files_changed=3,
        insertions=20,
        deletions=5,
        branch="main",
    )

    with (
        patch("zetherion_dev_agent.state.ScanState.load", return_value=state),
        patch("zetherion_dev_agent.state.ScanState.save", return_value=None),
        patch("zetherion_dev_agent.watchers.git.get_repo_name", return_value="repo"),
        patch("zetherion_dev_agent.watchers.git.get_new_commits", return_value=[commit]),
        patch("zetherion_dev_agent.watchers.git.get_latest_sha", return_value=commit.sha),
        patch("zetherion_dev_agent.watchers.git.get_tags", return_value=[]),
        patch("zetherion_dev_agent.sender.send_event", new_callable=AsyncMock) as send_event,
    ):
        send_event.return_value = True
        await _watch_loop(config, once=True, verbose=False)

    event_types = [call.args[2] for call in send_event.await_args_list]
    assert event_types.count("commit") == 1
    assert event_types.count("deploy") == 1


@pytest.mark.asyncio
async def test_new_tag_emits_tag_and_deploy_marker() -> None:
    config = AgentConfig(
        webhook_url="https://discord.test/webhook",
        agent_name="zetherion-dev-agent",
        repos=["/tmp/repo"],
        scan_interval=1,
        claude_code_enabled=False,
        annotations_enabled=False,
        git_enabled=True,
    )
    state = ScanState()

    tag = TagInfo(name="v1.2.3", sha="abcd1234")

    with (
        patch("zetherion_dev_agent.state.ScanState.load", return_value=state),
        patch("zetherion_dev_agent.state.ScanState.save", return_value=None),
        patch("zetherion_dev_agent.watchers.git.get_repo_name", return_value="repo"),
        patch("zetherion_dev_agent.watchers.git.get_new_commits", return_value=[]),
        patch("zetherion_dev_agent.watchers.git.get_latest_sha", return_value="abcd1234"),
        patch("zetherion_dev_agent.watchers.git.get_tags", return_value=[tag]),
        patch("zetherion_dev_agent.sender.send_event", new_callable=AsyncMock) as send_event,
    ):
        send_event.return_value = True
        await _watch_loop(config, once=True, verbose=False)

    event_types = [call.args[2] for call in send_event.await_args_list]
    assert event_types.count("tag") == 1
    assert event_types.count("deploy") == 1
