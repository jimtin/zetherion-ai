"""Docs-backed setup/help knowledge for Zetherion AI.

Indexes local markdown docs into Qdrant, serves semantic retrieval for
setup/help questions, and records unresolved questions for follow-up docs work.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from zetherion_ai.agent.providers import TaskType
from zetherion_ai.logging import get_logger

if TYPE_CHECKING:
    from zetherion_ai.agent.inference import InferenceBroker
    from zetherion_ai.memory.qdrant import QdrantMemory

log = get_logger("zetherion_ai.agent.docs_knowledge")

DOCS_COLLECTION = "docs_knowledge"
DOCS_EXTENSIONS = (".md", ".mdx")
DOCS_QUERY_HINTS = (
    "how do i",
    "how can i",
    "how to",
    "set up",
    "setup",
    "configure",
    "connect",
    "track",
    "integration",
    "docs",
    "documentation",
    "what command",
    "where do i",
    "enable",
    "disable",
)


class DocsKnowledgeBase:
    """RAG over local docs with automatic sync and unresolved-question tracking."""

    def __init__(
        self,
        *,
        memory: QdrantMemory,
        inference_broker: InferenceBroker,
        docs_root: str,
        state_file: str,
        gap_log_file: str,
        sync_interval_seconds: int = 300,
        max_hits: int = 6,
        min_score: float = 0.3,
        chunk_size: int = 1200,
        chunk_overlap: int = 180,
    ) -> None:
        self._memory = memory
        self._broker = inference_broker
        self._docs_root = Path(docs_root).resolve()
        self._state_file = Path(state_file).resolve()
        self._gap_log_file = Path(gap_log_file).resolve()
        self._sync_interval_seconds = max(sync_interval_seconds, 30)
        self._max_hits = max(max_hits, 1)
        self._min_score = min(max(min_score, 0.0), 1.0)
        self._chunk_size = max(chunk_size, 300)
        self._chunk_overlap = max(min(chunk_overlap, self._chunk_size // 2), 0)
        self._sync_lock = asyncio.Lock()
        self._gap_lock = asyncio.Lock()
        self._last_sync_monotonic = 0.0
        self._doc_hashes = self._load_state()

    @staticmethod
    def should_handle_question(message: str) -> bool:
        """Heuristic gate for setup/help documentation questions."""
        lower = message.lower()
        return any(hint in lower for hint in DOCS_QUERY_HINTS)

    async def maybe_answer(
        self,
        *,
        question: str,
        user_id: int,
        intent: str,
    ) -> str | None:
        """Answer from docs when possible; otherwise record a gap and return ``None``."""
        await self.sync()

        hits = await self._memory.search_collection(
            collection_name=DOCS_COLLECTION,
            query=question,
            limit=self._max_hits * 2,
            score_threshold=self._min_score,
        )
        if not hits:
            await self.record_gap(
                question=question,
                user_id=user_id,
                intent=intent,
                reason="no_matching_docs",
            )
            return None

        selected = hits[: self._max_hits]
        context_blocks = []
        for idx, hit in enumerate(selected, start=1):
            source = str(hit.get("source_path", "unknown"))
            content = str(hit.get("content", ""))
            context_blocks.append(f"[{idx}] Source: {source}\n{content}")
        context = "\n\n".join(context_blocks)

        prompt = (
            "You are answering a product setup/help question using ONLY provided docs context.\n"
            "If the answer is not clearly present, respond exactly with: INSUFFICIENT_CONTEXT\n\n"
            f"Question:\n{question}\n\n"
            f"Docs context:\n{context}\n\n"
            "Answer in concise practical steps."
        )

        result = await self._broker.infer(
            prompt=prompt,
            task_type=TaskType.DOCS_QA,
            temperature=0.1,
            max_tokens=650,
        )
        answer = (result.content or "").strip()
        if not answer or "INSUFFICIENT_CONTEXT" in answer.upper():
            await self.record_gap(
                question=question,
                user_id=user_id,
                intent=intent,
                reason="insufficient_context",
            )
            return None

        sources = sorted({str(hit.get("source_path", "unknown")) for hit in selected})
        source_lines = "\n".join(f"- `{source}`" for source in sources)
        return f"{answer}\n\nSources:\n{source_lines}"

    async def sync(self, *, force: bool = False) -> None:
        """Sync markdown docs into the vector store."""
        now = time.monotonic()
        if not force and (now - self._last_sync_monotonic) < self._sync_interval_seconds:
            return

        async with self._sync_lock:
            now = time.monotonic()
            if not force and (now - self._last_sync_monotonic) < self._sync_interval_seconds:
                return

            if not self._docs_root.exists():
                log.warning("docs_root_missing", root=str(self._docs_root))
                self._last_sync_monotonic = time.monotonic()
                return

            await self._memory.ensure_collection(DOCS_COLLECTION)

            files = self._iter_doc_files()
            current_hashes: dict[str, str] = {}
            changed_count = 0
            removed_count = 0

            for file_path in files:
                rel_path = file_path.relative_to(self._docs_root).as_posix()
                content = self._read_text(file_path)
                digest = self._hash_text(content)
                current_hashes[rel_path] = digest

                if self._doc_hashes.get(rel_path) == digest:
                    continue

                await self._memory.delete_by_field(DOCS_COLLECTION, "source_path", rel_path)
                await self._index_document(rel_path=rel_path, content=content, source_hash=digest)
                changed_count += 1

            removed_paths = set(self._doc_hashes) - set(current_hashes)
            for rel_path in removed_paths:
                if await self._memory.delete_by_field(DOCS_COLLECTION, "source_path", rel_path):
                    removed_count += 1

            self._doc_hashes = current_hashes
            self._save_state(self._doc_hashes)
            self._last_sync_monotonic = time.monotonic()
            log.info(
                "docs_knowledge_synced",
                files=len(files),
                changed=changed_count,
                removed=removed_count,
            )

    async def record_gap(
        self,
        *,
        question: str,
        user_id: int,
        intent: str,
        reason: str,
    ) -> None:
        """Append an unresolved docs question to the gap log."""
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "question": question,
            "user_id": user_id,
            "intent": intent,
            "reason": reason,
        }
        async with self._gap_lock:
            await asyncio.to_thread(self._append_gap_entry, entry)
        log.info("docs_gap_recorded", reason=reason, intent=intent)

    async def _index_document(self, *, rel_path: str, content: str, source_hash: str) -> None:
        """Chunk and index one markdown document."""
        chunks = self._chunk_text(content)
        timestamp = datetime.now(UTC).isoformat()
        for idx, chunk in enumerate(chunks):
            point_key = f"{rel_path}:{source_hash}:{idx}"
            point_id = hashlib.sha1(point_key.encode(), usedforsecurity=False)
            await self._memory.store_with_payload(
                collection_name=DOCS_COLLECTION,
                point_id=point_id.hexdigest(),
                payload={
                    "source_path": rel_path,
                    "source_hash": source_hash,
                    "chunk_index": idx,
                    "content": chunk,
                    "indexed_at": timestamp,
                },
                content_for_embedding=chunk,
            )

    def _iter_doc_files(self) -> list[Path]:
        return sorted(
            file_path
            for file_path in self._docs_root.rglob("*")
            if file_path.is_file() and file_path.suffix.lower() in DOCS_EXTENSIONS
        )

    @staticmethod
    def _read_text(path: Path) -> str:
        return path.read_text(encoding="utf-8", errors="ignore")

    def _chunk_text(self, text: str) -> list[str]:
        cleaned = text.strip()
        if not cleaned:
            return []

        chunks: list[str] = []
        start = 0
        text_len = len(cleaned)
        while start < text_len:
            end = min(start + self._chunk_size, text_len)
            if end < text_len:
                paragraph_break = cleaned.rfind("\n\n", start, end)
                if paragraph_break > start + 200:
                    end = paragraph_break
            chunk = cleaned[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end >= text_len:
                break
            next_start = max(end - self._chunk_overlap, 0)
            if next_start <= start:
                next_start = end
            start = next_start
        return chunks

    @staticmethod
    def _hash_text(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def _load_state(self) -> dict[str, str]:
        if not self._state_file.exists():
            return {}
        try:
            data = json.loads(self._state_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except Exception as exc:
            log.warning("docs_state_load_failed", error=str(exc))
        return {}

    def _save_state(self, state: dict[str, str]) -> None:
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        self._state_file.write_text(
            json.dumps(state, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _append_gap_entry(self, entry: dict[str, Any]) -> None:
        self._gap_log_file.parent.mkdir(parents=True, exist_ok=True)
        with self._gap_log_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
