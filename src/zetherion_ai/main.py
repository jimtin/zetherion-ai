"""Main entry point for Zetherion AI."""

import asyncio

from zetherion_ai.config import get_settings, set_secret_resolver, set_settings_manager
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
        log.info("settings_manager_initialized")

    # Initialize encrypted secrets manager (shares DB + encryptor)
    secrets_mgr = SecretsManager(encryptor=encryptor)
    if user_manager._pool:
        await secrets_mgr.initialize(user_manager._pool)
        set_secret_resolver(SecretResolver(secrets_mgr, settings))
        log.info("secrets_manager_initialized")

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
