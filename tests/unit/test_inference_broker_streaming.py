"""Targeted unit tests for InferenceBroker streaming and fallback paths."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zetherion_ai.agent.inference import InferenceBroker, InferenceResult, StreamChunk
from zetherion_ai.agent.providers import Provider, TaskType


def _secret(value: str) -> MagicMock:
    secret = MagicMock()
    secret.get_secret_value.return_value = value
    return secret


def _settings(
    *,
    anthropic_key: str | None = None,
    openai_key: str | None = None,
    gemini_key: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        anthropic_api_key=_secret(anthropic_key) if anthropic_key else None,
        openai_api_key=_secret(openai_key) if openai_key else None,
        gemini_api_key=_secret(gemini_key) if gemini_key else None,
        claude_model="claude-test",
        openai_model="gpt-test",
        router_model="gemini-test",
        ollama_generation_model="llama3.1:8b",
        ollama_url="http://ollama:11434",
        ollama_timeout=30,
    )


class TestInferenceStreamCoverage:
    """Coverage-focused tests for stream/fallback execution."""

    @pytest.mark.asyncio
    async def test_infer_stream_uses_stream_provider_and_tracks_cost(self) -> None:
        async_httpx = AsyncMock()
        with (
            patch("zetherion_ai.agent.inference.get_settings", return_value=_settings()),
            patch("zetherion_ai.agent.inference.httpx.AsyncClient", return_value=async_httpx),
            patch(
                "zetherion_ai.agent.inference.get_provider_for_task",
                return_value=Provider.OLLAMA,
            ),
        ):
            broker = InferenceBroker()

        async def fake_stream_provider(**_: object) -> AsyncGenerator[StreamChunk, None]:
            yield StreamChunk(content="hello")
            yield StreamChunk(
                content="",
                done=True,
                model="llama3",
                input_tokens=5,
                output_tokens=3,
            )

        with (
            patch.object(broker, "_stream_provider", side_effect=fake_stream_provider),
            patch.object(broker, "_estimate_cost", return_value=(0.25, False)),
            patch.object(broker, "_track_cost") as mock_track,
        ):
            chunks = [chunk async for chunk in broker.infer_stream("hi", TaskType.SIMPLE_QA)]

        assert [c.content for c in chunks[:-1]] == ["hello"]
        assert chunks[-1].done is True
        assert chunks[-1].model == "llama3"
        assert chunks[-1].provider == Provider.OLLAMA.value
        mock_track.assert_called_once()

    @pytest.mark.asyncio
    async def test_infer_stream_falls_back_when_primary_stream_fails(self) -> None:
        async_httpx = AsyncMock()
        with (
            patch("zetherion_ai.agent.inference.get_settings", return_value=_settings()),
            patch("zetherion_ai.agent.inference.httpx.AsyncClient", return_value=async_httpx),
            patch(
                "zetherion_ai.agent.inference.get_provider_for_task",
                return_value=Provider.CLAUDE,
            ),
        ):
            broker = InferenceBroker()

        async def broken_stream_provider(**_: object) -> AsyncGenerator[StreamChunk, None]:
            raise RuntimeError("stream failed")
            if False:  # pragma: no cover
                yield StreamChunk(content="")

        fallback = InferenceResult(
            content="fallback reply",
            provider=Provider.GEMINI,
            task_type=TaskType.CONVERSATION,
            model="gemini-fallback",
            input_tokens=8,
            output_tokens=6,
        )

        with (
            patch.object(broker, "_stream_provider", side_effect=broken_stream_provider),
            patch.object(broker, "_try_fallbacks", new=AsyncMock(return_value=fallback)),
            patch.object(broker, "_estimate_cost", return_value=(0.0, False)),
            patch.object(broker, "_track_cost"),
        ):
            chunks = [chunk async for chunk in broker.infer_stream("hi", TaskType.CONVERSATION)]

        assert [c.content for c in chunks[:-1]] == ["fallback ", "reply "]
        assert chunks[-1].done is True
        assert chunks[-1].model == "gemini-fallback"
        assert chunks[-1].provider == Provider.GEMINI.value

    @pytest.mark.asyncio
    async def test_stream_provider_gemini_uses_simulated_streaming(self) -> None:
        async_httpx = AsyncMock()
        with (
            patch("zetherion_ai.agent.inference.get_settings", return_value=_settings()),
            patch("zetherion_ai.agent.inference.httpx.AsyncClient", return_value=async_httpx),
        ):
            broker = InferenceBroker()

        gemini_result = InferenceResult(
            content="one two",
            provider=Provider.GEMINI,
            task_type=TaskType.SUMMARIZATION,
            model="gemini-test",
            input_tokens=10,
            output_tokens=20,
        )
        broker._call_gemini = AsyncMock(return_value=gemini_result)

        chunks = [
            chunk
            async for chunk in broker._stream_provider(
                provider=Provider.GEMINI,
                prompt="prompt",
                task_type=TaskType.SUMMARIZATION,
                system_prompt=None,
                messages=None,
                max_tokens=64,
                temperature=0.7,
            )
        ]

        assert [c.content for c in chunks[:-1]] == ["one", " two"]
        assert chunks[-1].done is True
        assert chunks[-1].model == "gemini-test"

    @pytest.mark.asyncio
    async def test_stream_provider_unknown_provider_raises(self) -> None:
        async_httpx = AsyncMock()
        with (
            patch("zetherion_ai.agent.inference.get_settings", return_value=_settings()),
            patch("zetherion_ai.agent.inference.httpx.AsyncClient", return_value=async_httpx),
        ):
            broker = InferenceBroker()

        with pytest.raises(ValueError, match="Unknown provider"):
            async for _ in broker._stream_provider(  # type: ignore[arg-type]
                provider="bad-provider",
                prompt="prompt",
                task_type=TaskType.SIMPLE_QA,
                system_prompt=None,
                messages=None,
                max_tokens=64,
                temperature=0.7,
            ):
                pass

    @pytest.mark.asyncio
    async def test_call_provider_unknown_provider_raises(self) -> None:
        async_httpx = AsyncMock()
        with (
            patch("zetherion_ai.agent.inference.get_settings", return_value=_settings()),
            patch("zetherion_ai.agent.inference.httpx.AsyncClient", return_value=async_httpx),
        ):
            broker = InferenceBroker()

        with pytest.raises(ValueError, match="Unknown provider"):
            await broker._call_provider(  # type: ignore[arg-type]
                provider="bad-provider",
                prompt="prompt",
                task_type=TaskType.SIMPLE_QA,
                system_prompt=None,
                messages=None,
                max_tokens=64,
                temperature=0.7,
            )


class TestInferenceProviderBranches:
    """Tests for provider-specific branch behavior."""

    @pytest.mark.asyncio
    async def test_call_claude_raises_when_client_missing(self) -> None:
        with (
            patch("zetherion_ai.agent.inference.get_settings", return_value=_settings()),
            patch("zetherion_ai.agent.inference.httpx.AsyncClient", return_value=AsyncMock()),
        ):
            broker = InferenceBroker()

        with pytest.raises(RuntimeError, match="Claude client not initialized"):
            await broker._call_claude(
                prompt="p",
                task_type=TaskType.CODE_GENERATION,
                system_prompt=None,
                messages=None,
                max_tokens=32,
                temperature=0.7,
            )

    @pytest.mark.asyncio
    async def test_call_openai_raises_when_client_missing(self) -> None:
        with (
            patch("zetherion_ai.agent.inference.get_settings", return_value=_settings()),
            patch("zetherion_ai.agent.inference.httpx.AsyncClient", return_value=AsyncMock()),
        ):
            broker = InferenceBroker()

        with pytest.raises(RuntimeError, match="OpenAI client not initialized"):
            await broker._call_openai(
                prompt="p",
                task_type=TaskType.COMPLEX_REASONING,
                system_prompt=None,
                messages=None,
                max_tokens=32,
                temperature=0.7,
            )

    @pytest.mark.asyncio
    async def test_call_gemini_raises_when_client_missing(self) -> None:
        with (
            patch("zetherion_ai.agent.inference.get_settings", return_value=_settings()),
            patch("zetherion_ai.agent.inference.httpx.AsyncClient", return_value=AsyncMock()),
        ):
            broker = InferenceBroker()

        with pytest.raises(RuntimeError, match="Gemini client not initialized"):
            await broker._call_gemini(
                prompt="p",
                task_type=TaskType.SUMMARIZATION,
                system_prompt="system prompt",
                messages=None,
                max_tokens=32,
                temperature=0.7,
            )

    @pytest.mark.asyncio
    async def test_call_ollama_builds_payload_with_system_and_history(self) -> None:
        with (
            patch("zetherion_ai.agent.inference.get_settings", return_value=_settings()),
            patch("zetherion_ai.agent.inference.httpx.AsyncClient", return_value=AsyncMock()),
        ):
            broker = InferenceBroker()

        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json.return_value = {
            "message": {"content": "ollama says hi"},
            "prompt_eval_count": 11,
            "eval_count": 7,
        }
        broker._httpx_client.post = AsyncMock(return_value=response)

        result = await broker._call_ollama(
            prompt="latest question",
            task_type=TaskType.CONVERSATION,
            system_prompt="be concise",
            messages=[{"role": "assistant", "content": "prior answer"}],
            max_tokens=99,
            temperature=0.1,
        )

        payload = broker._httpx_client.post.call_args.kwargs["json"]
        assert payload["messages"][0] == {"role": "system", "content": "be concise"}
        assert payload["messages"][1] == {"role": "assistant", "content": "prior answer"}
        assert payload["messages"][2] == {"role": "user", "content": "latest question"}
        assert result.content == "ollama says hi"
        assert result.input_tokens == 11
        assert result.output_tokens == 7


class TestInferenceStreamingProviderImplementations:
    """Tests for provider-specific streaming implementations."""

    @pytest.mark.asyncio
    async def test_stream_provider_dispatches_to_each_backend(self) -> None:
        with (
            patch("zetherion_ai.agent.inference.get_settings", return_value=_settings()),
            patch("zetherion_ai.agent.inference.httpx.AsyncClient", return_value=AsyncMock()),
        ):
            broker = InferenceBroker()

        async def _token_stream() -> AsyncGenerator[StreamChunk, None]:
            yield StreamChunk(content="x")

        with patch.object(broker, "_stream_claude", side_effect=lambda *_, **__: _token_stream()):
            chunks = [
                c
                async for c in broker._stream_provider(
                    provider=Provider.CLAUDE,
                    prompt="p",
                    task_type=TaskType.CONVERSATION,
                    system_prompt=None,
                    messages=None,
                    max_tokens=32,
                    temperature=0.7,
                )
            ]
            assert [c.content for c in chunks] == ["x"]

        with patch.object(broker, "_stream_openai", side_effect=lambda *_, **__: _token_stream()):
            chunks = [
                c
                async for c in broker._stream_provider(
                    provider=Provider.OPENAI,
                    prompt="p",
                    task_type=TaskType.CONVERSATION,
                    system_prompt=None,
                    messages=None,
                    max_tokens=32,
                    temperature=0.7,
                )
            ]
            assert [c.content for c in chunks] == ["x"]

        with patch.object(broker, "_stream_ollama", side_effect=lambda *_, **__: _token_stream()):
            chunks = [
                c
                async for c in broker._stream_provider(
                    provider=Provider.OLLAMA,
                    prompt="p",
                    task_type=TaskType.CONVERSATION,
                    system_prompt=None,
                    messages=None,
                    max_tokens=32,
                    temperature=0.7,
                )
            ]
            assert [c.content for c in chunks] == ["x"]

    @pytest.mark.asyncio
    async def test_stream_claude_yields_tokens_and_done_metadata(self) -> None:
        with (
            patch("zetherion_ai.agent.inference.get_settings", return_value=_settings()),
            patch("zetherion_ai.agent.inference.httpx.AsyncClient", return_value=AsyncMock()),
        ):
            broker = InferenceBroker()
        broker._claude_model = "claude-stream-model"

        async def text_stream() -> AsyncGenerator[str, None]:
            yield "Hello"
            yield " there"

        final_message = MagicMock()
        final_message.usage.input_tokens = 14
        final_message.usage.output_tokens = 6

        class FakeClaudeStream:
            def __init__(self) -> None:
                self.text_stream = text_stream()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_: object) -> None:
                return None

            async def get_final_message(self):
                return final_message

        claude_client = MagicMock()
        claude_client.messages.stream = MagicMock(return_value=FakeClaudeStream())
        broker._claude_client = claude_client

        with patch("zetherion_ai.agent.inference.get_dynamic", return_value="claude-stream-model"):
            chunks = [
                c
                async for c in broker._stream_claude(
                    prompt="prompt",
                    task_type=TaskType.CONVERSATION,
                    system_prompt="system",
                    messages=[{"role": "assistant", "content": "history"}],
                    max_tokens=64,
                    temperature=0.7,
                )
            ]

        assert [c.content for c in chunks[:-1]] == ["Hello", " there"]
        assert chunks[-1].done is True
        assert chunks[-1].model == "claude-stream-model"
        assert chunks[-1].input_tokens == 14
        assert chunks[-1].output_tokens == 6

    @pytest.mark.asyncio
    async def test_stream_openai_yields_tokens_and_usage(self) -> None:
        with (
            patch("zetherion_ai.agent.inference.get_settings", return_value=_settings()),
            patch("zetherion_ai.agent.inference.httpx.AsyncClient", return_value=AsyncMock()),
        ):
            broker = InferenceBroker()
        broker._openai_model = "openai-stream-model"

        chunk_1 = MagicMock()
        chunk_1.choices = [MagicMock(delta=MagicMock(content="Hello"))]
        chunk_1.usage = None
        chunk_2 = MagicMock()
        chunk_2.choices = [MagicMock(delta=MagicMock(content=" world"))]
        chunk_2.usage = MagicMock(prompt_tokens=22, completion_tokens=8)

        async def openai_stream() -> AsyncGenerator[MagicMock, None]:
            yield chunk_1
            yield chunk_2

        openai_client = MagicMock()
        openai_client.chat.completions.create = AsyncMock(return_value=openai_stream())
        broker._openai_client = openai_client

        with patch("zetherion_ai.agent.inference.get_dynamic", return_value="openai-stream-model"):
            chunks = [
                c
                async for c in broker._stream_openai(
                    prompt="prompt",
                    task_type=TaskType.CONVERSATION,
                    system_prompt="system",
                    messages=[{"role": "assistant", "content": "history"}],
                    max_tokens=64,
                    temperature=0.3,
                )
            ]

        assert [c.content for c in chunks[:-1]] == ["Hello", " world"]
        assert chunks[-1].done is True
        assert chunks[-1].model == "openai-stream-model"
        assert chunks[-1].input_tokens == 22
        assert chunks[-1].output_tokens == 8

    @pytest.mark.asyncio
    async def test_stream_ollama_yields_tokens_and_done_usage(self) -> None:
        with (
            patch("zetherion_ai.agent.inference.get_settings", return_value=_settings()),
            patch("zetherion_ai.agent.inference.httpx.AsyncClient", return_value=AsyncMock()),
        ):
            broker = InferenceBroker()
        broker._ollama_model = "ollama-stream-model"

        class FakeOllamaResponse:
            def raise_for_status(self) -> None:
                return None

            async def aiter_lines(self) -> AsyncGenerator[str, None]:
                yield ""
                yield '{"message": {"content": "Hello"}}'
                yield '{"done": true, "prompt_eval_count": 5, "eval_count": 3}'

        class FakeOllamaStreamCM:
            async def __aenter__(self):
                return FakeOllamaResponse()

            async def __aexit__(self, *_: object) -> None:
                return None

        broker._httpx_client.stream = MagicMock(return_value=FakeOllamaStreamCM())

        with patch("zetherion_ai.agent.inference.get_dynamic", return_value="ollama-stream-model"):
            chunks = [
                c
                async for c in broker._stream_ollama(
                    prompt="prompt",
                    task_type=TaskType.CONVERSATION,
                    system_prompt="system",
                    messages=[{"role": "assistant", "content": "history"}],
                    max_tokens=80,
                    temperature=0.2,
                )
            ]

        assert [c.content for c in chunks[:-1]] == ["Hello"]
        assert chunks[-1].done is True
        assert chunks[-1].model == "ollama-stream-model"
        assert chunks[-1].input_tokens == 5
        assert chunks[-1].output_tokens == 3

    @pytest.mark.asyncio
    async def test_call_claude_and_openai_include_history_messages(self) -> None:
        with (
            patch("zetherion_ai.agent.inference.get_settings", return_value=_settings()),
            patch("zetherion_ai.agent.inference.httpx.AsyncClient", return_value=AsyncMock()),
        ):
            broker = InferenceBroker()
        broker._claude_model = "claude-call"
        broker._openai_model = "openai-call"

        claude_response = MagicMock()
        claude_response.content = [MagicMock(text="Claude response")]
        claude_response.usage.input_tokens = 4
        claude_response.usage.output_tokens = 2
        claude_client = MagicMock()
        claude_client.messages.create = AsyncMock(return_value=claude_response)
        broker._claude_client = claude_client

        openai_response = MagicMock()
        openai_response.choices = [MagicMock(message=MagicMock(content="OpenAI response"))]
        openai_response.usage = MagicMock(prompt_tokens=6, completion_tokens=5)
        openai_client = MagicMock()
        openai_client.chat.completions.create = AsyncMock(return_value=openai_response)
        broker._openai_client = openai_client

        history = [{"role": "assistant", "content": "earlier"}]
        claude_result = await broker._call_claude(
            prompt="question",
            task_type=TaskType.CODE_GENERATION,
            system_prompt="sys",
            messages=history,
            max_tokens=32,
            temperature=0.4,
        )
        openai_result = await broker._call_openai(
            prompt="question",
            task_type=TaskType.CONVERSATION,
            system_prompt="sys",
            messages=history,
            max_tokens=32,
            temperature=0.4,
        )

        assert claude_result.content == "Claude response"
        assert openai_result.content == "OpenAI response"
        claude_messages = claude_client.messages.create.call_args.kwargs["messages"]
        openai_messages = openai_client.chat.completions.create.call_args.kwargs["messages"]
        assert claude_messages[0] == {"role": "assistant", "content": "earlier"}
        assert openai_messages[0] == {"role": "system", "content": "sys"}
        assert openai_messages[1] == {"role": "assistant", "content": "earlier"}

    @pytest.mark.asyncio
    async def test_call_gemini_prepends_system_prompt_to_content(self) -> None:
        with (
            patch("zetherion_ai.agent.inference.get_settings", return_value=_settings()),
            patch("zetherion_ai.agent.inference.httpx.AsyncClient", return_value=AsyncMock()),
        ):
            broker = InferenceBroker()
        broker._gemini_model = "gemini-call-model"

        gemini_client = MagicMock()
        gemini_response = MagicMock()
        gemini_response.text = "Gemini response"
        gemini_response.usage_metadata = None
        gemini_client.models.generate_content.return_value = gemini_response
        broker._gemini_client = gemini_client

        with patch("zetherion_ai.agent.inference.get_dynamic", return_value="gemini-call-model"):
            result = await broker._call_gemini(
                prompt="hello",
                task_type=TaskType.SUMMARIZATION,
                system_prompt="system prompt",
                messages=None,
                max_tokens=64,
                temperature=0.2,
            )

        assert result.model == "gemini-call-model"
        assert result.content == "Gemini response"
        assert (
            gemini_client.models.generate_content.call_args.kwargs["contents"]
            == "system prompt\n\nhello"
        )


class TestInferenceAncillaryBranches:
    """Tests for API-key refresh, persistent cost tracking, and health edges."""

    @pytest.mark.asyncio
    async def test_check_api_key_updates_reinitializes_clients(self) -> None:
        with (
            patch("zetherion_ai.agent.inference.get_settings", return_value=_settings()),
            patch("zetherion_ai.agent.inference.httpx.AsyncClient", return_value=AsyncMock()),
            patch("zetherion_ai.agent.inference.anthropic.AsyncAnthropic") as mock_anthropic_cls,
            patch("zetherion_ai.agent.inference.openai.AsyncOpenAI") as mock_openai_cls,
            patch("zetherion_ai.agent.inference.genai.Client") as mock_gemini_cls,
        ):
            broker = InferenceBroker()
            with patch(
                "zetherion_ai.agent.inference.get_secret",
                side_effect=lambda key: {
                    "anthropic_api_key": "new-anthropic",
                    "openai_api_key": "new-openai",
                    "gemini_api_key": "new-gemini",
                }.get(key),
            ):
                broker._check_api_key_updates()

        mock_anthropic_cls.assert_called_once_with(api_key="new-anthropic")
        mock_openai_cls.assert_called_once_with(api_key="new-openai")
        mock_gemini_cls.assert_called_once_with(api_key="new-gemini")
        assert Provider.CLAUDE in broker.available_providers
        assert Provider.OPENAI in broker.available_providers
        assert Provider.GEMINI in broker.available_providers

    @pytest.mark.asyncio
    async def test_track_cost_records_to_persistent_tracker(self) -> None:
        persistent_tracker = MagicMock()
        with (
            patch("zetherion_ai.agent.inference.get_settings", return_value=_settings()),
            patch("zetherion_ai.agent.inference.httpx.AsyncClient", return_value=AsyncMock()),
        ):
            broker = InferenceBroker(cost_tracker=persistent_tracker)

        result = InferenceResult(
            content="ok",
            provider=Provider.OLLAMA,
            task_type=TaskType.SIMPLE_QA,
            model="llama3.1:8b",
            input_tokens=3,
            output_tokens=4,
            latency_ms=12.5,
            estimated_cost_usd=0.0,
        )
        broker._track_cost(result, cost_estimated=True)

        persistent_tracker.record.assert_called_once_with(
            provider="ollama",
            model="llama3.1:8b",
            tokens_input=3,
            tokens_output=4,
            cost_usd=0.0,
            cost_estimated=True,
            task_type="simple_qa",
            latency_ms=12,
        )

    @pytest.mark.asyncio
    async def test_health_check_openai_without_client_returns_false(self) -> None:
        with (
            patch("zetherion_ai.agent.inference.get_settings", return_value=_settings()),
            patch("zetherion_ai.agent.inference.httpx.AsyncClient", return_value=AsyncMock()),
        ):
            broker = InferenceBroker()

        assert await broker.health_check(Provider.OPENAI) is False
