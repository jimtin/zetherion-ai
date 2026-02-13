"""Tests for zetherion_ai.updater.manager — UpdateManager and helpers."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from zetherion_ai import __version__
from zetherion_ai.updater.manager import (
    ReleaseInfo,
    UpdateManager,
    UpdateResult,
    UpdateStatus,
    is_newer,
    parse_semver,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_release(
    tag: str = "v99.0.0",
    version: str = "99.0.0",
    published_at: str = "2026-01-01T00:00:00Z",
    html_url: str = "https://github.com/test/repo/releases/tag/v99.0.0",
    body: str = "Release notes",
) -> ReleaseInfo:
    """Return a ReleaseInfo with sensible defaults."""
    return ReleaseInfo(
        tag=tag,
        version=version,
        published_at=published_at,
        html_url=html_url,
        body=body,
    )


def _make_manager(
    storage: AsyncMock | None = None,
    github_token: str | None = None,
) -> UpdateManager:
    """Return an UpdateManager wired to mocks."""
    return UpdateManager(
        github_repo="owner/repo",
        storage=storage,
        updater_url="http://test-updater:9090",
        updater_secret="test-secret",
        health_url="http://localhost:8080/health",
        github_token=github_token,
    )


def _mock_http_response(status_code: int = 200, json_data: dict | None = None):
    """Return a MagicMock that behaves like an httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    return resp


# ---------------------------------------------------------------------------
# TestParseSemver
# ---------------------------------------------------------------------------


class TestParseSemver:
    """Tests for the parse_semver() helper."""

    def test_valid_version(self) -> None:
        assert parse_semver("1.2.3") == (1, 2, 3, "")

    def test_with_v_prefix(self) -> None:
        assert parse_semver("v1.2.3") == (1, 2, 3, "")

    def test_with_prerelease(self) -> None:
        assert parse_semver("1.2.3-beta.1") == (1, 2, 3, "beta.1")

    def test_invalid_input(self) -> None:
        assert parse_semver("not-a-version") is None

    def test_empty_string(self) -> None:
        assert parse_semver("") is None

    def test_partial_version(self) -> None:
        assert parse_semver("1.2") is None

    def test_whitespace_is_stripped(self) -> None:
        assert parse_semver("  v2.0.0  ") == (2, 0, 0, "")

    def test_trailing_garbage(self) -> None:
        # The regex is anchored at the start but not end-of-string for
        # pre-release — however the pattern uses $ so trailing junk fails.
        assert parse_semver("1.2.3xyz") is None


# ---------------------------------------------------------------------------
# TestIsNewer
# ---------------------------------------------------------------------------


class TestIsNewer:
    """Tests for the is_newer() comparator."""

    def test_newer_major(self) -> None:
        assert is_newer("2.0.0", "1.0.0") is True

    def test_newer_minor(self) -> None:
        assert is_newer("1.1.0", "1.0.0") is True

    def test_newer_patch(self) -> None:
        assert is_newer("1.0.1", "1.0.0") is True

    def test_same_version(self) -> None:
        assert is_newer("1.0.0", "1.0.0") is False

    def test_older_version(self) -> None:
        assert is_newer("0.9.0", "1.0.0") is False

    def test_prerelease_sorts_lower_than_release(self) -> None:
        # 1.0.0-beta should NOT be newer than 1.0.0
        assert is_newer("1.0.0-beta", "1.0.0") is False

    def test_release_beats_prerelease(self) -> None:
        # 1.0.0 IS newer than 1.0.0-beta
        assert is_newer("1.0.0", "1.0.0-beta") is True

    def test_invalid_candidate(self) -> None:
        assert is_newer("bad", "1.0.0") is False

    def test_invalid_current(self) -> None:
        assert is_newer("1.0.0", "bad") is False

    def test_both_invalid(self) -> None:
        assert is_newer("bad", "worse") is False

    def test_with_v_prefix(self) -> None:
        assert is_newer("v2.0.0", "v1.0.0") is True


# ---------------------------------------------------------------------------
# TestReleaseInfo
# ---------------------------------------------------------------------------


