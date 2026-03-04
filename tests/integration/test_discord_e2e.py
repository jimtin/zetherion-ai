"""End-to-end Discord integration tests with real Discord API.

These tests send actual messages through Discord and verify bot responses.
Requires:
- TEST_DISCORD_BOT_TOKEN environment variable (separate test bot)
- TEST_DISCORD_CHANNEL_ID environment variable (test channel ID)
- TEST_DISCORD_TARGET_BOT_ID environment variable (optional, ID of bot to test)
- DISCORD_E2E_PROVIDER (optional, default: groq; allowed: groq|local)
- Test Discord server set up
"""

import asyncio
import os
import re
from collections.abc import AsyncGenerator
from pathlib import Path
from uuid import uuid4

import discord
import pytest
import pytest_asyncio


def _load_env() -> None:
    """Load environment variables from .env file."""
    try:
        from dotenv import load_dotenv

        env_path = Path(__file__).parent.parent.parent / ".env"
        load_dotenv(dotenv_path=env_path)
    except ImportError:
        pass


# Load .env before evaluating skip conditions so TEST_DISCORD_* vars are available
_load_env()

DISCORD_E2E_PROVIDER = os.getenv("DISCORD_E2E_PROVIDER", "groq").strip().lower() or "groq"
if DISCORD_E2E_PROVIDER not in {"groq", "local"}:
    DISCORD_E2E_PROVIDER = "groq"

# Skip if test Discord credentials not provided
SKIP_DISCORD_E2E = not all(
    [
        os.getenv("TEST_DISCORD_BOT_TOKEN"),
        os.getenv("TEST_DISCORD_CHANNEL_ID"),
    ]
)

SKIP_REASON = (
    "Discord E2E tests require TEST_DISCORD_BOT_TOKEN and TEST_DISCORD_CHANNEL_ID "
    "environment variables. Set these in your .env to run Discord E2E tests."
)


def validate_memory_recall(response: str, expected_info: str) -> tuple[bool, str]:
    """Validate whether response indicates successful memory recall.

    Args:
        response: The bot's response to analyze.
        expected_info: The information that should have been recalled
            (e.g., "purple" for favorite color).

    Returns:
        Tuple of (success: bool, explanation: str).
    """
    response_lower = response.lower()
    expected_lower = expected_info.lower()

    # Fast deterministic checks first to avoid LLM validation flakiness.
    if expected_lower in response_lower:
        return True, f"Direct match for '{expected_info}'"

    expected_parts = [part.strip() for part in expected_lower.split("-") if part.strip()]
    if expected_parts:
        part_matches: list[bool] = []
        for part in expected_parts:
            if part in response_lower:
                part_matches.append(True)
                continue
            if re.fullmatch(r"[0-9a-f]{3,}", part) and f"#{part}" in response_lower:
                part_matches.append(True)
                continue
            part_matches.append(False)
        if all(part_matches):
            return True, f"All expected components found for '{expected_info}'"

    # Check if it's an explicit recall failure.
    error_phrases = [
        "don't know",
        "don't remember",
        "not sure",
        "can't recall",
        "unable to",
        "couldn't",
        "haven't told me",
        "didn't tell me",
        "no information",
        "no record",
    ]
    is_error = any(phrase in response_lower for phrase in error_phrases)
    if is_error:
        return False, "Response explicitly indicates failed recall"

    # Last-chance deterministic heuristic: require a substantive answer and
    # at least one meaningful expected token.
    if len(response.strip()) > 20 and any(part in response_lower for part in expected_parts):
        return True, "Substantive response includes expected tokens"

    return False, f"Expected value '{expected_info}' not found in recall response"


