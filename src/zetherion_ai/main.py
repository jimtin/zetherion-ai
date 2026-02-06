"""Main entry point for SecureClaw."""

import asyncio

from zetherion_ai.config import get_settings
from zetherion_ai.discord.bot import SecureClawBot
from zetherion_ai.logging import get_logger, setup_logging
from zetherion_ai.memory.qdrant import QdrantMemory


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

    # Initialize memory system
    memory = QdrantMemory()
    await memory.initialize()

    # Initialize and run the Discord bot
    bot = SecureClawBot(memory=memory)

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
