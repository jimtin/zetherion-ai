"""Tests for UpdateCheckerSkill."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from zetherion_ai import __version__
from zetherion_ai.health.storage import UpdateRecord, UpdateStatus
from zetherion_ai.skills.base import (
    HeartbeatAction,
    SkillRequest,
)
from zetherion_ai.skills.permissions import Permission
from zetherion_ai.skills.update_checker import UpdateCheckerSkill

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(intent: str, user_id: str = "user1") -> SkillRequest:
    """Build a SkillRequest with the given intent."""
    return SkillRequest(id=uuid4(), user_id=user_id, intent=intent)


def _make_release(**overrides: object) -> MagicMock:
    """Return a mock ReleaseInfo."""
    release = MagicMock()
    release.version = overrides.get("version", "1.2.3")
    release.tag = overrides.get("tag", "v1.2.3")
    release.published_at = overrides.get("published_at", "2026-01-01T00:00:00Z")
    release.html_url = overrides.get("html_url", "https://github.com/r/r/releases/1")
    release.body = overrides.get("body", "Release notes")
    release.to_dict.return_value = {
        "tag": release.tag,
        "version": release.version,
        "published_at": release.published_at,
        "html_url": release.html_url,
        "body": release.body,
    }
    return release


def _make_update_result(status_value: str = "success", error: str | None = None) -> MagicMock:
    """Return a mock UpdateResult."""
    result = MagicMock()
    result.status.value = status_value
    result.error = error
    result.to_dict.return_value = {
        "status": status_value,
        "current_version": __version__,
        "target_version": "1.2.3",
        "error": error,
    }
    return result


def _make_skill(
    *,
    enabled: bool = True,
    github_repo: str = "owner/repo",
    auto_apply: bool = False,
    with_manager: bool = True,
    with_storage: bool = False,
    db_pool: MagicMock | None = None,
) -> UpdateCheckerSkill:
    """Create an UpdateCheckerSkill with mocks wired in (bypassing initialize)."""
    skill = UpdateCheckerSkill(
        enabled=enabled,
        github_repo=github_repo,
        auto_apply=auto_apply,
        db_pool=db_pool,
    )

    if with_manager:
        manager = AsyncMock()
        manager.current_version = __version__
        manager.check_for_update = AsyncMock(return_value=None)
        manager.apply_update = AsyncMock(return_value=_make_update_result())
        manager.rollback = AsyncMock(return_value=True)
        skill._manager = manager

    if with_storage:
        storage = MagicMock()
        storage._pool = db_pool or MagicMock()
        storage.get_update_history = AsyncMock(return_value=[])
        skill._storage = storage

    return skill


# ---------------------------------------------------------------------------
# 1. metadata
# ---------------------------------------------------------------------------


class TestMetadata:
    """Tests for the metadata property."""

    def test_metadata_name(self) -> None:
        skill = UpdateCheckerSkill()
        assert skill.metadata.name == "update_checker"

    def test_metadata_intents(self) -> None:
        skill = UpdateCheckerSkill()
        assert skill.metadata.intents == [
            "check_update",
            "apply_update",
            "rollback_update",
            "update_status",
        ]

    def test_metadata_permissions(self) -> None:
        skill = UpdateCheckerSkill()
        perms = skill.metadata.permissions
        assert Permission.READ_CONFIG in perms
        assert Permission.SEND_MESSAGES in perms
        assert Permission.SEND_DM in perms

    def test_metadata_version(self) -> None:
        skill = UpdateCheckerSkill()
        assert skill.metadata.version == "0.1.0"

    def test_metadata_description(self) -> None:
        skill = UpdateCheckerSkill()
        assert len(skill.metadata.description) > 0


# ---------------------------------------------------------------------------
# 2. initialize()
# ---------------------------------------------------------------------------


class TestInitialize:
    """Tests for the initialize() async method."""

    @pytest.mark.asyncio
    async def test_disabled_returns_true_no_manager(self) -> None:
        """When disabled, initialize() returns True and manager stays None."""
        skill = UpdateCheckerSkill(enabled=False, github_repo="owner/repo")
        result = await skill.initialize()
        assert result is True
        assert skill._manager is None

    @pytest.mark.asyncio
    async def test_no_repo_returns_true_no_manager(self) -> None:
        """When github_repo is empty, initialize() returns True and manager stays None."""
        skill = UpdateCheckerSkill(enabled=True, github_repo="")
        result = await skill.initialize()
        assert result is True
        assert skill._manager is None

    @pytest.mark.asyncio
    async def test_enabled_with_repo_creates_manager(self) -> None:
        """When enabled with a repo, initialize() creates the UpdateManager."""
        skill = UpdateCheckerSkill(
            enabled=True,
            github_repo="owner/repo",
            github_token="tok123",
        )

        with (
            patch("zetherion_ai.health.storage.HealthStorage") as mock_storage_cls,
            patch("zetherion_ai.updater.manager.UpdateManager") as mock_manager_cls,
        ):
            storage_instance = MagicMock()
            storage_instance.initialize = AsyncMock()
            mock_storage_cls.return_value = storage_instance

            manager_instance = MagicMock()
            mock_manager_cls.return_value = manager_instance

            result = await skill.initialize()

        assert result is True
        assert skill._manager is manager_instance
        assert skill._storage is storage_instance
        mock_manager_cls.assert_called_once_with(
            github_repo="owner/repo",
            storage=storage_instance,
            github_token="tok123",
        )

    @pytest.mark.asyncio
    async def test_with_db_pool_calls_storage_initialize(self) -> None:
        """When db_pool is provided, initialize() awaits storage.initialize()."""
        pool = MagicMock()
        skill = UpdateCheckerSkill(
            enabled=True,
            github_repo="owner/repo",
            db_pool=pool,
        )

        with (
            patch("zetherion_ai.health.storage.HealthStorage") as mock_storage_cls,
            patch("zetherion_ai.updater.manager.UpdateManager"),
        ):
            storage_instance = MagicMock()
            storage_instance.initialize = AsyncMock()
            mock_storage_cls.return_value = storage_instance

            result = await skill.initialize()

        assert result is True
        storage_instance.initialize.assert_awaited_once_with(pool)

    @pytest.mark.asyncio
    async def test_without_db_pool_skips_storage_initialize(self) -> None:
        """When db_pool is None, storage.initialize() is NOT called."""
        skill = UpdateCheckerSkill(
            enabled=True,
            github_repo="owner/repo",
            db_pool=None,
        )

        with (
            patch("zetherion_ai.health.storage.HealthStorage") as mock_storage_cls,
            patch("zetherion_ai.updater.manager.UpdateManager"),
        ):
            storage_instance = MagicMock()
            storage_instance.initialize = AsyncMock()
            mock_storage_cls.return_value = storage_instance

            await skill.initialize()

        storage_instance.initialize.assert_not_awaited()


# ---------------------------------------------------------------------------
# 3. handle() — intent dispatch
# ---------------------------------------------------------------------------


class TestHandle:
    """Tests for the handle() intent dispatch."""

    @pytest.mark.asyncio
    async def test_dispatches_check_update(self) -> None:
        skill = _make_skill()
        req = _make_request("check_update")
        resp = await skill.handle(req)
        assert resp.success is True

    @pytest.mark.asyncio
    async def test_dispatches_apply_update(self) -> None:
        skill = _make_skill()
        req = _make_request("apply_update")
        resp = await skill.handle(req)
        # No pending release and check returns None -> "No update available."
        assert resp.success is True
        assert "No update" in resp.message

    @pytest.mark.asyncio
    async def test_dispatches_rollback_update(self) -> None:
        skill = _make_skill()
        req = _make_request("rollback_update")
        resp = await skill.handle(req)
        # No storage -> error
        assert resp.success is False

    @pytest.mark.asyncio
    async def test_dispatches_update_status(self) -> None:
        skill = _make_skill()
        req = _make_request("update_status")
        resp = await skill.handle(req)
        assert resp.success is True
        assert __version__ in resp.message

    @pytest.mark.asyncio
    async def test_unknown_intent_returns_error(self) -> None:
        skill = _make_skill()
        req = _make_request("do_magic")
        resp = await skill.handle(req)
        assert resp.success is False
        assert "Unknown update intent" in resp.error
        assert "do_magic" in resp.error


# ---------------------------------------------------------------------------
# 4. on_heartbeat()
# ---------------------------------------------------------------------------


class TestOnHeartbeat:
    """Tests for the on_heartbeat() async method."""

    @pytest.mark.asyncio
    async def test_disabled_returns_empty(self) -> None:
        skill = _make_skill(enabled=False, with_manager=False)
        actions = await skill.on_heartbeat(["user1"])
        assert actions == []

    @pytest.mark.asyncio
    async def test_no_manager_returns_empty(self) -> None:
        skill = _make_skill(with_manager=False)
        actions = await skill.on_heartbeat(["user1"])
        assert actions == []

    @pytest.mark.asyncio
    async def test_beat_not_divisible_by_6_returns_empty(self) -> None:
        skill = _make_skill()
        # Beats 1 through 5 should not trigger a check
        for _ in range(5):
            actions = await skill.on_heartbeat(["user1"])
            assert actions == []
        skill._manager.check_for_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_beat_divisible_by_6_no_update_returns_empty(self) -> None:
        skill = _make_skill()
        skill._beat_count = 5  # next beat is 6
        skill._manager.check_for_update.return_value = None

        actions = await skill.on_heartbeat(["user1"])

        assert actions == []
        skill._manager.check_for_update.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_update_available_auto_apply_false_notifies(self) -> None:
        skill = _make_skill(auto_apply=False)
        release = _make_release(version="2.0.0")
        skill._beat_count = 5
        skill._manager.check_for_update.return_value = release

        actions = await skill.on_heartbeat(["owner_user"])

        assert len(actions) == 1
        action = actions[0]
        assert isinstance(action, HeartbeatAction)
        assert action.skill_name == "update_checker"
        assert action.action_type == "send_message"
        assert action.user_id == "owner_user"
        assert action.priority == 7
        assert "Update Available" in action.data["message"]
        assert "v2.0.0" in action.data["message"]
        assert "apply update" in action.data["message"]

    @pytest.mark.asyncio
    async def test_update_available_auto_apply_true_success(self) -> None:
        skill = _make_skill(auto_apply=True)
        release = _make_release(version="2.0.0")
        skill._beat_count = 5
        skill._manager.check_for_update.return_value = release
        skill._manager.apply_update.return_value = _make_update_result("success")

        actions = await skill.on_heartbeat(["owner_user"])

        assert len(actions) == 1
        action = actions[0]
        assert action.priority == 8
        assert "Update Applied" in action.data["message"]
        assert "v2.0.0" in action.data["message"]
        skill._manager.apply_update.assert_awaited_once_with(release)

    @pytest.mark.asyncio
    async def test_update_available_auto_apply_true_failure(self) -> None:
        skill = _make_skill(auto_apply=True)
        release = _make_release(version="2.0.0")
        skill._beat_count = 5
        skill._manager.check_for_update.return_value = release
        skill._manager.apply_update.return_value = _make_update_result(
            "failed", error="docker build failed"
        )

        actions = await skill.on_heartbeat(["owner_user"])

        assert len(actions) == 1
        action = actions[0]
        assert action.priority == 9
        assert "Update Failed" in action.data["message"]
        assert "v2.0.0" in action.data["message"]
        assert "docker build failed" in action.data["message"]

    @pytest.mark.asyncio
    async def test_empty_user_ids_gives_empty_user_id(self) -> None:
        skill = _make_skill(auto_apply=False)
        release = _make_release(version="2.0.0")
        skill._beat_count = 5
        skill._manager.check_for_update.return_value = release

        actions = await skill.on_heartbeat([])

        assert len(actions) == 1
        assert actions[0].user_id == ""

    @pytest.mark.asyncio
    async def test_stores_pending_release(self) -> None:
        skill = _make_skill(auto_apply=False)
        release = _make_release(version="2.0.0")
        skill._beat_count = 5
        skill._manager.check_for_update.return_value = release

        await skill.on_heartbeat(["user1"])

        assert skill._pending_release is release

    @pytest.mark.asyncio
    async def test_increments_beat_count(self) -> None:
        skill = _make_skill()
        assert skill._beat_count == 0
        await skill.on_heartbeat(["user1"])
        assert skill._beat_count == 1
        await skill.on_heartbeat(["user1"])
        assert skill._beat_count == 2


# ---------------------------------------------------------------------------
# 5. get_system_prompt_fragment()
# ---------------------------------------------------------------------------


class TestGetSystemPromptFragment:
    """Tests for the get_system_prompt_fragment() method."""

    def test_returns_version_string(self) -> None:
        skill = UpdateCheckerSkill()
        fragment = skill.get_system_prompt_fragment("user1")
        assert fragment is not None
        assert f"v{__version__}" in fragment
        assert "[Version]" in fragment

    def test_with_pending_release_includes_update_info(self) -> None:
        skill = UpdateCheckerSkill()
        skill._pending_release = _make_release(version="9.9.9")

        fragment = skill.get_system_prompt_fragment("user1")

        assert fragment is not None
        assert "Update v9.9.9 available" in fragment
        assert f"v{__version__}" in fragment

    def test_without_pending_release_no_update_info(self) -> None:
        skill = UpdateCheckerSkill()
        skill._pending_release = None

        fragment = skill.get_system_prompt_fragment("user1")

        assert "Update" not in fragment


# ---------------------------------------------------------------------------
# 6. _handle_check()
# ---------------------------------------------------------------------------


class TestHandleCheck:
    """Tests for the _handle_check() intent handler."""

    @pytest.mark.asyncio
    async def test_no_manager_returns_error(self) -> None:
        skill = _make_skill(with_manager=False)
        req = _make_request("check_update")
        resp = await skill.handle(req)
        assert resp.success is False
        assert "not configured" in resp.error

    @pytest.mark.asyncio
    async def test_no_update_returns_up_to_date(self) -> None:
        skill = _make_skill()
        skill._manager.check_for_update.return_value = None
        req = _make_request("check_update")

        resp = await skill.handle(req)

        assert resp.success is True
        assert resp.data["up_to_date"] is True
        assert "up to date" in resp.message

    @pytest.mark.asyncio
    async def test_update_available_returns_release_info(self) -> None:
        skill = _make_skill()
        release = _make_release(version="3.0.0")
        skill._manager.check_for_update.return_value = release
        req = _make_request("check_update")

        resp = await skill.handle(req)

        assert resp.success is True
        assert resp.data["up_to_date"] is False
        assert resp.data["release"]["version"] == "3.0.0"
        assert "3.0.0" in resp.message
        # Should set pending release
        assert skill._pending_release is release


# ---------------------------------------------------------------------------
# 7. _handle_apply()
# ---------------------------------------------------------------------------


class TestHandleApply:
    """Tests for the _handle_apply() intent handler."""

    @pytest.mark.asyncio
    async def test_no_manager_returns_error(self) -> None:
        skill = _make_skill(with_manager=False)
        req = _make_request("apply_update")
        resp = await skill.handle(req)
        assert resp.success is False
        assert "not configured" in resp.error

    @pytest.mark.asyncio
    async def test_no_pending_no_available_returns_no_update(self) -> None:
        skill = _make_skill()
        skill._manager.check_for_update.return_value = None
        req = _make_request("apply_update")

        resp = await skill.handle(req)

        assert resp.success is True
        assert "No update available" in resp.message

    @pytest.mark.asyncio
    async def test_no_pending_finds_and_applies_update(self) -> None:
        skill = _make_skill()
        release = _make_release(version="2.0.0")
        skill._manager.check_for_update.return_value = release
        skill._manager.apply_update.return_value = _make_update_result("success")
        req = _make_request("apply_update")

        resp = await skill.handle(req)

        assert resp.success is True
        skill._manager.apply_update.assert_awaited_once_with(release)
        # Pending should be cleared after apply
        assert skill._pending_release is None

    @pytest.mark.asyncio
    async def test_pending_exists_applies_it(self) -> None:
        skill = _make_skill()
        release = _make_release(version="2.0.0")
        skill._pending_release = release
        skill._manager.apply_update.return_value = _make_update_result("success")
        req = _make_request("apply_update")

        resp = await skill.handle(req)

        assert resp.success is True
        skill._manager.apply_update.assert_awaited_once_with(release)
        # check_for_update should NOT have been called since pending existed
        skill._manager.check_for_update.assert_not_awaited()
        assert skill._pending_release is None

    @pytest.mark.asyncio
    async def test_apply_success_message(self) -> None:
        skill = _make_skill()
        skill._pending_release = _make_release()
        skill._manager.apply_update.return_value = _make_update_result("success")
        req = _make_request("apply_update")

        resp = await skill.handle(req)

        assert resp.success is True
        assert "success" in resp.message
        assert "OK" in resp.message

    @pytest.mark.asyncio
    async def test_apply_failure_message(self) -> None:
        skill = _make_skill()
        skill._pending_release = _make_release()
        skill._manager.apply_update.return_value = _make_update_result(
            "failed", error="docker build failed"
        )
        req = _make_request("apply_update")

        resp = await skill.handle(req)

        assert resp.success is False
        assert "failed" in resp.message
        assert "docker build failed" in resp.message


# ---------------------------------------------------------------------------
# 8. _handle_rollback()
# ---------------------------------------------------------------------------


class TestHandleRollback:
    """Tests for the _handle_rollback() intent handler."""

    @pytest.mark.asyncio
    async def test_no_manager_returns_error(self) -> None:
        skill = _make_skill(with_manager=False)
        req = _make_request("rollback_update")
        resp = await skill.handle(req)
        assert resp.success is False
        assert "not configured" in resp.error

    @pytest.mark.asyncio
    async def test_no_storage_pool_returns_error(self) -> None:
        skill = _make_skill(with_storage=False)
        # _storage is None by default from _make_skill without with_storage
        req = _make_request("rollback_update")
        resp = await skill.handle(req)
        assert resp.success is False
        assert "No update history" in resp.error

    @pytest.mark.asyncio
    async def test_storage_pool_is_none_returns_error(self) -> None:
        skill = _make_skill(with_storage=True)
        skill._storage._pool = None
        req = _make_request("rollback_update")
        resp = await skill.handle(req)
        assert resp.success is False
        assert "No update history" in resp.error

    @pytest.mark.asyncio
    async def test_no_history_returns_no_history(self) -> None:
        skill = _make_skill(with_storage=True)
        skill._storage.get_update_history.return_value = []
        req = _make_request("rollback_update")

        resp = await skill.handle(req)

        assert resp.success is False
        assert "No update history to rollback" in resp.message

    @pytest.mark.asyncio
    async def test_no_git_sha_returns_error(self) -> None:
        skill = _make_skill(with_storage=True)
        record = UpdateRecord(
            timestamp=datetime.now(),
            version="0.1.0",
            previous_version="0.0.9",
            git_sha="",
            status=UpdateStatus.SUCCESS,
        )
        skill._storage.get_update_history.return_value = [record]
        req = _make_request("rollback_update")

        resp = await skill.handle(req)

        assert resp.success is False
        assert "No git SHA" in resp.error

    @pytest.mark.asyncio
    async def test_successful_rollback(self) -> None:
        skill = _make_skill(with_storage=True)
        sha = "abc123def456789000"
        record = UpdateRecord(
            timestamp=datetime.now(),
            version="0.1.0",
            previous_version="0.0.9",
            git_sha=sha,
            status=UpdateStatus.SUCCESS,
        )
        skill._storage.get_update_history.return_value = [record]
        skill._manager.rollback.return_value = True
        req = _make_request("rollback_update")

        resp = await skill.handle(req)

        assert resp.success is True
        assert "Rollback complete" in resp.message
        assert resp.data["rolled_back_to"] == sha[:12]
        skill._manager.rollback.assert_awaited_once_with(sha)

    @pytest.mark.asyncio
    async def test_failed_rollback(self) -> None:
        skill = _make_skill(with_storage=True)
        sha = "abc123def456789000"
        record = UpdateRecord(
            timestamp=datetime.now(),
            version="0.1.0",
            previous_version="0.0.9",
            git_sha=sha,
            status=UpdateStatus.SUCCESS,
        )
        skill._storage.get_update_history.return_value = [record]
        skill._manager.rollback.return_value = False
        req = _make_request("rollback_update")

        resp = await skill.handle(req)

        assert resp.success is False
        assert "Rollback failed" in resp.message

    @pytest.mark.asyncio
    async def test_exception_returns_error(self) -> None:
        skill = _make_skill(with_storage=True)
        skill._storage.get_update_history.side_effect = RuntimeError("DB down")
        req = _make_request("rollback_update")

        resp = await skill.handle(req)

        assert resp.success is False
        assert "Rollback failed" in resp.error
        assert "DB down" in resp.error


# ---------------------------------------------------------------------------
# 9. _handle_status()
# ---------------------------------------------------------------------------


class TestHandleStatus:
    """Tests for the _handle_status() intent handler."""

    @pytest.mark.asyncio
    async def test_returns_current_version(self) -> None:
        skill = _make_skill(github_repo="owner/repo", auto_apply=True, enabled=True)
        req = _make_request("update_status")

        resp = await skill.handle(req)

        assert resp.success is True
        assert f"v{__version__}" in resp.message
        assert resp.data["current_version"] == __version__
        assert resp.data["auto_apply"] is True
        assert resp.data["enabled"] is True
        assert resp.data["repo"] == "owner/repo"

    @pytest.mark.asyncio
    async def test_includes_pending_release(self) -> None:
        skill = _make_skill()
        release = _make_release(version="5.0.0")
        skill._pending_release = release
        req = _make_request("update_status")

        resp = await skill.handle(req)

        assert resp.success is True
        assert "pending_release" in resp.data
        assert resp.data["pending_release"]["version"] == "5.0.0"

    @pytest.mark.asyncio
    async def test_no_pending_release_omits_key(self) -> None:
        skill = _make_skill()
        skill._pending_release = None
        req = _make_request("update_status")

        resp = await skill.handle(req)

        assert resp.success is True
        assert "pending_release" not in resp.data
