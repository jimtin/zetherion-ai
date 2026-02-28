#!/usr/bin/env python3
"""Generate and publish a post-deploy CGS blog post for a deployed main SHA."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from typing import Any

REQUIRED_PRIMARY_MODEL = "gpt-5.2"
REQUIRED_SECONDARY_MODEL = "claude-sonnet-4-6"

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


class BlogError(RuntimeError):
    pass


def _is_enabled(raw: str | None) -> bool:
    if raw is None:
        return True
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _extract_json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    else:
        inline = re.search(r"\{.*\}", text, re.DOTALL)
        if inline:
            text = inline.group(0)

    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise BlogError("Model output was not a JSON object")
    return parsed


def _post_json(
    url: str,
    *,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: int = 60,
) -> tuple[int, str]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Content-Type", "application/json")
    for key, value in headers.items():
        request.add_header(key, value)

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            return response.status, body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return exc.code, body


def _openai_generate(*, api_key: str, model: str, system: str, user: str) -> str:
    status, body = _post_json(
        "https://api.openai.com/v1/responses",
        payload={
            "model": model,
            "input": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.6,
            "max_output_tokens": 2800,
        },
        headers={
            "Authorization": f"Bearer {api_key}",
        },
    )
    if status >= 400:
        raise BlogError(f"OpenAI draft generation failed (HTTP {status}): {body}")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise BlogError("OpenAI response was not JSON") from exc

    if isinstance(payload.get("output_text"), str) and payload["output_text"].strip():
        return str(payload["output_text"])

    output = payload.get("output")
    if isinstance(output, list):
        fragments: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") in {"output_text", "text"}:
                    text = block.get("text")
                    if isinstance(text, str):
                        fragments.append(text)
        if fragments:
            return "\n".join(fragments)

    raise BlogError("OpenAI response did not include draft text")


def _anthropic_refine(*, api_key: str, model: str, prompt: str) -> str:
    status, body = _post_json(
        "https://api.anthropic.com/v1/messages",
        payload={
            "model": model,
            "max_tokens": 2800,
            "temperature": 0.4,
            "messages": [{"role": "user", "content": prompt}],
        },
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    if status >= 400:
        raise BlogError(f"Anthropic refine generation failed (HTTP {status}): {body}")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise BlogError("Anthropic response was not JSON") from exc

    content = payload.get("content")
    if isinstance(content, list):
        text_parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str):
                    text_parts.append(text)
        if text_parts:
            return "\n".join(text_parts)

    raise BlogError("Anthropic response did not include refined text")


def _run_git(*args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise BlogError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def _commit_context(sha: str) -> dict[str, Any]:
    summary = _run_git("show", "--no-patch", "--pretty=format:%s", sha)
    body = _run_git("show", "--no-patch", "--pretty=format:%b", sha)
    files = _run_git("show", "--name-only", "--pretty=format:", sha)
    file_list = [line.strip() for line in files.splitlines() if line.strip()][:30]
    return {
        "summary": summary,
        "body": body,
        "files": file_list,
    }


def _normalize_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:100] or "zetherion-update"


def _seo_validate(blog: dict[str, Any]) -> None:
    title = str(blog.get("title", "")).strip()
    meta = str(blog.get("meta_description", "")).strip()
    keyword = str(blog.get("primary_keyword", "")).strip()
    content = str(blog.get("content_markdown", "")).strip()

    if len(title) < 40 or len(title) > 70:
        raise BlogError("SEO check failed: title length must be between 40 and 70 characters")
    if len(meta) < 140 or len(meta) > 160:
        raise BlogError("SEO check failed: meta description length must be 140-160 characters")
    if not keyword:
        raise BlogError("SEO check failed: primary_keyword is required")
    if not content:
        raise BlogError("SEO check failed: content_markdown is required")

    lower_keyword = keyword.lower()
    lower_title = title.lower()
    lower_content = content.lower()

    if lower_keyword not in lower_title:
        raise BlogError("SEO check failed: primary keyword must appear in title")

    first_120_words = " ".join(content.split()[:120]).lower()
    if lower_keyword not in first_120_words:
        raise BlogError("SEO check failed: primary keyword must appear in first 120 words")

    h2s = re.findall(r"^##\s+(.+)$", content, flags=re.MULTILINE)
    h3s = re.findall(r"^###\s+(.+)$", content, flags=re.MULTILINE)
    if len(h2s) < 3:
        raise BlogError("SEO check failed: content must contain at least 3 H2 headings")
    if not h3s:
        raise BlogError("SEO check failed: content must contain at least one H3 heading")
    if not any(lower_keyword in h.lower() for h in h2s):
        raise BlogError("SEO check failed: at least one H2 must include the primary keyword")

    for pattern in AI_SOUNDING_PATTERNS:
        if pattern in lower_content or pattern in lower_title:
            raise BlogError(
                "Quality check failed: banned AI-sounding phrase detected: " f"{pattern!r}"
            )


def _jsonld(
    blog: dict[str, Any],
    *,
    sha: str,
    release_tag: str,
    published_at: str,
) -> dict[str, Any]:
    title = str(blog.get("title", "")).strip()
    description = str(blog.get("meta_description", "")).strip()
    slug = str(blog.get("slug", "")).strip()
    if not slug:
        slug = _normalize_slug(title)

    return {
        "@context": "https://schema.org",
        "@type": "BlogPosting",
        "headline": title,
        "description": description,
        "datePublished": published_at,
        "dateModified": published_at,
        "author": {
            "@type": "Organization",
            "name": "Catalyst Group Solutions",
        },
        "publisher": {
            "@type": "Organization",
            "name": "Catalyst Group Solutions",
        },
        "identifier": f"{release_tag}:{sha}",
        "url": f"https://www.catalystgroupsolutions.com/blog/{slug}",
    }


def _build_draft_prompt(*, sha: str, release_tag: str, context: dict[str, Any]) -> tuple[str, str]:
    system = (
        "You are a senior technical content writer for Catalyst Group Solutions. "
        "Write in plain, human language with concrete examples and no hype. "
        "Output strict JSON only."
    )

    user = (
        "Write an SEO-focused release blog post for Catalyst Group Solutions. "
        "This post must explain what shipped and why it matters to clients.\n\n"
        f"Commit SHA: {sha}\n"
        f"Release tag: {release_tag}\n"
        f"Commit summary: {context.get('summary', '')}\n"
        f"Commit body: {context.get('body', '')}\n"
        f"Changed files: {', '.join(context.get('files', []))}\n\n"
        "Return JSON with fields:\n"
        "primary_keyword, title, meta_description, slug, excerpt, content_markdown\n\n"
        "Requirements:\n"
        "- non-AI-sounding, natural company voice\n"
        "- include why this release helps real client workflows\n"
        "- include at least 3 H2 headings and 1 H3 heading\n"
        "- include specific examples related to this release\n"
        "- no placeholders or TODOs\n"
        "- no markdown code fences around JSON\n"
    )
    return system, user


def _build_refine_prompt(draft: dict[str, Any]) -> str:
    return (
        "Refine this release blog draft to sound natural and human while preserving "
        "factual accuracy. "
        "Avoid generic AI phrasing, avoid buzzwords, and keep the writing grounded.\n\n"
        "Return JSON with the exact same fields and schema.\n\n"
        f"Draft JSON:\n{json.dumps(draft, indent=2)}"
    )


def _publish(
    *,
    publish_url: str,
    publish_token: str,
    payload: dict[str, Any],
    idempotency_key: str,
) -> tuple[str, dict[str, Any]]:
    status, body = _post_json(
        publish_url,
        payload=payload,
        headers={
            "Authorization": f"Bearer {publish_token}",
            "Idempotency-Key": idempotency_key,
        },
    )

    parsed: dict[str, Any]
    try:
        decoded = json.loads(body) if body else {}
        parsed = decoded if isinstance(decoded, dict) else {"raw": body}
    except json.JSONDecodeError:
        parsed = {"raw": body}

    if status in {200, 201}:
        return "published", parsed
    if status == 409:
        return "duplicate", parsed

    raise BlogError(f"CGS publish API failed (HTTP {status}): {body}")


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise BlogError(f"Missing required environment variable: {name}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--output", default="blog-publish-receipt.json")
    args = parser.parse_args()

    if not args.repo:
        print("ERROR: --repo is required (or set GITHUB_REPOSITORY)")
        return 1

    enabled = _is_enabled(os.environ.get("BLOG_PUBLISH_ENABLED"))
    if not enabled:
        receipt = {
            "status": "skipped_disabled",
            "sha": args.sha,
            "repo": args.repo,
            "release_tag": args.release_tag,
        }
        with open(args.output, "w", encoding="utf-8") as fh:
            json.dump(receipt, fh, indent=2)
        print(json.dumps(receipt))
        return 0

    try:
        publish_url = _require_env("CGS_BLOG_PUBLISH_URL")
        publish_token = _require_env("CGS_BLOG_PUBLISH_TOKEN")
        openai_key = _require_env("OPENAI_API_KEY")
        anthropic_key = _require_env("ANTHROPIC_API_KEY")

        primary_model = os.environ.get("BLOG_MODEL_PRIMARY", REQUIRED_PRIMARY_MODEL).strip()
        secondary_model = os.environ.get("BLOG_MODEL_SECONDARY", REQUIRED_SECONDARY_MODEL).strip()

        if primary_model != REQUIRED_PRIMARY_MODEL:
            raise BlogError("BLOG_MODEL_PRIMARY must be set to gpt-5.2 for post-deploy publishing")
        if secondary_model != REQUIRED_SECONDARY_MODEL:
            raise BlogError(
                "BLOG_MODEL_SECONDARY must be set to claude-sonnet-4-6 for post-deploy publishing"
            )

        context = _commit_context(args.sha)
        system_prompt, draft_prompt = _build_draft_prompt(
            sha=args.sha,
            release_tag=args.release_tag,
            context=context,
        )
        draft_text = _openai_generate(
            api_key=openai_key,
            model=primary_model,
            system=system_prompt,
            user=draft_prompt,
        )
        draft_json = _extract_json_object(draft_text)

        refined_text = _anthropic_refine(
            api_key=anthropic_key,
            model=secondary_model,
            prompt=_build_refine_prompt(draft_json),
        )
        blog = _extract_json_object(refined_text)

        title = str(blog.get("title", "")).strip()
        blog["slug"] = _normalize_slug(str(blog.get("slug", "")).strip() or title)
        _seo_validate(blog)

        now_iso = dt.datetime.now(dt.UTC).isoformat()
        json_ld = _jsonld(blog, sha=args.sha, release_tag=args.release_tag, published_at=now_iso)

        publish_payload = {
            "idempotency_key": f"blog-{args.sha}",
            "source": "zetherion-main-post-deploy",
            "sha": args.sha,
            "repo": args.repo,
            "release_tag": args.release_tag,
            "title": blog["title"],
            "slug": blog["slug"],
            "meta_description": blog["meta_description"],
            "excerpt": blog.get("excerpt", ""),
            "primary_keyword": blog["primary_keyword"],
            "content_markdown": blog["content_markdown"],
            "json_ld": json_ld,
            "models": {
                "draft": primary_model,
                "refine": secondary_model,
            },
            "published_at": now_iso,
        }

        publish_status, publish_response = _publish(
            publish_url=publish_url,
            publish_token=publish_token,
            payload=publish_payload,
            idempotency_key=f"blog-{args.sha}",
        )

        receipt = {
            "status": publish_status,
            "sha": args.sha,
            "repo": args.repo,
            "release_tag": args.release_tag,
            "models": {
                "draft": primary_model,
                "refine": secondary_model,
            },
            "blog": {
                "title": blog["title"],
                "slug": blog["slug"],
                "primary_keyword": blog["primary_keyword"],
            },
            "publish_response": publish_response,
        }
        with open(args.output, "w", encoding="utf-8") as fh:
            json.dump(receipt, fh, indent=2)

        print(json.dumps(receipt))
        return 0
    except BlogError as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
