"""Coverage tests for tenant document/release analytics helper methods."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock

import pytest

from zetherion_ai.api.auth import generate_api_key
from zetherion_ai.api.tenant import (
    TenantManager,
    _clean_json_dict,
    _clean_string_list,
    _normalise_notification_status,
)


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
async def test_tenant_crud_methods_cover_create_list_update_and_deactivate(
    tenant_manager: TenantManager,
) -> None:
    created_at = datetime.now(UTC)
    tenant_manager._fetchrow.side_effect = [  # type: ignore[attr-defined]
        {
            "tenant_id": "tenant-1",
            "name": "Tenant One",
            "domain": "example.com",
            "is_active": True,
            "rate_limit_rpm": 60,
            "config": {"region": "au"},
            "created_at": created_at,
            "updated_at": created_at,
        },
        {
            "tenant_id": "tenant-1",
            "name": "Tenant One",
            "domain": "example.com",
            "is_active": True,
            "rate_limit_rpm": 60,
            "config": {"region": "au"},
            "created_at": created_at,
            "updated_at": created_at,
        },
        {
            "tenant_id": "tenant-1",
            "name": "Tenant One Updated",
            "domain": "example.org",
            "is_active": True,
            "rate_limit_rpm": 60,
            "config": {"region": "us"},
            "created_at": created_at,
            "updated_at": created_at,
        },
        {
            "tenant_id": "tenant-1",
            "name": "Tenant One Updated",
            "domain": "example.org",
            "is_active": True,
            "rate_limit_rpm": 60,
            "config": {"region": "us"},
            "created_at": created_at,
            "updated_at": created_at,
        },
    ]
    tenant_manager._fetch.side_effect = [  # type: ignore[attr-defined]
        [
            {
                "tenant_id": "tenant-1",
                "name": "Tenant One",
                "domain": "example.com",
                "is_active": True,
                "rate_limit_rpm": 60,
                "config": {"region": "au"},
                "created_at": created_at,
                "updated_at": created_at,
            }
        ],
        [
            {
                "tenant_id": "tenant-1",
                "name": "Tenant One",
                "domain": "example.com",
                "is_active": True,
                "rate_limit_rpm": 60,
                "config": {"region": "au"},
                "created_at": created_at,
                "updated_at": created_at,
            },
            {
                "tenant_id": "tenant-2",
                "name": "Tenant Two",
                "domain": None,
                "is_active": False,
                "rate_limit_rpm": 60,
                "config": {},
                "created_at": created_at,
                "updated_at": created_at,
            },
        ],
    ]
    tenant_manager._execute.side_effect = [  # type: ignore[attr-defined]
        "INSERT 0 1",
        "INSERT 0 1",
        "INSERT 0 1",
        "UPDATE 1",
        "INSERT 0 1",
    ]

    tenant, plaintext_key = await tenant_manager.create_tenant(
        "Tenant One",
        domain="example.com",
        config={"region": "au"},
    )
    fetched = await tenant_manager.get_tenant("tenant-1")
    listed_active = await tenant_manager.list_tenants(active_only=True)
    listed_all = await tenant_manager.list_tenants(active_only=False)
    updated = await tenant_manager.update_tenant(
        "tenant-1",
        name="Tenant One Updated",
        domain="example.org",
        config={"region": "us"},
    )
    unchanged = await tenant_manager.update_tenant("tenant-1")
    deactivated = await tenant_manager.deactivate_tenant("tenant-1")

    assert tenant["tenant_id"] == "tenant-1"
    assert plaintext_key.startswith("sk_live_")
    create_args = tenant_manager._fetchrow.call_args_list[0].args  # type: ignore[attr-defined]
    assert json.loads(create_args[5]) == {"region": "au"}
    assert fetched is not None and fetched["name"] == "Tenant One"
    assert len(listed_active) == 1
    assert len(listed_all) == 2
    assert updated is not None and updated["domain"] == "example.org"
    assert unchanged is not None and unchanged["name"] == "Tenant One Updated"
    assert deactivated is True


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
async def test_session_message_memory_contact_and_web_methods_cover_branch_paths(
    tenant_manager: TenantManager,
) -> None:
    now = datetime.now(UTC)
    tenant_manager._fetchrow.side_effect = [  # type: ignore[attr-defined]
        {
            "web_session_id": "web-existing",
            "tenant_id": "tenant-1",
            "session_id": "session-1",
            "external_user_id": "user-1",
            "execution_mode": "live",
            "consent_replay": True,
            "replay_sampled": False,
            "started_at": now,
            "ended_at": None,
            "metadata": {"source": "existing"},
        },
        None,
        {
            "web_session_id": "web-created",
            "tenant_id": "tenant-1",
            "session_id": "session-2",
            "external_user_id": "user-2",
            "execution_mode": "test",
            "consent_replay": False,
            "replay_sampled": True,
            "started_at": now,
            "ended_at": None,
            "metadata": {"source": "new"},
        },
        {
            "web_session_id": "web-created",
            "tenant_id": "tenant-1",
            "session_id": "session-2",
            "external_user_id": "user-2",
            "execution_mode": "test",
            "consent_replay": False,
            "replay_sampled": True,
            "started_at": now,
            "ended_at": None,
            "metadata": {"source": "new"},
        },
        {
            "web_session_id": "web-created",
            "tenant_id": "tenant-1",
            "session_id": "session-2",
            "external_user_id": "user-2",
            "execution_mode": "test",
            "consent_replay": False,
            "replay_sampled": True,
            "started_at": now,
            "ended_at": now,
            "metadata": {"ended": True},
        },
        {
            "message_id": "msg-1",
            "session_id": "session-1",
            "tenant_id": "tenant-1",
            "execution_mode": "live",
            "role": "user",
            "content": "hello",
            "created_at": now,
        },
        {
            "memory_id": "mem-1",
            "tenant_id": "tenant-1",
            "memory_subject_id": "subject-1",
            "category": "profile",
            "memory_key": "timezone",
            "value": "Australia/Sydney",
            "confidence": 0.8,
            "source_session_id": "session-1",
            "created_at": now,
            "updated_at": now,
        },
        {
            "contact_id": "contact-1",
            "name": "Alice",
            "phone": "123",
            "tags": ["vip"],
            "custom_fields": {"source": "chat"},
        },
        {
            "contact_id": "contact-1",
            "tenant_id": "tenant-1",
            "name": "Alice",
            "email": "alice@example.com",
            "phone": "456",
            "source": "chat",
            "tags": ["vip", "trial"],
            "custom_fields": {"source": "chat", "plan": "pro"},
            "created_at": now,
            "updated_at": now,
        },
        None,
        {
            "contact_id": "contact-2",
            "tenant_id": "tenant-1",
            "name": "Bob",
            "email": "bob@example.com",
            "phone": None,
            "source": "chat",
            "tags": [],
            "custom_fields": {},
            "created_at": now,
            "updated_at": now,
        },
        {
            "interaction_id": "interaction-1",
            "tenant_id": "tenant-1",
            "contact_id": "contact-1",
            "session_id": "session-1",
            "interaction_type": "chat",
            "summary": "Asked a question",
            "entities": {},
            "sentiment": "positive",
            "intent": "support",
            "outcome": "answered",
            "created_at": now,
        },
        {
            "contact_id": "contact-1",
            "tenant_id": "tenant-1",
            "name": "Alice",
            "email": "alice@example.com",
            "phone": "456",
            "source": "chat",
            "tags": ["vip", "trial"],
            "custom_fields": {"source": "chat", "plan": "enterprise"},
            "created_at": now,
            "updated_at": now,
        },
        {
            "event_id": "evt-1",
            "tenant_id": "tenant-1",
            "web_session_id": "web-created",
            "session_id": "session-2",
            "execution_mode": "live",
            "event_type": "click",
            "event_name": "cta",
            "page_url": "/pricing",
            "element_selector": "#buy",
            "properties": {"plan": "pro"},
            "occurred_at": now,
        },
        {
            "chunk_id": "chunk-1",
            "tenant_id": "tenant-1",
            "web_session_id": "web-created",
            "sequence_no": 1,
            "object_key": "replays/chunk-1.json",
            "checksum_sha256": "abc",
            "chunk_size_bytes": 123,
            "metadata": {"codec": "json"},
            "created_at": now,
        },
        {
            "chunk_id": "chunk-1",
            "tenant_id": "tenant-1",
            "web_session_id": "web-created",
            "sequence_no": 1,
            "object_key": "replays/chunk-1.json",
            "checksum_sha256": "abc",
            "chunk_size_bytes": 123,
            "metadata": {"codec": "json"},
            "created_at": now,
        },
        {
            "chunk_id": "chunk-1",
            "tenant_id": "tenant-1",
            "web_session_id": "web-created",
            "sequence_no": 1,
            "object_key": "replays/chunk-1.json",
            "checksum_sha256": "abc",
            "chunk_size_bytes": 123,
            "metadata": {"codec": "json"},
            "created_at": now,
        },
    ]
    tenant_manager._fetch.side_effect = [  # type: ignore[attr-defined]
        [
            {
                "message_id": "msg-older",
                "session_id": "session-1",
                "execution_mode": "live",
                "role": "assistant",
                "content": "previous",
                "metadata": {},
                "created_at": now,
            },
            {
                "message_id": "msg-newer",
                "session_id": "session-1",
                "execution_mode": "live",
                "role": "user",
                "content": "latest",
                "metadata": {},
                "created_at": now,
            },
        ],
        [
            {
                "message_id": "msg-only",
                "session_id": "session-1",
                "execution_mode": "live",
                "role": "user",
                "content": "only",
                "metadata": {},
                "created_at": now,
            }
        ],
        [
            {
                "memory_id": "mem-1",
                "tenant_id": "tenant-1",
                "memory_subject_id": "subject-1",
                "category": "profile",
                "memory_key": "timezone",
                "value": "Australia/Sydney",
                "confidence": 0.8,
                "source_session_id": "session-1",
                "created_at": now,
                "updated_at": now,
            }
        ],
        [
            {
                "contact_id": "contact-1",
                "tenant_id": "tenant-1",
                "name": "Alice",
                "email": "alice@example.com",
                "phone": "456",
                "source": "chat",
                "tags": ["vip", "trial"],
                "custom_fields": {"source": "chat", "plan": "pro"},
                "created_at": now,
                "updated_at": now,
            }
        ],
        [
            {
                "contact_id": "contact-2",
                "tenant_id": "tenant-1",
                "name": "Bob",
                "email": "bob@example.com",
                "phone": None,
                "source": "chat",
                "tags": [],
                "custom_fields": {},
                "created_at": now,
                "updated_at": now,
            }
        ],
        [
            {
                "interaction_id": "interaction-1",
                "tenant_id": "tenant-1",
                "contact_id": "contact-1",
                "session_id": "session-1",
                "interaction_type": "chat",
                "summary": "Asked a question",
                "entities": {},
                "sentiment": "positive",
                "intent": "support",
                "outcome": "answered",
                "created_at": now,
            }
        ],
    ]
    tenant_manager._execute.side_effect = [None, "DELETE 1"]  # type: ignore[attr-defined]

    existing_web_session = await tenant_manager.ensure_web_session(
        "tenant-1",
        session_id="session-1",
        external_user_id="user-1",
        consent_replay=True,
    )
    created_web_session = await tenant_manager.ensure_web_session(
        "tenant-1",
        session_id="session-2",
        external_user_id="user-2",
        execution_mode="test",
        replay_sampled=True,
        metadata={"source": "new"},
    )
    fetched_web_session = await tenant_manager.get_web_session("tenant-1", "web-created")
    ended_web_session = await tenant_manager.end_web_session(
        "tenant-1",
        "web-created",
        ended_at=now,
        metadata_patch={"ended": True},
    )
    await tenant_manager.touch_session("session-1")
    deleted_session = await tenant_manager.delete_session("session-1", "tenant-1")
    message = await tenant_manager.add_message(
        "session-1",
        "tenant-1",
        "live",
        "user",
        "hello",
        metadata={"source": "chat"},
    )
    paged_messages = await tenant_manager.get_messages(
        "session-1",
        "tenant-1",
        limit=5,
        before_id="msg-cutoff",
    )
    recent_messages = await tenant_manager.get_messages("session-1", "tenant-1", limit=5)
    memory = await tenant_manager.upsert_subject_memory(
        tenant_id="tenant-1",
        memory_subject_id="subject-1",
        category="profile",
        memory_key="timezone",
        value="Australia/Sydney",
        source_session_id="session-1",
    )
    memories = await tenant_manager.list_subject_memories(
        tenant_id="tenant-1",
        memory_subject_id="subject-1",
        limit=10,
    )
    merged_contact = await tenant_manager.upsert_contact(
        "tenant-1",
        name="Alice",
        email="alice@example.com",
        phone="456",
        tags=["trial"],
        custom_fields={"plan": "pro"},
    )
    created_contact = await tenant_manager.upsert_contact(
        "tenant-1",
        name="Bob",
        email="bob@example.com",
    )
    interaction = await tenant_manager.add_interaction(
        "tenant-1",
        contact_id="contact-1",
        session_id="session-1",
        summary="Asked a question",
        sentiment="positive",
        intent="support",
        outcome="answered",
    )
    contacts_by_email = await tenant_manager.list_contacts(
        "tenant-1",
        email="alice@example.com",
        limit=10,
    )
    all_contacts = await tenant_manager.list_contacts("tenant-1", limit=10)
    interactions = await tenant_manager.get_interactions(
        "tenant-1",
        contact_id="contact-1",
        session_id="session-1",
        interaction_type="chat",
        limit=10,
    )
    patched_contact = await tenant_manager.update_contact_custom_fields(
        "tenant-1",
        "contact-1",
        {"plan": "enterprise"},
    )
    web_event = await tenant_manager.add_web_event(
        "tenant-1",
        web_session_id="web-created",
        session_id="session-2",
        event_type="click",
        event_name="cta",
        page_url="/pricing",
        element_selector="#buy",
        properties={"plan": "pro"},
        occurred_at=now,
    )
    replay_chunk = await tenant_manager.add_replay_chunk(
        "tenant-1",
        web_session_id="web-created",
        sequence_no=1,
        object_key="replays/chunk-1.json",
        checksum_sha256="abc",
        chunk_size_bytes=123,
        metadata={"codec": "json"},
    )
    latest_chunk = await tenant_manager.get_latest_replay_chunk(
        "tenant-1",
        web_session_id="web-created",
    )
    replay_chunk_by_seq = await tenant_manager.get_replay_chunk(
        "tenant-1",
        web_session_id="web-created",
        sequence_no=1,
    )

    assert existing_web_session["web_session_id"] == "web-existing"
    assert created_web_session["web_session_id"] == "web-created"
    assert fetched_web_session is not None
    assert fetched_web_session["web_session_id"] == "web-created"
    assert ended_web_session is not None and ended_web_session["metadata"] == {"ended": True}
    assert deleted_session is True
    assert message["message_id"] == "msg-1"
    assert [item["message_id"] for item in paged_messages] == ["msg-newer", "msg-older"]
    assert recent_messages == [
        {
            "message_id": "msg-only",
            "session_id": "session-1",
            "execution_mode": "live",
            "role": "user",
            "content": "only",
            "metadata": {},
            "created_at": now,
        }
    ]
    assert memory["memory_id"] == "mem-1"
    assert memories[0]["memory_key"] == "timezone"
    assert merged_contact["contact_id"] == "contact-1"
    assert merged_contact["tags"] == ["vip", "trial"]
    assert created_contact["contact_id"] == "contact-2"
    assert interaction["interaction_id"] == "interaction-1"
    assert contacts_by_email[0]["email"] == "alice@example.com"
    assert all_contacts[0]["contact_id"] == "contact-2"
    assert interactions[0]["intent"] == "support"
    assert patched_contact is not None and patched_contact["custom_fields"]["plan"] == "enterprise"
    assert web_event["event_id"] == "evt-1"
    assert replay_chunk["chunk_id"] == "chunk-1"
    assert latest_chunk is not None and latest_chunk["sequence_no"] == 1
    assert replay_chunk_by_seq is not None and replay_chunk_by_seq["chunk_id"] == "chunk-1"


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
async def test_session_context_and_subject_memory_methods(tenant_manager: TenantManager) -> None:
    now = datetime.now(UTC)
    tenant_manager._fetchrow.side_effect = [  # type: ignore[attr-defined]
        {
            "session_id": "session-1",
            "tenant_id": "tenant-1",
            "external_user_id": "visitor-1",
            "memory_subject_id": "subject-1",
            "execution_mode": "test",
            "test_profile_id": "profile-1",
            "conversation_summary": "",
            "created_at": now,
            "last_active": now,
            "expires_at": now,
        },
        {
            "memory_id": "memory-1",
            "tenant_id": "tenant-1",
            "memory_subject_id": "subject-1",
            "category": "preference",
            "memory_key": "response_style",
            "value": "brief",
            "confidence": 0.88,
            "source_session_id": "session-1",
            "created_at": now,
            "updated_at": now,
        },
    ]
    tenant_manager._fetch.return_value = [  # type: ignore[attr-defined]
        {
            "memory_id": "memory-1",
            "tenant_id": "tenant-1",
            "memory_subject_id": "subject-1",
            "category": "preference",
            "memory_key": "response_style",
            "value": "brief",
            "confidence": 0.88,
            "source_session_id": "session-1",
            "created_at": now,
            "updated_at": now,
        }
    ]

    session = await tenant_manager.create_session(
        "tenant-1",
        external_user_id="visitor-1",
        memory_subject_id="subject-1",
        execution_mode="test",
        test_profile_id="profile-1",
        metadata={"source": "widget"},
    )
    assert session["memory_subject_id"] == "subject-1"
    assert session["execution_mode"] == "test"
    assert session["test_profile_id"] == "profile-1"
    create_sql_args = tenant_manager._fetchrow.call_args_list[0].args  # type: ignore[attr-defined]
    assert "memory_subject_id" in create_sql_args[0]
    assert create_sql_args[4] == "test"
    assert create_sql_args[5] == "profile-1"
    assert json.loads(create_sql_args[6]) == {"source": "widget"}

    await tenant_manager.persist_session_context(
        session_id="session-1",
        tenant_id="tenant-1",
        memory_subject_id="subject-1",
        conversation_summary="Recent user requests: asked about pricing",
    )
    persist_sql = tenant_manager._execute.call_args_list[0].args[0]  # type: ignore[attr-defined]
    assert "conversation_summary" in persist_sql

    subject_memory = await tenant_manager.upsert_subject_memory(
        tenant_id="tenant-1",
        memory_subject_id="subject-1",
        category="preference",
        memory_key="response_style",
        value="brief",
        source_session_id="session-1",
        confidence=0.88,
    )
    assert subject_memory["memory_id"] == "memory-1"

    subject_memories = await tenant_manager.list_subject_memories(
        tenant_id="tenant-1",
        memory_subject_id="subject-1",
        limit=5,
    )
    assert subject_memories == [
        {
            "memory_id": "memory-1",
            "tenant_id": "tenant-1",
            "memory_subject_id": "subject-1",
            "category": "preference",
            "memory_key": "response_style",
            "value": "brief",
            "confidence": 0.88,
            "source_session_id": "session-1",
            "created_at": now,
            "updated_at": now,
        }
    ]


@pytest.mark.asyncio
async def test_api_key_registry_and_test_profile_methods(tenant_manager: TenantManager) -> None:
    now = datetime.now(UTC)
    live_key, live_prefix, live_hash = generate_api_key()
    tenant_manager._fetch.side_effect = [  # type: ignore[attr-defined]
        [
            {
                "tenant_id": "tenant-1",
                "name": "Tenant",
                "domain": "example.com",
                "is_active": True,
                "rate_limit_rpm": 60,
                "config": {},
                "api_key_id": "key-1",
                "key_kind": "live",
                "label": "primary",
                "api_key_hash": live_hash,
                "created_at": now,
                "updated_at": now,
            }
        ],
        [],
    ]
    tenant_manager.get_tenant = AsyncMock(  # type: ignore[method-assign]
        return_value={"tenant_id": "tenant-1", "is_active": True}
    )
    tenant_manager._fetchrow.side_effect = [  # type: ignore[attr-defined]
        {
            "profile_id": "profile-1",
            "tenant_id": "tenant-1",
            "name": "Sandbox",
            "description": "Primary",
            "is_default": True,
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        },
        {
            "rule_id": "rule-1",
            "tenant_id": "tenant-1",
            "profile_id": "profile-1",
            "priority": 10,
            "method": "POST",
            "route_pattern": "/api/v1/chat",
            "enabled": True,
            "match": {"body_contains": ["price"]},
            "response": {"json_body": {"content": "Sandbox"}},
            "latency_ms": 0,
            "created_at": now,
            "updated_at": now,
        },
    ]

    auth = await tenant_manager.authenticate_api_key(live_key)
    assert auth is not None
    assert auth["execution_mode"] == "live"
    assert auth["api_key_kind"] == "live"

    test_key = await tenant_manager.issue_api_key("tenant-1", key_kind="test")
    assert test_key is not None
    assert test_key.startswith("sk_test_")

    profile = await tenant_manager.create_test_profile(
        "tenant-1",
        name="Sandbox",
        description="Primary",
        is_default=True,
    )
    assert profile["profile_id"] == "profile-1"

    rule = await tenant_manager.create_test_rule(
        "tenant-1",
        "profile-1",
        priority=10,
        method="POST",
        route_pattern="/api/v1/chat",
        match={"body_contains": ["price"]},
        response={"json_body": {"content": "Sandbox"}},
    )
    assert rule["rule_id"] == "rule-1"


@pytest.mark.asyncio
async def test_test_profile_and_rule_mutation_helpers(tenant_manager: TenantManager) -> None:
    now = datetime.now(UTC)
    tenant_manager._fetch.return_value = [  # type: ignore[attr-defined]
        {
            "profile_id": "profile-1",
            "tenant_id": "tenant-1",
            "name": "Sandbox",
            "description": "Primary",
            "is_default": True,
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        }
    ]
    tenant_manager._fetchrow.side_effect = [  # type: ignore[attr-defined]
        {
            "profile_id": "profile-1",
            "tenant_id": "tenant-1",
            "name": "Sandbox",
            "description": "Primary",
            "is_default": True,
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        },
        {
            "profile_id": "profile-1",
            "tenant_id": "tenant-1",
            "name": "Sandbox",
            "description": "Primary",
            "is_default": True,
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        },
        {
            "profile_id": "profile-1",
            "tenant_id": "tenant-1",
            "name": "Sandbox updated",
            "description": "Updated",
            "is_default": False,
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        },
        {
            "profile_id": "profile-1",
            "tenant_id": "tenant-1",
            "name": "Sandbox default",
            "description": "Updated",
            "is_default": True,
            "is_active": False,
            "created_at": now,
            "updated_at": now,
        },
        {
            "rule_id": "rule-1",
            "tenant_id": "tenant-1",
            "profile_id": "profile-1",
            "priority": 5,
            "method": "POST",
            "route_pattern": "/api/v1/chat*",
            "enabled": True,
            "match": {"body_contains": ["price"]},
            "response": {"json_body": {"content": "ok"}},
            "latency_ms": 25,
            "created_at": now,
            "updated_at": now,
        },
    ]
    tenant_manager._execute.side_effect = [None, "DELETE 1", "DELETE 1"]  # type: ignore[attr-defined]

    profiles = await tenant_manager.list_test_profiles("tenant-1")
    assert profiles[0]["profile_id"] == "profile-1"

    fetched = await tenant_manager.get_test_profile("tenant-1", "profile-1")
    assert fetched["name"] == "Sandbox"

    explicit = await tenant_manager.resolve_test_profile("tenant-1", "profile-1")
    assert explicit["profile_id"] == "profile-1"

    updated = await tenant_manager.update_test_profile(
        "tenant-1",
        "profile-1",
        name="Sandbox updated",
        description="Updated",
    )
    assert updated["name"] == "Sandbox updated"

    updated_default = await tenant_manager.update_test_profile(
        "tenant-1",
        "profile-1",
        is_default=True,
        is_active=False,
    )
    assert updated_default["is_default"] is True
    assert updated_default["is_active"] is False

    deleted = await tenant_manager.delete_test_profile("tenant-1", "profile-1")
    assert deleted is True

    tenant_manager._fetch.return_value = [  # type: ignore[attr-defined]
        {
            "rule_id": "rule-1",
            "tenant_id": "tenant-1",
            "profile_id": "profile-1",
            "priority": 10,
            "method": "POST",
            "route_pattern": "/api/v1/chat",
            "enabled": True,
            "match": {"body_contains": ["price"]},
            "response": {"json_body": {"content": "Sandbox"}},
            "latency_ms": 0,
            "created_at": now,
            "updated_at": now,
        }
    ]
    rules = await tenant_manager.list_test_rules("tenant-1", "profile-1")
    assert rules[0]["rule_id"] == "rule-1"
    updated_rule = await tenant_manager.update_test_rule(
        "tenant-1",
        "profile-1",
        "rule-1",
        priority=5,
        method="post",
        route_pattern="/api/v1/chat*",
        enabled=True,
        match={"body_contains": ["price"]},
        response={"json_body": {"content": "ok"}},
        latency_ms=25,
    )
    assert updated_rule["priority"] == 5
    assert updated_rule["route_pattern"] == "/api/v1/chat*"

    deleted_rule = await tenant_manager.delete_test_rule("tenant-1", "profile-1", "rule-1")
    assert deleted_rule is True


@pytest.mark.asyncio
async def test_notification_subscription_helpers_and_matching(
    tenant_manager: TenantManager,
) -> None:
    now = datetime.now(UTC)
    listed_row = {
        "subscription_id": "sub-1",
        "tenant_id": "tenant-1",
        "source_app": " checkout ",
        "event_types_json": '["order.failed", "order.failed", "order.refunded"]',
        "channel_id": "webhook",
        "channel_config": '{"webhook_url":"https://example.com/hook"}',
        "template_json": '{"title":"Alert: {title}"}',
        "status": "paused",
        "created_at": now,
        "updated_at": now,
    }
    tenant_manager._fetch.return_value = [listed_row]  # type: ignore[attr-defined]
    listed = await tenant_manager.list_notification_subscriptions("tenant-1")
    assert listed == [
        {
            "subscription_id": "sub-1",
            "tenant_id": "tenant-1",
            "source_app": "checkout",
            "event_types": ["order.failed", "order.refunded"],
            "channel_id": "webhook",
            "channel_config": {"webhook_url": "https://example.com/hook"},
            "template": {"title": "Alert: {title}"},
            "status": "paused",
            "created_at": now,
            "updated_at": now,
        }
    ]

    invalid_row = {
        "subscription_id": "sub-2",
        "tenant_id": "tenant-1",
        "source_app": None,
        "event_types_json": "not-json",
        "channel_id": "email",
        "channel_config": "not-json",
        "template_json": "not-json",
        "status": "disabled",
        "created_at": now,
        "updated_at": now,
    }
    parsed_invalid = tenant_manager._notification_subscription_from_row(invalid_row)
    assert parsed_invalid["event_types"] == []
    assert parsed_invalid["channel_config"] == {}
    assert parsed_invalid["template"] == {}
    assert parsed_invalid["status"] == "active"

    tenant_manager._fetchrow.side_effect = [  # type: ignore[attr-defined]
        listed_row,
        {
            "subscription_id": "sub-3",
            "tenant_id": "tenant-1",
            "source_app": "checkout",
            "event_types_json": ["order.failed"],
            "channel_id": "email",
            "channel_config": {"email": "alerts@example.com", "account_id": "acct-1"},
            "template_json": {"body": "{body}"},
            "status": "paused",
            "created_at": now,
            "updated_at": now,
        },
        {
            "subscription_id": "sub-3",
            "tenant_id": "tenant-1",
            "source_app": None,
            "event_types_json": ["order.refunded"],
            "channel_id": "email",
            "channel_config": {"email": "alerts@example.com"},
            "template_json": {"body": "{body}"},
            "status": "active",
            "created_at": now,
            "updated_at": now,
        },
    ]

    fetched = await tenant_manager.get_notification_subscription("tenant-1", "sub-1")
    assert fetched is not None
    assert fetched["subscription_id"] == "sub-1"

    created = await tenant_manager.create_notification_subscription(
        "tenant-1",
        source_app=" checkout ",
        event_types=["order.failed", "order.failed"],
        channel_id="EMAIL",
        channel_config={"email": "alerts@example.com", "account_id": "acct-1"},
        template={"body": "{body}"},
        status="disabled",
    )
    assert created["status"] == "paused"
    create_args = tenant_manager._fetchrow.call_args_list[1].args  # type: ignore[attr-defined]
    assert create_args[1] == "tenant-1"
    assert create_args[2] == "checkout"
    assert json.loads(create_args[3]) == ["order.failed"]
    assert create_args[4] == "email"
    assert json.loads(create_args[5]) == {"email": "alerts@example.com", "account_id": "acct-1"}
    assert create_args[7] == "active"

    updated = await tenant_manager.update_notification_subscription(
        "tenant-1",
        "sub-3",
        source_app="",
        event_types=["order.refunded", "order.refunded"],
        channel_config={"email": "alerts@example.com"},
        template={"body": "{body}"},
        status="active",
    )
    assert updated is not None
    assert updated["source_app"] is None
    update_args = tenant_manager._fetchrow.call_args_list[2].args  # type: ignore[attr-defined]
    assert update_args[1] is None
    assert json.loads(update_args[2]) == ["order.refunded"]
    assert update_args[5] == "active"

    tenant_manager.get_notification_subscription = AsyncMock(  # type: ignore[method-assign]
        return_value={"subscription_id": "sub-noop"}
    )
    noop = await tenant_manager.update_notification_subscription("tenant-1", "sub-noop")
    assert noop == {"subscription_id": "sub-noop"}

    tenant_manager._execute.return_value = "DELETE 1"  # type: ignore[attr-defined]
    assert await tenant_manager.delete_notification_subscription("tenant-1", "sub-3") is True

    tenant_manager.list_notification_subscriptions = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            {
                "subscription_id": "sub-active",
                "source_app": "checkout",
                "event_types": ["order.failed"],
                "status": "active",
            },
            {
                "subscription_id": "sub-paused",
                "source_app": "checkout",
                "event_types": ["order.failed"],
                "status": "paused",
            },
            {
                "subscription_id": "sub-other-app",
                "source_app": "billing",
                "event_types": ["order.failed"],
                "status": "active",
            },
            {
                "subscription_id": "sub-other-event",
                "source_app": "checkout",
                "event_types": ["order.refunded"],
                "status": "active",
            },
        ]
    )
    matched = await tenant_manager.match_notification_subscriptions(
        "tenant-1",
        source_app="checkout",
        event_type="order.failed",
    )
    assert [item["subscription_id"] for item in matched] == ["sub-active"]


def test_notification_subscription_helper_normalizers() -> None:
    assert _normalise_notification_status("paused") == "paused"
    assert _normalise_notification_status("disabled") == "active"
    assert _clean_json_dict({"a": 1}) == {"a": 1}
    assert _clean_json_dict(None) == {}
    assert _clean_string_list(["a", "a", " ", 1]) == ["a", "1"]


@pytest.mark.asyncio
async def test_get_web_events_filters_test_rows_by_default(tenant_manager: TenantManager) -> None:
    tenant_manager._fetch.return_value = [{"event_id": "evt-1"}]  # type: ignore[attr-defined]

    rows = await tenant_manager.get_web_events("tenant-1", session_id="session-1", limit=5)
    assert rows == [{"event_id": "evt-1"}]
    filtered_sql = tenant_manager._fetch.call_args.args[0]  # type: ignore[attr-defined]
    assert "execution_mode = 'live'" in filtered_sql

    tenant_manager._fetch.reset_mock()  # type: ignore[attr-defined]
    tenant_manager._fetch.return_value = [{"event_id": "evt-2"}]  # type: ignore[attr-defined]

    include_test_rows = await tenant_manager.get_web_events(
        "tenant-1",
        session_id="session-1",
        include_test=True,
        limit=5,
    )
    assert include_test_rows == [{"event_id": "evt-2"}]
    include_test_sql = tenant_manager._fetch.call_args.args[0]  # type: ignore[attr-defined]
    assert "execution_mode = 'live'" not in include_test_sql


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
