"""Tests for updater_sidecar.executor — UpdateExecutor and subprocess orchestration."""

from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import AsyncMock, patch

from updater_sidecar.executor import UpdateExecutor

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_executor(
    health_urls: dict[str, str] | None = None,
) -> UpdateExecutor:
    """Return an UpdateExecutor with safe defaults for testing.

    Note: UpdateExecutor.__init__ does ``health_urls or DEFAULT_HEALTH_URLS``
    so passing an empty dict falls through to the default.  We always override
    ``_health_urls`` after construction to guarantee test isolation.
    """
    ex = UpdateExecutor(
        project_dir="/tmp/test-project",
        compose_file="/tmp/test-project/docker-compose.yml",
    )
    # Override explicitly so tests never hit real DEFAULT_HEALTH_URLS
    ex._health_urls = health_urls if health_urls is not None else {}
    return ex


def _patch_run_cmd(executor: UpdateExecutor, side_effects: list[str | None]):
    """Return a patch context manager for _run_cmd with ordered returns."""
    return patch.object(
        executor,
        "_run_cmd",
        new_callable=AsyncMock,
        side_effect=side_effects,
    )


def _patch_health(return_value: bool = True):
    """Patch check_service_health to return a fixed value."""
    return patch(
        "updater_sidecar.executor.check_service_health",
        new_callable=AsyncMock,
        return_value=return_value,
    )


# ---------------------------------------------------------------------------
# TestUpdateExecutorInit
# ---------------------------------------------------------------------------


class TestUpdateExecutorInit:
    """Tests for UpdateExecutor constructor and properties."""

    def test_default_state(self) -> None:
        ex = _make_executor()
        assert ex.state == "idle"
        assert ex.current_operation is None
        assert ex.is_busy is False

    def test_custom_health_urls(self) -> None:
        urls = {"svc1": "http://svc1:8080/health"}
        ex = _make_executor(health_urls=urls)
        assert ex._health_urls == urls

    def test_default_health_urls_used_when_none_in_constructor(self) -> None:
        """When constructed without overriding, DEFAULT_HEALTH_URLS are used."""
        ex = UpdateExecutor(
            project_dir="/proj",
            compose_file="/proj/docker-compose.yml",
        )
        # Should use DEFAULT_HEALTH_URLS (not overridden by helper)
        assert "zetherion-ai-skills" in ex._health_urls
        assert "zetherion-ai-api" in ex._health_urls


# ---------------------------------------------------------------------------
# TestApplyUpdate
# ---------------------------------------------------------------------------


