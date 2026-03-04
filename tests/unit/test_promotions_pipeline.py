"""Unit tests for Windows promotions pipeline blog publish contract handling."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _load_module():
    module_path = (
        Path(__file__).resolve().parents[2] / "scripts" / "windows" / "promotions-pipeline.py"
    )
    spec = importlib.util.spec_from_file_location("promotions_pipeline_test_module", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def promotions_module():
    return _load_module()


def _base_payload(sha: str) -> dict[str, object]:
    return {
        "idempotency_key": f"blog-{sha}",
        "sha": sha,
        "source": "zetherion-windows-post-deploy",
        "repo": "jimtin/zetherion-ai",
        "release_tag": "v0.4.9",
        "title": "Test title",
        "slug": "test-title",
        "meta_description": "Test description",
        "excerpt": "",
        "primary_keyword": "testing",
        "content_markdown": "## Quick Answers\n## Sources and Evidence\n",
        "json_ld": {"@context": "https://schema.org"},
        "models": {"draft": "gpt-5.2", "refine": "claude-sonnet-4-6"},
        "published_at": "2026-03-04T07:00:00Z",
    }


def test_publish_blog_requires_cgs_endpoint_suffix(promotions_module):
    sha = "3b43aa22e163ed3d44edc3625816da5f634d6385"
    with pytest.raises(promotions_module.PromotionError, match="must end with"):
        promotions_module._publish_blog(
            publish_url="https://cgs.example.com/not-correct",
            publish_token="token",
            payload=_base_payload(sha),
            sha=sha,
        )


def test_publish_blog_rejects_idempotency_mismatch(promotions_module):
    sha = "3b43aa22e163ed3d44edc3625816da5f634d6385"
    payload = _base_payload(sha)
    payload["idempotency_key"] = "blog-deadbeef"

    with pytest.raises(promotions_module.PromotionError, match="must equal blog-<sha>"):
        promotions_module._publish_blog(
            publish_url="https://cgs.example.com/service/ai/v1/internal/blog/publish",
            publish_token="token",
            payload=payload,
            sha=sha,
        )


def test_publish_blog_accepts_first_publish_envelope(monkeypatch, promotions_module):
    sha = "3b43aa22e163ed3d44edc3625816da5f634d6385"
    captured = {}

    def fake_post_json(url, *, payload, headers, timeout):
        captured["url"] = url
        captured["payload"] = payload
        captured["headers"] = headers
        captured["timeout"] = timeout
        return (
            201,
            (
                '{"request_id":"req_cgs_1","data":{"status":"published","receipt_id":"r_123"},'
                '"error":null}'
            ),
        )

    monkeypatch.setattr(promotions_module, "_post_json", fake_post_json)

    status, response = promotions_module._publish_blog(
        publish_url="https://cgs.example.com/service/ai/v1/internal/blog/publish",
        publish_token="token",
        payload=_base_payload(sha),
        sha=sha,
    )

    assert status == "published"
    assert response["http_status"] == 201
    assert response["request_id"] == "req_cgs_1"
    assert response["receipt_id"] == "r_123"
    assert captured["headers"]["Idempotency-Key"] == f"blog-{sha}"
    assert captured["headers"]["X-Request-Id"].startswith("req_")


def test_publish_blog_accepts_duplicate_success_envelope(monkeypatch, promotions_module):
    sha = "3b43aa22e163ed3d44edc3625816da5f634d6385"

    def fake_post_json(url, *, payload, headers, timeout):
        return (
            409,
            (
                '{"request_id":"req_dup","data":{"status":"duplicate","receipt_id":"r_dup"},'
                '"error":null}'
            ),
        )

    monkeypatch.setattr(promotions_module, "_post_json", fake_post_json)

    status, response = promotions_module._publish_blog(
        publish_url="https://cgs.example.com/service/ai/v1/internal/blog/publish",
        publish_token="token",
        payload=_base_payload(sha),
        sha=sha,
    )

    assert status == "duplicate"
    assert response["http_status"] == 409
    assert response["request_id"] == "req_dup"


def test_publish_blog_rejects_idempotency_conflict(monkeypatch, promotions_module):
    sha = "3b43aa22e163ed3d44edc3625816da5f634d6385"

    def fake_post_json(url, *, payload, headers, timeout):
        return (
            409,
            (
                '{"request_id":"req_conflict","data":null,'
                '"error":{"code":"AI_IDEMPOTENCY_CONFLICT","message":"conflict","retryable":false}}'
            ),
        )

    monkeypatch.setattr(promotions_module, "_post_json", fake_post_json)

    with pytest.raises(promotions_module.PromotionError, match="idempotency conflict") as exc_info:
        promotions_module._publish_blog(
            publish_url="https://cgs.example.com/service/ai/v1/internal/blog/publish",
            publish_token="token",
            payload=_base_payload(sha),
            sha=sha,
        )
    assert exc_info.value.retryable is False


def test_publish_blog_rejects_hard_auth_failures(monkeypatch, promotions_module):
    sha = "3b43aa22e163ed3d44edc3625816da5f634d6385"

    def fake_post_json(url, *, payload, headers, timeout):
        return (
            401,
            (
                '{"request_id":"req_auth","data":null,'
                '"error":{"code":"AI_AUTH_MISSING","message":"no auth","retryable":false}}'
            ),
        )

    monkeypatch.setattr(promotions_module, "_post_json", fake_post_json)

    with pytest.raises(promotions_module.PromotionError, match="hard failure") as exc_info:
        promotions_module._publish_blog(
            publish_url="https://cgs.example.com/service/ai/v1/internal/blog/publish",
            publish_token="token",
            payload=_base_payload(sha),
            sha=sha,
        )
    assert exc_info.value.retryable is False


def test_publish_blog_marks_http_429_as_retryable(monkeypatch, promotions_module):
    sha = "3b43aa22e163ed3d44edc3625816da5f634d6385"

    def fake_post_json(url, *, payload, headers, timeout):
        return (
            429,
            (
                '{"request_id":"req_rl","data":null,'
                '"error":{"code":"AI_RATE_LIMITED","message":"slow down","retryable":true}}'
            ),
        )

    monkeypatch.setattr(promotions_module, "_post_json", fake_post_json)

    with pytest.raises(promotions_module.PromotionError, match="retryable failure") as exc_info:
        promotions_module._publish_blog(
            publish_url="https://cgs.example.com/service/ai/v1/internal/blog/publish",
            publish_token="token",
            payload=_base_payload(sha),
            sha=sha,
        )
    assert exc_info.value.retryable is True
