"""Main entry point for Zetherion AI."""

import asyncio
from typing import Any

from zetherion_ai.admin import TenantAdminManager
from zetherion_ai.config import (
    get_settings,
    set_secret_resolver,
    set_settings_manager,
    set_tenant_admin_manager,
)
from zetherion_ai.discord.bot import ZetherionAIBot
from zetherion_ai.discord.user_manager import UserManager
from zetherion_ai.logging import get_logger, setup_logging
from zetherion_ai.memory.qdrant import QdrantMemory
from zetherion_ai.queue.manager import QueueManager
from zetherion_ai.queue.processors import QueueProcessors
from zetherion_ai.queue.storage import QueueStorage
from zetherion_ai.security.encryption import FieldEncryptor
from zetherion_ai.security.keys import KeyManager
from zetherion_ai.security.secret_resolver import SecretResolver
from zetherion_ai.security.secrets import SecretsManager
from zetherion_ai.settings_manager import SettingsManager


async def _bootstrap_dynamic_model_settings(
    settings_mgr: SettingsManager,
    settings: Any,
) -> None:
    """Seed dynamic model settings from env values only when DB keys are absent."""
    owner_id = getattr(settings, "owner_user_id", None)
    updated_by = owner_id if isinstance(owner_id, int) else None

    defaults = (
        ("openai_model", getattr(settings, "openai_model", None)),
        ("claude_model", getattr(settings, "claude_model", None)),
        ("groq_model", getattr(settings, "groq_model", None)),
        ("router_model", getattr(settings, "router_model", None)),
        ("ollama_generation_model", getattr(settings, "ollama_generation_model", None)),
    )

    for key, value in defaults:
        if isinstance(value, str) and value.strip():
            await settings_mgr.seed_if_missing(
                "models",
                key,
                value.strip(),
                data_type="string",
                description="Bootstrapped from environment defaults at startup",
                updated_by=updated_by,
            )


async def _bootstrap_dynamic_dev_agent_settings(
    settings_mgr: SettingsManager,
    settings: Any,
) -> None:
    """Seed dev-agent runtime defaults (only when keys are absent)."""
    owner_id = getattr(settings, "owner_user_id", None)
    updated_by = owner_id if isinstance(owner_id, int) else None

    defaults: tuple[tuple[str, Any, str], ...] = (
        ("enabled", getattr(settings, "dev_agent_enabled", False), "bool"),
        ("service_url", getattr(settings, "dev_agent_service_url", ""), "string"),
        ("cleanup_hour", getattr(settings, "dev_agent_cleanup_hour", 2), "int"),
        ("cleanup_minute", getattr(settings, "dev_agent_cleanup_minute", 30), "int"),
        (
            "approval_reprompt_hours",
            getattr(settings, "dev_agent_approval_reprompt_hours", 24),
            "int",
        ),
        ("discord_channel_id", getattr(settings, "dev_agent_discord_channel_id", ""), "string"),
        ("discord_guild_id", getattr(settings, "dev_agent_discord_guild_id", ""), "string"),
        (
            "webhook_name",
            getattr(settings, "dev_agent_webhook_name", "zetherion-dev-agent"),
            "string",
        ),
        ("webhook_id", getattr(settings, "dev_agent_webhook_id", ""), "string"),
    )

    for key, value, data_type in defaults:
        await settings_mgr.seed_if_missing(
            "dev_agent",
            key,
            value,
            data_type=data_type,
            description="Bootstrapped from environment defaults at startup",
            updated_by=updated_by,
        )


async def main() -> None:
    """Main application entry point."""
    setup_logging()
    log = get_logger("zetherion_ai.main")

    settings = get_settings()
    log.info(
        "starting_zetherion_ai",
        environment=settings.environment,
        qdrant_url=settings.qdrant_url,
    )

    # Initialize encryption (mandatory — config validation ensures passphrase exists)
    key_manager = KeyManager(
        passphrase=settings.encryption_passphrase.get_secret_value(),
        salt_path=settings.encryption_salt_path,
    )
    encryptor = FieldEncryptor(
        key=key_manager.key,
        strict=settings.encryption_strict,
    )
    log.info("encryption_initialized", strict=settings.encryption_strict)

    # Initialize RBAC user manager
    user_manager = UserManager(dsn=settings.postgres_dsn, allow_all=settings.allow_all_users)
    await user_manager.initialize()
    log.info("user_manager_initialized")

    # Initialize runtime settings manager (shares DB with user manager)
    settings_mgr = SettingsManager()
    if user_manager._pool:
        await settings_mgr.initialize(user_manager._pool)
        set_settings_manager(settings_mgr)
        await _bootstrap_dynamic_model_settings(settings_mgr, settings)
        await _bootstrap_dynamic_dev_agent_settings(settings_mgr, settings)
        log.info("settings_manager_initialized")

    # Initialize encrypted secrets manager (shares DB + encryptor)
    secrets_mgr = SecretsManager(encryptor=encryptor)
    if user_manager._pool:
        await secrets_mgr.initialize(user_manager._pool)
        set_secret_resolver(SecretResolver(secrets_mgr, settings))
        log.info("secrets_manager_initialized")

    tenant_admin_mgr: TenantAdminManager | None = None
    if user_manager._pool:
        tenant_admin_mgr = TenantAdminManager(pool=user_manager._pool, encryptor=encryptor)
        await tenant_admin_mgr.initialize()
        set_tenant_admin_manager(tenant_admin_mgr)
        log.info("tenant_admin_manager_initialized")

    # Initialize memory system with encryption
    memory = QdrantMemory(encryptor=encryptor)
    await memory.initialize()
    log.info("memory_initialized", qdrant_url=settings.qdrant_url)

    # Initialize priority message queue (if enabled and DB is available)
    queue_mgr: QueueManager | None = None
    if settings.queue_enabled and user_manager._pool:
        queue_storage = QueueStorage(pool=user_manager._pool)
        queue_processors = QueueProcessors()  # bot/agent wired in setup_hook
        queue_mgr = QueueManager(storage=queue_storage, processors=queue_processors)
        log.info("queue_initialized")

    # Initialize and run the Discord bot
    bot = ZetherionAIBot(
        memory=memory,
        user_manager=user_manager,
        settings_manager=settings_mgr,
        tenant_admin_manager=tenant_admin_mgr,
        queue_manager=queue_mgr,
    )
    log.info("bot_created")

    try:
        await bot.start(settings.discord_token.get_secret_value())
    except KeyboardInterrupt:
        log.info("shutdown_requested")
    finally:
        await bot.close()
        await user_manager.close()
        log.info("zetherion_ai_stopped")


def run() -> None:
    """Run the application."""
    asyncio.run(main())


if __name__ == "__main__":
    run()
