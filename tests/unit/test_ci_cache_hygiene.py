"""Unit tests for CI cache hygiene helpers."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_module():
    module_path = REPO_ROOT / "scripts" / "ci_cache_hygiene.py"
    spec = importlib.util.spec_from_file_location("ci_cache_hygiene_module", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_normalize_cache_supports_rest_and_gh_cli_fields() -> None:
    module = _load_module()

    rest = module.normalize_cache(
        {
            "id": 1,
            "key": "rest-key",
            "ref": "refs/heads/main",
            "created_at": "2026-03-01T00:00:00Z",
            "last_accessed_at": "2026-03-08T00:00:00Z",
            "size_in_bytes": 100,
        }
    )
    gh_cli = module.normalize_cache(
        {
            "id": 2,
            "key": "cli-key",
            "ref": "refs/pull/1/merge",
            "createdAt": "2026-03-01T00:00:00Z",
            "lastAccessedAt": "2026-03-02T00:00:00Z",
            "sizeInBytes": 200,
        }
    )

    assert rest.size_in_bytes == 100
    assert gh_cli.last_accessed_at == "2026-03-02T00:00:00Z"


def test_classify_cache_marks_stale_pull_request_refs_for_deletion() -> None:
    module = _load_module()
    cache = module.CacheRecord(
        cache_id=2,
        key="pr-cache",
        ref="refs/pull/70/merge",
        created_at="2026-03-01T00:00:00Z",
        last_accessed_at="2026-03-02T00:00:00Z",
        size_in_bytes=200,
    )

    decision = module.classify_cache(
        cache,
        now=datetime(2026, 3, 8, tzinfo=UTC),
        max_age_days=14,
        pr_ref_max_age_days=3,
    )

    assert decision.action == "delete"
    assert decision.reason == "stale_pull_request_cache"


def test_classify_cache_keeps_recent_main_cache() -> None:
    module = _load_module()
    cache = module.CacheRecord(
        cache_id=1,
        key="main-cache",
        ref="refs/heads/main",
        created_at="2026-03-01T00:00:00Z",
        last_accessed_at="2026-03-08T04:00:00Z",
        size_in_bytes=100,
    )

    decision = module.classify_cache(
        cache,
        now=datetime(2026, 3, 8, 5, tzinfo=UTC),
        max_age_days=14,
        pr_ref_max_age_days=3,
    )

    assert decision.action == "keep"
    assert decision.reason == "recently_accessed"


def test_summarize_decisions_tracks_reclaimable_bytes() -> None:
    module = _load_module()
    decisions = [
        module.CacheDecision(
            cache=module.CacheRecord(1, "keep", "refs/heads/main", "", "", 100),
            action="keep",
            reason="recently_accessed",
            last_access_age_days=0.0,
        ),
        module.CacheDecision(
            cache=module.CacheRecord(2, "drop", "refs/pull/1/merge", "", "", 250),
            action="delete",
            reason="stale_pull_request_cache",
            last_access_age_days=5.0,
        ),
    ]

    summary = module.summarize_decisions(decisions)

    assert summary["cache_count"] == 2
    assert summary["delete_candidate_count"] == 1
    assert summary["delete_candidate_size_bytes"] == 250


def test_main_writes_dry_run_report(tmp_path: Path) -> None:
    module = _load_module()
    caches_path = tmp_path / "caches.json"
    caches_path.write_text(
        json.dumps(
            [
                {
                    "id": 2,
                    "key": "pr-cache",
                    "ref": "refs/pull/70/merge",
                    "createdAt": "2026-03-01T00:00:00Z",
                    "lastAccessedAt": "2026-03-02T00:00:00Z",
                    "sizeInBytes": 200,
                }
            ]
        ),
        encoding="utf-8",
    )
    output_path = tmp_path / "cache-report.json"

    old_argv = sys.argv[:]
    try:
        sys.argv = [
            "ci_cache_hygiene.py",
            "--caches-file",
            str(caches_path),
            "--output",
            str(output_path),
            "--max-age-days",
            "14",
            "--pr-ref-max-age-days",
            "3",
        ]
        assert module.main() == 0
    finally:
        sys.argv = old_argv

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["mode"] == "dry_run"
    assert payload["summary"]["delete_candidate_count"] == 1
