#!/usr/bin/env python3
"""Windows-local post-deploy promotions for main deployment SHAs."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REQUIRED_PRIMARY_MODEL = "gpt-5.2"
REQUIRED_SECONDARY_MODEL = "claude-sonnet-4-6"

BLOG_SUCCESS_STATUSES = {"published", "duplicate", "skipped_disabled"}
RELEASE_SUCCESS_STATUSES = {"created", "skipped_existing", "skipped_disabled"}

AI_SOUNDING_PATTERNS = [
    "in today's rapidly evolving",
    "ever-evolving",
    "in conclusion",
    "as an ai language model",
    "delve into",
    "game changer",
    "unlock the potential",
    "leverage",
    "furthermore",
    "additionally",
    "moreover",
    "seamless",
]

SEMVER_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
PR_REF_RE = re.compile(r"\(#(\d+)\)|(?<![A-Za-z0-9_])#(\d+)\b")
ISSUE_REF_RE = re.compile(r"(?<![A-Za-z0-9_])#(\d+)\b")

RETRYABLE_EXIT_CODE = 2


class PromotionError(RuntimeError):
    """Raised when the promotion pipeline cannot continue."""


def _now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def _normalize_sha(value: str | None) -> str:
    return (value or "").strip().lower()


def _normalize_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:100] or "zetherion-update"


def _is_enabled(raw: str | None) -> bool:
    if raw is None:
        return True
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise PromotionError(f"Missing required environment variable: {name}")
    return value


def _write_json(path: Path, payload: dict[str, Any] | list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def _read_json(path: Path, *, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists():
        return {} if default is None else default
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PromotionError(f"Invalid JSON at {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise PromotionError(f"Expected JSON object at {path}")
    return raw


def _run_git(repo_path: Path, *args: str, allow_failure: bool = False) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 and not allow_failure:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise PromotionError(
            f"git {' '.join(args)} failed ({proc.returncode}): {detail}"
        )
    return proc.stdout.strip()


def _run_command(
    argv: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _is_ancestor(repo_path: Path, *, ancestor: str, descendant: str) -> bool:
    proc = _run_command(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=repo_path,
    )
    return proc.returncode == 0


def _post_json(
    url: str,
    *,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: int = 60,
) -> tuple[int, str]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    for key, value in headers.items():
        request.add_header(key, value)

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def _gh_request(
    token: str,
    repo: str,
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any] | list[dict[str, Any]]:
    url = f"https://api.github.com/repos/{repo}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None

    req = urllib.request.Request(url, data=data, method=method.upper())
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "zetherion-windows-promotions")
    if payload is not None:
        req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise PromotionError(
            f"GitHub API request failed ({method} {path}) HTTP {exc.code}: {detail}"
        ) from exc

    if not raw:
        return {}
    parsed = json.loads(raw)
    if isinstance(parsed, dict | list):
        return parsed
    raise PromotionError("GitHub API returned non-JSON payload")


def _infer_repo_from_git(repo_path: Path) -> str:
    remote = _run_git(repo_path, "remote", "get-url", "origin")
    if remote.startswith("git@github.com:"):
        return remote.removeprefix("git@github.com:").removesuffix(".git")
    if remote.startswith("https://github.com/"):
        return remote.removeprefix("https://github.com/").removesuffix(".git")
    raise PromotionError("Unable to infer GitHub repository from git remote origin")


def _extract_json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1)
    else:
        inline = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if inline:
            text = inline.group(0)
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise PromotionError("Model output must be a JSON object")
    return parsed


def _openai_generate(*, api_key: str, model: str, system: str, user: str) -> str:
    status, body = _post_json(
        "https://api.openai.com/v1/responses",
        payload={
            "model": model,
            "input": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.5,
            "max_output_tokens": 3200,
        },
        headers={"Authorization": f"Bearer {api_key}"},
    )
    if status >= 400:
        raise PromotionError(f"OpenAI draft generation failed (HTTP {status}): {body}")

    payload = json.loads(body)
    if isinstance(payload.get("output_text"), str) and payload["output_text"].strip():
        return str(payload["output_text"])

    output = payload.get("output")
    if isinstance(output, list):
        chunks: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") in {"output_text", "text"}:
                    text = block.get("text")
                    if isinstance(text, str) and text.strip():
                        chunks.append(text)
        if chunks:
            return "\n".join(chunks)

    raise PromotionError("OpenAI response did not include draft text")


def _anthropic_refine(*, api_key: str, model: str, prompt: str) -> str:
    status, body = _post_json(
        "https://api.anthropic.com/v1/messages",
        payload={
            "model": model,
            "max_tokens": 3200,
            "temperature": 0.35,
            "messages": [{"role": "user", "content": prompt}],
        },
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    if status >= 400:
        raise PromotionError(f"Anthropic refine generation failed (HTTP {status}): {body}")

    payload = json.loads(body)
    content = payload.get("content")
    if isinstance(content, list):
        pieces: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    pieces.append(text)
        if pieces:
            return "\n".join(pieces)
    raise PromotionError("Anthropic response did not include refined text")


def _parse_refs(text: str) -> tuple[list[int], list[int]]:
    pr_numbers: list[int] = []
    issue_numbers: list[int] = []

    for match in PR_REF_RE.finditer(text):
        pr_raw = match.group(1) or match.group(2)
        if not pr_raw:
            continue
        num = int(pr_raw)
        if num not in pr_numbers:
            pr_numbers.append(num)
        if num not in issue_numbers:
            issue_numbers.append(num)

    for match in ISSUE_REF_RE.finditer(text):
        num = int(match.group(1))
        if num not in issue_numbers:
            issue_numbers.append(num)

    return pr_numbers, issue_numbers


def _commit_numstat(repo_path: Path, sha: str) -> tuple[int, int]:
    raw = _run_git(repo_path, "show", "--numstat", "--pretty=format:", sha)
    additions = 0
    deletions = 0
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        add_raw, del_raw = parts[0].strip(), parts[1].strip()
        if add_raw.isdigit():
            additions += int(add_raw)
        if del_raw.isdigit():
            deletions += int(del_raw)
    return additions, deletions


def _classify_change(summary: str, body: str, files: list[str]) -> dict[str, bool]:
    text = f"{summary}\n{body}".lower()
    file_text = " ".join(files).lower()

    customer_facing = any(
        marker in file_text
        for marker in (
            "src/zetherion_ai/api",
            "src/zetherion_ai/agent",
            "cgs/",
            "docs/technical",
            "docs/development",
        )
    ) or any(
        marker in text
        for marker in (
            "client",
            "frontend",
            "api",
            "upload",
            "download",
            "rag",
            "vector",
            "document",
        )
    )

    operational = any(
        marker in file_text
        for marker in (
            ".github/workflows",
            "scripts/windows",
            "docker-compose",
            "dockerfile",
            "infra",
            ".ci/",
        )
    ) or any(marker in text for marker in ("deploy", "ci", "pipeline", "release", "rollback"))

    risk_sensitive = any(
        marker in file_text
        for marker in (
            "security",
            "auth",
            "memory",
            "queue",
            "discord",
            "tests/integration",
            "watchdog",
        )
    ) or any(
        marker in text
        for marker in (
            "security",
            "injection",
            "attack",
            "memory",
            "race",
            "retry",
            "rollback",
            "critical",
            "breaking",
        )
    )

    migration_notes = any(
        marker in text
        for marker in (
            "breaking",
            "migration",
            "deprecat",
            "backward incompatible",
            "manual step",
        )
    )

    return {
        "customer_facing": customer_facing,
        "operational": operational,
        "risk_sensitive": risk_sensitive,
        "migration_notes": migration_notes,
    }


def _resolve_commit_window(repo_path: Path, *, start_sha: str, end_sha: str) -> list[str]:
    if start_sha and _is_ancestor(repo_path, ancestor=start_sha, descendant=end_sha):
        revlist = _run_git(
            repo_path,
            "rev-list",
            "--first-parent",
            "--reverse",
            f"{start_sha}..{end_sha}",
        )
        commits = [line.strip() for line in revlist.splitlines() if line.strip()]
        if commits:
            return commits

    revlist = _run_git(
        repo_path,
        "rev-list",
        "--first-parent",
        "--reverse",
        "--max-count",
        "40",
        end_sha,
    )
    commits = [line.strip() for line in revlist.splitlines() if line.strip()]
    if not commits:
        return [end_sha]
    return commits


def _fetch_pull_request_details(
    *,
    token: str,
    repo: str,
    pr_numbers: set[int],
) -> dict[int, dict[str, Any]]:
    details: dict[int, dict[str, Any]] = {}
    for number in sorted(pr_numbers):
        try:
            payload = _gh_request(token, repo, "GET", f"/pulls/{number}")
        except PromotionError:
            continue
        if not isinstance(payload, dict):
            continue
        labels: list[str] = []
        raw_labels = payload.get("labels")
        if isinstance(raw_labels, list):
            for item in raw_labels:
                if isinstance(item, dict):
                    name = str(item.get("name", "")).strip()
                    if name:
                        labels.append(name)
        details[number] = {
            "number": number,
            "title": str(payload.get("title", "")),
            "url": str(payload.get("html_url", "")),
            "state": str(payload.get("state", "")),
            "labels": labels,
            "merged_at": str(payload.get("merged_at", "")),
        }
    return details


def _pull_requests_for_commit(*, token: str, repo: str, sha: str) -> list[int]:
    try:
        payload = _gh_request(token, repo, "GET", f"/commits/{sha}/pulls")
    except PromotionError:
        return []
    numbers: list[int] = []
    if isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue
            try:
                number = int(item.get("number", 0))
            except (TypeError, ValueError):
                continue
            if number > 0 and number not in numbers:
                numbers.append(number)
    return numbers


def _build_merge_intelligence(
    *,
    repo_path: Path,
    repo: str,
    sha: str,
    previous_sha: str,
    github_token: str,
) -> dict[str, Any]:
    commit_shas = _resolve_commit_window(repo_path, start_sha=previous_sha, end_sha=sha)
    records: list[dict[str, Any]] = []
    all_prs: set[int] = set()
    all_issues: set[int] = set()

    for index, commit_sha in enumerate(commit_shas, start=1):
        summary = _run_git(repo_path, "show", "--no-patch", "--pretty=format:%s", commit_sha)
        body = _run_git(repo_path, "show", "--no-patch", "--pretty=format:%b", commit_sha)
        author = _run_git(repo_path, "show", "--no-patch", "--pretty=format:%an", commit_sha)
        authored_at = _run_git(repo_path, "show", "--no-patch", "--pretty=format:%aI", commit_sha)
        files_raw = _run_git(repo_path, "show", "--name-only", "--pretty=format:", commit_sha)
        files = [line.strip() for line in files_raw.splitlines() if line.strip()][:120]
        additions, deletions = _commit_numstat(repo_path, commit_sha)

        pr_numbers, issue_numbers = _parse_refs(f"{summary}\n{body}")
        commit_linked_prs = _pull_requests_for_commit(
            token=github_token,
            repo=repo,
            sha=commit_sha,
        )
        for pr in commit_linked_prs:
            if pr not in pr_numbers:
                pr_numbers.append(pr)
        for pr in pr_numbers:
            all_prs.add(pr)
        for issue in issue_numbers:
            all_issues.add(issue)

        evidence_id = f"EV{index:03d}"
        records.append(
            {
                "evidence_id": evidence_id,
                "commit_sha": commit_sha,
                "summary": summary,
                "body": body,
                "author": author,
                "authored_at": authored_at,
                "files": files,
                "diff_stats": {"additions": additions, "deletions": deletions},
                "pr_numbers": pr_numbers,
                "issue_numbers": issue_numbers,
                "classification": _classify_change(summary, body, files),
            }
        )

    pr_details = _fetch_pull_request_details(token=github_token, repo=repo, pr_numbers=all_prs)
    classifications = {
        "customer_facing": [],
        "operational": [],
        "risk_sensitive": [],
        "migration_notes": [],
    }
    evidence_map: dict[str, dict[str, Any]] = {}

    for record in records:
        evidence_id = str(record["evidence_id"])
        evidence_map[evidence_id] = {
            "commit_sha": record["commit_sha"],
            "summary": record["summary"],
            "files": record["files"][:20],
            "pr_numbers": record["pr_numbers"],
            "issue_numbers": record["issue_numbers"],
        }
        classification = record.get("classification", {})
        if isinstance(classification, dict):
            for key in classifications:
                if classification.get(key):
                    classifications[key].append(evidence_id)

    return {
        "generated_at": _now_iso(),
        "window": {
            "from_sha": previous_sha or "",
            "to_sha": sha,
            "first_parent": True,
            "commit_count": len(records),
        },
        "repository": repo,
        "commits": records,
        "pull_requests": [pr_details[number] for number in sorted(pr_details.keys())],
        "linked_issues": sorted(all_issues),
        "classifications": classifications,
        "evidence_map": evidence_map,
    }


def _seo_validate(blog: dict[str, Any]) -> None:
    title = str(blog.get("title", "")).strip()
    meta = str(blog.get("meta_description", "")).strip()
    keyword = str(blog.get("primary_keyword", "")).strip()
    content = str(blog.get("content_markdown", "")).strip()

    if len(title) < 40 or len(title) > 70:
        raise PromotionError("SEO check failed: title length must be between 40 and 70 characters")
    if len(meta) < 140 or len(meta) > 160:
        raise PromotionError("SEO check failed: meta description length must be 140-160 characters")
    if not keyword:
        raise PromotionError("SEO check failed: primary_keyword is required")
    if not content:
        raise PromotionError("SEO check failed: content_markdown is required")

    lower_keyword = keyword.lower()
    lower_title = title.lower()
    lower_content = content.lower()
    if lower_keyword not in lower_title:
        raise PromotionError("SEO check failed: primary keyword must appear in title")

    first_140_words = " ".join(content.split()[:140]).lower()
    if lower_keyword not in first_140_words:
        raise PromotionError("SEO check failed: primary keyword must appear in first 140 words")

    h2s = re.findall(r"^##\s+(.+)$", content, flags=re.MULTILINE)
    h3s = re.findall(r"^###\s+(.+)$", content, flags=re.MULTILINE)
    if len(h2s) < 3:
        raise PromotionError("SEO check failed: content must contain at least 3 H2 headings")
    if not h3s:
        raise PromotionError("SEO check failed: content must contain at least one H3 heading")
    if not any(lower_keyword in heading.lower() for heading in h2s):
        raise PromotionError("SEO check failed: at least one H2 must include primary keyword")

    for pattern in AI_SOUNDING_PATTERNS:
        if pattern in lower_title or pattern in lower_content:
            raise PromotionError(f"Quality check failed: banned phrase detected: {pattern!r}")


def _validate_evidence_refs(
    items: Any,
    *,
    field_name: str,
    evidence_ids: set[str],
    min_items: int,
) -> list[dict[str, Any]]:
    if not isinstance(items, list) or len(items) < min_items:
        raise PromotionError(
            f"GEO check failed: '{field_name}' requires at least {min_items} entries"
        )

    normalized: list[dict[str, Any]] = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            raise PromotionError(f"GEO check failed: '{field_name}[{idx}]' must be an object")
        evidence = item.get("evidence_ids")
        if not isinstance(evidence, list) or not evidence:
            raise PromotionError(
                f"GEO check failed: '{field_name}[{idx}].evidence_ids' must contain at least one ID"
            )
        normalized_ids: list[str] = []
        for value in evidence:
            evidence_id = str(value).strip().upper()
            if not evidence_id or evidence_id not in evidence_ids:
                raise PromotionError(
                    f"GEO check failed: unknown evidence id in '{field_name}[{idx}]': {value!r}"
                )
            normalized_ids.append(evidence_id)
        item["evidence_ids"] = normalized_ids
        normalized.append(item)
    return normalized


def _geo_validate(blog: dict[str, Any], *, evidence_ids: set[str]) -> None:
    content = str(blog.get("content_markdown", "")).strip()
    lower_content = content.lower()

    if "## quick answers" not in lower_content:
        raise PromotionError("GEO check failed: content must include a '## Quick Answers' section")
    if "## sources and evidence" not in lower_content:
        raise PromotionError(
            "GEO check failed: content must include a '## Sources and Evidence' section"
        )
    if "(evidence:" not in lower_content:
        raise PromotionError("GEO check failed: content must include '(Evidence: ...)' citations")

    faq = _validate_evidence_refs(
        blog.get("faq"),
        field_name="faq",
        evidence_ids=evidence_ids,
        min_items=2,
    )
    claims = _validate_evidence_refs(
        blog.get("claims"),
        field_name="claims",
        evidence_ids=evidence_ids,
        min_items=3,
    )

    entities = blog.get("entities")
    if not isinstance(entities, list) or len([e for e in entities if str(e).strip()]) < 3:
        raise PromotionError("GEO check failed: 'entities' must contain at least 3 entries")

    for idx, item in enumerate(faq):
        question = str(item.get("question", "")).strip()
        answer = str(item.get("answer", "")).strip()
        if not question or not answer:
            raise PromotionError(f"GEO check failed: faq[{idx}] requires question and answer")

    for idx, item in enumerate(claims):
        statement = str(item.get("statement", "")).strip()
        if not statement:
            raise PromotionError(f"GEO check failed: claims[{idx}] requires statement text")


def _build_evidence_context(analysis: dict[str, Any]) -> str:
    evidence_map = analysis.get("evidence_map")
    if not isinstance(evidence_map, dict):
        raise PromotionError("Merge intelligence missing evidence_map")
    lines: list[str] = []
    for evidence_id in sorted(evidence_map.keys())[:60]:
        payload = evidence_map[evidence_id]
        if not isinstance(payload, dict):
            continue
        summary = str(payload.get("summary", "")).strip()
        files = payload.get("files")
        if not isinstance(files, list):
            files = []
        file_snippet = ", ".join(str(f) for f in files[:5])
        prs = payload.get("pr_numbers")
        pr_text = ""
        if isinstance(prs, list) and prs:
            pr_text = f" PRs={','.join(str(p) for p in prs[:3])}."
        lines.append(f"- {evidence_id}: {summary}. Files={file_snippet}.{pr_text}")
    return "\n".join(lines)


def _build_draft_prompt(*, sha: str, release_tag: str, analysis: dict[str, Any]) -> tuple[str, str]:
    window = analysis.get("window", {})
    classifications = analysis.get("classifications", {})
    evidence_context = _build_evidence_context(analysis)

    system = (
        "You are a senior technical content writer for Catalyst Group Solutions. "
        "Write naturally, with concrete language and no hype. "
        "Return strict JSON only with no markdown code fences."
    )
    user = (
        "Create an SEO + GEO optimized deployment post for Catalyst Group Solutions.\n\n"
        f"Deployed SHA: {sha}\n"
        f"Release tag: {release_tag}\n"
        f"Window from last promoted SHA: {window.get('from_sha', '') or '(initial baseline)'}\n"
        f"Window to deployed SHA: {window.get('to_sha', sha)}\n"
        f"Commit count in window: {window.get('commit_count', 0)}\n\n"
        "Classification evidence buckets:\n"
        f"- customer_facing: {', '.join(classifications.get('customer_facing', [])) or '(none)'}\n"
        f"- operational: {', '.join(classifications.get('operational', [])) or '(none)'}\n"
        f"- risk_sensitive: {', '.join(classifications.get('risk_sensitive', [])) or '(none)'}\n"
        "- migration_notes: "
        f"{', '.join(classifications.get('migration_notes', [])) or '(none)'}\n\n"
        "Evidence entries:\n"
        f"{evidence_context}\n\n"
        "Return JSON with exactly these keys:\n"
        "- primary_keyword (string)\n"
        "- title (string, 40-70 chars)\n"
        "- meta_description (string, 140-160 chars)\n"
        "- slug (string)\n"
        "- excerpt (string)\n"
        "- content_markdown (string)\n"
        "- entities (string[])\n"
        "- claims (array of objects: statement, evidence_ids[])\n"
        "- faq (array of objects: question, answer, evidence_ids[])\n\n"
        "Hard constraints:\n"
        "- Must include headings: '## Quick Answers' and '## Sources and Evidence'\n"
        "- Use '(Evidence: EVxxx, EVyyy)' style in content_markdown for factual sections\n"
        "- Every claim and FAQ answer must reference evidence_ids from the provided list only\n"
        "- Include at least 3 H2 headings and at least 1 H3 heading\n"
        "- Explain customer impact, operational impact, and risk handling clearly\n"
        "- Avoid AI-sounding phrases and generic marketing buzzwords\n"
        "- No placeholders or TODO text\n"
    )
    return system, user


def _build_refine_prompt(draft: dict[str, Any], analysis: dict[str, Any]) -> str:
    evidence_context = _build_evidence_context(analysis)
    return (
        "Refine this deployment blog draft so it reads like a human engineering update "
        "from a real team.\n"
        "Keep all factual claims tied to evidence IDs exactly as provided.\n"
        "Return strict JSON with the same schema and keys.\n\n"
        f"Evidence entries:\n{evidence_context}\n\n"
        f"Draft JSON:\n{json.dumps(draft, indent=2)}"
    )


def _jsonld_bundle(
    blog: dict[str, Any],
    *,
    sha: str,
    release_tag: str,
    published_at: str,
) -> dict[str, Any]:
    title = str(blog.get("title", "")).strip()
    description = str(blog.get("meta_description", "")).strip()
    slug = _normalize_slug(str(blog.get("slug", "")).strip() or title)

    faq_entries: list[dict[str, Any]] = []
    faq = blog.get("faq")
    if isinstance(faq, list):
        for item in faq:
            if not isinstance(item, dict):
                continue
            q = str(item.get("question", "")).strip()
            a = str(item.get("answer", "")).strip()
            if not q or not a:
                continue
            faq_entries.append(
                {
                    "@type": "Question",
                    "name": q,
                    "acceptedAnswer": {
                        "@type": "Answer",
                        "text": a,
                    },
                }
            )

    return {
        "blog_posting": {
            "@context": "https://schema.org",
            "@type": "BlogPosting",
            "headline": title,
            "description": description,
            "datePublished": published_at,
            "dateModified": published_at,
            "author": {"@type": "Organization", "name": "Catalyst Group Solutions"},
            "publisher": {"@type": "Organization", "name": "Catalyst Group Solutions"},
            "identifier": f"{release_tag}:{sha}",
            "url": f"https://www.catalystgroupsolutions.com/blog/{slug}",
        },
        "faq_page": {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": faq_entries,
        },
    }


def _publish_blog(
    *,
    publish_url: str,
    publish_token: str,
    payload: dict[str, Any],
    sha: str,
) -> tuple[str, dict[str, Any]]:
    status, body = _post_json(
        publish_url,
        payload=payload,
        headers={
            "Authorization": f"Bearer {publish_token}",
            "Idempotency-Key": f"blog-{sha}",
        },
        timeout=60,
    )
    try:
        parsed = json.loads(body) if body else {}
        if not isinstance(parsed, dict):
            parsed = {"raw": body}
    except json.JSONDecodeError:
        parsed = {"raw": body}

    if status in {200, 201}:
        return "published", parsed
    if status == 409:
        return "duplicate", parsed
    raise PromotionError(f"CGS publish API failed (HTTP {status}): {body}")


def _generate_and_publish_blog(
    *,
    sha: str,
    repo: str,
    release_tag: str,
    analysis: dict[str, Any],
) -> dict[str, Any]:
    if not _is_enabled(os.environ.get("BLOG_PUBLISH_ENABLED")):
        return {
            "status": "skipped_disabled",
            "reason": "BLOG_PUBLISH_ENABLED=false",
        }

    publish_url = _require_env("CGS_BLOG_PUBLISH_URL")
    publish_token = _require_env("CGS_BLOG_PUBLISH_TOKEN")
    openai_key = _require_env("OPENAI_API_KEY")
    anthropic_key = _require_env("ANTHROPIC_API_KEY")

    primary_model = os.environ.get("BLOG_MODEL_PRIMARY", REQUIRED_PRIMARY_MODEL).strip()
    secondary_model = os.environ.get("BLOG_MODEL_SECONDARY", REQUIRED_SECONDARY_MODEL).strip()
    if primary_model != REQUIRED_PRIMARY_MODEL:
        raise PromotionError("BLOG_MODEL_PRIMARY must be set to gpt-5.2")
    if secondary_model != REQUIRED_SECONDARY_MODEL:
        raise PromotionError("BLOG_MODEL_SECONDARY must be set to claude-sonnet-4-6")

    system_prompt, user_prompt = _build_draft_prompt(
        sha=sha,
        release_tag=release_tag,
        analysis=analysis,
    )
    draft_raw = _openai_generate(
        api_key=openai_key,
        model=primary_model,
        system=system_prompt,
        user=user_prompt,
    )
    draft_json = _extract_json_object(draft_raw)

    refined_raw = _anthropic_refine(
        api_key=anthropic_key,
        model=secondary_model,
        prompt=_build_refine_prompt(draft_json, analysis),
    )
    blog = _extract_json_object(refined_raw)
    blog["slug"] = _normalize_slug(str(blog.get("slug", "")).strip() or str(blog.get("title", "")))

    evidence_map = analysis.get("evidence_map")
    if not isinstance(evidence_map, dict) or not evidence_map:
        raise PromotionError("Analysis evidence map is required for claim validation")
    evidence_ids = {str(key).upper() for key in evidence_map}

    _seo_validate(blog)
    _geo_validate(blog, evidence_ids=evidence_ids)

    published_at = _now_iso()
    json_ld = _jsonld_bundle(blog, sha=sha, release_tag=release_tag, published_at=published_at)

    publish_payload = {
        "idempotency_key": f"blog-{sha}",
        "source": "zetherion-windows-post-deploy",
        "sha": sha,
        "repo": repo,
        "release_tag": release_tag,
        "title": blog["title"],
        "slug": blog["slug"],
        "meta_description": blog["meta_description"],
        "excerpt": blog.get("excerpt", ""),
        "primary_keyword": blog["primary_keyword"],
        "content_markdown": blog["content_markdown"],
        "json_ld": json_ld,
        "models": {"draft": primary_model, "refine": secondary_model},
        "published_at": published_at,
    }

    publish_status, publish_response = _publish_blog(
        publish_url=publish_url,
        publish_token=publish_token,
        payload=publish_payload,
        sha=sha,
    )
    return {
        "status": publish_status,
        "models": {"draft": primary_model, "refine": secondary_model},
        "blog": {
            "title": blog["title"],
            "slug": blog["slug"],
            "primary_keyword": blog["primary_keyword"],
            "entities": blog.get("entities", []),
            "claims": blog.get("claims", []),
            "faq": blog.get("faq", []),
        },
        "publish_response": publish_response,
        "json_ld": json_ld,
    }


def _run_release_increment(
    *,
    deploy_path: Path,
    repo: str,
    sha: str,
    output_path: Path,
) -> dict[str, Any]:
    env = os.environ.copy()
    if "GITHUB_TOKEN" not in env and "GH_TOKEN" in env:
        env["GITHUB_TOKEN"] = env["GH_TOKEN"]
    if "GH_TOKEN" not in env and "GITHUB_TOKEN" in env:
        env["GH_TOKEN"] = env["GITHUB_TOKEN"]
    if not env.get("GITHUB_TOKEN") and not env.get("GH_TOKEN"):
        raise PromotionError("Missing GitHub token for release increment (GITHUB_TOKEN/GH_TOKEN)")

    cmd = [
        sys.executable,
        str(deploy_path / "scripts" / "release-auto-increment.py"),
        "--sha",
        sha,
        "--repo",
        repo,
        "--output",
        str(output_path),
    ]
    proc = _run_command(cmd, cwd=deploy_path, env=env)
    if proc.returncode != 0:
        raise PromotionError(
            "Release increment failed: "
            f"{proc.stdout.strip() or ''} {proc.stderr.strip() or ''}".strip()
        )

    payload = _read_json(output_path, default={})
    status = str(payload.get("status", ""))
    if status not in RELEASE_SUCCESS_STATUSES:
        raise PromotionError(f"Unexpected release status: {status!r}")
    return payload


@dataclass
class PipelinePaths:
    analysis_path: Path
    receipt_path: Path
    state_path: Path
    stage_blog_path: Path
    stage_release_path: Path


def _build_paths(*, data_root: Path, sha: str) -> PipelinePaths:
    analysis_path = data_root / "analysis" / f"{sha}.json"
    receipt_path = data_root / "receipts" / f"{sha}.json"
    state_path = data_root / "state.json"
    stage_blog_path = data_root / "receipts" / f"{sha}.blog.json"
    stage_release_path = data_root / "receipts" / f"{sha}.release.json"
    return PipelinePaths(
        analysis_path=analysis_path,
        receipt_path=receipt_path,
        state_path=state_path,
        stage_blog_path=stage_blog_path,
        stage_release_path=stage_release_path,
    )


def _update_state_success(state_path: Path, *, sha: str, receipt: dict[str, Any]) -> None:
    state = _read_json(state_path, default={})
    promotions = state.get("promotions")
    if not isinstance(promotions, dict):
        promotions = {}
    promotions[sha] = {
        "status": "success",
        "updated_at": _now_iso(),
        "blog_status": receipt.get("blog", {}).get("status"),
        "release_status": receipt.get("release", {}).get("status"),
    }

    state["last_promoted_sha"] = sha
    state["updated_at"] = _now_iso()
    state["promotions"] = promotions
    _write_json(state_path, state)


def _update_state_partial(state_path: Path, *, sha: str, receipt: dict[str, Any]) -> None:
    state = _read_json(state_path, default={})
    promotions = state.get("promotions")
    if not isinstance(promotions, dict):
        promotions = {}
    promotions[sha] = {
        "status": receipt.get("status", "partial_failure"),
        "updated_at": _now_iso(),
        "blog_status": receipt.get("blog", {}).get("status"),
        "release_status": receipt.get("release", {}).get("status"),
        "last_error": receipt.get("last_error", ""),
    }
    state["updated_at"] = _now_iso()
    state["promotions"] = promotions
    _write_json(state_path, state)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sha", required=True, help="Deployed SHA to promote")
    parser.add_argument("--deploy-path", default=r"C:\ZetherionAI")
    parser.add_argument("--deployment-receipt", required=True)
    parser.add_argument("--data-root", default=r"C:\ZetherionAI\data\promotions")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    args = parser.parse_args()

    sha = _normalize_sha(args.sha)
    if not sha:
        print("ERROR: --sha cannot be empty")
        return 1

    deploy_path = Path(args.deploy_path)
    if not deploy_path.exists():
        print(f"ERROR: deploy path does not exist: {deploy_path}")
        return 1

    paths = _build_paths(data_root=Path(args.data_root), sha=sha)

    existing_receipt = _read_json(paths.receipt_path, default={})
    if existing_receipt.get("status") == "success":
        print(json.dumps(existing_receipt))
        return 0

    repo = args.repo.strip() if args.repo else ""
    if not repo:
        repo = _infer_repo_from_git(deploy_path)

    # Validate deployment receipt contract at runtime before any promotion work.
    validate_proc = _run_command(
        [
            sys.executable,
            str(deploy_path / "scripts" / "validate-deployment-receipt.py"),
            "--receipt",
            str(Path(args.deployment_receipt)),
            "--expected-sha",
            sha,
        ],
        cwd=deploy_path,
    )
    if validate_proc.returncode != 0:
        print(validate_proc.stdout.strip() or validate_proc.stderr.strip())
        print("ERROR: deployment receipt validation failed; promotions aborted")
        return RETRYABLE_EXIT_CODE

    github_token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    if not github_token:
        print("ERROR: Missing GITHUB_TOKEN/GH_TOKEN for merge intelligence + release")
        return RETRYABLE_EXIT_CODE

    state = _read_json(paths.state_path, default={})
    previous_sha = _normalize_sha(str(state.get("last_promoted_sha", "")))

    try:
        release_receipt = existing_receipt.get("release", {})
        blog_receipt = existing_receipt.get("blog", {})

        analysis = _build_merge_intelligence(
            repo_path=deploy_path,
            repo=repo,
            sha=sha,
            previous_sha=previous_sha,
            github_token=github_token,
        )
        _write_json(paths.analysis_path, analysis)

        release_done = str(release_receipt.get("status", "")) in RELEASE_SUCCESS_STATUSES
        blog_done = str(blog_receipt.get("status", "")) in BLOG_SUCCESS_STATUSES

        if not release_done:
            release_payload = _run_release_increment(
                deploy_path=deploy_path,
                repo=repo,
                sha=sha,
                output_path=paths.stage_release_path,
            )
            release_receipt = release_payload

        release_tag = str(release_receipt.get("tag", "")).strip()
        if not release_tag:
            # This should only happen when release auto increment is disabled.
            release_tag = "v0.0.0"

        if not blog_done:
            blog_payload = _generate_and_publish_blog(
                sha=sha,
                repo=repo,
                release_tag=release_tag,
                analysis=analysis,
            )
            blog_receipt = blog_payload
            _write_json(paths.stage_blog_path, blog_payload)

        final_receipt: dict[str, Any] = {
            "generated_at": _now_iso(),
            "sha": sha,
            "repo": repo,
            "analysis_path": str(paths.analysis_path),
            "blog": blog_receipt,
            "release": release_receipt,
        }

        blog_status = str(blog_receipt.get("status", ""))
        release_status = str(release_receipt.get("status", ""))
        if blog_status in BLOG_SUCCESS_STATUSES and release_status in RELEASE_SUCCESS_STATUSES:
            final_receipt["status"] = "success"
            final_receipt["retryable"] = False
            _write_json(paths.receipt_path, final_receipt)
            _update_state_success(paths.state_path, sha=sha, receipt=final_receipt)
            print(json.dumps(final_receipt))
            return 0

        final_receipt["status"] = "partial_failure"
        final_receipt["retryable"] = True
        final_receipt["last_error"] = "Promotion incomplete: pending blog or release stage"
        _write_json(paths.receipt_path, final_receipt)
        _update_state_partial(paths.state_path, sha=sha, receipt=final_receipt)
        print(json.dumps(final_receipt))
        return RETRYABLE_EXIT_CODE
    except PromotionError as exc:
        failure_receipt = {
            "generated_at": _now_iso(),
            "sha": sha,
            "repo": repo,
            "analysis_path": str(paths.analysis_path),
            "status": "failed_retryable",
            "retryable": True,
            "last_error": str(exc),
            "blog": blog_receipt,
            "release": release_receipt,
        }
        _write_json(paths.receipt_path, failure_receipt)
        _update_state_partial(paths.state_path, sha=sha, receipt=failure_receipt)
        print(f"ERROR: {exc}")
        return RETRYABLE_EXIT_CODE


if __name__ == "__main__":
    raise SystemExit(main())
