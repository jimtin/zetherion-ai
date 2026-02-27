"""Google provider adapters for Gmail, Calendar, and Google Tasks."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from zetherion_ai.integrations.providers.base import (
    CalendarProviderAdapter,
    EmailProviderAdapter,
    ProviderDestination,
    ProviderEvent,
    ProviderTask,
    TaskProviderAdapter,
)
from zetherion_ai.logging import get_logger
from zetherion_ai.routing.models import DestinationType, NormalizedEvent, NormalizedTask
from zetherion_ai.skills.gmail.accounts import GmailAccount, GmailAccountManager
from zetherion_ai.skills.gmail.auth import GmailAuth, OAuthError
from zetherion_ai.skills.gmail.calendar_sync import CalendarClient
from zetherion_ai.skills.gmail.client import GmailClient

log = get_logger("zetherion_ai.integrations.providers.google")

GOOGLE_TASKS_API_BASE = "https://tasks.googleapis.com/tasks/v1"
GOOGLE_CALENDAR_LIST_URL = "https://www.googleapis.com/calendar/v3/users/me/calendarList"


class GoogleProviderAdapter(EmailProviderAdapter, TaskProviderAdapter, CalendarProviderAdapter):
    """Unified Google adapter implementing email/task/calendar protocols."""

    def __init__(
        self,
        account_manager: GmailAccountManager,
        *,
        auth: GmailAuth | None = None,
        auth_resolver: Callable[[], GmailAuth | None] | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._accounts = account_manager
        self._auth = auth
        self._auth_resolver = auth_resolver
        self._timeout = timeout

    async def list_sources(self, user_id: int) -> list[ProviderDestination]:
        """List connected Gmail mailbox sources."""
        accounts = await self._accounts.list_accounts(user_id)
        return [
            ProviderDestination(
                destination_id=str(a.id) if a.id is not None else a.email,
                destination_type=DestinationType.MAILBOX,
                display_name=a.email,
                writable=True,
                is_primary=bool(a.is_primary),
                metadata={"account_email": a.email},
            )
            for a in accounts
        ]

    async def list_unread(self, user_id: int, *, limit: int = 20) -> list[dict[str, Any]]:
        """Return unread email summaries across connected accounts."""
        accounts = await self._accounts.list_accounts(user_id)
        if not accounts:
            return []
        max_results = max(int(limit), 1)
        ordered_accounts = sorted(accounts, key=lambda a: (not bool(a.is_primary), a.email.lower()))

        out: list[dict[str, Any]] = []
        remaining = max_results
        for index, account in enumerate(ordered_accounts):
            if remaining <= 0:
                break

            resolved_account = account
            if account.id is not None:
                loaded = await self._accounts.get_account(account.id)
                if loaded is not None:
                    resolved_account = loaded

            token = await self._ensure_access_token(resolved_account)
            if not token:
                continue

            # Keep a fair split across connected inboxes while filling remaining capacity.
            accounts_left = max(len(ordered_accounts) - index, 1)
            account_limit = max(1, remaining // accounts_left)

            client = GmailClient(token)
            stubs, _ = await client.list_messages(
                query="in:inbox is:unread",
                max_results=account_limit,
            )
            for stub in stubs:
                if len(out) >= max_results:
                    break
                msg = await client.get_message(stub.get("id", ""))
                out.append(
                    {
                        "account_ref": (
                            str(resolved_account.id)
                            if resolved_account.id is not None
                            else resolved_account.email
                        ),
                        "account_email": resolved_account.email,
                        "external_id": msg.gmail_id,
                        "thread_id": msg.thread_id,
                        "subject": msg.subject,
                        "from_email": msg.from_email,
                        "to_emails": msg.to_emails,
                        "body_preview": msg.snippet,
                        "received_at": msg.received_at.isoformat() if msg.received_at else None,
                        "has_attachments": bool(getattr(msg, "has_attachments", False)),
                        "attachment_filenames": list(
                            getattr(msg, "attachment_filenames", []) or []
                        ),
                        "attachment_count": len(
                            list(getattr(msg, "attachment_filenames", []) or [])
                        ),
                    }
                )
            remaining = max_results - len(out)

        return out

    async def list_task_lists(self, user_id: int) -> list[ProviderDestination]:
        """List Google Task lists for the user's primary account."""
        account = await self._get_preferred_account(user_id)
        if account is None:
            return []
        token = await self._ensure_access_token(account)
        if not token:
            return []

        payload = await self._get_json(
            f"{GOOGLE_TASKS_API_BASE}/users/@me/lists",
            access_token=token,
        )
        items = payload.get("items", [])
        out: list[ProviderDestination] = []
        for item in items:
            out.append(
                ProviderDestination(
                    destination_id=item.get("id", ""),
                    destination_type=DestinationType.TASK_LIST,
                    display_name=item.get("title", item.get("id", "task_list")),
                    writable=True,
                    is_primary=(item.get("id") == "@default"),
                    metadata={
                        "etag": item.get("etag", ""),
                        "account_ref": str(account.id) if account.id is not None else account.email,
                    },
                )
            )
        return out

    async def create_task(
        self,
        user_id: int,
        task_list_id: str,
        task: NormalizedTask,
    ) -> ProviderTask:
        """Create a task in Google Tasks."""
        account = await self._get_preferred_account(user_id)
        if account is None:
            raise RuntimeError("No Google account connected")
        token = await self._ensure_access_token(account)
        if not token:
            raise RuntimeError("Google token unavailable")

        body: dict[str, Any] = {
            "title": task.title,
            "notes": task.description,
        }
        if task.due_at:
            body["due"] = task.due_at.isoformat() + "Z"

        result = await self._post_json(
            f"{GOOGLE_TASKS_API_BASE}/lists/{task_list_id}/tasks",
            access_token=token,
            json_body=body,
        )

        due = _parse_google_datetime(result.get("due"))
        return ProviderTask(
            task_id=result.get("id", ""),
            list_id=task_list_id,
            title=result.get("title", task.title),
            due_at=due,
            completed=bool(result.get("status") == "completed"),
            metadata={"raw": result},
        )

    async def list_calendars(self, user_id: int) -> list[ProviderDestination]:
        """List Google Calendar calendars for the user."""
        account = await self._get_preferred_account(user_id)
        if account is None:
            return []
        token = await self._ensure_access_token(account)
        if not token:
            return []

        payload = await self._get_json(GOOGLE_CALENDAR_LIST_URL, access_token=token)
        items = payload.get("items", [])
        out: list[ProviderDestination] = []
        for item in items:
            out.append(
                ProviderDestination(
                    destination_id=item.get("id", ""),
                    destination_type=DestinationType.CALENDAR,
                    display_name=item.get("summary", item.get("id", "calendar")),
                    writable=bool(item.get("accessRole") in {"owner", "writer"}),
                    is_primary=bool(item.get("primary", False)),
                    metadata={
                        "time_zone": item.get("timeZone", "UTC"),
                        "account_ref": str(account.id) if account.id is not None else account.email,
                    },
                )
            )
        return out

    async def list_events(
        self,
        user_id: int,
        calendar_ids: list[str],
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> list[ProviderEvent]:
        """List events across calendars in the given window."""
        account = await self._get_preferred_account(user_id)
        if account is None:
            return []
        token = await self._ensure_access_token(account)
        if not token:
            return []

        client = CalendarClient(token, timeout=self._timeout)
        events: list[ProviderEvent] = []
        for calendar_id in calendar_ids:
            items = await client.list_events(
                calendar_id=calendar_id,
                time_min=window_start,
                time_max=window_end,
                max_results=250,
            )
            for item in items:
                if item.start is None or item.end is None:
                    continue
                events.append(
                    ProviderEvent(
                        event_id=item.event_id,
                        calendar_id=calendar_id,
                        title=item.summary,
                        start=item.start,
                        end=item.end,
                        all_day=item.all_day,
                        metadata={"status": item.status, "organizer": item.organizer},
                    )
                )
        return events

    async def create_event(
        self,
        user_id: int,
        calendar_id: str,
        event: NormalizedEvent,
    ) -> ProviderEvent:
        """Create a calendar event in Google Calendar."""
        account = await self._get_preferred_account(user_id)
        if account is None:
            raise RuntimeError("No Google account connected")
        token = await self._ensure_access_token(account)
        if not token:
            raise RuntimeError("Google token unavailable")

        client = CalendarClient(token, timeout=self._timeout)
        created = await client.create_event(
            summary=event.title,
            start=event.start,
            end=event.end,
            calendar_id=calendar_id,
            description=event.description,
            location=event.location,
            attendees=event.attendees,
        )
        if created.start is None or created.end is None:
            raise RuntimeError("Created event did not include start/end")

        return ProviderEvent(
            event_id=created.event_id,
            calendar_id=calendar_id,
            title=created.summary,
            start=created.start,
            end=created.end,
            all_day=created.all_day,
            metadata={"html_link": created.html_link},
        )

    async def _get_preferred_account(self, user_id: int) -> GmailAccount | None:
        account = await self._accounts.get_primary_account(user_id)
        if account is not None:
            return account
        accounts = await self._accounts.list_accounts(user_id)
        if not accounts:
            return None
        first = accounts[0]
        if first.id is None:
            return None
        return await self._accounts.get_account(first.id)

    async def _ensure_access_token(self, account: GmailAccount) -> str | None:
        if account.token_expiry:
            token_expiry = account.token_expiry
            if token_expiry.tzinfo is None:
                token_expiry = token_expiry.replace(tzinfo=UTC)
            if token_expiry > datetime.now(UTC) + timedelta(seconds=30):
                return account.access_token

        auth = self._auth
        if auth is None and self._auth_resolver is not None:
            auth = self._auth_resolver()
            if auth is not None:
                self._auth = auth

        if not auth or not account.refresh_token or account.id is None:
            return account.access_token or None

        try:
            refreshed = await auth.refresh_access_token(account.refresh_token)
        except OAuthError as exc:
            log.warning("google_token_refresh_failed", account=account.email, error=str(exc))
            return account.access_token or None

        access_token_value = refreshed.get("access_token")
        access_token = access_token_value if isinstance(access_token_value, str) else ""
        refresh_token_value = refreshed.get("refresh_token")
        refresh_token = refresh_token_value if isinstance(refresh_token_value, str) else None
        expiry = None
        if isinstance(refreshed.get("expires_in"), int):
            expiry = datetime.now(UTC) + timedelta(seconds=int(refreshed["expires_in"]))

        if access_token:
            await self._accounts.update_tokens(
                account.id,
                access_token,
                refresh_token=refresh_token,
                token_expiry=expiry,
            )
            return access_token
        return account.access_token or None

    async def _get_json(self, url: str, *, access_token: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(
                url,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            response.raise_for_status()
            data: dict[str, Any] = response.json()
            return data

    async def _post_json(
        self,
        url: str,
        *,
        access_token: str,
        json_body: dict[str, Any],
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {access_token}"},
                json=json_body,
            )
            response.raise_for_status()
            data: dict[str, Any] = response.json()
            return data


def _parse_google_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None