class TestApplyUpdate:
    """Tests for UpdateExecutor.apply_update()."""

    async def test_successful_update_no_health_urls(self) -> None:
        """Full update succeeds when there are no health URLs to check."""
        ex = _make_executor(health_urls={})

        # apply_update calls: git rev-parse HEAD, git fetch, git checkout,
        # git rev-parse HEAD, docker compose build,
        # then for each of 3 services: docker compose up -d
        run_results = [
            "abc123\n",  # git rev-parse HEAD (initial)
            "ok\n",  # git fetch origin tag v1.0.0
            "ok\n",  # git checkout v1.0.0
            "def456\n",  # git rev-parse HEAD (new)
            "ok\n",  # docker compose build
            "ok\n",  # restart zetherion-ai-skills
            "ok\n",  # restart zetherion-ai-api
            "ok\n",  # restart zetherion-ai-bot
        ]

        with _patch_run_cmd(ex, run_results):
            result = await ex.apply_update("v1.0.0", "1.0.0")

        assert result.status == "success"
        assert result.previous_sha == "abc123"
        assert result.new_sha == "def456"
        assert "git_fetch" in result.steps_completed
        assert "git_checkout" in result.steps_completed
        assert "docker_build" in result.steps_completed
        assert "restart_zetherion-ai-skills" in result.steps_completed
        assert "restart_zetherion-ai-api" in result.steps_completed
        assert "restart_zetherion-ai-bot" in result.steps_completed
        assert result.completed_at is not None
        assert result.duration_seconds >= 0
        assert result.error is None

    async def test_successful_update_with_health_checks(self) -> None:
        """Update succeeds with health checks passing."""
        health_urls = {
            "zetherion-ai-skills": "http://skills:8080/health",
            "zetherion-ai-api": "http://api:8443/health",
        }
        ex = _make_executor(health_urls=health_urls)

        run_results = [
            "abc123\n",  # git rev-parse HEAD (initial)
            "ok\n",  # git fetch
            "ok\n",  # git checkout
            "def456\n",  # git rev-parse HEAD (new)
            "ok\n",  # docker compose build
            "ok\n",  # restart zetherion-ai-skills
            "ok\n",  # restart zetherion-ai-api
            "ok\n",  # restart zetherion-ai-bot
        ]

        with (
            _patch_run_cmd(ex, run_results),
            _patch_health(return_value=True),
        ):
            result = await ex.apply_update("v1.0.0", "1.0.0")

        assert result.status == "success"
        assert "health_zetherion-ai-skills" in result.steps_completed
        assert "health_zetherion-ai-api" in result.steps_completed

    async def test_git_rev_parse_fails(self) -> None:
        """If initial git rev-parse HEAD fails, update fails immediately."""
        ex = _make_executor(health_urls={})

        with _patch_run_cmd(ex, [None]):
            result = await ex.apply_update("v1.0.0", "1.0.0")

        assert result.status == "failed"
        assert result.error == "Failed to get current git SHA"
        assert result.completed_at is not None

    async def test_git_fetch_fails(self) -> None:
        ex = _make_executor(health_urls={})

        with _patch_run_cmd(ex, ["abc123\n", None]):
            result = await ex.apply_update("v1.0.0", "1.0.0")

        assert result.status == "failed"
        assert result.error == "git fetch failed"
        assert result.previous_sha == "abc123"

    async def test_git_checkout_fails(self) -> None:
        ex = _make_executor(health_urls={})

        with _patch_run_cmd(ex, ["abc123\n", "ok\n", None]):
            result = await ex.apply_update("v1.0.0", "1.0.0")

        assert result.status == "failed"
        assert result.error == "git checkout failed"
        assert "git_fetch" in result.steps_completed
        assert "git_checkout" not in result.steps_completed

    async def test_docker_build_fails_triggers_rollback(self) -> None:
        """Docker build failure triggers rollback attempt."""
        ex = _make_executor(health_urls={})

        run_results = [
            "abc123\n",  # git rev-parse HEAD
            "ok\n",  # git fetch
            "ok\n",  # git checkout
            "def456\n",  # git rev-parse HEAD (new)
            None,  # docker compose build FAILS
        ]

        with (
            _patch_run_cmd(ex, run_results),
            patch.object(ex, "_attempt_rollback", new_callable=AsyncMock, return_value=True),
        ):
            result = await ex.apply_update("v1.0.0", "1.0.0")

        assert result.status == "rolled_back"
        assert result.error == "docker build failed"

    async def test_service_restart_fails_triggers_rollback(self) -> None:
        """Service restart failure triggers rollback."""
        ex = _make_executor(health_urls={})

        run_results = [
            "abc123\n",  # git rev-parse HEAD
            "ok\n",  # git fetch
            "ok\n",  # git checkout
            "def456\n",  # git rev-parse HEAD (new)
            "ok\n",  # docker compose build
            None,  # restart first service FAILS
        ]

        with (
            _patch_run_cmd(ex, run_results),
            patch.object(ex, "_attempt_rollback", new_callable=AsyncMock, return_value=True),
        ):
            result = await ex.apply_update("v1.0.0", "1.0.0")

        assert result.status == "rolled_back"
        assert "Failed to restart" in result.error

    async def test_health_check_fails_triggers_rollback(self) -> None:
        """Health check failure after restart triggers rollback."""
        health_urls = {"zetherion-ai-skills": "http://skills:8080/health"}
        ex = _make_executor(health_urls=health_urls)

        run_results = [
            "abc123\n",  # git rev-parse HEAD
            "ok\n",  # git fetch
            "ok\n",  # git checkout
            "def456\n",  # git rev-parse HEAD (new)
            "ok\n",  # docker compose build
            "ok\n",  # restart zetherion-ai-skills
        ]

        with (
            _patch_run_cmd(ex, run_results),
            _patch_health(return_value=False),
            patch.object(ex, "_attempt_rollback", new_callable=AsyncMock, return_value=True),
        ):
            result = await ex.apply_update("v1.0.0", "1.0.0")

        assert result.status == "rolled_back"
        assert "Health check failed" in result.error

    async def test_state_returns_to_idle_after_update(self) -> None:
        ex = _make_executor(health_urls={})

        run_results = [
            "abc123\n",
            "ok\n",
            "ok\n",
            "def456\n",
            "ok\n",
            "ok\n",
            "ok\n",
            "ok\n",
        ]

        with _patch_run_cmd(ex, run_results):
            await ex.apply_update("v1.0.0", "1.0.0")

        assert ex.state == "idle"
        assert ex.current_operation is None

    async def test_state_returns_to_idle_after_failure(self) -> None:
        ex = _make_executor(health_urls={})

        with _patch_run_cmd(ex, [None]):
            await ex.apply_update("v1.0.0", "1.0.0")

        assert ex.state == "idle"
        assert ex.current_operation is None

    async def test_concurrent_update_returns_failed(self) -> None:
        """Second concurrent apply_update returns failed immediately."""

        ex = _make_executor(health_urls={})

        # The first call will hold the lock via slow _run_cmd
        slow_call_started = asyncio.Event()

        async def slow_run_cmd(cmd: str, timeout: int = 120) -> str | None:
            slow_call_started.set()
            await asyncio.sleep(10)
            return "ok\n"

        with patch.object(ex, "_run_cmd", side_effect=slow_run_cmd):
            task1 = asyncio.create_task(ex.apply_update("v1.0.0", "1.0.0"))
            # Wait for the first task to actually start and acquire the lock
            await slow_call_started.wait()

            # Second call should fail immediately
            result2 = await ex.apply_update("v2.0.0", "2.0.0")
            assert result2.status == "failed"
            assert result2.error == "Update already in progress"

            task1.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task1

    async def test_unexpected_exception_is_caught(self) -> None:
        """Unexpected errors are caught and reported."""
        ex = _make_executor(health_urls={})

        with patch.object(
            ex,
            "_run_cmd",
            new_callable=AsyncMock,
            side_effect=RuntimeError("unexpected boom"),
        ):
            result = await ex.apply_update("v1.0.0", "1.0.0")

        assert result.status == "failed"
        assert "Unexpected error" in result.error
        assert ex.state == "idle"


