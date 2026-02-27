"""Tests for dev-agent policy persistence and cleanup history."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEV_AGENT_SRC = PROJECT_ROOT / "zetherion-dev-agent" / "src"
if str(DEV_AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(DEV_AGENT_SRC))

from zetherion_dev_agent.policy_store import PolicyStore


def test_record_discovery_creates_pending(tmp_path: Path) -> None:
    db_path = tmp_path / "policy.db"
    store = PolicyStore(str(db_path))
    try:
        created = store.record_project_discovery("proj-a")
        assert created is True

        pending = store.list_pending_approvals()
        assert len(pending) == 1
        assert pending[0].project_id == "proj-a"
        assert pending[0].prompt_count == 0
    finally:
        store.close()


def test_set_policy_updates_mode_and_pending_state(tmp_path: Path) -> None:
    db_path = tmp_path / "policy.db"
    store = PolicyStore(str(db_path))
    try:
        _ = store.record_project_discovery("proj-b")
        store.mark_prompted("proj-b")
        store.set_policy("proj-b", "auto_clean", source="test")

        assert store.get_policy("proj-b") == "auto_clean"
        pending = store.list_pending_approvals()
        assert pending == []

        rows = store.list_policies()
        assert len(rows) == 1
        assert rows[0]["project_id"] == "proj-b"
        assert rows[0]["mode"] == "auto_clean"
    finally:
        store.close()


def test_cleanup_history_roundtrip(tmp_path: Path) -> None:
    db_path = tmp_path / "policy.db"
    store = PolicyStore(str(db_path))
    try:
        store.record_cleanup_run(
            project_id="proj-c",
            actions=[{"action_type": "remove_containers", "target": "abc"}],
            dry_run=False,
            success=True,
            error=None,
        )
        history = store.list_cleanup_runs(limit=5)
        assert len(history) == 1
        assert history[0]["project_id"] == "proj-c"
        assert history[0]["success"] is True
        assert history[0]["dry_run"] is False
        assert history[0]["actions"][0]["action_type"] == "remove_containers"
    finally:
        store.close()
