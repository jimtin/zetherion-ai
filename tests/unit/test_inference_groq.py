"""Unit tests for Groq provider integration in InferenceBroker."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zetherion_ai.agent.inference import InferenceBroker, InferenceResult
from zetherion_ai.agent.providers import Provider, TaskType


def _mock_settings(**overrides: object) -> MagicMock:
    """Build a Settings mock with Groq API key configured."""
    s = MagicMock()
    s.anthropic_api_key = None
    s.openai_api_key = None
    s.gemini_api_key = None
    s.groq_api_key = MagicMock()
    s.groq_api_key.get_secret_value.return_value = "gsk_test_key"
    s.groq_model = "llama-3.3-70b-versatile"
    s.groq_base_url = "https://api.groq.com/openai/v1"
    s.claude_model = "claude-sonnet-4-5"
    s.openai_model = "gpt-5.2"
    s.router_model = "gemini-2.5-flash"
    s.ollama_generation_model = "llama3.1:8b"
    s.ollama_url = "http://ollama:11434"
    s.ollama_router_url = "http://ollama-router:11434"
    s.ollama_timeout = 30
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


@patch("zetherion_ai.agent.inference.get_settings")
@patch("zetherion_ai.agent.inference.openai.AsyncOpenAI")
def test_groq_client_initialized_with_correct_base_url(
    mock_openai_cls: MagicMock,
    mock_get_settings: MagicMock,
) -> None:
    """Groq client uses OpenAI SDK with Groq base_url."""
    mock_get_settings.return_value = _mock_settings()
    mock_client_instance = MagicMock()
    mock_openai_cls.return_value = mock_client_instance

    broker = InferenceBroker()

    # Should have been called for Groq client
    mock_openai_cls.assert_called_with(
        api_key="gsk_test_key",
        base_url="https://api.groq.com/openai/v1",
    )
    assert Provider.GROQ in broker.available_providers


@patch("zetherion_ai.agent.inference.get_settings")
@patch("zetherion_ai.agent.inference.openai.AsyncOpenAI")
def test_groq_client_uses_configured_base_url(
    mock_openai_cls: MagicMock,
    mock_get_settings: MagicMock,
) -> None:
    """Groq client honors custom base URL from settings."""
    mock_get_settings.return_value = _mock_settings(groq_base_url="https://groq-proxy.local/v1")
    mock_openai_cls.return_value = MagicMock()

    InferenceBroker()

    mock_openai_cls.assert_called_with(
        api_key="gsk_test_key",
        base_url="https://groq-proxy.local/v1",
    )


@patch("zetherion_ai.agent.inference.get_settings")
def test_groq_not_available_without_api_key(mock_get_settings: MagicMock) -> None:
    """Groq provider is not in available_providers without API key."""
    mock_get_settings.return_value = _mock_settings(groq_api_key=None)
    broker = InferenceBroker()
    assert Provider.GROQ not in broker.available_providers


@patch("zetherion_ai.agent.inference.get_settings")
def test_groq_in_cost_tracker(mock_get_settings: MagicMock) -> None:
    """Groq provider has a cost tracker entry."""
    mock_get_settings.return_value = _mock_settings()
    broker = InferenceBroker()
    assert Provider.GROQ in broker._cost_tracker


@pytest.mark.asyncio
@patch("zetherion_ai.agent.inference.get_dynamic", return_value="llama-3.3-70b-versatile")
@patch("zetherion_ai.agent.inference.get_settings")
async def test_call_groq_returns_inference_result(
    mock_get_settings: MagicMock,
    mock_get_dynamic: MagicMock,
) -> None:
    """_call_groq makes an OpenAI-compatible chat completion call."""
    mock_get_settings.return_value = _mock_settings()

    broker = InferenceBroker()
    broker._groq_client = MagicMock()

    # Mock the Groq client's create method
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = '{"category": "work_client"}'
    mock_response.usage.prompt_tokens = 100
    mock_response.usage.completion_tokens = 50
    mock_create = AsyncMock(return_value=mock_response)
    broker._groq_client.chat.completions.create = mock_create

    result = await broker._call_groq(
        prompt="Classify this email",
        task_type=TaskType.CLASSIFICATION,
        system_prompt="You are an email classifier",
        messages=None,
        max_tokens=500,
        temperature=0.1,
    )

    assert isinstance(result, InferenceResult)
    assert result.provider == Provider.GROQ
    assert result.model == "llama-3.3-70b-versatile"
    assert result.content == '{"category": "work_client"}'
    assert result.input_tokens == 100
    assert result.output_tokens == 50

    # Verify the API call
    mock_create.assert_awaited_once()
    call_kwargs = mock_create.await_args.kwargs
    assert call_kwargs["model"] == "llama-3.3-70b-versatile"
    assert call_kwargs["temperature"] == 0.1
    assert call_kwargs["max_tokens"] == 500


@pytest.mark.asyncio
@patch("zetherion_ai.agent.inference.get_dynamic", return_value="llama-3.3-70b-versatile")
@patch("zetherion_ai.agent.inference.get_settings")
async def test_call_groq_includes_system_prompt(
    mock_get_settings: MagicMock,
    mock_get_dynamic: MagicMock,
) -> None:
    """_call_groq includes system prompt in messages."""
    mock_get_settings.return_value = _mock_settings()
    broker = InferenceBroker()
    broker._groq_client = MagicMock()

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "{}"
    mock_response.usage.prompt_tokens = 50
    mock_response.usage.completion_tokens = 10
    mock_create = AsyncMock(return_value=mock_response)
    broker._groq_client.chat.completions.create = mock_create

    await broker._call_groq(
        prompt="test",
        task_type=TaskType.CLASSIFICATION,
        system_prompt="Be precise",
        messages=None,
        max_tokens=100,
        temperature=0.1,
    )

    call_kwargs = mock_create.await_args.kwargs
    api_messages = call_kwargs["messages"]
    assert api_messages[0]["role"] == "system"
    assert api_messages[0]["content"] == "Be precise"
    assert api_messages[-1]["role"] == "user"
    assert api_messages[-1]["content"] == "test"


@pytest.mark.asyncio
@patch("zetherion_ai.agent.inference.get_dynamic", return_value="llama-3.3-70b-versatile")
@patch("zetherion_ai.agent.inference.get_settings")
async def test_call_groq_raises_without_client(
    mock_get_settings: MagicMock,
    mock_get_dynamic: MagicMock,
) -> None:
    """_call_groq raises RuntimeError if client not initialized."""
    mock_get_settings.return_value = _mock_settings(groq_api_key=None)
    broker = InferenceBroker()
    broker._groq_client = None

    with pytest.raises(RuntimeError, match="Groq client not initialized"):
        await broker._call_groq(
            prompt="test",
            task_type=TaskType.CLASSIFICATION,
            system_prompt=None,
            messages=None,
            max_tokens=100,
            temperature=0.1,
        )


@patch("zetherion_ai.agent.inference.get_secret")
@patch("zetherion_ai.agent.inference.get_settings")
def test_groq_api_key_hot_reload(
    mock_get_settings: MagicMock,
    mock_get_secret: MagicMock,
) -> None:
    """Groq client reinitializes when API key rotates."""
    mock_get_settings.return_value = _mock_settings()
    broker = InferenceBroker()

    old_client = broker._groq_client

    # Simulate key rotation via get_secret
    mock_get_secret.side_effect = lambda name, *a: (
        "gsk_new_rotated_key" if name == "groq_api_key" else None
    )

    broker._check_api_key_updates()

    assert broker._current_groq_key == "gsk_new_rotated_key"
    assert broker._groq_client is not old_client
    assert Provider.GROQ in broker._available_providers


@pytest.mark.asyncio
@patch("zetherion_ai.agent.inference.get_settings")
async def test_groq_health_check_calls_models_list(
    mock_get_settings: MagicMock,
) -> None:
    """Health check for Groq calls models.list()."""
    mock_get_settings.return_value = _mock_settings()
    broker = InferenceBroker()
    broker._groq_client = MagicMock()
    mock_list = AsyncMock()
    broker._groq_client.models.list = mock_list

    result = await broker.health_check(Provider.GROQ)

    assert result is True
    mock_list.assert_awaited_once()


@pytest.mark.asyncio
@patch("zetherion_ai.agent.inference.get_settings")
async def test_groq_health_check_returns_false_without_client(
    mock_get_settings: MagicMock,
) -> None:
    """Health check returns False when Groq client is not initialized."""
    mock_get_settings.return_value = _mock_settings(groq_api_key=None)
    broker = InferenceBroker()
    broker._groq_client = None

    result = await broker.health_check(Provider.GROQ)

    assert result is False


@pytest.mark.asyncio
@patch("zetherion_ai.agent.inference.get_dynamic", return_value="llama-3.3-70b-versatile")
@patch("zetherion_ai.agent.inference.get_settings")
async def test_stream_groq_yields_chunks(
    mock_get_settings: MagicMock,
    mock_get_dynamic: MagicMock,
) -> None:
    """_stream_groq yields StreamChunks and a final done chunk."""
    mock_get_settings.return_value = _mock_settings()
    broker = InferenceBroker()
    broker._groq_client = MagicMock()

    # Build mock streaming response
    chunk1 = MagicMock()
    chunk1.choices = [MagicMock()]
    chunk1.choices[0].delta.content = "Hello"
    chunk1.usage = None

    chunk2 = MagicMock()
    chunk2.choices = [MagicMock()]
    chunk2.choices[0].delta.content = " world"
    chunk2.usage = None

    # Final chunk with usage
    chunk_final = MagicMock()
    chunk_final.choices = []
    chunk_final.usage = MagicMock()
    chunk_final.usage.prompt_tokens = 10
    chunk_final.usage.completion_tokens = 5

    async def _mock_stream():
        for c in [chunk1, chunk2, chunk_final]:
            yield c

    mock_create = AsyncMock(return_value=_mock_stream())
    broker._groq_client.chat.completions.create = mock_create

    chunks = []
    async for chunk in broker._stream_groq(
        prompt="test",
        task_type=TaskType.CLASSIFICATION,
        system_prompt="sys",
        messages=None,
        max_tokens=100,
        temperature=0.1,
    ):
        chunks.append(chunk)

    # 2 content chunks + 1 final done chunk
    assert len(chunks) == 3
    assert chunks[0].content == "Hello"
    assert chunks[1].content == " world"
    assert chunks[2].done is True
    assert chunks[2].input_tokens == 10
    assert chunks[2].output_tokens == 5


@pytest.mark.asyncio
@patch("zetherion_ai.agent.inference.get_dynamic", return_value="llama-3.3-70b-versatile")
@patch("zetherion_ai.agent.inference.get_settings")
async def test_call_groq_with_messages_list(
    mock_get_settings: MagicMock,
    mock_get_dynamic: MagicMock,
) -> None:
    """_call_groq passes through a messages list correctly."""
    mock_get_settings.return_value = _mock_settings()
    broker = InferenceBroker()
    broker._groq_client = MagicMock()

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = '{"result":"ok"}'
    mock_response.usage.prompt_tokens = 80
    mock_response.usage.completion_tokens = 20
    mock_create = AsyncMock(return_value=mock_response)
    broker._groq_client.chat.completions.create = mock_create

    result = await broker._call_groq(
        prompt="final question",
        task_type=TaskType.CLASSIFICATION,
        system_prompt="system",
        messages=[
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ],
        max_tokens=200,
        temperature=0.2,
    )

    assert result.content == '{"result":"ok"}'

    call_kwargs = mock_create.await_args.kwargs
    api_messages = call_kwargs["messages"]
    # system + 2 conversation + final user prompt
    assert len(api_messages) == 4
    assert api_messages[0]["role"] == "system"
    assert api_messages[1]["role"] == "user"
    assert api_messages[1]["content"] == "hi"
    assert api_messages[2]["role"] == "assistant"
    assert api_messages[3]["role"] == "user"
    assert api_messages[3]["content"] == "final question"


@pytest.mark.asyncio
@patch("zetherion_ai.agent.inference.get_dynamic", return_value="llama-3.3-70b-versatile")
@patch("zetherion_ai.agent.inference.get_settings")
async def test_stream_groq_raises_without_client(
    mock_get_settings: MagicMock,
    mock_get_dynamic: MagicMock,
) -> None:
    """_stream_groq raises RuntimeError if client not initialized."""
    mock_get_settings.return_value = _mock_settings(groq_api_key=None)
    broker = InferenceBroker()
    broker._groq_client = None

    with pytest.raises(RuntimeError, match="Groq client not initialized"):
        async for _ in broker._stream_groq(
            prompt="test",
            task_type=TaskType.CLASSIFICATION,
            system_prompt=None,
            messages=None,
            max_tokens=100,
            temperature=0.1,
        ):
            pass
