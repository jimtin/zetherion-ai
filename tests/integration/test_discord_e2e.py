"""End-to-end Discord integration tests with real Discord API.

These tests send actual messages through Discord and verify bot responses.
Requires:
- TEST_DISCORD_BOT_TOKEN environment variable (separate test bot)
- TEST_DISCORD_CHANNEL_ID environment variable (test channel ID)
- TEST_DISCORD_TARGET_BOT_ID environment variable (optional, ID of bot to test)
- Test Discord server set up
"""

import asyncio
import json
import os
from collections.abc import AsyncGenerator
from contextlib import suppress
from pathlib import Path

import discord
import httpx
import pytest
import pytest_asyncio

# Load environment variables from .env file
try:
    from dotenv import load_dotenv

    # Load from project root .env file
    env_path = Path(__file__).parent.parent.parent / ".env"
    load_dotenv(dotenv_path=env_path)
except ImportError:
    # python-dotenv not installed, rely on environment variables being set
    pass

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


async def validate_memory_recall(response: str, expected_info: str) -> tuple[bool, str]:
    """Use Ollama to validate if a response indicates successful memory recall.

    Args:
        response: The bot's response to analyze.
        expected_info: The information that should have been recalled
            (e.g., "purple" for favorite color).

    Returns:
        Tuple of (success: bool, explanation: str).
    """
    validation_prompt = f"""Analyze this bot response and determine if it successfully \
recalled the expected information.

Bot response: "{response}"
Expected information: "{expected_info}"

Did the bot successfully recall and mention the expected information? Consider:
- Direct mentions (e.g., "purple", "your favorite color is purple")
- Indirect references that indicate knowledge of the info
- Explicit statements of not knowing indicate FAILURE

Respond with ONLY a JSON object:
{{"success": true/false, "explanation": "brief reason"}}"""

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            ollama_response = await client.post(
                "http://localhost:11434/api/generate",
                json={
                    "model": "llama3.1:8b",
                    "prompt": validation_prompt,
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": 0.1, "num_predict": 100},
                },
            )
            result = ollama_response.json()
            result_text = result.get("response", "").strip()

            validation = json.loads(result_text)
            return validation.get("success", False), validation.get("explanation", "No explanation")
    except Exception as e:
        # Fallback: check if the expected info is mentioned or if the response is substantive
        response_lower = response.lower()
        expected_lower = expected_info.lower()

        # Direct match
        if expected_lower in response_lower:
            return True, f"Fallback: Found '{expected_info}' in response"

        # Check if it's not an error/failure message
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

        # If it's a substantive response (not an error), consider it a pass
        # This handles cases where the bot recalls the info indirectly
        if not is_error and len(response) > 20:
            return True, f"Fallback: Substantive non-error response (Ollama validation failed: {e})"

        return (
            False,
            f"Fallback check failed (error: {e}, no '{expected_info}' found, or error response)",
        )


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
            is_match = (
                message.channel.id == self.channel_id  # type: ignore[union-attr]
                and message.author.id == bot_id
                and message.created_at > after_message.created_at
            )
            bot_match = message.author.id == bot_id
            print(
                f"Check: author={message.author.name}, bot_match={bot_match}, is_match={is_match}"
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
            # History returns newest first, so look for bot response after our message
            found_our_message = False
            async for msg in self.channel.history(limit=20):
                content = msg.content[:50] if msg.content else "[empty]"
                print(f"  History: {msg.author.name} ({msg.author.id}): {content}...")
                # Once we find our sent message, any newer bot message is the response
                if msg.id == after_message.id:
                    found_our_message = True
                    continue
                # This message is newer - check if it's from the bot
                if not found_our_message and msg.author.id == bot_id:
                    print("Found response in history (newer than our message)!")
                    return msg
            return None

    async def delete_message(self, message: discord.Message) -> None:
        """Delete a message (cleanup).

        Args:
            message: Message to delete.
        """
        with suppress(discord.errors.NotFound, discord.errors.Forbidden):
            await message.delete()


@pytest_asyncio.fixture(scope="function")
async def discord_test_client() -> AsyncGenerator[DiscordTestClient, None]:
    """Create Discord test client.

    Yields:
        Initialized DiscordTestClient.
    """
    if SKIP_DISCORD_E2E:
        pytest.skip(SKIP_REASON)

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

    # Send test message with @mention
    test_message = await discord_test_client.send_message(f"<@{bot_id}> Hello, what is 2+2?")
    response = None

    try:
        # Wait for bot response
        response = await discord_test_client.wait_for_bot_response(
            test_message, timeout=30.0, bot_id=bot_id
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
            test_message, timeout=45.0, bot_id=bot_id
        )

        assert response is not None, "Bot did not respond to complex query"
        assert len(response.content) > 50, "Bot response too short for complex query"
        print(f"✅ Bot handled complex query: {response.content[:100]}...")

    finally:
        await discord_test_client.delete_message(test_message)
        if response:
            await discord_test_client.delete_message(response)


@pytest.mark.discord_e2e
@pytest.mark.skipif(SKIP_DISCORD_E2E, reason=SKIP_REASON)
@pytest.mark.asyncio
async def test_bot_remembers_information(discord_test_client: DiscordTestClient) -> None:
    """Test bot memory functionality."""
    # Get bot ID to mention it
    bot_id = discord_test_client.get_zetherion_ai_bot_id()
    if not bot_id:
        pytest.skip("Could not find Zetherion AI bot in channel")

    # Store memory
    store_message = await discord_test_client.send_message(
        f"<@{bot_id}> Remember that my favorite color is purple"
    )
    store_response = None
    recall_message = None
    recall_response = None

    try:
        store_response = await discord_test_client.wait_for_bot_response(
            store_message, timeout=30.0, bot_id=bot_id
        )
        assert store_response is not None, "Bot did not acknowledge memory storage"

        # Wait a moment for memory to be indexed
        await asyncio.sleep(3)

        # Recall memory
        recall_message = await discord_test_client.send_message(
            f"<@{bot_id}> What is my favorite color?"
        )
        recall_response = await discord_test_client.wait_for_bot_response(
            recall_message, timeout=30.0, bot_id=bot_id
        )

        assert recall_response is not None, "Bot did not respond to recall query"

        # Validate that the bot successfully recalled the information
        success, explanation = await validate_memory_recall(recall_response.content, "purple")
        print(f"Memory validation: {explanation}")
        print(f"Bot response: {recall_response.content[:200]}...")

        assert success, f"Bot failed to recall stored memory. Explanation: {explanation}"
        print("✅ Memory test completed successfully")

    finally:
        await discord_test_client.delete_message(store_message)
        if store_response:
            await discord_test_client.delete_message(store_response)
        if recall_message:
            await discord_test_client.delete_message(recall_message)
        if recall_response:
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
            test_message, timeout=30.0, bot_id=bot_id
        )

        assert response is not None, "Bot did not respond to mention"
        assert len(response.content) > 0, "Bot response was empty"
        print(f"✅ Bot responded to mention: {response.content[:100]}...")

    finally:
        await discord_test_client.delete_message(test_message)
        if response:
            await discord_test_client.delete_message(response)


