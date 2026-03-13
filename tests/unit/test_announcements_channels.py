"""Coverage for announcement channel helpers and tenant Gmail sender."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import zetherion_ai.announcements.channels as channels
from zetherion_ai.announcements.dispatcher import AnnouncementDispatchError


@pytest.mark.asyncio
async def test_tenant_google_sender_requires_tenant_id() -> None:
    sender = channels.TenantGoogleAnnouncementEmailSender(MagicMock())

    with pytest.raises(AnnouncementDispatchError) as exc_info:
        await sender.send(
            to_address="ops@example.com",
            subject="Notice",
            body="Body",
            metadata={},
        )
    assert exc_info.value.code == "missing_tenant_id"
    assert "tenant_id" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_tenant_google_sender_uses_requested_account_and_sends_message(monkeypatch) -> None:
    tenant_admin_manager = MagicMock()
    tenant_admin_manager._refresh_google_access_token_if_needed = AsyncMock(  # noqa: SLF001
        return_value={
            "account_id": "acct-1",
            "access_token": "token-123",
            "scopes": ["https://www.googleapis.com/auth/gmail.send"],
        }
    )

    sent_messages: list[dict[str, str]] = []

    class FakeGmailClient:
        def __init__(self, access_token: str) -> None:
            assert access_token == "token-123"

        async def send_message(self, *, to: str, subject: str, body: str) -> None:
            sent_messages.append({"to": to, "subject": subject, "body": body})

    monkeypatch.setattr(channels, "GmailClient", FakeGmailClient)
    sender = channels.TenantGoogleAnnouncementEmailSender(tenant_admin_manager)

    await sender.send(
        to_address="ops@example.com",
        subject="Notice",
        body="Body",
        metadata={"tenant_id": "tenant-1", "account_id": "acct-1"},
    )

    tenant_admin_manager._refresh_google_access_token_if_needed.assert_awaited_once_with(
        tenant_id="tenant-1",
        account_id="acct-1",
    )
    assert sent_messages == [{"to": "ops@example.com", "subject": "Notice", "body": "Body"}]


@pytest.mark.asyncio
async def test_tenant_google_sender_handles_account_resolution_failures() -> None:
    tenant_admin_manager = MagicMock()
    tenant_admin_manager.list_email_accounts = AsyncMock(return_value=[])
    sender = channels.TenantGoogleAnnouncementEmailSender(tenant_admin_manager)

    with pytest.raises(AnnouncementDispatchError) as exc_info:
        await sender.send(
            to_address="ops@example.com",
            subject="Notice",
            body="Body",
            metadata={"tenant_id": "tenant-1"},
        )
    assert exc_info.value.code == "email_sender_account_missing"
    assert "no connected Google mailbox" in str(exc_info.value.detail)

    tenant_admin_manager.list_email_accounts.return_value = [{"status": "connected"}]
    with pytest.raises(AnnouncementDispatchError) as exc_info:
        await sender.send(
            to_address="ops@example.com",
            subject="Notice",
            body="Body",
            metadata={"tenant_id": "tenant-1"},
        )
    assert exc_info.value.code == "email_sender_account_invalid"
    assert "missing account_id" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_tenant_google_sender_validates_scope_token_and_wraps_failures(monkeypatch) -> None:
    tenant_admin_manager = MagicMock()
    tenant_admin_manager.list_email_accounts = AsyncMock(
        return_value=[{"status": "connected", "account_id": "acct-1"}]
    )
    tenant_admin_manager._refresh_google_access_token_if_needed = AsyncMock(  # noqa: SLF001
        side_effect=[
            {"account_id": "acct-1", "access_token": "token", "scopes": ["profile"]},
            {"account_id": "acct-1", "access_token": "", "scopes": ["https://mail.google.com/"]},
            {
                "account_id": "acct-1",
                "access_token": "token-123",
                "scopes": ["https://www.googleapis.com/auth/gmail.send"],
            },
        ]
    )

    class FailingGmailClient:
        def __init__(self, _access_token: str) -> None:
            return None

        async def send_message(self, *, to: str, subject: str, body: str) -> None:
            raise RuntimeError("gmail exploded")

    monkeypatch.setattr(channels, "GmailClient", FailingGmailClient)
    sender = channels.TenantGoogleAnnouncementEmailSender(tenant_admin_manager)

    with pytest.raises(AnnouncementDispatchError) as exc_info:
        await sender.send(
            to_address="ops@example.com",
            subject="Scope",
            body="Body",
            metadata={"tenant_id": "tenant-1"},
        )
    assert exc_info.value.code == "email_sender_scope_missing"
    assert "missing gmail.send scope" in str(exc_info.value.detail)

    with pytest.raises(AnnouncementDispatchError) as exc_info:
        await sender.send(
            to_address="ops@example.com",
            subject="Token",
            body="Body",
            metadata={"tenant_id": "tenant-1"},
        )
    assert exc_info.value.code == "email_sender_token_missing"
    assert "no access token" in str(exc_info.value.detail)

    with pytest.raises(AnnouncementDispatchError) as exc_info:
        await sender.send(
            to_address="ops@example.com",
            subject="Failure",
            body="Body",
            metadata={"tenant_id": "tenant-1"},
        )
    assert exc_info.value.code == "gmail_send_failed"
    assert "gmail exploded" in str(exc_info.value.detail)


def test_build_announcement_channel_registry_includes_optional_channels() -> None:
    tenant_admin_manager = MagicMock()
    registry = channels.build_announcement_channel_registry(
        discord_bot=object(),  # type: ignore[arg-type]
        tenant_admin_manager=tenant_admin_manager,
    )

    assert registry.channels() == ["discord_dm", "email", "webhook"]
    assert registry.get_definition("discord_dm") is not None
    assert registry.get_definition("email") is not None
    assert [definition.channel for definition in registry.definitions(public_only=True)] == [
        "email",
        "webhook",
    ]
