#!/usr/bin/env python3
"""List and optionally prune stale GitHub Actions caches."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DEFAULT_API_URL = "https://api.github.com"
DEFAULT_MAX_AGE_DAYS = 14
DEFAULT_PR_REF_MAX_AGE_DAYS = 3
PROTECTED_REFS = {"refs/heads/main", "refs/heads/develop", "refs/heads/gh-pages"}


@dataclass(frozen=True)
class CacheRecord:
    cache_id: int
    key: str
    ref: str
    created_at: str
    last_accessed_at: str
    size_in_bytes: int


@dataclass(frozen=True)
class CacheDecision:
    cache: CacheRecord
    action: str
    reason: str
    last_access_age_days: float


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    value = raw.strip()
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(value).astimezone(UTC)
    except ValueError:
        return None


def _api_request_json(
    *, api_url: str, token: str, method: str, path: str, query: dict[str, Any] | None = None
) -> dict[str, Any]:
    url = f"{api_url.rstrip('/')}{path}"
    if query:
        url = f"{url}?{urlencode(query)}"
    request = Request(
        url,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "Zetherion-CI-Cache-Hygiene/1.0",
        },
    )
    with urlopen(request) as response:  # noqa: S310
        if response.status == 204:
            return {}
        return json.load(response)


def fetch_caches(*, repo: str, token: str, api_url: str = DEFAULT_API_URL) -> list[dict[str, Any]]:
    caches: list[dict[str, Any]] = []
    page = 1
    while True:
        payload = _api_request_json(
            api_url=api_url,
            token=token,
            method="GET",
            path=f"/repos/{repo}/actions/caches",
            query={"per_page": 100, "page": page},
        )
        items = payload.get("actions_caches", [])
        if not isinstance(items, list) or not items:
            break
        caches.extend(item for item in items if isinstance(item, dict))
        if len(items) < 100:
            break
        page += 1
    return caches


def delete_cache(*, repo: str, cache_id: int, token: str, api_url: str = DEFAULT_API_URL) -> None:
    _api_request_json(
        api_url=api_url,
        token=token,
        method="DELETE",
        path=f"/repos/{repo}/actions/caches/{cache_id}",
    )


def normalize_cache(item: dict[str, Any]) -> CacheRecord:
    return CacheRecord(
        cache_id=int(item.get("id", 0) or 0),
        key=str(item.get("key", "") or ""),
        ref=str(item.get("ref", "") or ""),
        created_at=str(item.get("created_at") or item.get("createdAt") or ""),
        last_accessed_at=str(item.get("last_accessed_at") or item.get("lastAccessedAt") or ""),
        size_in_bytes=int(item.get("size_in_bytes") or item.get("sizeInBytes") or 0),
    )


def classify_cache(
    cache: CacheRecord,
    *,
    now: datetime,
    max_age_days: int,
    pr_ref_max_age_days: int,
) -> CacheDecision:
    last_accessed = _parse_ts(cache.last_accessed_at) or _parse_ts(cache.created_at) or now
    age_days = max(0.0, round((now - last_accessed).total_seconds() / 86400.0, 2))
    if cache.ref.startswith("refs/pull/") and age_days >= pr_ref_max_age_days:
        return CacheDecision(
            cache=cache,
            action="delete",
            reason="stale_pull_request_cache",
            last_access_age_days=age_days,
        )
    if cache.ref not in PROTECTED_REFS and age_days >= max_age_days:
        return CacheDecision(
            cache=cache,
            action="delete",
            reason="stale_branch_cache",
            last_access_age_days=age_days,
        )
    if cache.ref in PROTECTED_REFS and age_days >= max_age_days:
        return CacheDecision(
            cache=cache,
            action="delete",
            reason="stale_protected_ref_cache",
            last_access_age_days=age_days,
        )
    return CacheDecision(
        cache=cache,
        action="keep",
        reason="recently_accessed",
        last_access_age_days=age_days,
    )


def summarize_decisions(decisions: list[CacheDecision]) -> dict[str, Any]:
    kept = [item for item in decisions if item.action == "keep"]
    deletions = [item for item in decisions if item.action == "delete"]
    return {
        "cache_count": len(decisions),
        "kept_count": len(kept),
        "delete_candidate_count": len(deletions),
        "total_size_bytes": sum(item.cache.size_in_bytes for item in decisions),
        "kept_size_bytes": sum(item.cache.size_in_bytes for item in kept),
        "delete_candidate_size_bytes": sum(item.cache.size_in_bytes for item in deletions),
        "delete_candidates": [
            {
                "id": item.cache.cache_id,
                "key": item.cache.key,
                "ref": item.cache.ref,
                "size_in_bytes": item.cache.size_in_bytes,
                "last_accessed_at": item.cache.last_accessed_at,
                "last_access_age_days": item.last_access_age_days,
                "reason": item.reason,
            }
            for item in deletions
        ],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "### CI Cache Hygiene",
        "",
        f"- Mode: `{payload['mode']}`",
        f"- Total caches: `{summary['cache_count']}`",
        f"- Delete candidates: `{summary['delete_candidate_count']}`",
        f"- Reclaimable bytes: `{summary['delete_candidate_size_bytes']}`",
        "",
        "| Cache ID | Ref | Age (days) | Size (bytes) | Reason |",
        "|----------|-----|------------|--------------|--------|",
    ]
    for item in summary["delete_candidates"][:10]:
        lines.append(
            "| `{cache_id}` | `{ref}` | {age} | {size} | `{reason}` |".format(
                cache_id=item["id"],
                ref=item["ref"],
                age=item["last_access_age_days"],
                size=item["size_in_bytes"],
                reason=item["reason"],
            )
        )
    lines.append("")
    return "\n".join(lines)


def _write_output(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _append_summary(text: str) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with Path(summary_path).open("a", encoding="utf-8") as handle:
        handle.write(text)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo")
    parser.add_argument("--token")
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--output", required=True)
    parser.add_argument("--caches-file")
    parser.add_argument("--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS)
    parser.add_argument("--pr-ref-max-age-days", type=int, default=DEFAULT_PR_REF_MAX_AGE_DAYS)
    parser.add_argument("--delete", action="store_true")
    return parser


def _require_value(value: str | None, env_name: str) -> str:
    resolved = (value or os.environ.get(env_name) or "").strip()
    if not resolved:
        raise SystemExit(f"{env_name} is required")
    return resolved


def main() -> int:
    args = build_parser().parse_args()
    if args.caches_file:
        raw_caches = json.loads(Path(args.caches_file).read_text(encoding="utf-8"))
    else:
        repo = _require_value(args.repo, "GITHUB_REPOSITORY")
        token = _require_value(args.token, "GITHUB_TOKEN")
        raw_caches = fetch_caches(repo=repo, token=token, api_url=args.api_url)
    caches = [normalize_cache(item) for item in raw_caches]
    now = _now()
    decisions = [
        classify_cache(
            cache,
            now=now,
            max_age_days=int(args.max_age_days),
            pr_ref_max_age_days=int(args.pr_ref_max_age_days),
        )
        for cache in caches
    ]
    deleted_ids: list[int] = []
    errors: list[dict[str, Any]] = []
    if args.delete:
        repo = _require_value(args.repo, "GITHUB_REPOSITORY")
        token = _require_value(args.token, "GITHUB_TOKEN")
        for decision in decisions:
            if decision.action != "delete":
                continue
            try:
                delete_cache(
                    repo=repo,
                    cache_id=decision.cache.cache_id,
                    token=token,
                    api_url=args.api_url,
                )
                deleted_ids.append(decision.cache.cache_id)
            except Exception as exc:  # noqa: BLE001
                errors.append(
                    {
                        "id": decision.cache.cache_id,
                        "key": decision.cache.key,
                        "ref": decision.cache.ref,
                        "error": str(exc),
                    }
                )
    payload = {
        "generated_at": now.isoformat(),
        "mode": "delete" if args.delete else "dry_run",
        "summary": summarize_decisions(decisions),
        "deleted_cache_ids": deleted_ids,
        "errors": errors,
    }
    _write_output(Path(args.output), payload)
    markdown = render_markdown(payload)
    print(markdown)
    _append_summary(markdown)
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