# ---------------------------------------------------------------------------
# TestRollback
# ---------------------------------------------------------------------------


class TestRollback:
    """Tests for UpdateExecutor.rollback()."""

    async def test_successful_rollback(self) -> None:
        ex = _make_executor(health_urls={})

        with patch.object(ex, "_attempt_rollback", new_callable=AsyncMock, return_value=True):
            result = await ex.rollback("abc123")

        assert result.status == "success"
        assert result.previous_sha == "abc123"
        assert result.new_sha == "abc123"
        assert result.completed_at is not None
        assert result.duration_seconds >= 0

    async def test_failed_rollback(self) -> None:
        ex = _make_executor(health_urls={})

        with patch.object(ex, "_attempt_rollback", new_callable=AsyncMock, return_value=False):
            result = await ex.rollback("abc123")

        assert result.status == "failed"
        assert result.error == "Rollback failed"

    async def test_rollback_state_returns_to_idle(self) -> None:
        ex = _make_executor(health_urls={})

        with patch.object(ex, "_attempt_rollback", new_callable=AsyncMock, return_value=True):
            await ex.rollback("abc123")

        assert ex.state == "idle"
        assert ex.current_operation is None

    async def test_concurrent_rollback_returns_failed(self) -> None:

        ex = _make_executor(health_urls={})

        slow_started = asyncio.Event()

        async def slow_rollback(sha: str) -> bool:
            slow_started.set()
            await asyncio.sleep(10)
            return True

        with patch.object(ex, "_attempt_rollback", side_effect=slow_rollback):
            task1 = asyncio.create_task(ex.rollback("abc123"))
            await slow_started.wait()

            result2 = await ex.rollback("def456")
            assert result2.status == "failed"
            assert result2.error == "Operation already in progress"

            task1.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task1


# ---------------------------------------------------------------------------
# TestAttemptRollback
# ---------------------------------------------------------------------------