class TestReleaseInfo:
    """Tests for the ReleaseInfo dataclass."""

    def test_to_dict_structure(self) -> None:
        info = _make_release(body="short body")
        d = info.to_dict()
        assert d == {
            "tag": "v99.0.0",
            "version": "99.0.0",
            "published_at": "2026-01-01T00:00:00Z",
            "html_url": "https://github.com/test/repo/releases/tag/v99.0.0",
            "body": "short body",
        }

    def test_body_truncated_to_500_chars(self) -> None:
        long_body = "x" * 1000
        info = _make_release(body=long_body)
        d = info.to_dict()
        assert len(d["body"]) == 500


# ---------------------------------------------------------------------------
# TestUpdateResult
# ---------------------------------------------------------------------------


class TestUpdateResult:
    """Tests for the UpdateResult dataclass."""

    def test_to_dict_structure(self) -> None:
        result = UpdateResult(
            status=UpdateStatus.SUCCESS,
            current_version="1.0.0",
            target_version="2.0.0",
            previous_git_sha="abc123",
            new_git_sha="def456",
            error=None,
            health_check_passed=True,
            steps_completed=["git_fetch", "git_checkout"],
        )
        d = result.to_dict()
        assert d["status"] == "success"
        assert d["current_version"] == "1.0.0"
        assert d["target_version"] == "2.0.0"
        assert d["previous_git_sha"] == "abc123"
        assert d["new_git_sha"] == "def456"
        assert d["error"] is None
        assert d["health_check_passed"] is True
        assert d["steps_completed"] == ["git_fetch", "git_checkout"]
        assert "started_at" in d
        assert d["completed_at"] is None

    def test_default_values(self) -> None:
        result = UpdateResult(
            status=UpdateStatus.CHECKING,
            current_version="0.1.0",
        )
        assert result.target_version is None
        assert result.previous_git_sha is None
        assert result.new_git_sha is None
        assert result.error is None
        assert result.health_check_passed is None
        assert result.steps_completed == []
        assert result.completed_at is None
        # started_at should be an ISO timestamp string
        datetime.fromisoformat(result.started_at)


# ---------------------------------------------------------------------------
# TestCheckForUpdate
# ---------------------------------------------------------------------------


class TestCheckForUpdate:
    """Tests for UpdateManager.check_for_update()."""

    async def test_newer_version_available(self) -> None:
        mgr = _make_manager()
        api_data = {
            "tag_name": "v99.0.0",
            "published_at": "2026-06-01T00:00:00Z",
            "html_url": "https://github.com/owner/repo/releases/tag/v99.0.0",
            "body": "big release",
        }
        mock_resp = _mock_http_response(200, api_data)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            release = await mgr.check_for_update()

        assert release is not None
        assert release.version == "99.0.0"
        assert release.tag == "v99.0.0"

    async def test_already_up_to_date(self) -> None:
        mgr = _make_manager()
        # Return version equal to current (0.1.0)
        api_data = {"tag_name": "v0.1.0", "published_at": "", "html_url": "", "body": ""}
        mock_resp = _mock_http_response(200, api_data)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            release = await mgr.check_for_update()

        assert release is None

    async def test_github_404(self) -> None:
        mgr = _make_manager()
        mock_resp = _mock_http_response(404)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            release = await mgr.check_for_update()

        assert release is None

    async def test_github_500(self) -> None:
        mgr = _make_manager()
        mock_resp = _mock_http_response(500)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            release = await mgr.check_for_update()

        assert release is None

    async def test_network_error(self) -> None:
        mgr = _make_manager()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            release = await mgr.check_for_update()

        assert release is None

    async def test_github_token_sent_in_header(self) -> None:
        mgr = _make_manager(github_token="ghp_secret123")
        api_data = {
            "tag_name": "v99.0.0",
            "published_at": "",
            "html_url": "",
            "body": "",
        }
        mock_resp = _mock_http_response(200, api_data)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            await mgr.check_for_update()

        # Verify the Authorization header was included
        call_kwargs = mock_client.get.call_args
        headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
        assert headers.get("Authorization") == "Bearer ghp_secret123"

    async def test_no_token_omits_auth_header(self) -> None:
        mgr = _make_manager(github_token=None)
        api_data = {"tag_name": "v0.0.1", "published_at": "", "html_url": "", "body": ""}
        mock_resp = _mock_http_response(200, api_data)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            await mgr.check_for_update()

        call_kwargs = mock_client.get.call_args
        headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
        assert "Authorization" not in headers


