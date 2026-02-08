"""Pytest fixtures for Zetherion AI tests."""

import os
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest


@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    """Set up test environment variables before any imports.

    This fixture runs automatically before any tests and ensures that
    Settings can be imported without validation errors.
    """
    # Set minimal required environment variables for Settings
    os.environ.setdefault("DISCORD_TOKEN", "test-discord-token-placeholder")
    os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key-placeholder")
    # Encryption passphrase is mandatory (no toggle — hard fail without it)
    os.environ.setdefault("ENCRYPTION_PASSPHRASE", "test-encryption-passphrase-for-unit-tests")
    # Disable InferenceBroker by default to avoid network calls in tests
    os.environ.setdefault("INFERENCE_BROKER_ENABLED", "false")

    # Clear the settings cache to ensure tests start fresh
    from zetherion_ai.config import get_settings

    get_settings.cache_clear()

    yield

    # Cleanup after all tests
    get_settings.cache_clear()


@pytest.fixture
def mock_settings():
    """Mock settings for testing."""
    from zetherion_ai.config import Settings

    settings = Settings(
        discord_token="test-discord-token",
        gemini_api_key="test-gemini-key",
        anthropic_api_key="test-anthropic-key",
        openai_api_key="test-openai-key",
        allowed_user_ids=[123, 456],
        qdrant_host="localhost",
        qdrant_port=6333,
        environment="test",
        log_level="DEBUG",
        # Encryption settings (mandatory — no toggle)
        encryption_passphrase="test-encryption-passphrase-for-mock",
    )
    return settings


@pytest.fixture
def mock_qdrant_client():
    """Mock AsyncQdrantClient for testing."""
    client = AsyncMock()

    # Mock get_collections
    client.get_collections = AsyncMock(return_value=MagicMock(collections=[]))

    # Mock create_collection
    client.create_collection = AsyncMock()

    # Mock upsert
    client.upsert = AsyncMock()

    # Mock search
    client.search = AsyncMock(return_value=[])

    # Mock scroll
    client.scroll = AsyncMock(return_value=([], None))

    return client


@pytest.fixture
def mock_embeddings_client():
    """Mock Gemini embeddings client for testing."""
    client = Mock()
    client.models.embed_content = Mock(return_value=Mock(embeddings=[Mock(values=[0.1] * 768)]))
    return client


@pytest.fixture
def mock_gemini_client():
    """Mock Gemini client for routing/generation."""
    client = Mock()

    # Mock for routing
    client.models.generate_content = Mock(
        return_value=Mock(
            text='{"intent": "simple_query", "confidence": 0.9, "reasoning": "greeting"}'
        )
    )

    return client


@pytest.fixture
def mock_claude_client():
    """Mock Claude client for testing."""
    client = AsyncMock()

    response = Mock()
    response.content = [Mock(text="Test response from Claude")]
    response.usage = Mock(input_tokens=100, output_tokens=50)

    client.messages.create = AsyncMock(return_value=response)

    return client


@pytest.fixture
def mock_openai_client():
    """Mock OpenAI client for testing."""
    client = AsyncMock()

    response = Mock()
    response.choices = [Mock(message=Mock(content="Test response from OpenAI"))]

    client.chat.completions.create = AsyncMock(return_value=response)

    return client


@pytest.fixture
def mock_discord_message():
    """Mock Discord message for testing."""
    message = AsyncMock()
    message.author = Mock(id=123, bot=False)
    message.content = "Test message"
    message.channel = Mock(id=456)
    message.mentions = []
    message.reply = AsyncMock()
    return message


@pytest.fixture
def mock_discord_interaction():
    """Mock Discord interaction for testing."""
    interaction = AsyncMock()
    interaction.user = Mock(id=123)
    interaction.channel = Mock(id=456)
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


@pytest.fixture(autouse=True)
def clear_settings_cache():
    """Clear settings cache before each test."""
    from zetherion_ai.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def sample_vector():
    """Sample embedding vector for testing."""
    return [0.1] * 768


@pytest.fixture
def sample_conversation_messages():
    """Sample conversation messages for testing."""
    return [
        {
            "id": "msg-1",
            "role": "user",
            "content": "Hello",
            "timestamp": "2026-02-05T10:00:00",
            "user_id": 123,
            "channel_id": 456,
        },
        {
            "id": "msg-2",
            "role": "assistant",
            "content": "Hi there!",
            "timestamp": "2026-02-05T10:00:01",
            "user_id": 123,
            "channel_id": 456,
        },
    ]


@pytest.fixture
def sample_memories():
    """Sample long-term memories for testing."""
    return [
        {
            "id": "mem-1",
            "content": "User prefers dark mode",
            "type": "preference",
            "score": 0.95,
            "timestamp": "2026-02-04T12:00:00",
        },
        {
            "id": "mem-2",
            "content": "User is a Python developer",
            "type": "fact",
            "score": 0.88,
            "timestamp": "2026-02-03T15:30:00",
        },
    ]