class TestAttemptRollback:
    """Tests for UpdateExecutor._attempt_rollback()."""

    async def test_empty_sha_returns_false(self) -> None:
        ex = _make_executor(health_urls={})
        result = await ex._attempt_rollback("")
        assert result is False

    async def test_successful_rollback_no_health_urls(self) -> None:
        """Full rollback succeeds: checkout, build, restart all services."""
        ex = _make_executor(health_urls={})

        # checkout, build, restart x3
        run_results = [
            "ok\n",  # git checkout
            "ok\n",  # docker compose build
            "ok\n",  # restart svc1
            "ok\n",  # restart svc2
            "ok\n",  # restart svc3
        ]

        with _patch_run_cmd(ex, run_results):
            result = await ex._attempt_rollback("abc123")

        assert result is True

    async def test_checkout_fails(self) -> None:
        ex = _make_executor(health_urls={})

        with _patch_run_cmd(ex, [None]):
            result = await ex._attempt_rollback("abc123")

        assert result is False

    async def test_build_fails(self) -> None:
        ex = _make_executor(health_urls={})

        with _patch_run_cmd(ex, ["ok\n", None]):
            result = await ex._attempt_rollback("abc123")

        assert result is False

    async def test_restart_fails(self) -> None:
        ex = _make_executor(health_urls={})

        with _patch_run_cmd(ex, ["ok\n", "ok\n", None]):
            result = await ex._attempt_rollback("abc123")

        assert result is False

    async def test_health_check_fails_during_rollback(self) -> None:
        health_urls = {"zetherion-ai-skills": "http://skills:8080/health"}
        ex = _make_executor(health_urls=health_urls)

        run_results = [
            "ok\n",  # git checkout
            "ok\n",  # docker compose build
            "ok\n",  # restart svc1
            "ok\n",  # restart svc2
            "ok\n",  # restart svc3
        ]

        with (
            _patch_run_cmd(ex, run_results),
            _patch_health(return_value=False),
        ):
            result = await ex._attempt_rollback("abc123")

        assert result is False


# ---------------------------------------------------------------------------
# TestGetDiagnostics
# ---------------------------------------------------------------------------


class TestGetDiagnostics:
    """Tests for UpdateExecutor.get_diagnostics()."""

    async def test_all_commands_succeed(self) -> None:
        ex = _make_executor()

        run_results = [
            "abc123def456\n",  # git rev-parse HEAD
            "v1.0.0\n",  # git describe
            "\n",  # git status (clean)
            '{"Name":"svc1","State":"running"}\n',  # docker compose ps
            "/dev/sda1 50G 20G 30G 40% /\n",  # df -h
        ]

        with _patch_run_cmd(ex, run_results):
            diag = await ex.get_diagnostics()

        assert diag["git_sha"] == "abc123def456"
        assert diag["git_ref"] == "v1.0.0"
        assert diag["git_clean"] is True
        assert "svc1" in diag["containers_raw"]
        assert "/" in diag["disk_usage"]

    async def test_commands_fail_gracefully(self) -> None:
        ex = _make_executor()

        with _patch_run_cmd(ex, [None, None, None, None, None]):
            diag = await ex.get_diagnostics()

        assert diag["git_sha"] == "unknown"
        assert diag["git_ref"] == "unknown"
        assert diag["git_clean"] is False
        assert diag["containers_raw"] == "unavailable"
        assert diag["disk_usage"] == "unavailable"

    async def test_dirty_working_tree(self) -> None:
        ex = _make_executor()

        run_results = [
            "abc123\n",
            "main\n",
            " M src/app.py\n",  # dirty working tree
            "{}",
            "/dev/sda1 50G 20G 30G 40% /\n",
        ]

        with _patch_run_cmd(ex, run_results):
            diag = await ex.get_diagnostics()

        assert diag["git_clean"] is False


# ---------------------------------------------------------------------------
# TestRunCmd
# ---------------------------------------------------------------------------


class TestRunCmd:
    """Tests for UpdateExecutor._run_cmd()."""

    async def test_successful_command(self) -> None:
        ex = _make_executor()

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"output\n", b""))
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_shell", return_value=mock_proc):
            result = await ex._run_cmd("echo hello")

        assert result == "output\n"

    async def test_failed_command_returns_none(self) -> None:
        ex = _make_executor()

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b"error msg"))
        mock_proc.returncode = 1

        with patch("asyncio.create_subprocess_shell", return_value=mock_proc):
            result = await ex._run_cmd("false")

        assert result is None

    async def test_timeout_returns_none(self) -> None:
        ex = _make_executor()

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))

        with patch("asyncio.create_subprocess_shell", return_value=mock_proc):
            with patch("asyncio.wait_for", side_effect=TimeoutError):
                result = await ex._run_cmd("sleep 999", timeout=1)

        assert result is None

    async def test_generic_exception_returns_none(self) -> None:
        ex = _make_executor()

        with patch(
            "asyncio.create_subprocess_shell",
            side_effect=OSError("no such file"),
        ):
            result = await ex._run_cmd("bad_command")

        assert result is None

    async def test_cwd_is_project_dir(self) -> None:
        ex = _make_executor()

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"ok\n", b""))
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_shell", return_value=mock_proc) as mock_create:
            await ex._run_cmd("pwd")

        mock_create.assert_awaited_once()
        call_kwargs = mock_create.call_args
        assert call_kwargs.kwargs.get("cwd") == "/tmp/test-project"