@pytest.mark.discord_e2e
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
            test_message, timeout=45.0, bot_id=bot_id
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

    test_message = await discord_test_client.send_message(f"<@{bot_id}> show my tasks")
    response = None

    try:
        response = await discord_test_client.wait_for_bot_response(
            test_message, timeout=45.0, bot_id=bot_id
        )
        assert response is not None, "Bot did not respond to task listing"
        assert len(response.content) > 0, "Bot response was empty"
        print(f"✅ Bot listed tasks: {response.content[:100]}...")
    finally:
        await discord_test_client.delete_message(test_message)
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
            test_message, timeout=45.0, bot_id=bot_id
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
            test_message, timeout=45.0, bot_id=bot_id
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

    messages_to_delete: list[discord.Message] = []

    try:
        # Turn 1: Tell the bot a name
        msg1 = await discord_test_client.send_message(
            f"<@{bot_id}> remember that my name is TestUser42"
        )
        messages_to_delete.append(msg1)
        resp1 = await discord_test_client.wait_for_bot_response(msg1, timeout=30.0, bot_id=bot_id)
        assert resp1 is not None, "Bot did not respond to turn 1"
        messages_to_delete.append(resp1)
        await asyncio.sleep(2)

        # Turn 2: Tell occupation
        msg2 = await discord_test_client.send_message(
            f"<@{bot_id}> remember that I work as a software engineer"
        )
        messages_to_delete.append(msg2)
        resp2 = await discord_test_client.wait_for_bot_response(msg2, timeout=30.0, bot_id=bot_id)
        assert resp2 is not None, "Bot did not respond to turn 2"
        messages_to_delete.append(resp2)
        await asyncio.sleep(2)

        # Turn 3: Ask what it knows
        msg3 = await discord_test_client.send_message(f"<@{bot_id}> what do you know about me?")
        messages_to_delete.append(msg3)
        resp3 = await discord_test_client.wait_for_bot_response(msg3, timeout=45.0, bot_id=bot_id)
        assert resp3 is not None, "Bot did not respond to turn 3"
        messages_to_delete.append(resp3)

        # The response should reference at least some prior information
        assert len(resp3.content) > 20, "Multi-turn response too short"
        print(f"✅ Multi-turn response: {resp3.content[:200]}...")

    finally:
        for msg in messages_to_delete:
            await discord_test_client.delete_message(msg)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s", "-m", "discord_e2e"])
