"""Tests for updater_sidecar.executor blue/green update orchestration."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from updater_sidecar.executor import UpdateExecutor

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_executor(tmp_path: Path, health_urls: dict[str, str] | None = None) -> UpdateExecutor:
    """Create an executor with writable temp paths."""
    project_dir = tmp_path / "project"
    project_dir.mkdir(parents=True, exist_ok=True)

    state_path = project_dir / "data" / "updater-state.json"
    route_path = project_dir / "config" / "traefik" / "dynamic" / "updater-routes.yml"

    return UpdateExecutor(
        project_dir=str(project_dir),
        compose_file=str(project_dir / "docker-compose.yml"),
        health_urls=health_urls,
        state_path=str(state_path),
        route_config_path=str(route_path),
        pause_on_failure=True,
    )


# ---------------------------------------------------------------------------
# Init / state
# ---------------------------------------------------------------------------


class TestUpdateExecutorInit:
    """Tests for constructor/state bootstrapping."""

    def test_default_state_and_route_file(self, tmp_path: Path) -> None:
        ex = _make_executor(tmp_path)
        assert ex.state == "idle"
        assert ex.current_operation is None
        assert ex.active_color == "blue"
        assert ex.paused is False

        route_file = Path(ex._route_config_path)
        assert route_file.exists()
        route_text = route_file.read_text(encoding="utf-8")
        assert "api-blue" in route_text
        assert "cgs-api-blue" in route_text
        assert "skills-blue" not in route_text

    def test_load_existing_state(self, tmp_path: Path) -> None:
        ex1 = _make_executor(tmp_path)
        state_file = Path(ex1._state_path)
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(
            json.dumps(
                {
                    "active_color": "green",
                    "paused": True,
                    "pause_reason": "failure",
                    "last_good_tag": "v0.4.0",
                }
            ),
            encoding="utf-8",
        )

        ex2 = _make_executor(tmp_path)
        assert ex2.active_color == "green"
        assert ex2.paused is True
        assert ex2.pause_reason == "failure"
        assert ex2.last_good_tag == "v0.4.0"


# ---------------------------------------------------------------------------
# apply_update
# ---------------------------------------------------------------------------


class TestApplyUpdate:
    """Tests for apply_update() flow."""

    @pytest.mark.asyncio
    async def test_paused_update_returns_failed(self, tmp_path: Path) -> None:
        ex = _make_executor(tmp_path)
        ex._runtime["paused"] = True
        ex._runtime["pause_reason"] = "manual pause"

        result = await ex.apply_update("v1.0.0", "1.0.0")

        assert result.status == "failed"
        assert result.paused is True
        assert "paused" in (result.error or "")

    @pytest.mark.asyncio
    async def test_successful_update_switches_color(self, tmp_path: Path) -> None:
        ex = _make_executor(tmp_path)

        run_results = [
            "abc123\n",  # git rev-parse HEAD
            "ok\n",  # git fetch
            "ok\n",  # git checkout tag
            "def456\n",  # git rev-parse HEAD
            "ok\n",  # docker compose build
            "ok\n",  # up skills-green
            "ok\n",  # up api-green
            "ok\n",  # up cgs-gateway-green
            "ok\n",  # restart bot
            "zetherion-ai-bot\n",  # ps running bot
            "ok\n",  # stop old blue services
        ]

        with (
            patch.object(ex, "_run_cmd", new_callable=AsyncMock, side_effect=run_results),
            patch(
                "updater_sidecar.executor.check_service_health",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch.object(ex, "_attempt_rollback", new_callable=AsyncMock, return_value=True),
        ):
            result = await ex.apply_update("v1.0.1", "1.0.1")

        assert result.status == "success"
        assert result.previous_sha == "abc123"
        assert result.new_sha == "def456"
        assert result.active_color == "green"
        assert ex.active_color == "green"
        assert ex.last_good_tag == "v1.0.1"

        route_text = Path(ex._route_config_path).read_text(encoding="utf-8")
        assert "api-green" in route_text
        assert "cgs-api-green" in route_text
        assert "skills-green" not in route_text
        assert "cgs-ui" not in route_text

    @pytest.mark.asyncio
    async def test_update_fetches_exact_tag_from_origin(self, tmp_path: Path) -> None:
        ex = _make_executor(tmp_path)
        run_results = [
            "abc123\n",
            "ok\n",
            "ok\n",
            "def456\n",
            "ok\n",
            "ok\n",
            "ok\n",
            "ok\n",
            "ok\n",
            "zetherion-ai-bot\n",
            "ok\n",
        ]
        run_cmd = AsyncMock(side_effect=run_results)

        with (
            patch.object(ex, "_run_cmd", run_cmd),
            patch(
                "updater_sidecar.executor.check_service_health",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch.object(ex, "_attempt_rollback", new_callable=AsyncMock, return_value=True),
        ):
            result = await ex.apply_update("v1.2.3", "1.2.3")

        assert result.status == "success"
        commands = [str(call.args[0]) for call in run_cmd.call_args_list]
        assert any(
            cmd.startswith("git fetch --force origin ")
            and "refs/tags/v1.2.3:refs/tags/v1.2.3" in cmd
            for cmd in commands
        )
        assert any(
            cmd.startswith("git checkout --force ") and "refs/tags/v1.2.3" in cmd
            for cmd in commands
        )

    @pytest.mark.asyncio
    async def test_build_failure_rolls_back_and_pauses(self, tmp_path: Path) -> None:
        ex = _make_executor(tmp_path)

        run_results = [
            "abc123\n",  # git rev-parse
            "ok\n",  # fetch
            "ok\n",  # checkout
            "def456\n",  # rev-parse
            None,  # build fails
        ]

        with (
            patch.object(ex, "_run_cmd", new_callable=AsyncMock, side_effect=run_results),
            patch.object(ex, "_attempt_rollback", new_callable=AsyncMock, return_value=True),
        ):
            result = await ex.apply_update("v1.0.2", "1.0.2")

        assert result.status == "rolled_back"
        assert ex.paused is True
        assert "docker build failed" in ex.pause_reason

    @pytest.mark.asyncio
    async def test_concurrent_update_rejected(self, tmp_path: Path) -> None:
        ex = _make_executor(tmp_path)

        async def slow_apply(*_args, **_kwargs):
            await asyncio.sleep(0.2)
            return ex._default_state()  # pragma: no cover - never used

        with patch.object(ex, "_do_apply", side_effect=slow_apply):
            t1 = asyncio.create_task(ex.apply_update("v1", "1"))
            await asyncio.sleep(0.05)
            result2 = await ex.apply_update("v2", "2")
            t1.cancel()
            with pytest.raises(asyncio.CancelledError):
                await t1

        assert result2.status == "failed"
        assert "in progress" in (result2.error or "")


# ---------------------------------------------------------------------------
# unpause / rollback / routes
# ---------------------------------------------------------------------------


class TestStateTransitions:
    """Tests for unpause/rollback/helpers."""

    @pytest.mark.asyncio
    async def test_unpause_clears_pause(self, tmp_path: Path) -> None:
        ex = _make_executor(tmp_path)
        ex._runtime["paused"] = True
        ex._runtime["pause_reason"] = "failure"

        ok = await ex.unpause()

        assert ok is True
        assert ex.paused is False
        assert ex.pause_reason == ""

    @pytest.mark.asyncio
    async def test_unpause_while_busy_returns_false(self, tmp_path: Path) -> None:
        ex = _make_executor(tmp_path)
        await ex._lock.acquire()
        try:
            ok = await ex.unpause()
        finally:
            ex._lock.release()

        assert ok is False

    @pytest.mark.asyncio
    async def test_manual_rollback_success(self, tmp_path: Path) -> None:
        ex = _make_executor(tmp_path)
        with patch.object(ex, "_attempt_rollback", new_callable=AsyncMock, return_value=True):
            result = await ex.rollback("abc123")

        assert result.status == "success"
        assert result.new_sha == "abc123"

    @pytest.mark.asyncio
    async def test_manual_rollback_failure(self, tmp_path: Path) -> None:
        ex = _make_executor(tmp_path)
        with patch.object(ex, "_attempt_rollback", new_callable=AsyncMock, return_value=False):
            result = await ex.rollback("abc123")

        assert result.status == "failed"
        assert result.error == "Rollback failed"

    def test_switch_active_color_updates_file_and_state(self, tmp_path: Path) -> None:
        ex = _make_executor(tmp_path)

        assert ex._switch_active_color("green") is True
        assert ex.active_color == "green"
        text = Path(ex._route_config_path).read_text(encoding="utf-8")
        assert "api-green" in text
        assert "cgs-api-green" in text
        assert "skills-green" not in text

    def test_switch_active_color_rejects_invalid(self, tmp_path: Path) -> None:
        ex = _make_executor(tmp_path)
        assert ex._switch_active_color("purple") is False


# ---------------------------------------------------------------------------
# Diagnostics / command runner
# ---------------------------------------------------------------------------


class TestDiagnosticsAndCommands:
    """Tests for diagnostics and low-level command execution."""

    @pytest.mark.asyncio
    async def test_get_diagnostics(self, tmp_path: Path) -> None:
        ex = _make_executor(tmp_path)
        ex._runtime["active_color"] = "green"

        outputs = [
            "abc123\n",  # rev-parse
            "v1.0.0\n",  # describe/branch
            "",  # status porcelain
            "[]\n",  # compose ps
            "disk\n",  # df
        ]

        with patch.object(ex, "_run_cmd", new_callable=AsyncMock, side_effect=outputs):
            data = await ex.get_diagnostics()

        assert data["git_sha"] == "abc123"
        assert data["git_ref"] == "v1.0.0"
        assert data["git_clean"] is True
        assert data["active_color"] == "green"
        assert "disk_usage" in data

    @pytest.mark.asyncio
    async def test_run_cmd_success_and_failure(self, tmp_path: Path) -> None:
        ex = _make_executor(tmp_path)

        out = await ex._run_cmd("printf 'ok'")
        assert out == "ok"

        fail = await ex._run_cmd("exit 1")
        assert fail is None


class TestSignatureVerification:
    """Tests for signed release verification in apply path."""

    @staticmethod
    def _mock_release_lookup(
        release_payload: dict[str, object],
        status_code: int = 200,
    ) -> AsyncMock:
        """Mock httpx.AsyncClient for GitHub release metadata lookup."""
        response = Mock()
        response.status_code = status_code
        response.json = Mock(return_value=release_payload)

        client = AsyncMock()
        client.get = AsyncMock(return_value=response)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        return client

    @staticmethod
    async def _write_assets(
        *,
        urls: dict[Path, str],
        manifest: dict[str, object],
    ) -> str | None:
        """Helper to emulate asset download into temp files."""
        for path in urls:
            if path.name.endswith(".json"):
                path.write_text(json.dumps(manifest), encoding="utf-8")
            else:
                path.write_bytes(b"dummy")
        return None

    @pytest.mark.asyncio
    async def test_verify_signature_success(self, tmp_path: Path) -> None:
        ex = _make_executor(tmp_path)
        release_payload = {
            "assets": [
                {
                    "name": "release-manifest.json",
                    "browser_download_url": "https://example/manifest",
                },
                {"name": "release-manifest.sig", "browser_download_url": "https://example/sig"},
                {"name": "release-manifest.pem", "browser_download_url": "https://example/pem"},
            ]
        }

        async def _download_assets_side_effect(
            *,
            urls: dict[Path, str],
            headers: dict[str, str],
        ) -> str | None:
            del headers
            return await self._write_assets(
                urls=urls,
                manifest={"repo": "owner/repo", "tag": "v1.2.3", "version": "1.2.3"},
            )

        with (
            patch(
                "httpx.AsyncClient",
                return_value=self._mock_release_lookup(release_payload),
            ),
            patch.object(
                ex,
                "_download_assets",
                new_callable=AsyncMock,
                side_effect=_download_assets_side_effect,
            ),
            patch.object(ex, "_run_cmd", new_callable=AsyncMock, return_value="verified"),
        ):
            err = await ex._verify_release_signature(
                tag="v1.2.3",
                version="1.2.3",
                github_repo="owner/repo",
                github_token="",
                verify_identity="https://github.com/owner/repo/.github/workflows/release.yml@refs/tags/*",
                verify_oidc_issuer="https://token.actions.githubusercontent.com",
                verify_rekor_url="https://rekor.sigstore.dev",
                manifest_asset_name="release-manifest.json",
                signature_asset_name="release-manifest.sig",
                certificate_asset_name="release-manifest.pem",
            )

        assert err is None

    @pytest.mark.asyncio
    async def test_verify_signature_rejects_manifest_repo_mismatch(self, tmp_path: Path) -> None:
        ex = _make_executor(tmp_path)
        release_payload = {
            "assets": [
                {
                    "name": "release-manifest.json",
                    "browser_download_url": "https://example/manifest",
                },
                {"name": "release-manifest.sig", "browser_download_url": "https://example/sig"},
                {"name": "release-manifest.pem", "browser_download_url": "https://example/pem"},
            ]
        }

        async def _download_assets_side_effect(
            *,
            urls: dict[Path, str],
            headers: dict[str, str],
        ) -> str | None:
            del headers
            return await self._write_assets(
                urls=urls,
                manifest={"repo": "wrong/repo", "tag": "v1.2.3", "version": "1.2.3"},
            )

        with (
            patch(
                "httpx.AsyncClient",
                return_value=self._mock_release_lookup(release_payload),
            ),
            patch.object(
                ex,
                "_download_assets",
                new_callable=AsyncMock,
                side_effect=_download_assets_side_effect,
            ),
            patch.object(ex, "_run_cmd", new_callable=AsyncMock, return_value="verified"),
        ):
            err = await ex._verify_release_signature(
                tag="v1.2.3",
                version="1.2.3",
                github_repo="owner/repo",
                github_token="",
                verify_identity="https://github.com/owner/repo/.github/workflows/release.yml@refs/tags/*",
                verify_oidc_issuer="https://token.actions.githubusercontent.com",
                verify_rekor_url="https://rekor.sigstore.dev",
                manifest_asset_name="release-manifest.json",
                signature_asset_name="release-manifest.sig",
                certificate_asset_name="release-manifest.pem",
            )

        assert err is not None
        assert "manifest repo mismatch" in err

    @pytest.mark.asyncio
    async def test_verify_signature_rejects_missing_release_assets(self, tmp_path: Path) -> None:
        ex = _make_executor(tmp_path)
        release_payload = {
            "assets": [
                {
                    "name": "release-manifest.json",
                    "browser_download_url": "https://example/manifest",
                },
                {"name": "release-manifest.sig", "browser_download_url": "https://example/sig"},
            ]
        }

        with patch(
            "httpx.AsyncClient",
            return_value=self._mock_release_lookup(release_payload),
        ):
            err = await ex._verify_release_signature(
                tag="v1.2.3",
                version="1.2.3",
                github_repo="owner/repo",
                github_token="",
                verify_identity="https://github.com/owner/repo/.github/workflows/release.yml@refs/tags/*",
                verify_oidc_issuer="https://token.actions.githubusercontent.com",
                verify_rekor_url="https://rekor.sigstore.dev",
                manifest_asset_name="release-manifest.json",
                signature_asset_name="release-manifest.sig",
                certificate_asset_name="release-manifest.pem",
            )

        assert err is not None
        assert "missing required assets" in err

    @pytest.mark.asyncio
    async def test_verify_signature_fails_when_cosign_fails(self, tmp_path: Path) -> None:
        ex = _make_executor(tmp_path)
        release_payload = {
            "assets": [
                {
                    "name": "release-manifest.json",
                    "browser_download_url": "https://example/manifest",
                },
                {"name": "release-manifest.sig", "browser_download_url": "https://example/sig"},
                {"name": "release-manifest.pem", "browser_download_url": "https://example/pem"},
            ]
        }

        async def _download_assets_side_effect(
            *,
            urls: dict[Path, str],
            headers: dict[str, str],
        ) -> str | None:
            del headers
            return await self._write_assets(
                urls=urls,
                manifest={"repo": "owner/repo", "tag": "v1.2.3", "version": "1.2.3"},
            )

        with (
            patch(
                "httpx.AsyncClient",
                return_value=self._mock_release_lookup(release_payload),
            ),
            patch.object(
                ex,
                "_download_assets",
                new_callable=AsyncMock,
                side_effect=_download_assets_side_effect,
            ),
            patch.object(ex, "_run_cmd", new_callable=AsyncMock, return_value=None),
        ):
            err = await ex._verify_release_signature(
                tag="v1.2.3",
                version="1.2.3",
                github_repo="owner/repo",
                github_token="",
                verify_identity="https://github.com/owner/repo/.github/workflows/release.yml@refs/tags/*",
                verify_oidc_issuer="https://token.actions.githubusercontent.com",
                verify_rekor_url="https://rekor.sigstore.dev",
                manifest_asset_name="release-manifest.json",
                signature_asset_name="release-manifest.sig",
                certificate_asset_name="release-manifest.pem",
            )

        assert err is not None
        assert "cosign verify-blob returned non-zero" in err
