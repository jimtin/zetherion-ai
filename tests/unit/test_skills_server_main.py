"""Unit tests for the skills server main() entry point.

Verifies that the main() function in server.py correctly creates a
SkillRegistry, registers all three built-in skills, and launches
the async server via asyncio.run.
"""

from unittest.mock import MagicMock, patch


class TestSkillsServerMain:
    """Tests for the skills server main() function."""

    @patch("zetherion_ai.skills.server.asyncio.run")
    @patch("zetherion_ai.skills.server.SkillRegistry")
    @patch("zetherion_ai.config.get_settings")
    def test_registry_register_called_three_times(
        self,
        mock_get_settings,
        mock_registry_cls,
        mock_asyncio_run,
    ) -> None:
        """registry.register should be called exactly 3 times."""
        from zetherion_ai.skills.server import main

        mock_registry = MagicMock()
        mock_registry_cls.return_value = mock_registry

        main()

        assert mock_registry.register.call_count == 3

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

        mock_registry = MagicMock()
        mock_registry_cls.return_value = mock_registry

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

        mock_registry = MagicMock()
        mock_registry_cls.return_value = mock_registry

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

        mock_registry = MagicMock()
        mock_registry_cls.return_value = mock_registry

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

        mock_registry = MagicMock()
        mock_registry_cls.return_value = mock_registry

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
        """Skills should be registered in the order: TaskManager, Calendar, Profile."""
        from zetherion_ai.skills.calendar import CalendarSkill
        from zetherion_ai.skills.profile_skill import ProfileSkill
        from zetherion_ai.skills.server import main
        from zetherion_ai.skills.task_manager import TaskManagerSkill

        mock_registry = MagicMock()
        mock_registry_cls.return_value = mock_registry

        main()

        calls = mock_registry.register.call_args_list
        assert len(calls) == 3
        assert isinstance(calls[0].args[0], TaskManagerSkill)
        assert isinstance(calls[1].args[0], CalendarSkill)
        assert isinstance(calls[2].args[0], ProfileSkill)

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

        mock_registry = MagicMock()
        mock_registry_cls.return_value = mock_registry

        main()

        mock_get_settings.assert_called_once()
