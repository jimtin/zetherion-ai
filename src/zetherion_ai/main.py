"""Main entry point for Zetherion AI."""

import asyncio

from zetherion_ai.config import get_settings
from zetherion_ai.discord.bot import ZetherionAIBot
from zetherion_ai.logging import get_logger, setup_logging
from zetherion_ai.memory.qdrant import QdrantMemory
from zetherion_ai.security.encryption import FieldEncryptor
from zetherion_ai.security.keys import KeyManager


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

    # Initialize memory system with encryption
    memory = QdrantMemory(encryptor=encryptor)
    await memory.initialize()

    # Initialize and run the Discord bot
    bot = ZetherionAIBot(memory=memory)

    try:
        await bot.start(settings.discord_token.get_secret_value())
    except KeyboardInterrupt:
        log.info("shutdown_requested")
    finally:
        await bot.close()
        log.info("zetherion_ai_stopped")


def run() -> None:
    """Run the application."""
    asyncio.run(main())


if __name__ == "__main__":
    run()
