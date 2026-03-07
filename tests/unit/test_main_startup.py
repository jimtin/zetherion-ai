"""Unit tests for main.py startup wiring."""

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from zetherion_ai.trust.scope import TrustDomain


class TestMainStartup:
    """Tests for main() startup wiring and encryption initialization."""

    @patch("zetherion_ai.main.ensure_trust_storage_schema", new_callable=AsyncMock)
    @patch("zetherion_ai.main.ensure_postgres_isolation_schemas", new_callable=AsyncMock)
    @patch("zetherion_ai.main.set_settings_manager")
    @patch("zetherion_ai.main.SettingsManager")
    @patch("zetherion_ai.main.UserManager")
    @patch("zetherion_ai.main.ZetherionAIBot")
    @patch("zetherion_ai.main.QdrantMemory")
    @patch("zetherion_ai.main.build_runtime_encryptors")
    @patch("zetherion_ai.main.get_settings")
    @patch("zetherion_ai.main.setup_logging")
    @patch("zetherion_ai.main.get_logger")
    async def test_encryption_always_initialized(
        self,
        mock_get_logger,
        mock_setup_logging,
        mock_get_settings,
        mock_build_runtime_encryptors,
        mock_qdrant_memory_cls,
        mock_bot_cls,
        mock_user_manager_cls,
        mock_settings_manager_cls,
        mock_set_settings_manager,
        mock_ensure_postgres_isolation_schemas,
        mock_ensure_trust_storage_schema,
    ) -> None:
        """Runtime encryption bundle should always be created."""
        from zetherion_ai.main import main

        settings = MagicMock()
        settings.encryption_passphrase.get_secret_value.return_value = "test-passphrase-long-enough"
        settings.encryption_salt_path = "data/salt.bin"
        settings.encryption_strict = False
        settings.environment = "test"
        settings.qdrant_url = "http://localhost:6333"
        settings.qdrant_owner_url = "http://localhost:6333"
        settings.discord_token.get_secret_value.return_value = "fake-token"
        settings.postgres_dsn = "postgresql://test:test@localhost:5432/test"
        settings.postgres_control_plane_schema = "control_plane"
        mock_get_settings.return_value = settings
        mock_get_logger.return_value = MagicMock()

        encryptors = SimpleNamespace(
            owner_personal=MagicMock(name="owner_encryptor"),
            tenant_data=MagicMock(name="tenant_encryptor"),
            owner_personal_salt_path="data/owner-salt.bin",
            tenant_data_salt_path="data/tenant-salt.bin",
        )
        mock_build_runtime_encryptors.return_value = encryptors

        mock_memory = AsyncMock()
        mock_qdrant_memory_cls.return_value = mock_memory

        mock_user_mgr = AsyncMock()
        mock_user_mgr._pool = MagicMock()
        mock_user_manager_cls.return_value = mock_user_mgr

        mock_settings_mgr = AsyncMock()
        mock_settings_manager_cls.return_value = mock_settings_mgr

        mock_bot = AsyncMock()
        mock_bot.start = AsyncMock()
        mock_bot.close = AsyncMock()
        mock_bot_cls.return_value = mock_bot

        await main()

        mock_build_runtime_encryptors.assert_called_once_with(settings)
        mock_ensure_postgres_isolation_schemas.assert_awaited_once_with(
            mock_user_mgr._pool,
            settings,
        )
        mock_ensure_trust_storage_schema.assert_awaited_once_with(
            mock_user_mgr._pool,
            schema="control_plane",
        )

    @patch("zetherion_ai.main.ensure_trust_storage_schema", new_callable=AsyncMock)
    @patch("zetherion_ai.main.ensure_postgres_isolation_schemas", new_callable=AsyncMock)
    @patch("zetherion_ai.main.set_settings_manager")
    @patch("zetherion_ai.main.SettingsManager")
    @patch("zetherion_ai.main.UserManager")
    @patch("zetherion_ai.main.ZetherionAIBot")
    @patch("zetherion_ai.main.QdrantMemory")
    @patch("zetherion_ai.main.build_runtime_encryptors")
    @patch("zetherion_ai.main.get_settings")
    @patch("zetherion_ai.main.setup_logging")
    @patch("zetherion_ai.main.get_logger")
    async def test_qdrant_memory_receives_owner_encryptor(
        self,
        mock_get_logger,
        mock_setup_logging,
        mock_get_settings,
        mock_build_runtime_encryptors,
        mock_qdrant_memory_cls,
        mock_bot_cls,
        mock_user_manager_cls,
        mock_settings_manager_cls,
        mock_set_settings_manager,
        mock_ensure_postgres_isolation_schemas,
        mock_ensure_trust_storage_schema,
    ) -> None:
        """QdrantMemory should be instantiated with the owner-domain encryptor."""
        from zetherion_ai.main import main

        settings = MagicMock()
        settings.encryption_passphrase.get_secret_value.return_value = "test-passphrase-long-enough"
        settings.encryption_salt_path = "data/salt.bin"
        settings.encryption_strict = False
        settings.environment = "test"
        settings.qdrant_url = "http://localhost:6333"
        settings.qdrant_owner_url = "http://owner-qdrant:6333"
        settings.discord_token.get_secret_value.return_value = "fake-token"
        settings.postgres_dsn = "postgresql://test:test@localhost:5432/test"
        settings.postgres_control_plane_schema = "control_plane"
        mock_get_settings.return_value = settings
        mock_get_logger.return_value = MagicMock()

        owner_encryptor = MagicMock(name="owner_encryptor")
        encryptors = SimpleNamespace(
            owner_personal=owner_encryptor,
            tenant_data=MagicMock(name="tenant_encryptor"),
            owner_personal_salt_path="data/owner-salt.bin",
            tenant_data_salt_path="data/tenant-salt.bin",
        )
        mock_build_runtime_encryptors.return_value = encryptors

        mock_memory = AsyncMock()
        mock_qdrant_memory_cls.return_value = mock_memory

        mock_user_mgr = AsyncMock()
        mock_user_mgr._pool = MagicMock()
        mock_user_manager_cls.return_value = mock_user_mgr

        mock_settings_mgr = AsyncMock()
        mock_settings_manager_cls.return_value = mock_settings_mgr

        mock_bot = AsyncMock()
        mock_bot.start = AsyncMock()
        mock_bot.close = AsyncMock()
        mock_bot_cls.return_value = mock_bot

        await main()

        mock_qdrant_memory_cls.assert_called_once_with(
            encryptor=owner_encryptor,
            trust_domain=TrustDomain.OWNER_PERSONAL,
        )
        mock_memory.initialize.assert_awaited_once()

    def test_missing_encryption_passphrase_crashes(self) -> None:
        """Missing ENCRYPTION_PASSPHRASE should raise Pydantic ValidationError."""
        from zetherion_ai.config import Settings

        saved = os.environ.get("ENCRYPTION_PASSPHRASE")
        os.environ.pop("ENCRYPTION_PASSPHRASE", None)
        try:
            with pytest.raises(ValidationError):
                Settings(
                    discord_token="test-token",
                    gemini_api_key="test-key",
                    _env_file=None,
                )
        finally:
            if saved is not None:
                os.environ["ENCRYPTION_PASSPHRASE"] = saved

    @patch("zetherion_ai.main.ensure_trust_storage_schema", new_callable=AsyncMock)
    @patch("zetherion_ai.main.ensure_postgres_isolation_schemas", new_callable=AsyncMock)
    @patch("zetherion_ai.main.set_settings_manager")
    @patch("zetherion_ai.main.SettingsManager")
    @patch("zetherion_ai.main.UserManager")
    @patch("zetherion_ai.main.ZetherionAIBot")
    @patch("zetherion_ai.main.QdrantMemory")
    @patch("zetherion_ai.main.build_runtime_encryptors")
    @patch("zetherion_ai.main.get_settings")
    @patch("zetherion_ai.main.setup_logging")
    @patch("zetherion_ai.main.get_logger")
    async def test_bot_receives_memory(
        self,
        mock_get_logger,
        mock_setup_logging,
        mock_get_settings,
        mock_build_runtime_encryptors,
        mock_qdrant_memory_cls,
        mock_bot_cls,
        mock_user_manager_cls,
        mock_settings_manager_cls,
        mock_set_settings_manager,
        mock_ensure_postgres_isolation_schemas,
        mock_ensure_trust_storage_schema,
    ) -> None:
        """ZetherionAIBot should receive the memory, user manager, and settings manager."""
        from zetherion_ai.main import main

        settings = MagicMock()
        settings.encryption_passphrase.get_secret_value.return_value = "test-passphrase-long-enough"
        settings.encryption_salt_path = "data/salt.bin"
        settings.encryption_strict = False
        settings.environment = "test"
        settings.qdrant_url = "http://localhost:6333"
        settings.qdrant_owner_url = "http://localhost:6333"
        settings.discord_token.get_secret_value.return_value = "fake-token"
        settings.postgres_dsn = "postgresql://test:test@localhost:5432/test"
        settings.postgres_control_plane_schema = "control_plane"
        mock_get_settings.return_value = settings
        mock_get_logger.return_value = MagicMock()

        mock_build_runtime_encryptors.return_value = SimpleNamespace(
            owner_personal=MagicMock(name="owner_encryptor"),
            tenant_data=MagicMock(name="tenant_encryptor"),
            owner_personal_salt_path="data/owner-salt.bin",
            tenant_data_salt_path="data/tenant-salt.bin",
        )

        mock_memory = AsyncMock()
        mock_qdrant_memory_cls.return_value = mock_memory

        mock_user_mgr = AsyncMock()
        mock_user_mgr._pool = MagicMock()
        mock_user_manager_cls.return_value = mock_user_mgr

        mock_settings_mgr = AsyncMock()
        mock_settings_manager_cls.return_value = mock_settings_mgr

        mock_bot = AsyncMock()
        mock_bot.start = AsyncMock()
        mock_bot.close = AsyncMock()
        mock_bot_cls.return_value = mock_bot

        await main()

        mock_bot_cls.assert_called_once()
        call_kwargs = mock_bot_cls.call_args.kwargs
        assert call_kwargs["memory"] is mock_memory
        assert call_kwargs["user_manager"] is mock_user_mgr
        assert call_kwargs["settings_manager"] is mock_settings_mgr
        mock_bot.start.assert_awaited_once_with("fake-token")

    @patch("zetherion_ai.main.ensure_trust_storage_schema", new_callable=AsyncMock)
    @patch("zetherion_ai.main.ensure_postgres_isolation_schemas", new_callable=AsyncMock)
    @patch("zetherion_ai.main.set_settings_manager")
    @patch("zetherion_ai.main.SettingsManager")
    @patch("zetherion_ai.main.UserManager")
    @patch("zetherion_ai.main.ZetherionAIBot")
    @patch("zetherion_ai.main.QdrantMemory")
    @patch("zetherion_ai.main.build_runtime_encryptors")
    @patch("zetherion_ai.main.get_settings")
    @patch("zetherion_ai.main.setup_logging")
    @patch("zetherion_ai.main.get_logger")
    async def test_main_bootstraps_model_settings_once(
        self,
        mock_get_logger,
        mock_setup_logging,
        mock_get_settings,
        mock_build_runtime_encryptors,
        mock_qdrant_memory_cls,
        mock_bot_cls,
        mock_user_manager_cls,
        mock_settings_manager_cls,
        mock_set_settings_manager,
        mock_ensure_postgres_isolation_schemas,
        mock_ensure_trust_storage_schema,
    ) -> None:
        """Startup should seed model settings into DB when keys are missing."""
        from zetherion_ai.main import main

        settings = MagicMock()
        settings.encryption_passphrase.get_secret_value.return_value = "test-passphrase-long-enough"
        settings.encryption_salt_path = "data/salt.bin"
        settings.encryption_strict = False
        settings.environment = "test"
        settings.qdrant_url = "http://localhost:6333"
        settings.qdrant_owner_url = "http://localhost:6333"
        settings.discord_token.get_secret_value.return_value = "fake-token"
        settings.postgres_dsn = "postgresql://test:test@localhost:5432/test"
        settings.postgres_control_plane_schema = "control_plane"
        settings.owner_user_id = 123
        settings.openai_model = "gpt-5.2"
        settings.claude_model = "claude-sonnet-4-5-20250929"
        settings.groq_model = "llama-3.3-70b-versatile"
        settings.router_model = "gemini-2.0-flash"
        settings.ollama_generation_model = "llama3.1:8b"
        mock_get_settings.return_value = settings
        mock_get_logger.return_value = MagicMock()

        mock_build_runtime_encryptors.return_value = SimpleNamespace(
            owner_personal=MagicMock(name="owner_encryptor"),
            tenant_data=MagicMock(name="tenant_encryptor"),
            owner_personal_salt_path="data/owner-salt.bin",
            tenant_data_salt_path="data/tenant-salt.bin",
        )

        mock_memory = AsyncMock()
        mock_qdrant_memory_cls.return_value = mock_memory

        mock_user_mgr = AsyncMock()
        mock_user_mgr._pool = MagicMock()
        mock_user_manager_cls.return_value = mock_user_mgr

        mock_settings_mgr = AsyncMock()
        mock_settings_manager_cls.return_value = mock_settings_mgr

        mock_bot = AsyncMock()
        mock_bot.start = AsyncMock()
        mock_bot.close = AsyncMock()
        mock_bot_cls.return_value = mock_bot

        await main()

        expected_keys = {
            "openai_model",
            "claude_model",
            "groq_model",
            "router_model",
            "ollama_generation_model",
        }
        seeded_keys = {
            call.args[1] for call in mock_settings_mgr.seed_if_missing.await_args_list if call.args
        }
        assert expected_keys.issubset(seeded_keys)
