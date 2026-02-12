"""Tests for the dev observation adapter."""

from datetime import datetime

from zetherion_ai.observation.adapters.dev import DevObservationAdapter
from zetherion_ai.observation.models import ObservationEvent


class TestDevObservationAdapter:
    def test_constructor_stores_owner_id(self):
        adapter = DevObservationAdapter(owner_user_id=123)
        assert adapter._owner_user_id == 123

    def test_adapt_commit_event(self):
        adapter = DevObservationAdapter(owner_user_id=456)
        event = adapter.adapt(
            event_type="commit",
            fields={"sha": "abc1234", "project": "test-project", "branch": "main"},
            content="feat: Add new feature",
        )
        assert isinstance(event, ObservationEvent)
        assert event.source == "dev_agent"
        assert event.source_id == "abc1234"
        assert event.user_id == 456
        assert event.author == "dev-agent"
        assert event.author_is_owner is True
        assert event.content == "feat: Add new feature"
        assert event.context["event_type"] == "commit"
        assert event.context["project"] == "test-project"

    def test_adapt_uses_sha_for_source_id(self):
        adapter = DevObservationAdapter(owner_user_id=1)
        event = adapter.adapt("commit", {"sha": "deadbeef"}, "msg")
        assert event.source_id == "deadbeef"

    def test_adapt_generates_uuid_when_no_sha(self):
        adapter = DevObservationAdapter(owner_user_id=1)
        event = adapter.adapt("session", {"project": "test"}, "session summary")
        # source_id should be a UUID string (36 chars with dashes)
        assert len(event.source_id) == 36

    def test_adapt_annotation_event(self):
        adapter = DevObservationAdapter(owner_user_id=1)
        event = adapter.adapt(
            "annotation",
            {"project": "proj", "annotation_type": "TODO", "file": "main.py", "line": "42"},
            "Fix this bug",
        )
        assert event.context["event_type"] == "annotation"
        assert event.context["annotation_type"] == "TODO"
        assert event.context["file"] == "main.py"

    def test_adapt_session_event(self):
        adapter = DevObservationAdapter(owner_user_id=1)
        event = adapter.adapt(
            "session",
            {"session_id": "sess123", "tools_used": "5"},
            "Session summary",
        )
        assert event.context["event_type"] == "session"
        assert event.context["session_id"] == "sess123"

    def test_adapt_tag_event(self):
        adapter = DevObservationAdapter(owner_user_id=1)
        event = adapter.adapt("tag", {"tag_name": "v1.0.0", "sha": "abc"}, "New tag")
        assert event.context["event_type"] == "tag"
        assert event.context["tag_name"] == "v1.0.0"
        assert event.source_id == "abc"

    def test_adapt_with_explicit_timestamp(self):
        adapter = DevObservationAdapter(owner_user_id=1)
        ts = datetime(2026, 1, 15, 12, 0, 0)
        event = adapter.adapt("commit", {"sha": "abc"}, "msg", timestamp=ts)
        assert event.timestamp == ts

    def test_adapt_without_timestamp_uses_now(self):
        adapter = DevObservationAdapter(owner_user_id=1)
        before = datetime.now()
        event = adapter.adapt("commit", {"sha": "abc"}, "msg")
        after = datetime.now()
        assert before <= event.timestamp <= after