# ---------------------------------------------------------------------------
# TestApplyUpdate
# ---------------------------------------------------------------------------


class TestApplyUpdate:
    """Tests for UpdateManager.apply_update() — delegates to updater sidecar via HTTP."""

    def _mock_sidecar_post(self, status_code: int = 200, json_data: dict | None = None):
        """Return a patch that mocks httpx.AsyncClient for the sidecar POST."""
        resp = MagicMock()
        resp.status_code = status_code
        resp.json.return_value = json_data or {}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        return mock_client

    async def test_successful_update(self) -> None:
        mgr = _make_manager()
        release = _make_release()

        sidecar_response = {
            "status": "success",
            "previous_sha": "abc123",
            "new_sha": "def456",
            "steps_completed": [
                "git_fetch",
                "git_checkout",
                "docker_build",
                "service_restart",
            ],
        }
        mock_client = self._mock_sidecar_post(200, sidecar_response)

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch.object(mgr, "_record_update", new_callable=AsyncMock),
        ):
            result = await mgr.apply_update(release)

        assert result.status == UpdateStatus.SUCCESS
        assert result.previous_git_sha == "abc123"
        assert result.new_git_sha == "def456"
        assert result.health_check_passed is True
        assert result.completed_at is not None
        assert "git_fetch" in result.steps_completed
        assert "git_checkout" in result.steps_completed
        assert "docker_build" in result.steps_completed
        assert "service_restart" in result.steps_completed

    async def test_git_fetch_fails(self) -> None:
        mgr = _make_manager()
        release = _make_release()

        sidecar_response = {
            "status": "failed",
            "error": "git fetch failed",
            "previous_sha": "abc123",
            "steps_completed": [],
        }
        mock_client = self._mock_sidecar_post(200, sidecar_response)

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch.object(mgr, "_record_update", new_callable=AsyncMock),
        ):
            result = await mgr.apply_update(release)

        assert result.status == UpdateStatus.FAILED
        assert result.error == "git fetch failed"
        assert result.completed_at is not None

    async def test_git_checkout_fails(self) -> None:
        mgr = _make_manager()
        release = _make_release()

        sidecar_response = {
            "status": "failed",
            "error": "git checkout failed",
            "previous_sha": "abc123",
            "steps_completed": ["git_fetch"],
        }
        mock_client = self._mock_sidecar_post(200, sidecar_response)

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch.object(mgr, "_record_update", new_callable=AsyncMock),
        ):
            result = await mgr.apply_update(release)

        assert result.status == UpdateStatus.FAILED
        assert result.error == "git checkout failed"
        assert "git_fetch" in result.steps_completed
        assert "git_checkout" not in result.steps_completed

    async def test_docker_build_fails(self) -> None:
        mgr = _make_manager()
        release = _make_release()

        sidecar_response = {
            "status": "failed",
            "error": "docker build failed",
            "previous_sha": "abc123",
            "steps_completed": ["git_fetch", "git_checkout"],
        }
        mock_client = self._mock_sidecar_post(200, sidecar_response)

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch.object(mgr, "_record_update", new_callable=AsyncMock),
        ):
            result = await mgr.apply_update(release)

        assert result.status == UpdateStatus.FAILED
        assert result.error == "docker build failed"
        assert "docker_build" not in result.steps_completed

    async def test_service_restart_fails(self) -> None:
        mgr = _make_manager()
        release = _make_release()

        sidecar_response = {
            "status": "failed",
            "error": "service restart failed",
            "previous_sha": "abc123",
            "new_sha": "def456",
            "steps_completed": ["git_fetch", "git_checkout", "docker_build"],
        }
        mock_client = self._mock_sidecar_post(200, sidecar_response)

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch.object(mgr, "_record_update", new_callable=AsyncMock),
        ):
            result = await mgr.apply_update(release)

        assert result.status == UpdateStatus.FAILED
        assert result.error == "service restart failed"
        assert "service_restart" not in result.steps_completed

    async def test_health_check_fails_triggers_rollback(self) -> None:
        mgr = _make_manager()
        release = _make_release()

        sidecar_response = {
            "status": "rolled_back",
            "error": "health check failed after update",
            "previous_sha": "abc123",
            "new_sha": "def456",
            "steps_completed": [
                "git_fetch",
                "git_checkout",
                "docker_build",
                "service_restart",
            ],
        }
        mock_client = self._mock_sidecar_post(200, sidecar_response)

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch.object(mgr, "_record_update", new_callable=AsyncMock),
        ):
            result = await mgr.apply_update(release)

        assert result.status == UpdateStatus.ROLLED_BACK
        assert result.error == "health check failed after update"
        assert result.health_check_passed is False

    async def test_health_check_fails_rollback_also_fails(self) -> None:
        mgr = _make_manager()
        release = _make_release()

        # The sidecar tried to rollback but it also failed — reported as "failed"
        sidecar_response = {
            "status": "failed",
            "error": "health check failed after update",
            "previous_sha": "abc123",
            "new_sha": "def456",
            "steps_completed": [
                "git_fetch",
                "git_checkout",
                "docker_build",
                "service_restart",
            ],
        }
        mock_client = self._mock_sidecar_post(200, sidecar_response)

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch.object(mgr, "_record_update", new_callable=AsyncMock),
        ):
            result = await mgr.apply_update(release)

        assert result.status == UpdateStatus.FAILED
        assert result.error == "health check failed after update"

    async def test_initial_sha_is_none_when_sidecar_omits_it(self) -> None:
        mgr = _make_manager()
        release = _make_release()

        sidecar_response = {
            "status": "failed",
            "error": "git fetch failed",
            "steps_completed": [],
        }
        mock_client = self._mock_sidecar_post(200, sidecar_response)

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch.object(mgr, "_record_update", new_callable=AsyncMock),
        ):
            result = await mgr.apply_update(release)

        assert result.previous_git_sha is None
        assert result.status == UpdateStatus.FAILED

    async def test_conflict_409_update_already_in_progress(self) -> None:
        mgr = _make_manager()
        release = _make_release()

        mock_client = self._mock_sidecar_post(409, {"error": "update in progress"})

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch.object(mgr, "_record_update", new_callable=AsyncMock),
        ):
            result = await mgr.apply_update(release)

        assert result.status == UpdateStatus.FAILED
        assert result.error == "Update already in progress"

    async def test_no_updater_url_returns_failed(self) -> None:
        mgr = UpdateManager(
            github_repo="owner/repo",
            updater_url="",
            updater_secret="",
            health_url="http://localhost:8080/health",
        )
        release = _make_release()

        with patch.object(mgr, "_record_update", new_callable=AsyncMock):
            result = await mgr.apply_update(release)

        assert result.status == UpdateStatus.FAILED
        assert result.error == "No updater sidecar URL configured"

    async def test_network_error_reaching_sidecar(self) -> None:
        mgr = _make_manager()
        release = _make_release()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            side_effect=httpx.ConnectError("connection refused"),
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch.object(mgr, "_record_update", new_callable=AsyncMock),
        ):
            result = await mgr.apply_update(release)

        assert result.status == UpdateStatus.FAILED
        assert "Cannot reach updater sidecar" in result.error

    async def test_updater_secret_sent_in_header(self) -> None:
        mgr = _make_manager()
        release = _make_release()

        sidecar_response = {
            "status": "success",
            "previous_sha": "abc",
            "new_sha": "def",
            "steps_completed": [],
        }
        mock_client = self._mock_sidecar_post(200, sidecar_response)

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch.object(mgr, "_record_update", new_callable=AsyncMock),
        ):
            await mgr.apply_update(release)

        # Verify the updater secret was sent in the header
        call_kwargs = mock_client.post.call_args
        headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
        assert headers.get("X-Updater-Secret") == "test-secret"

    async def test_no_secret_omits_header(self) -> None:
        mgr = UpdateManager(
            github_repo="owner/repo",
            updater_url="http://test-updater:9090",
            updater_secret="",
            health_url="http://localhost:8080/health",
        )
        release = _make_release()

        sidecar_response = {
            "status": "success",
            "previous_sha": "abc",
            "new_sha": "def",
            "steps_completed": [],
        }
        mock_client = self._mock_sidecar_post(200, sidecar_response)

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch.object(mgr, "_record_update", new_callable=AsyncMock),
        ):
            await mgr.apply_update(release)

        call_kwargs = mock_client.post.call_args
        headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
        assert "X-Updater-Secret" not in headers


