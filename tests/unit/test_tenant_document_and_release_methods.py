"""Coverage tests for tenant document/release analytics helper methods."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock

import pytest

from zetherion_ai.api.tenant import TenantManager


@pytest.fixture()
def tenant_manager() -> TenantManager:
    manager = TenantManager("postgresql://user:pass@localhost:5432/db")
    manager._fetchrow = AsyncMock()  # type: ignore[method-assign]
    manager._fetch = AsyncMock()  # type: ignore[method-assign]
    manager._fetchval = AsyncMock()  # type: ignore[method-assign]
    manager._execute = AsyncMock()  # type: ignore[method-assign]
    return manager


@pytest.mark.asyncio
async def test_document_upload_and_document_crud_methods(tenant_manager: TenantManager) -> None:
    tenant_manager._fetchrow.side_effect = [  # type: ignore[attr-defined]
        {"upload_id": "up-1"},
        {"upload_id": "up-1"},
        {"document_id": "doc-1"},
        {"document_id": "doc-1"},
        {"job_id": "job-1"},
    ]
    tenant_manager._fetch.return_value = [{"document_id": "doc-1"}]  # type: ignore[attr-defined]

    upload = await tenant_manager.create_document_upload(
        "tenant-1",
        upload_id="up-1",
        file_name="report.pdf",
        mime_type="application/pdf",
        size_bytes=123,
        metadata={"source": "portal"},
        expires_at=datetime.now(UTC),
    )
    assert upload["upload_id"] == "up-1"
    create_upload_args = tenant_manager._fetchrow.call_args_list[0].args  # type: ignore[attr-defined]
    assert json.loads(create_upload_args[6]) == {"source": "portal"}

    fetched_upload = await tenant_manager.get_document_upload("tenant-1", "up-1")
    assert fetched_upload == {"upload_id": "up-1"}

    await tenant_manager.mark_document_upload_completed(
        "tenant-1",
        upload_id="up-1",
        document_id="doc-1",
    )
    tenant_manager._execute.assert_awaited()  # type: ignore[attr-defined]

    created_document = await tenant_manager.create_document(
        "tenant-1",
        document_id="doc-1",
        file_name="report.pdf",
        mime_type="application/pdf",
        object_key="documents/tenant-1/doc-1/report.pdf",
        status="uploaded",
        size_bytes=123,
        checksum_sha256="abc",
        metadata={"tag": "legal"},
    )
    assert created_document["document_id"] == "doc-1"

    fetched_document = await tenant_manager.get_document("tenant-1", "doc-1")
    assert fetched_document == {"document_id": "doc-1"}

    docs = await tenant_manager.list_documents("tenant-1", limit=10)
    assert docs == [{"document_id": "doc-1"}]
    default_list_sql = tenant_manager._fetch.call_args_list[0].args[0]  # type: ignore[attr-defined]
    assert "status NOT IN ('archiving', 'archived', 'purged')" in default_list_sql

    docs_with_archived = await tenant_manager.list_documents(
        "tenant-1",
        limit=10,
        include_archived=True,
    )
    assert docs_with_archived == [{"document_id": "doc-1"}]
    include_archived_sql = tenant_manager._fetch.call_args_list[1].args[0]  # type: ignore[attr-defined]
    assert "status NOT IN ('archiving', 'archived', 'purged')" not in include_archived_sql

    await tenant_manager.update_document_status(
        "tenant-1",
        document_id="doc-1",
        status="failed",
        error_message="boom",
    )
    await tenant_manager.update_document_index_payload(
        "tenant-1",
        document_id="doc-1",
        extracted_text="hello",
        preview_html="<html/>",
        chunk_count=2,
        status="indexed",
        error_message=None,
    )

    job = await tenant_manager.create_document_ingestion_job(
        "tenant-1",
        document_id="doc-1",
        status="processing",
    )
    assert job["job_id"] == "job-1"
    await tenant_manager.update_document_ingestion_job(
        "tenant-1",
        job_id="job-1",
        status="indexed",
        error_message=None,
    )


@pytest.mark.asyncio
async def test_document_archive_job_and_lifecycle_methods(tenant_manager: TenantManager) -> None:
    now = datetime.now(UTC)
    purge_after = datetime.now(UTC)

    tenant_manager._fetchrow.side_effect = [  # type: ignore[attr-defined]
        {"job_id": "archive-job-1", "status": "queued"},
        {"document_id": "doc-1", "status": "archiving", "archived_reason": "user-request"},
        {"document_id": "doc-1", "status": "archived", "purge_after": purge_after},
        {"document_id": "doc-1", "status": "processing"},
        {"document_id": "doc-1", "status": "purged", "purged_at": now},
    ]
    tenant_manager._fetch.side_effect = [  # type: ignore[attr-defined]
        [{"job_id": "archive-job-1", "status": "running"}],
        [{"document_id": "doc-1", "status": "archived"}],
    ]

    archive_job = await tenant_manager.create_document_archive_job(
        "tenant-1",
        document_id="doc-1",
        status="queued",
    )
    assert archive_job["job_id"] == "archive-job-1"

    claimed_jobs = await tenant_manager.claim_document_archive_jobs(limit=5)
    assert claimed_jobs == [{"job_id": "archive-job-1", "status": "running"}]

    await tenant_manager.mark_document_archive_job_succeeded("tenant-1", job_id="archive-job-1")
    await tenant_manager.mark_document_archive_job_failed(
        "tenant-1",
        job_id="archive-job-1",
        error_message="temporary failure",
        next_attempt_at=now,
    )

    archiving = await tenant_manager.mark_document_archiving(
        "tenant-1",
        document_id="doc-1",
        archived_reason="user-request",
    )
    assert archiving == {
        "document_id": "doc-1",
        "status": "archiving",
        "archived_reason": "user-request",
    }

    archived = await tenant_manager.mark_document_archived(
        "tenant-1",
        document_id="doc-1",
        archived_at=now,
        purge_after=purge_after,
    )
    assert archived == {"document_id": "doc-1", "status": "archived", "purge_after": purge_after}

    restoring = await tenant_manager.mark_document_restoring(
        "tenant-1",
        document_id="doc-1",
    )
    assert restoring == {"document_id": "doc-1", "status": "processing"}

    purged = await tenant_manager.mark_document_purged(
        "tenant-1",
        document_id="doc-1",
        purged_at=now,
    )
    assert purged == {"document_id": "doc-1", "status": "purged", "purged_at": now}

    due = await tenant_manager.list_documents_due_for_purge(limit=10)
    assert due == [{"document_id": "doc-1", "status": "archived"}]

    execute_sql_calls = [call.args[0] for call in tenant_manager._execute.call_args_list]  # type: ignore[attr-defined]
    assert any("status = 'succeeded'" in sql for sql in execute_sql_calls)
    assert any("status = 'failed'" in sql for sql in execute_sql_calls)


@pytest.mark.asyncio
async def test_release_marker_and_nonce_methods(tenant_manager: TenantManager) -> None:
    tenant_manager._fetchrow.return_value = {"marker_id": "m-1"}  # type: ignore[attr-defined]
    marker = await tenant_manager.add_release_marker(
        "tenant-1",
        source="deploy",
        environment="production",
        commit_sha="abc123",
        branch="main",
        tag_name="v1.2.3",
        metadata={"runner": "windows"},
    )
    assert marker["marker_id"] == "m-1"

    tenant_manager._fetch.return_value = [{"marker_id": "m-1"}]  # type: ignore[attr-defined]
    markers = await tenant_manager.get_release_markers("tenant-1", limit=5)
    assert markers == [{"marker_id": "m-1"}]

    tenant_manager._execute.side_effect = ["DELETE 1", "INSERT 0 1"]  # type: ignore[attr-defined]
    assert (
        await tenant_manager.register_release_nonce(
            "tenant-1",
            nonce="nonce-1",
            signature="sig",
        )
        is True
    )
    tenant_manager._execute.side_effect = ["DELETE 1", "INSERT 0 0"]  # type: ignore[attr-defined]
    assert await tenant_manager.register_release_nonce("tenant-1", nonce="nonce-1") is False


@pytest.mark.asyncio
async def test_prune_and_funnel_methods(tenant_manager: TenantManager) -> None:
    tenant_manager._fetchval.return_value = 7  # type: ignore[attr-defined]
    deleted_count = await tenant_manager.prune_web_events("tenant-1", retention_days=30)
    assert deleted_count == 7

    tenant_manager._fetch.return_value = [  # type: ignore[attr-defined]
        {"object_key": "replay/a"},
        {"object_key": "replay/b"},
        {"object_key": None},
    ]
    removed = await tenant_manager.prune_replay_chunks("tenant-1", retention_days=14)
    assert removed == ["replay/a", "replay/b"]

    tenant_manager._fetchrow.return_value = {"stage_name": "qualified"}  # type: ignore[attr-defined]
    stage = await tenant_manager.upsert_funnel_stage_daily(
        "tenant-1",
        metric_date=date(2026, 3, 1),
        funnel_name="signup",
        stage_name="qualified",
        stage_order=2,
        users_count=15,
        drop_off_rate=0.2,
        conversion_rate=0.8,
        metadata={"channel": "organic"},
    )
    assert stage["stage_name"] == "qualified"

    tenant_manager._fetch.return_value = [{"metric_date": date(2026, 3, 1)}]  # type: ignore[attr-defined]
    filtered = await tenant_manager.get_funnel_daily(
        "tenant-1",
        metric_date=date(2026, 3, 1),
    )
    assert filtered == [{"metric_date": date(2026, 3, 1)}]

    unfiltered = await tenant_manager.get_funnel_daily("tenant-1", metric_date=None, limit=10)
    assert unfiltered == [{"metric_date": date(2026, 3, 1)}]


@pytest.mark.asyncio
async def test_recommendation_methods_and_feedback_status_mapping(
    tenant_manager: TenantManager,
) -> None:
    tenant_manager._fetchrow.side_effect = [  # type: ignore[attr-defined]
        {"recommendation_id": "rec-1"},
        {"feedback_id": "fb-1"},
        {"feedback_id": "fb-2"},
    ]
    recommendation = await tenant_manager.create_recommendation(
        "tenant-1",
        recommendation_type="retention",
        title="Improve onboarding",
        description="Add activation emails",
        evidence={"drop_off": 0.4},
        risk_class="medium",
        confidence=0.8,
        expected_impact=0.2,
        status="open",
        source="detector",
    )
    assert recommendation["recommendation_id"] == "rec-1"

    tenant_manager._fetch.return_value = [  # type: ignore[attr-defined]
        {"recommendation_id": "rec-1"},
        {"recommendation_id": "rec-2"},
    ]
    filtered = await tenant_manager.list_recommendations("tenant-1", status="open", limit=5)
    assert len(filtered) == 2
    unfiltered = await tenant_manager.list_recommendations("tenant-1", status=None, limit=5)
    assert len(unfiltered) == 2

    feedback_accepted = await tenant_manager.add_recommendation_feedback(
        "tenant-1",
        "rec-1",
        feedback_type="accepted",
        note="looks good",
        actor="ops",
    )
    assert feedback_accepted["feedback_id"] == "fb-1"

    feedback_observe = await tenant_manager.add_recommendation_feedback(
        "tenant-1",
        "rec-1",
        feedback_type="needs-review",
        note=None,
        actor=None,
    )
    assert feedback_observe["feedback_id"] == "fb-2"
