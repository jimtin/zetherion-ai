"""Unit tests for main.py startup wiring.

Verifies that the application entry point correctly initializes encryption,
passes the encryptor to QdrantMemory, and fails when required config is missing.
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError


class TestMainStartup:
    """Tests for main() startup wiring and encryption initialization."""

    @patch("zetherion_ai.main.set_settings_manager")
    @patch("zetherion_ai.main.SettingsManager")
    @patch("zetherion_ai.main.UserManager")
    @patch("zetherion_ai.main.ZetherionAIBot")
    @patch("zetherion_ai.main.QdrantMemory")
    @patch("zetherion_ai.main.FieldEncryptor")
    @patch("zetherion_ai.main.KeyManager")
    @patch("zetherion_ai.main.get_settings")
    @patch("zetherion_ai.main.setup_logging")
    @patch("zetherion_ai.main.get_logger")
    async def test_encryption_always_initialized(
        self,
        mock_get_logger,
        mock_setup_logging,
        mock_get_settings,
        mock_key_manager_cls,
        mock_field_encryptor_cls,
        mock_qdrant_memory_cls,
        mock_bot_cls,
        mock_user_manager_cls,
        mock_settings_manager_cls,
        mock_set_settings_manager,
    ) -> None:
        """KeyManager and FieldEncryptor should always be created."""
        from zetherion_ai.main import main

        settings = MagicMock()
        settings.encryption_passphrase.get_secret_value.return_value = "test-passphrase-long-enough"
        settings.encryption_salt_path = "data/salt.bin"
        settings.encryption_strict = False
        settings.environment = "test"
        settings.qdrant_url = "http://localhost:6333"
        settings.discord_token.get_secret_value.return_value = "fake-token"
        settings.postgres_dsn = "postgresql://test:test@localhost:5432/test"
        mock_get_settings.return_value = settings
        mock_get_logger.return_value = MagicMock()

        mock_km_instance = MagicMock()
        mock_km_instance.key = b"\x00" * 32
        mock_key_manager_cls.return_value = mock_km_instance

        mock_encryptor_instance = MagicMock()
        mock_field_encryptor_cls.return_value = mock_encryptor_instance

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

        mock_key_manager_cls.assert_called_once_with(
            passphrase="test-passphrase-long-enough",
            salt_path="data/salt.bin",
        )
        mock_field_encryptor_cls.assert_called_once_with(
            key=mock_km_instance.key,
            strict=False,
        )

    @patch("zetherion_ai.main.set_settings_manager")
    @patch("zetherion_ai.main.SettingsManager")
    @patch("zetherion_ai.main.UserManager")
    @patch("zetherion_ai.main.ZetherionAIBot")
    @patch("zetherion_ai.main.QdrantMemory")
    @patch("zetherion_ai.main.FieldEncryptor")
    @patch("zetherion_ai.main.KeyManager")
    @patch("zetherion_ai.main.get_settings")
    @patch("zetherion_ai.main.setup_logging")
    @patch("zetherion_ai.main.get_logger")
    async def test_qdrant_memory_receives_encryptor(
        self,
        mock_get_logger,
        mock_setup_logging,
        mock_get_settings,
        mock_key_manager_cls,
        mock_field_encryptor_cls,
        mock_qdrant_memory_cls,
        mock_bot_cls,
        mock_user_manager_cls,
        mock_settings_manager_cls,
        mock_set_settings_manager,
    ) -> None:
        """QdrantMemory should be instantiated with the encryptor."""
        from zetherion_ai.main import main

        settings = MagicMock()
        settings.encryption_passphrase.get_secret_value.return_value = "test-passphrase-long-enough"
        settings.encryption_salt_path = "data/salt.bin"
        settings.encryption_strict = False
        settings.environment = "test"
        settings.qdrant_url = "http://localhost:6333"
        settings.discord_token.get_secret_value.return_value = "fake-token"
        settings.postgres_dsn = "postgresql://test:test@localhost:5432/test"
        mock_get_settings.return_value = settings
        mock_get_logger.return_value = MagicMock()

        mock_km_instance = MagicMock()
        mock_km_instance.key = b"\x00" * 32
        mock_key_manager_cls.return_value = mock_km_instance

        mock_encryptor_instance = MagicMock()
        mock_field_encryptor_cls.return_value = mock_encryptor_instance

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
            encryptor=mock_encryptor_instance,
        )
        mock_memory.initialize.assert_awaited_once()

    def test_missing_encryption_passphrase_crashes(self) -> None:
        """Missing ENCRYPTION_PASSPHRASE should raise Pydantic ValidationError."""
        from zetherion_ai.config import Settings

        _env = {
            "DISCORD_TOKEN": "test-token",
            "GEMINI_API_KEY": "test-key",
            # ENCRYPTION_PASSPHRASE deliberately omitted
        }

        # Temporarily remove the env var if it exists
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

    @patch("zetherion_ai.main.set_settings_manager")
    @patch("zetherion_ai.main.SettingsManager")
    @patch("zetherion_ai.main.UserManager")
    @patch("zetherion_ai.main.ZetherionAIBot")
    @patch("zetherion_ai.main.QdrantMemory")
    @patch("zetherion_ai.main.FieldEncryptor")
    @patch("zetherion_ai.main.KeyManager")
    @patch("zetherion_ai.main.get_settings")
    @patch("zetherion_ai.main.setup_logging")
    @patch("zetherion_ai.main.get_logger")
    async def test_bot_receives_memory(
        self,
        mock_get_logger,
        mock_setup_logging,
        mock_get_settings,
        mock_key_manager_cls,
        mock_field_encryptor_cls,
        mock_qdrant_memory_cls,
        mock_bot_cls,
        mock_user_manager_cls,
        mock_settings_manager_cls,
        mock_set_settings_manager,
    ) -> None:
        """ZetherionAIBot should receive the memory, user_manager, and settings_manager."""
        from zetherion_ai.main import main

        settings = MagicMock()
        settings.encryption_passphrase.get_secret_value.return_value = "test-passphrase-long-enough"
        settings.encryption_salt_path = "data/salt.bin"
        settings.encryption_strict = False
        settings.environment = "test"
        settings.qdrant_url = "http://localhost:6333"
        settings.discord_token.get_secret_value.return_value = "fake-token"
        settings.postgres_dsn = "postgresql://test:test@localhost:5432/test"
        mock_get_settings.return_value = settings
        mock_get_logger.return_value = MagicMock()

        mock_km_instance = MagicMock()
        mock_km_instance.key = b"\x00" * 32
        mock_key_manager_cls.return_value = mock_km_instance

        mock_encryptor_instance = MagicMock()
        mock_field_encryptor_cls.return_value = mock_encryptor_instance

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

        mock_bot_cls.assert_called_once_with(
            memory=mock_memory,
            user_manager=mock_user_mgr,
            settings_manager=mock_settings_mgr,
        )
        mock_bot.start.assert_awaited_once_with("fake-token")
