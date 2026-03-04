"""Unit tests for CGS gateway tenant mutation rate limiter."""

from __future__ import annotations

from unittest.mock import patch

from zetherion_ai.cgs_gateway.rate_limit import TenantMutationRateLimiter


def test_rate_limiter_first_request_allows_and_consumes_token() -> None:
    limiter = TenantMutationRateLimiter(default_limit_per_minute=3)

    with patch("time.monotonic", return_value=100.0):
        allowed, retry_after = limiter.check(tenant_id="tenant-a", family="documents")

    assert allowed is True
    assert retry_after == 0


def test_rate_limiter_blocks_when_bucket_empty_then_recovers_after_refill() -> None:
    limiter = TenantMutationRateLimiter(default_limit_per_minute=1)

    with patch("time.monotonic", side_effect=[100.0, 101.0, 161.0]):
        first_allowed, _ = limiter.check(tenant_id="tenant-a", family="documents")
        second_allowed, retry_after = limiter.check(tenant_id="tenant-a", family="documents")
        third_allowed, third_retry_after = limiter.check(tenant_id="tenant-a", family="documents")

    assert first_allowed is True
    assert second_allowed is False
    assert retry_after > 0
    assert third_allowed is True
    assert third_retry_after == 0


def test_rate_limiter_family_specific_limit_and_normalization() -> None:
    limiter = TenantMutationRateLimiter(
        default_limit_per_minute=10,
        family_limits_per_minute={" admin ": 1, "documents": 2},
    )

    with patch("time.monotonic", side_effect=[200.0, 201.0, 202.0]):
        first, _ = limiter.check(tenant_id=" tenant-a ", family=" ADMIN ")
        second, retry_after = limiter.check(tenant_id="tenant-a", family="admin")
        third, _ = limiter.check(tenant_id="tenant-a", family="documents")

    assert first is True
    assert second is False
    assert retry_after >= 1
    assert third is True