# ---------------------------------------------------------------------------
# TestRollback
# ---------------------------------------------------------------------------


class TestRollback:
    """Tests for UpdateManager.rollback() — delegates to updater sidecar via HTTP."""

    def _mock_sidecar_post(self, status_code: int = 200, json_data: dict | None = None):
        """Return a mock httpx.AsyncClient for the sidecar POST."""
        resp = MagicMock()
        resp.status_code = status_code
        resp.json.return_value = json_data or {}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        return mock_client

    async def test_successful_rollback(self) -> None:
        mgr = _make_manager()

        mock_client = self._mock_sidecar_post(200, {"status": "success"})
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await mgr.rollback("abc123")

        assert result is True

    async def test_empty_sha_returns_false(self) -> None:
        mgr = _make_manager()
        result = await mgr.rollback("")
        assert result is False

    async def test_sidecar_returns_failure(self) -> None:
        mgr = _make_manager()

        mock_client = self._mock_sidecar_post(200, {"status": "failed", "error": "checkout failed"})
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await mgr.rollback("abc123")

        assert result is False

    async def test_network_error(self) -> None:
        mgr = _make_manager()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            side_effect=httpx.ConnectError("connection refused"),
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await mgr.rollback("abc123")

        assert result is False

    async def test_no_updater_url_returns_false(self) -> None:
        mgr = UpdateManager(
            github_repo="owner/repo",
            updater_url="",
            updater_secret="",
            health_url="http://localhost:8080/health",
        )
        result = await mgr.rollback("abc123")
        assert result is False

    async def test_updater_secret_sent_in_header(self) -> None:
        mgr = _make_manager()

        mock_client = self._mock_sidecar_post(200, {"status": "success"})
        with patch("httpx.AsyncClient", return_value=mock_client):
            await mgr.rollback("abc123")

        call_kwargs = mock_client.post.call_args
        headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
        assert headers.get("X-Updater-Secret") == "test-secret"

    async def test_no_secret_omits_header(self) -> None:
        mgr = UpdateManager(
            github_repo="owner/repo",
            updater_url="http://test-updater:9090",
            updater_secret="",
            health_url="http://localhost:8080/health",
        )

        mock_client = self._mock_sidecar_post(200, {"status": "success"})
        with patch("httpx.AsyncClient", return_value=mock_client):
            await mgr.rollback("abc123")

        call_kwargs = mock_client.post.call_args
        headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
        assert "X-Updater-Secret" not in headers


