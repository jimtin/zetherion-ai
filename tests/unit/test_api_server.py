"""Unit tests for public API server wiring and lifecycle."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zetherion_ai.api.server import PublicAPIServer, main, run_server


class TestPublicAPIServerCreateApp:
    """Tests for PublicAPIServer.create_app wiring."""

    def test_create_app_includes_inference_broker(self) -> None:
        tenant_manager = MagicMock()
        broker = object()

        server = PublicAPIServer(
            tenant_manager=tenant_manager,
            jwt_secret="test-secret",
            inference_broker=broker,
        )
        app = server.create_app()

        assert app["inference_broker"] is broker

    def test_create_app_omits_inference_broker_when_not_provided(self) -> None:
        server = PublicAPIServer(
            tenant_manager=MagicMock(),
            jwt_secret="test-secret",
        )
        app = server.create_app()
        assert "inference_broker" not in app

    def test_create_app_adds_cors_when_allowed_origins_set(self) -> None:
        server = PublicAPIServer(
            tenant_manager=MagicMock(),
            jwt_secret="test-secret",
            allowed_origins=["https://example.com"],
        )
        app = server.create_app()
        # CORS + auth + rate-limit
        assert len(app.middlewares) == 3

    def test_create_app_registers_youtube_routes_and_skills(self) -> None:
        tenant_manager = MagicMock()
        storage = object()
        skill = object()

        with patch("zetherion_ai.api.server.register_youtube_routes") as mock_register:
            server = PublicAPIServer(
                tenant_manager=tenant_manager,
                jwt_secret="test-secret",
                youtube_storage=storage,
                youtube_skills={"intelligence": skill},
            )
            app = server.create_app()

        mock_register.assert_called_once_with(app)
        assert app["youtube_storage"] is storage
        assert app["youtube_intelligence"] is skill


class TestPublicAPIServerLifecycle:
    """Tests for start/stop lifecycle methods."""

    @pytest.mark.asyncio
    async def test_start_creates_runner_and_site(self) -> None:
        runner = AsyncMock()
        site = AsyncMock()

        with (
            patch("zetherion_ai.api.server.web.AppRunner", return_value=runner) as mock_runner_cls,
            patch("zetherion_ai.api.server.web.TCPSite", return_value=site) as mock_site_cls,
        ):
            server = PublicAPIServer(tenant_manager=MagicMock(), jwt_secret="secret")
            await server.start()

        mock_runner_cls.assert_called_once()
        runner.setup.assert_awaited_once()
        mock_site_cls.assert_called_once_with(runner, "0.0.0.0", 8443)
        site.start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_cleans_runner_and_resets_state(self) -> None:
        runner = AsyncMock()
        server = PublicAPIServer(tenant_manager=MagicMock(), jwt_secret="secret")
        server._runner = runner

        await server.stop()

        runner.cleanup.assert_awaited_once()
        assert server._runner is None

    @pytest.mark.asyncio
    async def test_stop_is_noop_when_not_started(self) -> None:
        server = PublicAPIServer(tenant_manager=MagicMock(), jwt_secret="secret")
        await server.stop()
        assert server._runner is None


class TestRunServer:
    """Tests for run_server() lifecycle."""

    @pytest.mark.asyncio
    @patch("zetherion_ai.api.server.PublicAPIServer")
    async def test_run_server_wires_constructor_and_lifecycle(self, mock_server_cls) -> None:
        tenant_manager = MagicMock()

        mock_server = AsyncMock()
        mock_server_cls.return_value = mock_server

        with patch(
            "zetherion_ai.api.server.asyncio.sleep",
            side_effect=asyncio.CancelledError,
        ):
            await run_server(
                tenant_manager=tenant_manager,
                jwt_secret="test-secret",
            )

        mock_server_cls.assert_called_once()
        kwargs = mock_server_cls.call_args.kwargs
        assert kwargs["tenant_manager"] is tenant_manager
        assert kwargs["jwt_secret"] == "test-secret"
        assert kwargs["host"] == "0.0.0.0"
        assert kwargs["port"] == 8443
        assert kwargs["youtube_storage"] is None
        assert kwargs["youtube_skills"] is None
        # Newer local API versions pass this through; CI's committed version may not.
        if "inference_broker" in kwargs:
            assert kwargs["inference_broker"] is None
        mock_server.start.assert_awaited_once()
        mock_server.stop.assert_awaited_once()


class TestMain:
    """Tests for main() bootstrap behavior."""

    def test_main_exits_when_jwt_secret_missing(self) -> None:
        with (
            patch.dict("os.environ", {}, clear=True),
            patch(
                "zetherion_ai.config.get_settings",
                return_value=SimpleNamespace(postgres_dsn=None),
            ),
        ):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 1

    def test_main_handles_keyboard_interrupt(self) -> None:
        def _raise_keyboard_interrupt(coro):
            coro.close()
            raise KeyboardInterrupt

        with (
            patch.dict("os.environ", {"API_JWT_SECRET": "secret"}, clear=True),
            patch(
                "zetherion_ai.config.get_settings",
                return_value=SimpleNamespace(postgres_dsn=None),
            ),
            patch("zetherion_ai.api.server.TenantManager"),
            patch(
                "zetherion_ai.api.server.asyncio.run",
                side_effect=_raise_keyboard_interrupt,
            ) as mock_run,
        ):
            main()
        mock_run.assert_called_once()

    def test_main_runs_server_with_expected_args(self) -> None:
        settings = SimpleNamespace(postgres_dsn=None)
        tenant_manager = AsyncMock()

        with (
            patch.dict(
                "os.environ",
                {"API_JWT_SECRET": "secret", "API_HOST": "127.0.0.1", "API_PORT": "9443"},
                clear=True,
            ),
            patch("zetherion_ai.config.get_settings", return_value=settings),
            patch("zetherion_ai.api.server.TenantManager", return_value=tenant_manager),
            patch("zetherion_ai.api.server.run_server", new_callable=AsyncMock) as mock_run_server,
        ):
            main()

        tenant_manager.initialize.assert_awaited_once()
        mock_run_server.assert_awaited_once()
        args = mock_run_server.call_args.args
        kwargs = mock_run_server.call_args.kwargs
        assert args[0] is tenant_manager
        assert args[1] == "secret"
        assert args[2] == "127.0.0.1"
        assert args[3] == 9443
        assert kwargs["youtube_storage"] is None
        assert kwargs["youtube_skills"] == {}
        # Optional in newer local API versions.
        if "inference_broker" in kwargs:
            assert kwargs["inference_broker"] is not None

    def test_main_continues_when_youtube_init_fails(self) -> None:
        settings = SimpleNamespace(postgres_dsn="postgres://test")
        tenant_manager = AsyncMock()

        with (
            patch.dict("os.environ", {"API_JWT_SECRET": "secret"}, clear=True),
            patch("zetherion_ai.config.get_settings", return_value=settings),
            patch("zetherion_ai.api.server.TenantManager", return_value=tenant_manager),
            patch(
                "zetherion_ai.skills.youtube.storage.YouTubeStorage",
                side_effect=RuntimeError("init failed"),
            ),
            patch("zetherion_ai.api.server.run_server", new_callable=AsyncMock) as mock_run_server,
        ):
            main()

        tenant_manager.initialize.assert_awaited_once()
        mock_run_server.assert_awaited_once()
        args = mock_run_server.call_args.args
        kwargs = mock_run_server.call_args.kwargs
        assert args[0] is tenant_manager
        assert args[1] == "secret"
        assert args[2] == "0.0.0.0"
        assert args[3] == 8443
        assert kwargs["youtube_storage"] is None
        assert kwargs["youtube_skills"] == {}
        if "inference_broker" in kwargs:
            assert kwargs["inference_broker"] is not None
