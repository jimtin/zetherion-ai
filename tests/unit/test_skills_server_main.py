"""Unit tests for the skills server main() entry point.

Verifies that the main() function in server.py correctly creates a
SkillRegistry, registers all three built-in skills, and launches
the async server via asyncio.run.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch


def _close_coro_in_asyncio_run(mock_asyncio_run: MagicMock) -> None:
    """Ensure patched asyncio.run closes the coroutine argument to avoid warnings."""

    def _runner(coro):
        coro.close()
        return None

    mock_asyncio_run.side_effect = _runner


def _configure_mock_settings(mock_get_settings: MagicMock) -> MagicMock:
    """Configure mock settings with defaults so conditional branches behave correctly."""
    mock_settings = MagicMock()
    mock_settings.telemetry_central_mode = False
    mock_settings.postgres_dsn = ""  # No Postgres by default
    mock_get_settings.return_value = mock_settings
    return mock_settings


class TestSkillsServerMain:
    """Tests for the skills server main() function."""

    @patch("zetherion_ai.skills.server.asyncio.run")
    @patch("zetherion_ai.skills.server.SkillRegistry")
    @patch("zetherion_ai.config.get_settings")
    def test_registry_register_called_eight_times(
        self,
        mock_get_settings,
        mock_registry_cls,
        mock_asyncio_run,
    ) -> None:
        """registry.register should be called for all default built-in skills."""
        from zetherion_ai.skills.server import main

        _configure_mock_settings(mock_get_settings)
        mock_registry = MagicMock()
        mock_registry_cls.return_value = mock_registry
        _close_coro_in_asyncio_run(mock_asyncio_run)

        main()

        assert mock_registry.register.call_count == 8

    @patch("zetherion_ai.skills.server.asyncio.run")
    @patch("zetherion_ai.skills.server.SkillRegistry")
    @patch("zetherion_ai.config.get_settings")
    def test_task_manager_skill_registered(
        self,
        mock_get_settings,
        mock_registry_cls,
        mock_asyncio_run,
    ) -> None:
        """TaskManagerSkill should be registered with the registry."""
        from zetherion_ai.skills.server import main
        from zetherion_ai.skills.task_manager import TaskManagerSkill

        _configure_mock_settings(mock_get_settings)
        mock_registry = MagicMock()
        mock_registry_cls.return_value = mock_registry
        _close_coro_in_asyncio_run(mock_asyncio_run)

        main()

        # Check that at least one register call received a TaskManagerSkill instance
        registered_types = [type(c.args[0]) for c in mock_registry.register.call_args_list]
        assert TaskManagerSkill in registered_types

    @patch("zetherion_ai.skills.server.asyncio.run")
    @patch("zetherion_ai.skills.server.SkillRegistry")
    @patch("zetherion_ai.config.get_settings")
    def test_calendar_skill_registered(
        self,
        mock_get_settings,
        mock_registry_cls,
        mock_asyncio_run,
    ) -> None:
        """CalendarSkill should be registered with the registry."""
        from zetherion_ai.skills.calendar import CalendarSkill
        from zetherion_ai.skills.server import main

        _configure_mock_settings(mock_get_settings)
        mock_registry = MagicMock()
        mock_registry_cls.return_value = mock_registry
        _close_coro_in_asyncio_run(mock_asyncio_run)

        main()

        registered_types = [type(c.args[0]) for c in mock_registry.register.call_args_list]
        assert CalendarSkill in registered_types

    @patch("zetherion_ai.skills.server.asyncio.run")
    @patch("zetherion_ai.skills.server.SkillRegistry")
    @patch("zetherion_ai.config.get_settings")
    def test_profile_skill_registered(
        self,
        mock_get_settings,
        mock_registry_cls,
        mock_asyncio_run,
    ) -> None:
        """ProfileSkill should be registered with the registry."""
        from zetherion_ai.skills.profile_skill import ProfileSkill
        from zetherion_ai.skills.server import main

        _configure_mock_settings(mock_get_settings)
        mock_registry = MagicMock()
        mock_registry_cls.return_value = mock_registry
        _close_coro_in_asyncio_run(mock_asyncio_run)

        main()

        registered_types = [type(c.args[0]) for c in mock_registry.register.call_args_list]
        assert ProfileSkill in registered_types

    @patch("zetherion_ai.skills.server.asyncio.run")
    @patch("zetherion_ai.skills.server.SkillRegistry")
    @patch("zetherion_ai.config.get_settings")
    def test_asyncio_run_invoked(
        self,
        mock_get_settings,
        mock_registry_cls,
        mock_asyncio_run,
    ) -> None:
        """asyncio.run should be called to start the server."""
        from zetherion_ai.skills.server import main

        _configure_mock_settings(mock_get_settings)
        mock_registry = MagicMock()
        mock_registry_cls.return_value = mock_registry
        _close_coro_in_asyncio_run(mock_asyncio_run)

        main()

        mock_asyncio_run.assert_called_once()

    @patch("zetherion_ai.skills.server.asyncio.run")
    @patch("zetherion_ai.skills.server.SkillRegistry")
    @patch("zetherion_ai.config.get_settings")
    def test_registration_order(
        self,
        mock_get_settings,
        mock_registry_cls,
        mock_asyncio_run,
    ) -> None:
        """Skills registered in correct order including DevWatcher and Milestone."""
        from zetherion_ai.skills.calendar import CalendarSkill
        from zetherion_ai.skills.dev_watcher import DevWatcherSkill
        from zetherion_ai.skills.gmail.skill import GmailSkill
        from zetherion_ai.skills.health_analyzer import HealthAnalyzerSkill
        from zetherion_ai.skills.milestone import MilestoneSkill
        from zetherion_ai.skills.profile_skill import ProfileSkill
        from zetherion_ai.skills.server import main
        from zetherion_ai.skills.task_manager import TaskManagerSkill
        from zetherion_ai.skills.update_checker import UpdateCheckerSkill

        _configure_mock_settings(mock_get_settings)
        mock_registry = MagicMock()
        mock_registry_cls.return_value = mock_registry
        _close_coro_in_asyncio_run(mock_asyncio_run)

        main()

        calls = mock_registry.register.call_args_list
        assert len(calls) == 8
        assert isinstance(calls[0].args[0], TaskManagerSkill)
        assert isinstance(calls[1].args[0], CalendarSkill)
        assert isinstance(calls[2].args[0], ProfileSkill)
        assert isinstance(calls[3].args[0], HealthAnalyzerSkill)
        assert isinstance(calls[4].args[0], DevWatcherSkill)
        assert isinstance(calls[5].args[0], MilestoneSkill)
        assert isinstance(calls[6].args[0], GmailSkill)
        assert isinstance(calls[7].args[0], UpdateCheckerSkill)

    @patch("zetherion_ai.skills.server.asyncio.run")
    @patch("zetherion_ai.skills.server.SkillRegistry")
    @patch("zetherion_ai.config.get_settings")
    def test_get_settings_called(
        self,
        mock_get_settings,
        mock_registry_cls,
        mock_asyncio_run,
    ) -> None:
        """get_settings() should be called to validate configuration."""
        from zetherion_ai.skills.server import main

        _configure_mock_settings(mock_get_settings)
        mock_registry = MagicMock()
        mock_registry_cls.return_value = mock_registry
        _close_coro_in_asyncio_run(mock_asyncio_run)

        main()

        mock_get_settings.assert_called_once()

    @patch("zetherion_ai.skills.server.asyncio.run")
    @patch("zetherion_ai.skills.server.SkillRegistry")
    @patch("zetherion_ai.config.get_settings")
    @patch("zetherion_ai.skills.update_checker.UpdateCheckerSkill")
    def test_update_checker_receives_token_interval_and_secret_fallback(
        self,
        mock_update_checker_cls,
        mock_get_settings,
        mock_registry_cls,
        mock_asyncio_run,
        tmp_path,
    ) -> None:
        """UpdateCheckerSkill should get github token, beat interval, and file-based secret."""
        from zetherion_ai.skills.server import main

        mock_settings = _configure_mock_settings(mock_get_settings)
        mock_settings.auto_update_repo = "owner/repo"
        mock_settings.auto_update_enabled = True
        mock_settings.update_require_approval = False
        mock_settings.auto_update_check_interval_minutes = 15
        mock_settings.updater_url = "http://unused"
        mock_settings.updater_service_url = "http://updater:9090"
        mock_settings.updater_secret = ""

        mock_secret = MagicMock()
        mock_secret.get_secret_value.return_value = "ghp_token"
        mock_settings.github_token = mock_secret

        secret_file = Path(tmp_path) / ".updater-secret"
        secret_file.write_text("file-secret", encoding="utf-8")

        mock_registry = MagicMock()
        mock_registry_cls.return_value = mock_registry
        _close_coro_in_asyncio_run(mock_asyncio_run)

        with patch.dict(
            "os.environ",
            {"UPDATER_SECRET_PATH": str(secret_file)},
            clear=False,
        ):
            main()

        mock_update_checker_cls.assert_called_once()
        kwargs = mock_update_checker_cls.call_args.kwargs
        assert kwargs["github_token"] == "ghp_token"
        assert kwargs["updater_secret"] == "file-secret"
        assert kwargs["check_every_n_beats"] == 3
