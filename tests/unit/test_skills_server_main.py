"""Unit tests for the skills server main() entry point.

Verifies that the main() function in server.py correctly creates a
SkillRegistry, registers all three built-in skills, and launches
the async server via asyncio.run.
"""

from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


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
    mock_settings.auto_update_repo = ""
    mock_settings.auto_update_enabled = False
    mock_settings.update_require_approval = True
    mock_settings.auto_update_check_interval_minutes = 15
    mock_settings.updater_service_url = "http://updater:9090"
    mock_settings.updater_secret = ""
    mock_settings.updater_verify_signatures = True
    mock_settings.updater_verify_identity = ""
    mock_settings.updater_verify_oidc_issuer = "https://token.actions.githubusercontent.com"
    mock_settings.updater_verify_rekor_url = "https://rekor.sigstore.dev"
    mock_settings.updater_release_manifest_asset = "release-manifest.json"
    mock_settings.updater_release_signature_asset = "release-manifest.sig"
    mock_settings.updater_release_certificate_asset = "release-manifest.pem"
    mock_settings.postgres_owner_personal_schema = "owner_personal"
    mock_settings.postgres_control_plane_schema = "control_plane"
    mock_settings.github_token = None
    mock_get_settings.return_value = mock_settings
    return mock_settings


class TestSkillsServerMain:
    """Tests for the skills server main() function."""

    @patch("zetherion_ai.skills.server.asyncio.run")
    @patch("zetherion_ai.skills.server.SkillRegistry")
    @patch("zetherion_ai.config.get_settings")
    def test_registry_register_called_nine_times(
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

        assert mock_registry.register.call_count == 9

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
        from zetherion_ai.skills.email import EmailSkill
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
        assert len(calls) == 9
        assert isinstance(calls[0].args[0], TaskManagerSkill)
        assert isinstance(calls[1].args[0], CalendarSkill)
        assert isinstance(calls[2].args[0], ProfileSkill)
        assert isinstance(calls[3].args[0], HealthAnalyzerSkill)
        assert isinstance(calls[4].args[0], DevWatcherSkill)
        assert isinstance(calls[5].args[0], MilestoneSkill)
        assert isinstance(calls[6].args[0], GmailSkill)
        assert isinstance(calls[7].args[0], EmailSkill)
        assert isinstance(calls[8].args[0], UpdateCheckerSkill)

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


@patch("zetherion_ai.skills.server.run_server", new_callable=AsyncMock)
@patch("zetherion_ai.skills.server.SkillRegistry")
@patch("zetherion_ai.config.set_tenant_admin_manager")
@patch("zetherion_ai.config.set_secret_resolver")
@patch("zetherion_ai.config.set_settings_manager")
@patch("zetherion_ai.config.get_settings")
def test_work_router_startup_uses_runtime_tenant_encryptor(
    mock_get_settings,
    mock_set_settings_manager,
    mock_set_secret_resolver,
    mock_set_tenant_admin_manager,
    mock_registry_cls,
    mock_run_server,
) -> None:
    """Postgres/work-router startup should pass the runtime tenant encryptor downstream."""
    from zetherion_ai.skills.server import main

    mock_settings = _configure_mock_settings(mock_get_settings)
    mock_settings.postgres_dsn = "postgresql://zetherion:password@postgres:5432/zetherion"
    mock_settings.work_router_enabled = True
    mock_settings.provider_outlook_enabled = False
    mock_settings.email_security_gate_enabled = True
    mock_settings.local_extraction_required = False
    mock_settings.postgres_pool_min_size = 1
    mock_settings.postgres_pool_max_size = 1
    mock_settings.profile_confidence_threshold = 0.6
    mock_settings.profile_confirmation_expiry_hours = 24
    mock_settings.profile_max_pending_confirmations = 10
    mock_settings.updater_secret = "test-secret"

    mock_registry = MagicMock()
    mock_registry.initialize_all = AsyncMock()
    mock_registry_cls.return_value = mock_registry

    runtime_conn = AsyncMock()
    runtime_cm = AsyncMock()
    runtime_cm.__aenter__.return_value = runtime_conn
    runtime_cm.__aexit__.return_value = False
    runtime_pool = MagicMock()
    runtime_pool.acquire.return_value = runtime_cm
    runtime_pool.close = AsyncMock()

    integration_pool = MagicMock()
    integration_pool.close = AsyncMock()

    personal_pool = MagicMock()
    personal_pool.close = AsyncMock()

    tenant_encryptor = MagicMock(name="tenant_encryptor")
    settings_manager = MagicMock()
    settings_manager.initialize = AsyncMock()
    secrets_manager = MagicMock()
    secrets_manager.initialize = AsyncMock()
    announcement_repository = MagicMock()
    announcement_repository.initialize = AsyncMock()
    tenant_admin_manager = MagicMock()
    tenant_admin_manager.initialize = AsyncMock()
    tenant_admin_manager.set_critical_scorer = MagicMock()
    gmail_account_manager = MagicMock()
    gmail_account_manager.ensure_schema = AsyncMock()
    integration_storage = MagicMock()
    integration_storage.ensure_schema = AsyncMock()
    provider_registry = MagicMock()
    security_pipeline = MagicMock()
    security_pipeline.close = AsyncMock()
    email_inference_broker = MagicMock()
    email_inference_broker.close = AsyncMock()
    tenant_inference_broker = MagicMock()
    tenant_inference_broker.close = AsyncMock()
    yt_storage = MagicMock()
    yt_storage.initialize = AsyncMock()
    tenant_manager = MagicMock()
    tenant_manager.initialize = AsyncMock()
    personal_storage = MagicMock()
    personal_storage.ensure_schema = AsyncMock()
    google_auth = MagicMock()
    google_auth.generate_auth_url.return_value = ("https://example.test/auth", "state-token")

    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "asyncpg.create_pool",
                new_callable=AsyncMock,
                side_effect=[runtime_pool, integration_pool, personal_pool],
            )
        )
        mock_build_runtime_encryptors = stack.enter_context(
            patch(
                "zetherion_ai.security.domain_keys.build_runtime_encryptors",
                return_value=SimpleNamespace(
                    tenant_data=tenant_encryptor,
                    owner_personal=MagicMock(name="owner_encryptor"),
                ),
            )
        )
        stack.enter_context(
            patch("zetherion_ai.settings_manager.SettingsManager", return_value=settings_manager)
        )
        stack.enter_context(
            patch("zetherion_ai.security.secrets.SecretsManager", return_value=secrets_manager)
        )
        stack.enter_context(
            patch(
                "zetherion_ai.security.secret_resolver.SecretResolver",
                return_value=MagicMock(),
            )
        )
        mock_ensure_postgres_isolation_schemas = stack.enter_context(
            patch(
                "zetherion_ai.trust.data_plane.ensure_postgres_isolation_schemas",
                new_callable=AsyncMock,
            )
        )
        mock_ensure_owner_personal_intelligence_schema = stack.enter_context(
            patch(
                "zetherion_ai.personal.operational_storage.ensure_owner_personal_intelligence_schema",
                new_callable=AsyncMock,
            )
        )
        mock_ensure_trust_storage_schema = stack.enter_context(
            patch(
                "zetherion_ai.trust.storage.ensure_trust_storage_schema",
                new_callable=AsyncMock,
            )
        )
        stack.enter_context(
            patch(
                "zetherion_ai.skills.server.AnnouncementRepository",
                return_value=announcement_repository,
            )
        )
        stack.enter_context(
            patch(
                "zetherion_ai.skills.server.AnnouncementPolicyEngine",
                return_value=MagicMock(),
            )
        )
        stack.enter_context(
            patch(
                "zetherion_ai.memory.qdrant.QdrantMemory",
                side_effect=RuntimeError("qdrant unavailable in unit test"),
            )
        )
        stack.enter_context(
            patch("zetherion_ai.admin.TenantAdminManager", return_value=tenant_admin_manager)
        )
        stack.enter_context(
            patch("zetherion_ai.skills.youtube.storage.YouTubeStorage", return_value=yt_storage)
        )
        stack.enter_context(
            patch(
                "zetherion_ai.skills.youtube.intelligence.YouTubeIntelligenceSkill",
                return_value=MagicMock(),
            )
        )
        stack.enter_context(
            patch(
                "zetherion_ai.skills.youtube.management.YouTubeManagementSkill",
                return_value=MagicMock(),
            )
        )
        stack.enter_context(
            patch(
                "zetherion_ai.skills.youtube.strategy.YouTubeStrategySkill",
                return_value=MagicMock(),
            )
        )
        stack.enter_context(
            patch("zetherion_ai.api.tenant.TenantManager", return_value=tenant_manager)
        )
        stack.enter_context(
            patch(
                "zetherion_ai.skills.client_provisioning.ClientProvisioningSkill",
                return_value=MagicMock(),
            )
        )
        stack.enter_context(
            patch(
                "zetherion_ai.skills.tenant_intelligence.TenantIntelligenceSkill",
                return_value=MagicMock(),
            )
        )
        stack.enter_context(
            patch(
                "zetherion_ai.skills.client_insights.ClientInsightsSkill",
                return_value=MagicMock(),
            )
        )
        stack.enter_context(
            patch(
                "zetherion_ai.skills.client_app_watcher.ClientAppWatcherSkill",
                return_value=MagicMock(),
            )
        )
        stack.enter_context(
            patch("zetherion_ai.personal.storage.PersonalStorage", return_value=personal_storage)
        )
        stack.enter_context(
            patch(
                "zetherion_ai.skills.personal_model.PersonalModelSkill",
                return_value=MagicMock(),
            )
        )
        mock_gmail_account_manager = stack.enter_context(
            patch(
                "zetherion_ai.skills.gmail.accounts.GmailAccountManager",
                return_value=gmail_account_manager,
            )
        )
        stack.enter_context(
            patch(
                "zetherion_ai.integrations.storage.IntegrationStorage",
                return_value=integration_storage,
            )
        )
        stack.enter_context(
            patch(
                "zetherion_ai.routing.registry.ProviderRegistry",
                return_value=provider_registry,
            )
        )
        stack.enter_context(
            patch(
                "zetherion_ai.routing.registry.ProviderAdapters",
                side_effect=lambda **kwargs: kwargs,
            )
        )
        stack.enter_context(
            patch(
                "zetherion_ai.routing.registry.ProviderCapabilities",
                side_effect=lambda **kwargs: kwargs,
            )
        )
        stack.enter_context(
            patch(
                "zetherion_ai.security.content_pipeline.ContentSecurityPipeline",
                return_value=security_pipeline,
            )
        )
        stack.enter_context(
            patch(
                "zetherion_ai.routing.task_calendar_router.TaskCalendarRouter",
                return_value=MagicMock(),
            )
        )
        stack.enter_context(
            patch("zetherion_ai.routing.email_router.EmailRouter", return_value=MagicMock())
        )
        stack.enter_context(
            patch(
                "zetherion_ai.agent.inference.InferenceBroker",
                side_effect=[MagicMock(), tenant_inference_broker, email_inference_broker],
            )
        )
        stack.enter_context(
            patch(
                "zetherion_ai.integrations.providers.google.GoogleProviderAdapter",
                return_value=MagicMock(),
            )
        )
        stack.enter_context(
            patch(
                "zetherion_ai.integrations.providers.outlook.OutlookProviderAdapter",
                return_value=MagicMock(),
            )
        )
        stack.enter_context(
            patch("zetherion_ai.skills.server._resolve_google_oauth", return_value=google_auth)
        )
        stack.enter_context(
            patch(
                "zetherion_ai.skills.server._build_google_oauth_handler",
                return_value=MagicMock(),
            )
        )
        stack.enter_context(
            patch(
                "zetherion_ai.skills.server._build_google_oauth_authorize_handler",
                return_value=MagicMock(),
            )
        )

        main()

    mock_build_runtime_encryptors.assert_called_once_with(mock_settings)
    mock_gmail_account_manager.assert_called_once_with(integration_pool, tenant_encryptor)
    tenant_manager.initialize.assert_awaited_once()
    mock_ensure_postgres_isolation_schemas.assert_awaited_once_with(runtime_pool, mock_settings)
    mock_ensure_owner_personal_intelligence_schema.assert_awaited_once_with(
        runtime_pool,
        schema="owner_personal",
    )
    mock_ensure_trust_storage_schema.assert_awaited_once_with(
        runtime_pool,
        schema="control_plane",
    )
    mock_run_server.assert_awaited_once()
    runtime_pool.close.assert_awaited_once()
    integration_pool.close.assert_awaited_once()
    personal_pool.close.assert_awaited_once()
    security_pipeline.close.assert_awaited_once()
    tenant_inference_broker.close.assert_awaited_once()
    email_inference_broker.close.assert_awaited_once()