# ---------------------------------------------------------------------------
# TestValidateHealth
# ---------------------------------------------------------------------------


class TestValidateHealth:
    """Tests for UpdateManager._validate_health()."""

    async def test_healthy_on_first_try(self) -> None:
        mgr = _make_manager()
        mock_resp = _mock_http_response(200)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await mgr._validate_health(retries=3, delay=0)

        assert result is True
        # Should only call once since it succeeded first try
        assert mock_client.get.await_count == 1

    async def test_healthy_after_retries(self) -> None:
        mgr = _make_manager()

        fail_resp = _mock_http_response(503)
        ok_resp = _mock_http_response(200)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[fail_resp, fail_resp, ok_resp])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await mgr._validate_health(retries=5, delay=1)

        assert result is True
        assert mock_client.get.await_count == 3

    async def test_all_retries_fail(self) -> None:
        mgr = _make_manager()
        fail_resp = _mock_http_response(503)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=fail_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await mgr._validate_health(retries=3, delay=1)

        assert result is False
        assert mock_client.get.await_count == 3

    async def test_network_error_retries(self) -> None:
        mgr = _make_manager()
        ok_resp = _mock_http_response(200)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[httpx.ConnectError("refused"), ok_resp])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await mgr._validate_health(retries=3, delay=1)

        assert result is True
        assert mock_client.get.await_count == 2

    async def test_sleep_called_between_retries(self) -> None:
        mgr = _make_manager()
        fail_resp = _mock_http_response(500)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=fail_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        ):
            await mgr._validate_health(retries=3, delay=5)

        # sleep should be called between retries, but NOT after the last attempt
        assert mock_sleep.await_count == 2
        mock_sleep.assert_awaited_with(5)

    async def test_no_sleep_after_last_retry(self) -> None:
        mgr = _make_manager()
        fail_resp = _mock_http_response(500)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=fail_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        ):
            await mgr._validate_health(retries=1, delay=5)

        # With only 1 retry, there should be no sleep at all
        mock_sleep.assert_not_awaited()