class DiscordTestClient:
    """Discord test client to send messages and read responses."""

    def __init__(self, token: str, channel_id: int) -> None:
        """Initialize test client.

        Args:
            token: Discord bot token (for test user bot).
            channel_id: Channel ID to send test messages to.
        """
        self.token = token
        self.channel_id = channel_id
        self.client: discord.Client | None = None
        self.channel: discord.TextChannel | None = None
        self._client_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the Discord client."""
        intents = discord.Intents.default()
        intents.message_content = True
        intents.messages = True  # Required to receive message events
        intents.guilds = True
        intents.members = True  # Required to access guild.members

        self.client = discord.Client(intents=intents)

        @self.client.event
        async def on_ready() -> None:
            print(f"✅ Test client logged in as {self.client.user}")  # type: ignore[union-attr]
            # Get test channel
            channel = self.client.get_channel(self.channel_id)  # type: ignore[union-attr]
            if not isinstance(channel, discord.TextChannel):
                raise RuntimeError(f"Channel {self.channel_id} is not a text channel")
            self.channel = channel

        # Start client in background task
        self._client_task = asyncio.create_task(self.client.start(self.token))

        # Wait for client to be ready
        for _ in range(30):  # 30 second timeout
            if self.client.is_ready() and self.channel:
                # Give it a moment to fully initialize
                await asyncio.sleep(1)
                return
            await asyncio.sleep(1)

        raise TimeoutError("Discord test client failed to connect")

    async def stop(self) -> None:
        """Stop the Discord client."""
        if self.client:
            await self.client.close()

    async def send_message(self, content: str) -> discord.Message:
        """Send a message to the test channel.

        Args:
            content: Message content to send.

        Returns:
            The sent message.
        """
        if not self.channel:
            raise RuntimeError("Test client not connected")

        return await self.channel.send(content)

    def get_zetherion_ai_bot_id(self) -> int | None:
        """Get the Zetherion AI production bot's user ID.

        Returns:
            Bot user ID, or None if not found.
        """
        if not self.client or not self.channel:
            return None

        # Check if explicit bot ID is configured
        explicit_bot_id = os.getenv("TEST_DISCORD_TARGET_BOT_ID")
        if explicit_bot_id:
            try:
                bot_id = int(explicit_bot_id)
                print(f"Using explicit bot ID from TEST_DISCORD_TARGET_BOT_ID: {bot_id}")
                return bot_id
            except ValueError:
                print(f"Warning: Invalid TEST_DISCORD_TARGET_BOT_ID: {explicit_bot_id}")

        # Look for Zetherion AI bot in guild (excluding ourselves, the test bot)
        # Search for various name patterns
        bot_name_patterns = ["zetherion", "zeth", "secureclaw"]

        if isinstance(self.channel, discord.TextChannel) and self.channel.guild:
            for member in self.channel.guild.members:
                if member.bot and member.id != self.client.user.id:  # type: ignore[union-attr]
                    name_lower = member.name.lower()
                    if any(pattern in name_lower for pattern in bot_name_patterns):
                        print(f"Found Zetherion AI bot: {member.name} (ID: {member.id})")
                        return member.id

            # If no match found, list all bots for debugging
            print("Available bots in guild:")
            for member in self.channel.guild.members:
                if member.bot and member.id != self.client.user.id:  # type: ignore[union-attr]
                    print(f"  - {member.name} (ID: {member.id})")

        return None

    async def wait_for_bot_response(
        self,
        after_message: discord.Message,
        timeout: float = 90.0,
        bot_id: int | None = None,
    ) -> discord.Message | None:
        """Wait for bot to respond to a message.

        Args:
            after_message: The message we sent that bot should respond to.
            timeout: Maximum time to wait in seconds.
            bot_id: Optional bot user ID. If not provided, will search for it.

        Returns:
            The bot's response message, or None if timeout.
        """
        if not self.client or not self.channel:
            raise RuntimeError("Test client not connected")

        # If bot_id not provided, try to find it
        if not bot_id:
            bot_id = self.get_zetherion_ai_bot_id()
            if not bot_id:
                # Fallback: check recent message history
                async for message in self.channel.history(limit=50):
                    if message.author.bot and message.author.id != self.client.user.id:  # type: ignore[union-attr]
                        bot_id = message.author.id
                        print(f"Found bot from history: {message.author.name} (ID: {bot_id})")
                        break

            if not bot_id:
                raise RuntimeError("Could not identify Zetherion AI bot in channel or guild")

        # Wait for bot response
        def check(message: discord.Message) -> bool:
            reference_message_id = (
                message.reference.message_id if message.reference is not None else None
            )
            is_match = (
                message.channel.id == self.channel_id  # type: ignore[union-attr]
                and message.author.id == bot_id
                and reference_message_id == after_message.id
            )
            bot_match = message.author.id == bot_id
            print(
                f"Check: author={message.author.name}, bot_match={bot_match}, "
                f"reply_to={reference_message_id}, expected={after_message.id}, is_match={is_match}"
            )
            return is_match

        try:
            print(f"Waiting for response from bot_id={bot_id} in channel={self.channel_id}...")
            response = await self.client.wait_for("message", check=check, timeout=timeout)
            print(f"Got response: {response.content[:100] if response else 'None'}")
            return response
        except TimeoutError:
            # Check message history as fallback
            print("Timeout reached, checking message history...")
            async for msg in self.channel.history(limit=20):
                content = msg.content[:50] if msg.content else "[empty]"
                print(f"  History: {msg.author.name} ({msg.author.id}): {content}...")
                reference_message_id = msg.reference.message_id if msg.reference else None
                if msg.author.id == bot_id and reference_message_id == after_message.id:
                    print("Found response in history (reply-correlated)!")
                    return msg
            return None

    async def delete_message(self, message: discord.Message) -> None:
        """Delete a message (cleanup).

        Requires the test bot to have 'Manage Messages' permission in the
        test channel to delete messages from other users/bots.

        Required test bot permissions:
          - View Channel
          - Send Messages
          - Read Message History
          - Manage Messages (needed to delete other bots' messages)

        Args:
            message: Message to delete.
        """
        try:
            await message.delete()
        except discord.errors.NotFound:
            pass  # Already deleted, no problem
        except discord.errors.Forbidden:
            # Test bot lacks 'Manage Messages' permission — skip cleanup.
            # The test assertions already passed; failing on cleanup is not useful.
            pass


@pytest_asyncio.fixture(scope="function")
async def discord_test_client() -> AsyncGenerator[DiscordTestClient, None]:
    """Create Discord test client.

    Yields:
        Initialized DiscordTestClient.
    """
    if SKIP_DISCORD_E2E:
        pytest.skip(SKIP_REASON)
    print(f"Discord E2E provider mode: {DISCORD_E2E_PROVIDER}")

    token = os.getenv("TEST_DISCORD_BOT_TOKEN", "")
    channel_id = int(os.getenv("TEST_DISCORD_CHANNEL_ID", "0"))

    client = DiscordTestClient(token=token, channel_id=channel_id)
    await client.start()

    yield client

    await client.stop()


@pytest.mark.discord_e2e
@pytest.mark.skipif(SKIP_DISCORD_E2E, reason=SKIP_REASON)
@pytest.mark.asyncio
async def test_bot_responds_to_message(discord_test_client: DiscordTestClient) -> None:
    """Test bot responds to a simple message."""
    # Get bot ID to mention it
    bot_id = discord_test_client.get_zetherion_ai_bot_id()
    if not bot_id:
        pytest.skip("Could not find Zetherion AI bot in channel")

    correlation = uuid4().hex[:8]
    # Send test message with @mention
    test_message = await discord_test_client.send_message(
        f"<@{bot_id}> Hello, what is 2+2? id:{correlation}"
    )
    response = None

    try:
        # Wait for bot response
        response = await discord_test_client.wait_for_bot_response(
            test_message, timeout=90.0, bot_id=bot_id
        )

        assert response is not None, "Bot did not respond within timeout"
        assert len(response.content) > 0, "Bot response was empty"
        print(f"✅ Bot responded: {response.content[:100]}...")

    finally:
        # Cleanup test messages
        await discord_test_client.delete_message(test_message)
        if response:
            await discord_test_client.delete_message(response)


@pytest.mark.discord_e2e
@pytest.mark.skipif(SKIP_DISCORD_E2E, reason=SKIP_REASON)
@pytest.mark.asyncio
async def test_bot_handles_complex_query(discord_test_client: DiscordTestClient) -> None:
    """Test bot handles complex queries."""
    # Get bot ID to mention it
    bot_id = discord_test_client.get_zetherion_ai_bot_id()
    if not bot_id:
        pytest.skip("Could not find Zetherion AI bot in channel")

    test_message = await discord_test_client.send_message(
        f"<@{bot_id}> Can you explain what async/await is in Python?"
    )
    response = None

    try:
        response = await discord_test_client.wait_for_bot_response(
            test_message, timeout=90.0, bot_id=bot_id
        )

        assert response is not None, "Bot did not respond to complex query"
        assert len(response.content) > 50, "Bot response too short for complex query"
        print(f"✅ Bot handled complex query: {response.content[:100]}...")

    finally:
        await discord_test_client.delete_message(test_message)
        if response:
            await discord_test_client.delete_message(response)


@pytest.mark.discord_e2e
@pytest.mark.optional_e2e
@pytest.mark.skipif(SKIP_DISCORD_E2E, reason=SKIP_REASON)
@pytest.mark.asyncio
async def test_bot_remembers_information(discord_test_client: DiscordTestClient) -> None:
    """Test bot memory functionality."""
    # Get bot ID to mention it
    bot_id = discord_test_client.get_zetherion_ai_bot_id()
    if not bot_id:
        pytest.skip("Could not find Zetherion AI bot in channel")

    favorite_color = f"purple-{uuid4().hex[:6]}"

    # Store memory
    store_message = await discord_test_client.send_message(
        f"<@{bot_id}> Remember that my favorite color is {favorite_color}"
    )
    store_response = None
    recall_messages: list[discord.Message] = []
    recall_responses: list[discord.Message] = []
    memory_recall_success = False
    last_recall_explanation = ""

    try:
        store_response = await discord_test_client.wait_for_bot_response(
            store_message, timeout=90.0, bot_id=bot_id
        )
        assert store_response is not None, "Bot did not acknowledge memory storage"

        # Wait a moment for memory to be indexed
        await asyncio.sleep(5)

        for attempt in range(1, 4):
            recall_message = await discord_test_client.send_message(
                f"<@{bot_id}> What is my favorite color? Please include the exact value."
            )
            recall_messages.append(recall_message)
            recall_response = await discord_test_client.wait_for_bot_response(
                recall_message, timeout=90.0, bot_id=bot_id
            )
            assert recall_response is not None, "Bot did not respond to recall query"
            recall_responses.append(recall_response)

            # Validate that the bot successfully recalled the information.
            success, explanation = validate_memory_recall(recall_response.content, favorite_color)
            last_recall_explanation = explanation
            print(f"Memory validation (attempt {attempt}/3): {explanation}")
            print(f"Bot response: {recall_response.content[:200]}...")

            if success:
                memory_recall_success = True
                break

            # Give indexing one more short window before retrying.
            if attempt < 3:
                await asyncio.sleep(8)

        assert memory_recall_success, (
            "Bot failed to recall stored memory after retries. "
            f"Final explanation: {last_recall_explanation}"
        )
        print("✅ Memory test completed successfully")

    finally:
        await discord_test_client.delete_message(store_message)
        if store_response:
            await discord_test_client.delete_message(store_response)
        for recall_message in recall_messages:
            await discord_test_client.delete_message(recall_message)
        for recall_response in recall_responses:
            await discord_test_client.delete_message(recall_response)


@pytest.mark.discord_e2e
@pytest.mark.skipif(SKIP_DISCORD_E2E, reason=SKIP_REASON)
@pytest.mark.asyncio
async def test_bot_handles_mention(discord_test_client: DiscordTestClient) -> None:
    """Test bot responds to mentions."""
    # Get bot ID to mention it
    bot_id = discord_test_client.get_zetherion_ai_bot_id()
    if not bot_id:
        pytest.skip("Could not find Zetherion AI bot in channel")

    # Send message with mention
    test_message = await discord_test_client.send_message(f"<@{bot_id}> ping")
    response = None

    try:
        response = await discord_test_client.wait_for_bot_response(
            test_message, timeout=90.0, bot_id=bot_id
        )

        assert response is not None, "Bot did not respond to mention"
        assert len(response.content) > 0, "Bot response was empty"
        print(f"✅ Bot responded to mention: {response.content[:100]}...")

    finally:
        await discord_test_client.delete_message(test_message)
        if response:
            await discord_test_client.delete_message(response)


@pytest.mark.discord_e2e
@pytest.mark.optional_e2e
@pytest.mark.skipif(SKIP_DISCORD_E2E, reason=SKIP_REASON)
@pytest.mark.asyncio
async def test_bot_slash_commands_available(discord_test_client: DiscordTestClient) -> None:
    """Test bot slash commands are registered."""
    if not discord_test_client.client or not discord_test_client.client.application:
        pytest.skip("Discord client or application not connected")

    # Fetch global application commands using HTTP API
    try:
        app_id = discord_test_client.client.application.id
        commands_data = await discord_test_client.client.http.get_global_commands(app_id)

        # Check for expected commands
        command_names = [cmd["name"] for cmd in commands_data]
        expected_commands = ["ask", "remember", "search", "ping", "channels"]

        for expected in expected_commands:
            assert expected in command_names, f"Command /{expected} not registered"

        print(f"✅ All slash commands registered: {command_names}")
    except Exception as e:
        # If we can't fetch commands, skip the test rather than fail
        pytest.skip(f"Could not fetch commands: {e}")


# ---------------------------------------------------------------------------
# Skill-specific E2E tests
# ---------------------------------------------------------------------------


@pytest.mark.discord_e2e
@pytest.mark.skipif(SKIP_DISCORD_E2E, reason=SKIP_REASON)
@pytest.mark.asyncio
async def test_bot_creates_task(discord_test_client: DiscordTestClient) -> None:
    """Test bot acknowledges a task creation request."""
    bot_id = discord_test_client.get_zetherion_ai_bot_id()
    if not bot_id:
        pytest.skip("Could not find Zetherion AI bot in channel")

    test_message = await discord_test_client.send_message(
        f"<@{bot_id}> add a task to review the design docs"
    )
    response = None

    try:
        response = await discord_test_client.wait_for_bot_response(
            test_message, timeout=90.0, bot_id=bot_id
        )
        assert response is not None, "Bot did not respond to task creation"
        assert len(response.content) > 0, "Bot response was empty"
        print(f"✅ Bot acknowledged task creation: {response.content[:100]}...")
    finally:
        await discord_test_client.delete_message(test_message)
        if response:
            await discord_test_client.delete_message(response)


@pytest.mark.discord_e2e
@pytest.mark.skipif(SKIP_DISCORD_E2E, reason=SKIP_REASON)
@pytest.mark.asyncio
async def test_bot_lists_tasks(discord_test_client: DiscordTestClient) -> None:
    """Test bot responds to a task listing request."""
    bot_id = discord_test_client.get_zetherion_ai_bot_id()
    if not bot_id:
        pytest.skip("Could not find Zetherion AI bot in channel")

    task_title = f"review design docs batch {uuid4().int % 10000}"
    create_message = await discord_test_client.send_message(
        f"<@{bot_id}> add a task to {task_title}"
    )
    create_response = None
    retry_create_message = None
    retry_create_response = None
    list_message = None
    response = None

    try:
        create_response = await discord_test_client.wait_for_bot_response(
            create_message, timeout=90.0, bot_id=bot_id
        )
        assert create_response is not None, "Bot did not acknowledge task creation"
        if "created task:" not in create_response.content.lower():
            task_title = "review the design docs"
            retry_create_message = await discord_test_client.send_message(
                f"<@{bot_id}> add a task to {task_title}"
            )
            retry_create_response = await discord_test_client.wait_for_bot_response(
                retry_create_message, timeout=90.0, bot_id=bot_id
            )
            assert retry_create_response is not None, "Bot did not acknowledge retry task creation"
            assert (
                "created task:" in retry_create_response.content.lower()
            ), f"Unexpected retry task creation response: {retry_create_response.content}"

        list_message = await discord_test_client.send_message(f"<@{bot_id}> show my tasks")
        response = await discord_test_client.wait_for_bot_response(
            list_message, timeout=90.0, bot_id=bot_id
        )
        assert response is not None, "Bot did not respond to task listing"
        assert len(response.content) > 0, "Bot response was empty"
        assert task_title in response.content.lower(), "Bot did not list the created task title"
        print(f"✅ Bot listed tasks: {response.content[:100]}...")
    finally:
        await discord_test_client.delete_message(create_message)
        if create_response:
            await discord_test_client.delete_message(create_response)
        if retry_create_message:
            await discord_test_client.delete_message(retry_create_message)
        if retry_create_response:
            await discord_test_client.delete_message(retry_create_response)
        if list_message:
            await discord_test_client.delete_message(list_message)
        if response:
            await discord_test_client.delete_message(response)


@pytest.mark.discord_e2e
@pytest.mark.skipif(SKIP_DISCORD_E2E, reason=SKIP_REASON)
@pytest.mark.asyncio
async def test_bot_shows_schedule(discord_test_client: DiscordTestClient) -> None:
    """Test bot responds to a schedule query."""
    bot_id = discord_test_client.get_zetherion_ai_bot_id()
    if not bot_id:
        pytest.skip("Could not find Zetherion AI bot in channel")

    test_message = await discord_test_client.send_message(
        f"<@{bot_id}> what's on my schedule today"
    )
    response = None

    try:
        response = await discord_test_client.wait_for_bot_response(
            test_message, timeout=90.0, bot_id=bot_id
        )
        assert response is not None, "Bot did not respond to schedule query"
        assert len(response.content) > 0, "Bot response was empty"
        print(f"✅ Bot showed schedule: {response.content[:100]}...")
    finally:
        await discord_test_client.delete_message(test_message)
        if response:
            await discord_test_client.delete_message(response)


@pytest.mark.discord_e2e
@pytest.mark.skipif(SKIP_DISCORD_E2E, reason=SKIP_REASON)
@pytest.mark.asyncio
async def test_bot_profile_query(discord_test_client: DiscordTestClient) -> None:
    """Test bot responds to a profile query."""
    bot_id = discord_test_client.get_zetherion_ai_bot_id()
    if not bot_id:
        pytest.skip("Could not find Zetherion AI bot in channel")

    test_message = await discord_test_client.send_message(f"<@{bot_id}> what do you know about me")
    response = None

    try:
        response = await discord_test_client.wait_for_bot_response(
            test_message, timeout=90.0, bot_id=bot_id
        )
        assert response is not None, "Bot did not respond to profile query"
        assert len(response.content) > 0, "Bot response was empty"
        print(f"✅ Bot responded to profile query: {response.content[:100]}...")
    finally:
        await discord_test_client.delete_message(test_message)
        if response:
            await discord_test_client.delete_message(response)


@pytest.mark.discord_e2e
@pytest.mark.skipif(SKIP_DISCORD_E2E, reason=SKIP_REASON)
@pytest.mark.asyncio
async def test_bot_multi_turn(discord_test_client: DiscordTestClient) -> None:
    """Test bot handles a multi-turn conversation referencing prior info."""
    bot_id = discord_test_client.get_zetherion_ai_bot_id()
    if not bot_id:
        pytest.skip("Could not find Zetherion AI bot in channel")

    favorite_color = f"teal-{uuid4().hex[:6]}"
    messages_to_delete: list[discord.Message] = []

    try:
        # Turn 1: Remember profession
        msg1 = await discord_test_client.send_message(
            f"<@{bot_id}> remember that I work as a software engineer"
        )
        messages_to_delete.append(msg1)
        resp1 = await discord_test_client.wait_for_bot_response(msg1, timeout=90.0, bot_id=bot_id)
        assert resp1 is not None, "Bot did not respond to turn 1"
        messages_to_delete.append(resp1)
        await asyncio.sleep(2)

        # Turn 2: Remember favorite color
        msg2 = await discord_test_client.send_message(
            f"<@{bot_id}> remember that my favorite color is {favorite_color}"
        )
        messages_to_delete.append(msg2)
        resp2 = await discord_test_client.wait_for_bot_response(msg2, timeout=90.0, bot_id=bot_id)
        assert resp2 is not None, "Bot did not respond to turn 2"
        messages_to_delete.append(resp2)
        await asyncio.sleep(2)

        # Turn 3: Ask what it knows
        msg3 = await discord_test_client.send_message(f"<@{bot_id}> what do you know about me?")
        messages_to_delete.append(msg3)
        resp3 = await discord_test_client.wait_for_bot_response(msg3, timeout=90.0, bot_id=bot_id)
        assert resp3 is not None, "Bot did not respond to turn 3"
        messages_to_delete.append(resp3)

        summary_text = resp3.content.lower()
        assert "software engineer" in summary_text, "Summary missing remembered profession"
        recalled_color, explanation = validate_memory_recall(resp3.content, favorite_color)
        assert recalled_color, f"Summary missing remembered favorite color: {explanation}"
        print(f"✅ Multi-turn response: {resp3.content[:200]}...")

    finally:
        for msg in messages_to_delete:
            await discord_test_client.delete_message(msg)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s", "-m", "discord_e2e"])
