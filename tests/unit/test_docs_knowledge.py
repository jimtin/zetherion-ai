"""Unit tests for docs-backed knowledge retrieval."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from zetherion_ai.agent.docs_knowledge import DOCS_COLLECTION, DocsKnowledgeBase


def _make_service(tmp_path: Path) -> tuple[DocsKnowledgeBase, AsyncMock, AsyncMock]:
    memory = AsyncMock()
    memory.ensure_collection = AsyncMock()
    memory.delete_by_field = AsyncMock(return_value=True)
    memory.store_with_payload = AsyncMock()
    memory.search_collection = AsyncMock(return_value=[])

    broker = AsyncMock()
    broker.infer = AsyncMock()

    docs_root = tmp_path / "docs"
    docs_root.mkdir()
    state_file = tmp_path / "state.json"
    gap_log = tmp_path / "gaps.jsonl"

    service = DocsKnowledgeBase(
        memory=memory,
        inference_broker=broker,
        docs_root=str(docs_root),
        state_file=str(state_file),
        gap_log_file=str(gap_log),
        sync_interval_seconds=30,
    )
    return service, memory, broker


class TestShouldHandleQuestion:
    def test_true_for_setup_question(self) -> None:
        assert DocsKnowledgeBase.should_handle_question("How do I add an email account?") is True

    def test_false_for_non_setup_question(self) -> None:
        assert DocsKnowledgeBase.should_handle_question("Tell me a joke.") is False


class TestSync:
    @pytest.mark.asyncio
    async def test_sync_indexes_markdown_files(self, tmp_path: Path) -> None:
        service, memory, _ = _make_service(tmp_path)
        (tmp_path / "docs" / "user").mkdir()
        (tmp_path / "docs" / "user" / "gmail.md").write_text(
            "# Gmail\n\nUse /gmail connect to add an account.",
            encoding="utf-8",
        )

        await service.sync(force=True)

        memory.ensure_collection.assert_awaited_once_with(DOCS_COLLECTION)
        assert memory.store_with_payload.await_count >= 1
        state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
        assert "user/gmail.md" in state

    @pytest.mark.asyncio
    async def test_sync_reindexes_when_file_changes(self, tmp_path: Path) -> None:
        service, memory, _ = _make_service(tmp_path)
        target = tmp_path / "docs" / "setup.md"
        target.write_text("Initial content", encoding="utf-8")

        await service.sync(force=True)
        first_store_count = memory.store_with_payload.await_count

        target.write_text("Updated content for docs", encoding="utf-8")
        await service.sync(force=True)

        assert memory.delete_by_field.await_count >= 2
        assert memory.store_with_payload.await_count > first_store_count


class TestMaybeAnswer:
    @pytest.mark.asyncio
    async def test_returns_answer_with_sources(self, tmp_path: Path) -> None:
        service, memory, broker = _make_service(tmp_path)
        (tmp_path / "docs" / "setup.md").write_text("Gmail setup instructions", encoding="utf-8")
        memory.search_collection = AsyncMock(
            return_value=[
                {
                    "id": "1",
                    "score": 0.9,
                    "source_path": "setup.md",
                    "content": "Use /gmail connect to link an account.",
                }
            ]
        )
        broker_result = MagicMock()
        broker_result.content = "Use `/gmail connect` and follow the OAuth prompt."
        broker.infer = AsyncMock(return_value=broker_result)

        answer = await service.maybe_answer(
            question="How do I add an email for you to track?",
            user_id=42,
            intent="email_management",
        )

        assert answer is not None
        assert "/gmail connect" in answer
        assert "setup.md" in answer

    @pytest.mark.asyncio
    async def test_records_gap_when_no_matches(self, tmp_path: Path) -> None:
        service, memory, _ = _make_service(tmp_path)
        (tmp_path / "docs" / "setup.md").write_text("Some content", encoding="utf-8")
        memory.search_collection = AsyncMock(return_value=[])

        answer = await service.maybe_answer(
            question="How do I rotate SMTP keys?",
            user_id=7,
            intent="system_command",
        )

        assert answer is None
        lines = (tmp_path / "gaps.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        payload = json.loads(lines[0])
        assert payload["reason"] == "no_matching_docs"
        assert payload["question"] == "How do I rotate SMTP keys?"

    @pytest.mark.asyncio
    async def test_records_gap_when_context_insufficient(self, tmp_path: Path) -> None:
        service, memory, broker = _make_service(tmp_path)
        (tmp_path / "docs" / "setup.md").write_text("Only limited content", encoding="utf-8")
        memory.search_collection = AsyncMock(
            return_value=[
                {
                    "id": "1",
                    "score": 0.85,
                    "source_path": "setup.md",
                    "content": "Limited note.",
                }
            ]
        )
        broker_result = MagicMock()
        broker_result.content = "INSUFFICIENT_CONTEXT"
        broker.infer = AsyncMock(return_value=broker_result)

        answer = await service.maybe_answer(
            question="How do I migrate accounts from another provider?",
            user_id=12,
            intent="email_management",
        )

        assert answer is None
        payload = json.loads((tmp_path / "gaps.jsonl").read_text(encoding="utf-8").strip())
        assert payload["reason"] == "insufficient_context"
