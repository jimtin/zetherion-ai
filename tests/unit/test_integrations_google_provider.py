"""Unit tests for Google provider adapter mailbox aggregation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zetherion_ai.integrations.providers.google import GoogleProviderAdapter
from zetherion_ai.skills.gmail.accounts import GmailAccount


def _message(
    msg_id: str,
    *,
    sender: str,
    has_attachments: bool = False,
    attachment_filenames: list[str] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        gmail_id=msg_id,
        thread_id=f"thread-{msg_id}",
        subject=f"subject-{msg_id}",
        from_email=sender,
        to_emails=["me@example.com"],
        snippet=f"snippet-{msg_id}",
        received_at=datetime(2026, 2, 13, 12, 0, 0),
        has_attachments=has_attachments,
        attachment_filenames=attachment_filenames or [],
    )


@pytest.mark.asyncio
async def test_list_unread_scans_all_connected_accounts() -> None:
    account_manager = MagicMock()
    account_manager.list_accounts = AsyncMock(
        return_value=[
            GmailAccount(id=2, email="second@example.com", is_primary=False),
            GmailAccount(id=1, email="primary@example.com", is_primary=True),
        ]
    )
    account_manager.get_account = AsyncMock(
        side_effect=[
            GmailAccount(id=1, email="primary@example.com", access_token="token-primary"),
            GmailAccount(id=2, email="second@example.com", access_token="token-second"),
        ]
    )
    adapter = GoogleProviderAdapter(account_manager=account_manager)
    adapter._ensure_access_token = AsyncMock(side_effect=["token-primary", "token-second"])  # type: ignore[assignment]

    client_primary = MagicMock()
    client_primary.list_messages = AsyncMock(return_value=([{"id": "p1"}], None))
    client_primary.get_message = AsyncMock(return_value=_message("p1", sender="boss@example.com"))

    client_second = MagicMock()
    client_second.list_messages = AsyncMock(return_value=([{"id": "s1"}], None))
    client_second.get_message = AsyncMock(return_value=_message("s1", sender="ops@example.com"))

    with patch(
        "zetherion_ai.integrations.providers.google.GmailClient",
        side_effect=[client_primary, client_second],
    ):
        out = await adapter.list_unread(user_id=42, limit=2)

    assert len(out) == 2
    assert out[0]["account_ref"] == "1"
    assert out[1]["account_ref"] == "2"
    client_primary.list_messages.assert_awaited_once_with(
        query="in:inbox is:unread",
        max_results=1,
    )
    client_second.list_messages.assert_awaited_once_with(
        query="in:inbox is:unread",
        max_results=1,
    )


@pytest.mark.asyncio
async def test_list_unread_skips_accounts_without_valid_token() -> None:
    account_manager = MagicMock()
    account_manager.list_accounts = AsyncMock(
        return_value=[
            GmailAccount(id=1, email="one@example.com", is_primary=True),
            GmailAccount(id=2, email="two@example.com", is_primary=False),
        ]
    )
    account_manager.get_account = AsyncMock(
        side_effect=[
            GmailAccount(id=1, email="one@example.com", access_token="token-one"),
            GmailAccount(id=2, email="two@example.com", access_token="token-two"),
        ]
    )
    adapter = GoogleProviderAdapter(account_manager=account_manager)
    adapter._ensure_access_token = AsyncMock(side_effect=[None, "token-two"])  # type: ignore[assignment]

    client_second = MagicMock()
    client_second.list_messages = AsyncMock(return_value=([{"id": "s1"}], None))
    client_second.get_message = AsyncMock(return_value=_message("s1", sender="ops@example.com"))

    with patch(
        "zetherion_ai.integrations.providers.google.GmailClient",
        return_value=client_second,
    ):
        out = await adapter.list_unread(user_id=42, limit=2)

    assert len(out) == 1
    assert out[0]["account_ref"] == "2"
    client_second.list_messages.assert_awaited_once_with(
        query="in:inbox is:unread",
        max_results=2,
    )


@pytest.mark.asyncio
async def test_list_unread_returns_empty_when_no_accounts() -> None:
    account_manager = MagicMock()
    account_manager.list_accounts = AsyncMock(return_value=[])
    account_manager.get_account = AsyncMock(return_value=None)
    adapter = GoogleProviderAdapter(account_manager=account_manager)

    out = await adapter.list_unread(user_id=42, limit=5)

    assert out == []


@pytest.mark.asyncio
async def test_list_unread_hydrates_account_tokens_before_fetch() -> None:
    account_manager = MagicMock()
    summary_account = GmailAccount(id=1, email="primary@example.com", is_primary=True)
    full_account = GmailAccount(
        id=1,
        email="primary@example.com",
        access_token="token-primary",
        refresh_token="refresh-primary",
        is_primary=True,
    )
    account_manager.list_accounts = AsyncMock(return_value=[summary_account])
    account_manager.get_account = AsyncMock(return_value=full_account)

    adapter = GoogleProviderAdapter(account_manager=account_manager)
    adapter._ensure_access_token = AsyncMock(return_value="token-primary")  # type: ignore[assignment]

    client_primary = MagicMock()
    client_primary.list_messages = AsyncMock(return_value=([{"id": "p1"}], None))
    client_primary.get_message = AsyncMock(return_value=_message("p1", sender="boss@example.com"))

    with patch(
        "zetherion_ai.integrations.providers.google.GmailClient",
        return_value=client_primary,
    ):
        out = await adapter.list_unread(user_id=42, limit=1)

    assert len(out) == 1
    account_manager.get_account.assert_awaited_once_with(1)
    hydrated_arg = adapter._ensure_access_token.await_args.args[0]
    assert hydrated_arg.access_token == "token-primary"


@pytest.mark.asyncio
async def test_list_unread_includes_attachment_metadata() -> None:
    account_manager = MagicMock()
    account_manager.list_accounts = AsyncMock(
        return_value=[GmailAccount(id=1, email="primary@example.com", is_primary=True)]
    )
    account_manager.get_account = AsyncMock(
        return_value=GmailAccount(id=1, email="primary@example.com", access_token="token-primary")
    )
    adapter = GoogleProviderAdapter(account_manager=account_manager)
    adapter._ensure_access_token = AsyncMock(return_value="token-primary")  # type: ignore[assignment]

    client_primary = MagicMock()
    client_primary.list_messages = AsyncMock(return_value=([{"id": "p1"}], None))
    client_primary.get_message = AsyncMock(
        return_value=_message(
            "p1",
            sender="boss@example.com",
            has_attachments=True,
            attachment_filenames=["agenda.pdf"],
        )
    )

    with patch(
        "zetherion_ai.integrations.providers.google.GmailClient",
        return_value=client_primary,
    ):
        out = await adapter.list_unread(user_id=42, limit=1)

    assert len(out) == 1
    assert out[0]["has_attachments"] is True
    assert out[0]["attachment_count"] == 1
    assert out[0]["attachment_filenames"] == ["agenda.pdf"]


@pytest.mark.asyncio
async def test_ensure_access_token_handles_aware_expiry() -> None:
    account_manager = MagicMock()
    adapter = GoogleProviderAdapter(account_manager=account_manager)
    account = GmailAccount(
        id=1,
        email="primary@example.com",
        access_token="token-1",
        token_expiry=datetime.now(UTC) + timedelta(minutes=15),
    )

    token = await adapter._ensure_access_token(account)

    assert token == "token-1"


@pytest.mark.asyncio
async def test_ensure_access_token_handles_naive_expiry() -> None:
    account_manager = MagicMock()
    adapter = GoogleProviderAdapter(account_manager=account_manager)
    account = GmailAccount(
        id=1,
        email="primary@example.com",
        access_token="token-1",
        token_expiry=datetime.now() + timedelta(minutes=15),
    )

    token = await adapter._ensure_access_token(account)

    assert token == "token-1"


@pytest.mark.asyncio
async def test_ensure_access_token_refresh_sets_timezone_aware_expiry() -> None:
    account_manager = MagicMock()
    account_manager.update_tokens = AsyncMock()
    auth = MagicMock()
    auth.refresh_access_token = AsyncMock(
        return_value={"access_token": "new-token", "expires_in": 3600}
    )
    adapter = GoogleProviderAdapter(account_manager=account_manager, auth=auth)
    account = GmailAccount(
        id=1,
        email="primary@example.com",
        access_token="old-token",
        refresh_token="refresh-token",
        token_expiry=datetime.now(UTC) - timedelta(minutes=5),
    )

    token = await adapter._ensure_access_token(account)

    assert token == "new-token"
    account_manager.update_tokens.assert_awaited_once()
    updated_expiry = account_manager.update_tokens.await_args.kwargs["token_expiry"]
    assert updated_expiry.tzinfo is not None
