"""Tests for Gmail API client."""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from zetherion_ai.skills.gmail.client import (
    EmailMessage,
    GmailClient,
    GmailClientError,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """Create a GmailClient instance with a test token."""
    return GmailClient(access_token="test-access-token")


@pytest.fixture
def mock_httpx():
    """Mock httpx.AsyncClient so that no real HTTP calls are made.

    Yields the mock client instance that ``async with httpx.AsyncClient(...)``
    resolves to, allowing callers to set ``mock_httpx.get.return_value``, etc.
    """
    with patch("zetherion_ai.skills.gmail.client.httpx.AsyncClient") as mock_cls:
        client_instance = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=client_instance)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        yield client_instance


def _make_response(
    status_code: int = 200,
    json_data: dict | list | None = None,
    text: str = "",
) -> MagicMock:
    """Create a mock ``httpx.Response``."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text
    if json_data is not None:
        resp.json.return_value = json_data
    # raise_for_status should succeed for 2xx codes
    if 200 <= status_code < 300:
        resp.raise_for_status = MagicMock()
    else:
        http_error = httpx.HTTPStatusError(
            message=f"HTTP {status_code}",
            request=MagicMock(spec=httpx.Request),
            response=resp,
        )
        resp.raise_for_status.side_effect = http_error
    return resp


def _b64(text: str) -> str:
    """Encode a string as base64url (no padding), matching Gmail API format."""
    return base64.urlsafe_b64encode(text.encode()).decode()


# ===================================================================
# 1. Constructor tests
# ===================================================================


class TestConstructor:
    """Tests for GmailClient.__init__."""

    def test_valid_initialization(self):
        c = GmailClient(access_token="tok-abc")
        assert c._access_token == "tok-abc"
        assert c._timeout == 30.0
        assert isinstance(c._semaphore, asyncio.Semaphore)

    def test_custom_max_rps_and_timeout(self):
        c = GmailClient(access_token="tok", max_rps=5, timeout=60.0)
        assert c._max_rps == 5
        assert c._timeout == 60.0

    def test_empty_access_token_raises_value_error(self):
        with pytest.raises(ValueError, match="access_token is required"):
            GmailClient(access_token="")

    def test_none_access_token_raises_value_error(self):
        with pytest.raises(ValueError, match="access_token is required"):
            GmailClient(access_token=None)  # type: ignore[arg-type]


# ===================================================================
# 2. list_messages tests
# ===================================================================


class TestListMessages:
    """Tests for GmailClient.list_messages."""

    @pytest.mark.asyncio
    async def test_returns_message_stubs_and_next_token(self, client, mock_httpx):
        mock_httpx.get.return_value = _make_response(
            json_data={
                "messages": [{"id": "m1", "threadId": "t1"}],
                "nextPageToken": "page2",
            }
        )
        messages, token = await client.list_messages()
        assert messages == [{"id": "m1", "threadId": "t1"}]
        assert token == "page2"

    @pytest.mark.asyncio
    async def test_empty_results(self, client, mock_httpx):
        mock_httpx.get.return_value = _make_response(json_data={})
        messages, token = await client.list_messages()
        assert messages == []
        assert token is None

    @pytest.mark.asyncio
    async def test_with_query_parameter(self, client, mock_httpx):
        mock_httpx.get.return_value = _make_response(
            json_data={"messages": [], "nextPageToken": None}
        )
        await client.list_messages(query="is:unread")
        call_kwargs = mock_httpx.get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params")
        assert params["q"] == "is:unread"

    @pytest.mark.asyncio
    async def test_with_label_filter(self, client, mock_httpx):
        mock_httpx.get.return_value = _make_response(json_data={"messages": []})
        await client.list_messages(label_ids=["INBOX", "STARRED"])
        call_kwargs = mock_httpx.get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params")
        assert params["labelIds"] == "INBOX,STARRED"

    @pytest.mark.asyncio
    async def test_with_page_token(self, client, mock_httpx):
        mock_httpx.get.return_value = _make_response(json_data={"messages": []})
        await client.list_messages(page_token="tok123")
        call_kwargs = mock_httpx.get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params")
        assert params["pageToken"] == "tok123"


# ===================================================================
# 3. get_message tests
# ===================================================================


class TestGetMessage:
    """Tests for GmailClient.get_message."""

    @pytest.mark.asyncio
    async def test_parses_full_message(self, client, mock_httpx):
        mock_httpx.get.return_value = _make_response(
            json_data={
                "id": "msg1",
                "threadId": "th1",
                "internalDate": "1700000000000",
                "labelIds": ["INBOX"],
                "snippet": "Hello there",
                "payload": {
                    "mimeType": "text/plain",
                    "headers": [
                        {"name": "Subject", "value": "Test Subject"},
                        {"name": "From", "value": "alice@example.com"},
                        {"name": "To", "value": "bob@example.com"},
                    ],
                    "body": {"data": _b64("Hello world")},
                },
            }
        )
        msg = await client.get_message("msg1")
        assert isinstance(msg, EmailMessage)
        assert msg.gmail_id == "msg1"
        assert msg.thread_id == "th1"
        assert msg.subject == "Test Subject"
        assert msg.from_email == "alice@example.com"
        assert msg.to_emails == ["bob@example.com"]
        assert msg.body_text == "Hello world"
        assert msg.is_read is True
        assert msg.snippet == "Hello there"

    @pytest.mark.asyncio
    async def test_handles_multipart_body(self, client, mock_httpx):
        mock_httpx.get.return_value = _make_response(
            json_data={
                "id": "msg2",
                "threadId": "th2",
                "labelIds": [],
                "snippet": "",
                "payload": {
                    "mimeType": "multipart/alternative",
                    "headers": [{"name": "Subject", "value": "Multi"}],
                    "parts": [
                        {
                            "mimeType": "text/plain",
                            "body": {"data": _b64("plain text")},
                        },
                        {
                            "mimeType": "text/html",
                            "body": {"data": _b64("<p>html</p>")},
                        },
                    ],
                },
            }
        )
        msg = await client.get_message("msg2")
        assert msg.body_text == "plain text"
        assert msg.body_html == "<p>html</p>"

    @pytest.mark.asyncio
    async def test_handles_nested_multipart(self, client, mock_httpx):
        mock_httpx.get.return_value = _make_response(
            json_data={
                "id": "msg3",
                "threadId": "th3",
                "labelIds": [],
                "snippet": "",
                "payload": {
                    "mimeType": "multipart/mixed",
                    "headers": [],
                    "parts": [
                        {
                            "mimeType": "multipart/alternative",
                            "parts": [
                                {
                                    "mimeType": "text/plain",
                                    "body": {"data": _b64("nested plain")},
                                },
                                {
                                    "mimeType": "text/html",
                                    "body": {"data": _b64("<b>nested html</b>")},
                                },
                            ],
                        },
                    ],
                },
            }
        )
        msg = await client.get_message("msg3")
        assert msg.body_text == "nested plain"
        assert msg.body_html == "<b>nested html</b>"

    @pytest.mark.asyncio
    async def test_handles_missing_fields(self, client, mock_httpx):
        mock_httpx.get.return_value = _make_response(
            json_data={
                "id": "msg4",
                "threadId": "th4",
                "payload": {"headers": [], "mimeType": "text/plain", "body": {}},
            }
        )
        msg = await client.get_message("msg4")
        assert msg.gmail_id == "msg4"
        assert msg.subject == ""
        assert msg.from_email == ""
        assert msg.to_emails == []
        assert msg.body_text == ""
        assert msg.received_at is None


# ===================================================================
# 4. send_message tests
# ===================================================================


class TestSendMessage:
    """Tests for GmailClient.send_message."""

    @pytest.mark.asyncio
    async def test_sends_message_successfully(self, client, mock_httpx):
        mock_httpx.post.return_value = _make_response(json_data={"id": "sent1", "threadId": "th1"})
        result = await client.send_message(
            to="bob@example.com",
            subject="Hi",
            body="Hello Bob",
        )
        assert result["id"] == "sent1"
        mock_httpx.post.assert_awaited_once()
        call_kwargs = mock_httpx.post.call_args
        url = call_kwargs[0][0] if call_kwargs[0] else call_kwargs.kwargs["url"]
        assert url.endswith("/messages/send")

    @pytest.mark.asyncio
    async def test_send_with_reply_to_and_thread_id(self, client, mock_httpx):
        mock_httpx.post.return_value = _make_response(json_data={"id": "sent2", "threadId": "th2"})
        result = await client.send_message(
            to="bob@example.com",
            subject="Re: Hi",
            body="Reply",
            reply_to_message_id="<orig@mail.com>",
            thread_id="th2",
        )
        assert result["threadId"] == "th2"
        call_kwargs = mock_httpx.post.call_args
        json_body = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert "threadId" in json_body

    @pytest.mark.asyncio
    async def test_send_http_error_raises_gmail_client_error(self, client, mock_httpx):
        mock_httpx.post.return_value = _make_response(status_code=403, text="Forbidden")
        with pytest.raises(GmailClientError, match="403"):
            await client.send_message(to="x@y.com", subject="Nope", body="fail")


# ===================================================================
# 5. modify_message tests
# ===================================================================


class TestModifyMessage:
    """Tests for GmailClient.modify_message and mark_as_read."""

    @pytest.mark.asyncio
    async def test_add_labels(self, client, mock_httpx):
        mock_httpx.post.return_value = _make_response(
            json_data={"id": "m1", "labelIds": ["STARRED"]}
        )
        result = await client.modify_message("m1", add_labels=["STARRED"])
        assert result["labelIds"] == ["STARRED"]
        call_kwargs = mock_httpx.post.call_args
        json_body = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert json_body["addLabelIds"] == ["STARRED"]

    @pytest.mark.asyncio
    async def test_remove_labels(self, client, mock_httpx):
        mock_httpx.post.return_value = _make_response(json_data={"id": "m1", "labelIds": []})
        result = await client.modify_message("m1", remove_labels=["UNREAD"])
        call_kwargs = mock_httpx.post.call_args
        json_body = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert json_body["removeLabelIds"] == ["UNREAD"]
        assert result["id"] == "m1"

    @pytest.mark.asyncio
    async def test_mark_as_read(self, client, mock_httpx):
        mock_httpx.post.return_value = _make_response(json_data={"id": "m1", "labelIds": ["INBOX"]})
        result = await client.mark_as_read("m1")
        call_kwargs = mock_httpx.post.call_args
        json_body = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert json_body.get("removeLabelIds") == ["UNREAD"]
        assert result["id"] == "m1"


# ===================================================================
# 6. get_profile tests
# ===================================================================


class TestGetProfile:
    """Tests for GmailClient.get_profile."""

    @pytest.mark.asyncio
    async def test_returns_profile_data(self, client, mock_httpx):
        mock_httpx.get.return_value = _make_response(
            json_data={
                "emailAddress": "me@example.com",
                "messagesTotal": 100,
                "threadsTotal": 50,
                "historyId": "12345",
            }
        )
        profile = await client.get_profile()
        assert profile["emailAddress"] == "me@example.com"
        assert profile["historyId"] == "12345"

    @pytest.mark.asyncio
    async def test_error_handling(self, client, mock_httpx):
        mock_httpx.get.return_value = _make_response(status_code=401, text="Unauthorized")
        with pytest.raises(GmailClientError, match="401"):
            await client.get_profile()


# ===================================================================
# 7. get_history tests
# ===================================================================


class TestGetHistory:
    """Tests for GmailClient.get_history."""

    @pytest.mark.asyncio
    async def test_returns_history_and_latest_id(self, client, mock_httpx):
        mock_httpx.get.return_value = _make_response(
            json_data={
                "history": [
                    {"id": "100", "messages": [{"id": "m1"}]},
                    {"id": "101", "messages": [{"id": "m2"}]},
                ],
                "historyId": "102",
            }
        )
        history, latest_id = await client.get_history("99")
        assert len(history) == 2
        assert latest_id == "102"

    @pytest.mark.asyncio
    async def test_empty_history(self, client, mock_httpx):
        mock_httpx.get.return_value = _make_response(json_data={"historyId": "99"})
        history, latest_id = await client.get_history("99")
        assert history == []
        assert latest_id == "99"

    @pytest.mark.asyncio
    async def test_get_history_passes_params(self, client, mock_httpx):
        mock_httpx.get.return_value = _make_response(json_data={"historyId": "50"})
        await client.get_history("42", max_results=25)
        call_kwargs = mock_httpx.get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params")
        assert params["startHistoryId"] == "42"
        assert params["maxResults"] == 25


# ===================================================================
# 8. _parse_message tests
# ===================================================================


class TestParseMessage:
    """Tests for GmailClient._parse_message."""

    def test_handles_internal_date_parsing(self, client):
        data = {
            "id": "pm1",
            "threadId": "t1",
            "internalDate": "1700000000000",
            "labelIds": [],
            "snippet": "",
            "payload": {"headers": [], "mimeType": "text/plain", "body": {}},
        }
        msg = client._parse_message(data)
        assert msg.received_at is not None
        assert isinstance(msg.received_at, datetime)

    def test_handles_invalid_internal_date(self, client):
        data = {
            "id": "pm2",
            "threadId": "t2",
            "internalDate": "not-a-number",
            "labelIds": [],
            "snippet": "",
            "payload": {"headers": [], "mimeType": "text/plain", "body": {}},
        }
        msg = client._parse_message(data)
        assert msg.received_at is None

    def test_handles_missing_internal_date(self, client):
        data = {
            "id": "pm3",
            "threadId": "t3",
            "labelIds": [],
            "snippet": "",
            "payload": {"headers": [], "mimeType": "text/plain", "body": {}},
        }
        msg = client._parse_message(data)
        assert msg.received_at is None

    def test_unread_detection(self, client):
        data = {
            "id": "pm4",
            "threadId": "t4",
            "labelIds": ["INBOX", "UNREAD"],
            "snippet": "",
            "payload": {"headers": [], "mimeType": "text/plain", "body": {}},
        }
        msg = client._parse_message(data)
        assert msg.is_read is False

    def test_read_detection(self, client):
        data = {
            "id": "pm5",
            "threadId": "t5",
            "labelIds": ["INBOX"],
            "snippet": "",
            "payload": {"headers": [], "mimeType": "text/plain", "body": {}},
        }
        msg = client._parse_message(data)
        assert msg.is_read is True

    def test_cc_parsing(self, client):
        data = {
            "id": "pm6",
            "threadId": "t6",
            "labelIds": [],
            "snippet": "",
            "payload": {
                "headers": [
                    {"name": "Cc", "value": "cc1@example.com, cc2@example.com"},
                ],
                "mimeType": "text/plain",
                "body": {},
            },
        }
        msg = client._parse_message(data)
        assert msg.cc_emails == ["cc1@example.com", "cc2@example.com"]


# ===================================================================
# 9. _extract_body tests
# ===================================================================


class TestExtractBody:
    """Tests for GmailClient._extract_body."""

    def test_text_plain_body(self, client):
        payload = {
            "mimeType": "text/plain",
            "body": {"data": _b64("plain content")},
        }
        text, html = client._extract_body(payload)
        assert text == "plain content"
        assert html == ""

    def test_text_html_body(self, client):
        payload = {
            "mimeType": "text/html",
            "body": {"data": _b64("<h1>HTML</h1>")},
        }
        text, html = client._extract_body(payload)
        assert text == ""
        assert html == "<h1>HTML</h1>"

    def test_multipart_with_nested_parts(self, client):
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {
                            "mimeType": "text/plain",
                            "body": {"data": _b64("inner text")},
                        },
                        {
                            "mimeType": "text/html",
                            "body": {"data": _b64("<em>inner html</em>")},
                        },
                    ],
                },
            ],
        }
        text, html = client._extract_body(payload)
        assert text == "inner text"
        assert html == "<em>inner html</em>"

    def test_empty_body_data(self, client):
        payload = {
            "mimeType": "text/plain",
            "body": {"data": ""},
        }
        text, html = client._extract_body(payload)
        assert text == ""
        assert html == ""

    def test_missing_body_key(self, client):
        payload = {
            "mimeType": "text/plain",
            "body": {},
        }
        text, html = client._extract_body(payload)
        assert text == ""
        assert html == ""

    def test_multipart_skips_duplicate_text_plain(self, client):
        """When text_body is already extracted, subsequent text/plain parts are skipped."""
        payload = {
            "mimeType": "multipart/alternative",
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": _b64("first plain")},
                },
                {
                    "mimeType": "text/plain",
                    "body": {"data": _b64("second plain")},
                },
            ],
        }
        text, html = client._extract_body(payload)
        assert text == "first plain"
        assert html == ""

    def test_multipart_skips_duplicate_text_html(self, client):
        """When html_body is already extracted, subsequent text/html parts are skipped."""
        payload = {
            "mimeType": "multipart/alternative",
            "parts": [
                {
                    "mimeType": "text/html",
                    "body": {"data": _b64("<b>first</b>")},
                },
                {
                    "mimeType": "text/html",
                    "body": {"data": _b64("<i>second</i>")},
                },
            ],
        }
        text, html = client._extract_body(payload)
        assert text == ""
        assert html == "<b>first</b>"

    def test_nested_multipart_skips_when_already_populated(self, client):
        """Nested multipart does not overwrite already-extracted bodies."""
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": _b64("top-level plain")},
                },
                {
                    "mimeType": "text/html",
                    "body": {"data": _b64("<p>top-level html</p>")},
                },
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {
                            "mimeType": "text/plain",
                            "body": {"data": _b64("nested plain")},
                        },
                        {
                            "mimeType": "text/html",
                            "body": {"data": _b64("<p>nested html</p>")},
                        },
                    ],
                },
            ],
        }
        text, html = client._extract_body(payload)
        assert text == "top-level plain"
        assert html == "<p>top-level html</p>"


# ===================================================================
# 10. Rate limiting tests
# ===================================================================


class TestRateLimiting:
    """Tests for the rate limiter."""

    def test_semaphore_exists(self, client):
        assert isinstance(client._semaphore, asyncio.Semaphore)

    @pytest.mark.asyncio
    async def test_rate_limit_resets_window(self, client):
        """Calling _rate_limit increments the request count."""
        await client._rate_limit()
        assert client._request_count >= 1


# ===================================================================
# 11. Error handling tests
# ===================================================================


class TestErrorHandling:
    """Tests for _get and _post error paths."""

    @pytest.mark.asyncio
    async def test_get_raises_on_http_status_error(self, client, mock_httpx):
        mock_httpx.get.return_value = _make_response(status_code=500, text="Internal Server Error")
        with pytest.raises(GmailClientError, match="500"):
            await client._get("https://example.com/test")

    @pytest.mark.asyncio
    async def test_get_raises_on_request_error(self, client, mock_httpx):
        mock_httpx.get.side_effect = httpx.RequestError(
            "Connection refused", request=MagicMock(spec=httpx.Request)
        )
        with pytest.raises(GmailClientError, match="request failed"):
            await client._get("https://example.com/test")

    @pytest.mark.asyncio
    async def test_post_raises_on_http_status_error(self, client, mock_httpx):
        mock_httpx.post.return_value = _make_response(status_code=429, text="Rate limited")
        with pytest.raises(GmailClientError, match="429"):
            await client._post("https://example.com/test", {})

    @pytest.mark.asyncio
    async def test_post_raises_on_request_error(self, client, mock_httpx):
        mock_httpx.post.side_effect = httpx.RequestError(
            "Timeout", request=MagicMock(spec=httpx.Request)
        )
        with pytest.raises(GmailClientError, match="request failed"):
            await client._post("https://example.com/test", {})


# ===================================================================
# 12. EmailMessage.to_dict tests
# ===================================================================


class TestEmailMessageToDict:
    """Tests for the EmailMessage dataclass."""

    def test_to_dict_with_received_at(self):
        dt = datetime(2024, 1, 15, 10, 30, 0)
        msg = EmailMessage(
            gmail_id="d1",
            thread_id="t1",
            subject="S",
            from_email="a@b.com",
            to_emails=["c@d.com"],
            cc_emails=[],
            body_text="body",
            received_at=dt,
            labels=["INBOX"],
            is_read=True,
            snippet="snip",
        )
        d = msg.to_dict()
        assert d["gmail_id"] == "d1"
        assert d["received_at"] == dt.isoformat()
        assert d["is_read"] is True

    def test_to_dict_without_received_at(self):
        msg = EmailMessage(gmail_id="d2", thread_id="t2")
        d = msg.to_dict()
        assert d["received_at"] is None
        assert d["subject"] == ""


# ===================================================================
# 13. Headers helper test
# ===================================================================


class TestDecodeBodyData:
    """Tests for GmailClient._decode_body_data."""

    def test_valid_base64(self, client):
        encoded = _b64("hello world")
        result = client._decode_body_data({"data": encoded})
        assert result == "hello world"

    def test_empty_data_returns_empty_string(self, client):
        assert client._decode_body_data({"data": ""}) == ""

    def test_missing_data_key_returns_empty_string(self, client):
        assert client._decode_body_data({}) == ""

    def test_corrupt_base64_returns_empty_string(self, client):
        """Corrupt data that cannot be base64-decoded returns empty string."""
        # Patch urlsafe_b64decode to raise an exception to cover the
        # generic except branch (lines 387-388).
        with patch(
            "zetherion_ai.skills.gmail.client.base64.urlsafe_b64decode",
            side_effect=Exception("decode boom"),
        ):
            result = client._decode_body_data({"data": "not-valid"})
        assert result == ""


class TestHeaders:
    """Test _headers method."""

    def test_authorization_header(self, client):
        headers = client._headers()
        assert headers == {"Authorization": "Bearer test-access-token"}