# ---------------------------------------------------------------------------
# TestRecordUpdate
# ---------------------------------------------------------------------------


class TestRecordUpdate:
    """Tests for UpdateManager._record_update()."""

    async def test_record_saved_to_storage(self) -> None:
        storage = AsyncMock()
        storage.save_update_record = AsyncMock()
        mgr = _make_manager(storage=storage)

        result = UpdateResult(
            status=UpdateStatus.SUCCESS,
            current_version="0.1.0",
            target_version="2.0.0",
            previous_git_sha="abc123",
            new_git_sha="def456",
        )

        await mgr._record_update(result)

        storage.save_update_record.assert_awaited_once()
        saved_record = storage.save_update_record.call_args[0][0]
        assert saved_record.version == "2.0.0"
        assert saved_record.previous_version == "0.1.0"
        assert saved_record.git_sha == "def456"

    async def test_noop_when_storage_is_none(self) -> None:
        mgr = _make_manager(storage=None)

        result = UpdateResult(
            status=UpdateStatus.SUCCESS,
            current_version="0.1.0",
        )

        # Should not raise
        await mgr._record_update(result)

    async def test_exception_in_storage_is_swallowed(self) -> None:
        storage = AsyncMock()
        storage.save_update_record = AsyncMock(side_effect=RuntimeError("db connection lost"))
        mgr = _make_manager(storage=storage)

        result = UpdateResult(
            status=UpdateStatus.SUCCESS,
            current_version="0.1.0",
            target_version="2.0.0",
        )

        # Should not raise despite storage error
        await mgr._record_update(result)

    async def test_import_error_is_swallowed(self) -> None:
        """If the health.storage module cannot be imported, the error is caught."""
        storage = AsyncMock()
        mgr = _make_manager(storage=storage)

        result = UpdateResult(
            status=UpdateStatus.CHECKING,
            current_version="0.1.0",
        )

        with patch(
            "zetherion_ai.updater.manager.UpdateStatus",
            side_effect=ImportError("no module"),
        ):
            # The except block should catch this
            await mgr._record_update(result)


# ---------------------------------------------------------------------------
# TestUpdateManagerInit
# ---------------------------------------------------------------------------


class TestUpdateManagerInit:
    """Tests for UpdateManager constructor and properties."""

    def test_current_version_returns_package_version(self) -> None:
        mgr = _make_manager()
        assert mgr.current_version == __version__

    def test_constructor_stores_attributes(self) -> None:
        storage = AsyncMock()
        mgr = UpdateManager(
            github_repo="owner/repo",
            storage=storage,
            updater_url="http://updater:9090",
            updater_secret="s3cret",
            health_url="http://localhost:9090/health",
            github_token="ghp_token",
        )

        assert mgr._repo == "owner/repo"
        assert mgr._storage is storage
        assert mgr._updater_url == "http://updater:9090"
        assert mgr._updater_secret == "s3cret"
        assert mgr._health_url == "http://localhost:9090/health"
        assert mgr._github_token == "ghp_token"

    def test_defaults(self) -> None:
        mgr = UpdateManager(github_repo="owner/repo")

        assert mgr._storage is None
        assert mgr._updater_url == ""
        assert mgr._updater_secret == ""
        assert mgr._health_url == "http://localhost:8080/health"
        assert mgr._github_token is None


# ---------------------------------------------------------------------------
# TestUpdateStatus enum
# ---------------------------------------------------------------------------


class TestUpdateStatusEnum:
    """Tests for the UpdateStatus enum values."""

    def test_all_values(self) -> None:
        assert UpdateStatus.CHECKING.value == "checking"
        assert UpdateStatus.AVAILABLE.value == "available"
        assert UpdateStatus.NO_UPDATE.value == "no_update"
        assert UpdateStatus.DOWNLOADING.value == "downloading"
        assert UpdateStatus.BUILDING.value == "building"
        assert UpdateStatus.RESTARTING.value == "restarting"
        assert UpdateStatus.VALIDATING.value == "validating"
        assert UpdateStatus.SUCCESS.value == "success"
        assert UpdateStatus.FAILED.value == "failed"
        assert UpdateStatus.ROLLED_BACK.value == "rolled_back"
