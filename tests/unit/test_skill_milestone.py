"""Tests for Milestone & Promotion Skill."""

from datetime import datetime
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from zetherion_ai.skills.base import SkillRequest, SkillStatus
from zetherion_ai.skills.milestone import (
    DRAFT_THRESHOLD,
    MILESTONES_COLLECTION,
    PLATFORMS,
    Milestone,
    MilestoneSkill,
    PromoDraft,
    _generate_draft,
    _generate_github_draft,
    _generate_linkedin_draft,
    _generate_x_draft,
    _platform_label,
    _score_significance,
    _status_icon,
)
from zetherion_ai.skills.permissions import Permission

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def skill():
    """Create a MilestoneSkill instance without memory."""
    return MilestoneSkill(memory=None)


@pytest.fixture
def skill_with_memory():
    """Create a MilestoneSkill instance with mock memory."""
    memory = AsyncMock()
    memory.ensure_collection = AsyncMock(return_value=True)
    memory.store_with_payload = AsyncMock()
    memory.filter_by_field = AsyncMock(return_value=[])
    return MilestoneSkill(memory=memory)


@pytest.fixture
def sample_milestone():
    """Create a sample Milestone instance."""
    return Milestone(
        user_id="user123",
        title="feat: Add personal understanding layer",
        description="Added Phase 9 personal understanding layer with dev watcher",
        category="feature",
        significance=8,
        detected_from="commit",
        source_entries=["entry1", "entry2"],
        status="detected",
    )


@pytest.fixture
def sample_draft(sample_milestone):
    """Create a sample PromoDraft instance."""
    return PromoDraft(
        milestone_id=sample_milestone.id,
        user_id="user123",
        platform="x",
        content="Test tweet about milestone",
        status="pending",
    )


def _make_request(
    intent: str,
    user_id: str = "user123",
    message: str = "",
    context: dict | None = None,
) -> SkillRequest:
    """Helper to build a SkillRequest."""
    return SkillRequest(
        id=uuid4(),
        user_id=user_id,
        intent=intent,
        message=message,
        context=context or {},
    )


# ------------------------------------------------------------------
# 1. Milestone data model
# ------------------------------------------------------------------


class TestMilestoneDataModel:
    """Tests for the Milestone dataclass to_dict/from_dict."""

    def test_to_dict_returns_all_fields(self, sample_milestone):
        d = sample_milestone.to_dict()
        assert d["user_id"] == "user123"
        assert d["title"] == "feat: Add personal understanding layer"
        assert d["category"] == "feature"
        assert d["significance"] == 8
        assert d["detected_from"] == "commit"
        assert d["source_entries"] == ["entry1", "entry2"]
        assert d["status"] == "detected"
        assert d["id"] == str(sample_milestone.id)
        assert "created_at" in d

    def test_from_dict_roundtrip(self, sample_milestone):
        d = sample_milestone.to_dict()
        restored = Milestone.from_dict(d)
        assert restored.id == sample_milestone.id
        assert restored.user_id == sample_milestone.user_id
        assert restored.title == sample_milestone.title
        assert restored.description == sample_milestone.description
        assert restored.category == sample_milestone.category
        assert restored.significance == sample_milestone.significance

    def test_from_dict_without_id_generates_new(self):
        d = {"title": "hello", "user_id": "u1"}
        m = Milestone.from_dict(d)
        assert isinstance(m.id, UUID)
        assert m.title == "hello"

    def test_from_dict_without_created_at_uses_now(self):
        d = {"title": "hello", "user_id": "u1"}
        m = Milestone.from_dict(d)
        # created_at should be approximately now
        assert (datetime.now() - m.created_at).total_seconds() < 5

    def test_from_dict_with_created_at_parses_iso(self):
        ts = "2024-06-15T10:30:00"
        d = {"title": "test", "created_at": ts}
        m = Milestone.from_dict(d)
        assert m.created_at == datetime.fromisoformat(ts)


# ------------------------------------------------------------------
# 2. PromoDraft data model
# ------------------------------------------------------------------


class TestPromoDraftDataModel:
    """Tests for the PromoDraft dataclass to_dict/from_dict."""

    def test_to_dict_returns_all_fields(self, sample_draft):
        d = sample_draft.to_dict()
        assert d["user_id"] == "user123"
        assert d["platform"] == "x"
        assert d["content"] == "Test tweet about milestone"
        assert d["status"] == "pending"
        assert d["id"] == str(sample_draft.id)
        assert d["milestone_id"] == str(sample_draft.milestone_id)
        assert "created_at" in d

    def test_from_dict_roundtrip(self, sample_draft):
        d = sample_draft.to_dict()
        restored = PromoDraft.from_dict(d)
        assert restored.id == sample_draft.id
        assert restored.milestone_id == sample_draft.milestone_id
        assert restored.user_id == sample_draft.user_id
        assert restored.platform == sample_draft.platform
        assert restored.content == sample_draft.content
        assert restored.status == sample_draft.status

    def test_from_dict_without_id_generates_new(self):
        d = {"platform": "linkedin", "content": "hello"}
        draft = PromoDraft.from_dict(d)
        assert isinstance(draft.id, UUID)
        assert draft.platform == "linkedin"

    def test_from_dict_without_milestone_id_generates_new(self):
        d = {"platform": "x", "content": "hello"}
        draft = PromoDraft.from_dict(d)
        assert isinstance(draft.milestone_id, UUID)

    def test_from_dict_defaults_status_to_pending(self):
        d = {"platform": "x"}
        draft = PromoDraft.from_dict(d)
        assert draft.status == "pending"

    def test_from_dict_without_created_at_uses_now(self):
        d = {"platform": "x"}
        draft = PromoDraft.from_dict(d)
        assert (datetime.now() - draft.created_at).total_seconds() < 5


# ------------------------------------------------------------------
# 3. Skill metadata
# ------------------------------------------------------------------


class TestMilestoneSkillMetadata:
    """Tests for MilestoneSkill metadata properties."""

    def test_name(self, skill):
        assert skill.metadata.name == "milestone_tracker"
        assert skill.name == "milestone_tracker"

    def test_version(self, skill):
        assert skill.metadata.version == "1.0.0"

    def test_intents(self, skill):
        expected = [
            "milestone_list",
            "milestone_drafts",
            "milestone_approve",
            "milestone_reject",
            "milestone_detect",
        ]
        assert skill.metadata.intents == expected

    def test_permissions(self, skill):
        perms = skill.metadata.permissions
        assert Permission.READ_OWN_COLLECTION in perms
        assert Permission.WRITE_OWN_COLLECTION in perms
        assert Permission.SEND_MESSAGES in perms
        assert Permission.READ_PROFILE in perms

    def test_collections(self, skill):
        assert skill.metadata.collections == [MILESTONES_COLLECTION]
        assert MILESTONES_COLLECTION == "skill_milestones"

    def test_has_five_intents(self, skill):
        assert len(skill.metadata.intents) == 5


# ------------------------------------------------------------------
# 4. Initialization
# ------------------------------------------------------------------


class TestMilestoneInitialization:
    """Tests for skill initialization paths."""

    @pytest.mark.asyncio
    async def test_initialize_no_memory(self, skill):
        result = await skill.safe_initialize()
        assert result is True
        assert skill.status == SkillStatus.READY

    @pytest.mark.asyncio
    async def test_initialize_with_memory(self, skill_with_memory):
        result = await skill_with_memory.safe_initialize()
        assert result is True
        assert skill_with_memory.status == SkillStatus.READY
        skill_with_memory._memory.ensure_collection.assert_awaited_once_with(
            MILESTONES_COLLECTION, vector_size=768
        )

    @pytest.mark.asyncio
    async def test_initialize_memory_failure(self):
        memory = AsyncMock()
        memory.ensure_collection = AsyncMock(side_effect=RuntimeError("connection failed"))
        skill = MilestoneSkill(memory=memory)
        result = await skill.safe_initialize()
        # initialize() catches the exception and returns False
        assert result is False
        assert skill.status == SkillStatus.ERROR


# ------------------------------------------------------------------
# 5. Handle routing
# ------------------------------------------------------------------


class TestMilestoneHandleRouting:
    """Tests for the handle() dispatch."""

    @pytest.mark.asyncio
    async def test_unknown_intent_returns_error(self, skill):
        await skill.safe_initialize()
        req = _make_request(intent="milestone_nonexistent")
        resp = await skill.handle(req)
        assert resp.success is False
        assert "Unknown intent" in resp.error


# ------------------------------------------------------------------
# 6. milestone_list
# ------------------------------------------------------------------


class TestMilestoneList:
    """Tests for the milestone_list intent."""

    @pytest.mark.asyncio
    async def test_no_milestones(self, skill):
        await skill.safe_initialize()
        req = _make_request(intent="milestone_list")
        resp = await skill.handle(req)
        assert resp.success is True
        assert "No milestones detected" in resp.message

    @pytest.mark.asyncio
    async def test_with_milestones_sorted_by_date(self, skill):
        await skill.safe_initialize()

        # Insert milestones with different dates
        older = Milestone(
            user_id="user123",
            title="Old milestone",
            significance=5,
            category="feature",
            created_at=datetime(2024, 1, 1),
        )
        newer = Milestone(
            user_id="user123",
            title="New milestone",
            significance=7,
            category="release",
            created_at=datetime(2024, 6, 1),
        )
        skill._milestones_cache["user123"] = {
            older.id: older,
            newer.id: newer,
        }

        req = _make_request(intent="milestone_list")
        resp = await skill.handle(req)
        assert resp.success is True
        assert "Milestones (2)" in resp.message
        # Newer should appear first
        new_pos = resp.message.index("New milestone")
        old_pos = resp.message.index("Old milestone")
        assert new_pos < old_pos

    @pytest.mark.asyncio
    async def test_list_capped_at_10(self, skill):
        await skill.safe_initialize()

        # Insert 15 milestones
        milestones = {}
        for i in range(15):
            m = Milestone(
                user_id="user123",
                title=f"Milestone {i}",
                significance=5,
                category="feature",
                created_at=datetime(2024, 1, i + 1),
            )
            milestones[m.id] = m
        skill._milestones_cache["user123"] = milestones

        req = _make_request(intent="milestone_list")
        resp = await skill.handle(req)
        assert resp.success is True
        assert "Milestones (15)" in resp.message
        # Data should only contain 10
        assert len(resp.data["milestones"]) == 10


# ------------------------------------------------------------------
# 7. milestone_drafts
# ------------------------------------------------------------------


class TestMilestoneDrafts:
    """Tests for the milestone_drafts intent."""

    @pytest.mark.asyncio
    async def test_no_pending_drafts(self, skill):
        await skill.safe_initialize()
        req = _make_request(intent="milestone_drafts")
        resp = await skill.handle(req)
        assert resp.success is True
        assert "No pending promo drafts" in resp.message

    @pytest.mark.asyncio
    async def test_pending_drafts_grouped_by_milestone(self, skill, sample_milestone):
        await skill.safe_initialize()

        # Store the milestone
        skill._milestones_cache["user123"] = {sample_milestone.id: sample_milestone}

        # Create drafts for different platforms
        draft_x = PromoDraft(
            user_id="user123",
            milestone_id=sample_milestone.id,
            platform="x",
            content="Tweet content",
            status="pending",
        )
        draft_li = PromoDraft(
            user_id="user123",
            milestone_id=sample_milestone.id,
            platform="linkedin",
            content="LinkedIn content",
            status="pending",
        )
        draft_rejected = PromoDraft(
            user_id="user123",
            milestone_id=sample_milestone.id,
            platform="github",
            content="Already rejected",
            status="rejected",
        )
        skill._drafts_cache["user123"] = {
            draft_x.id: draft_x,
            draft_li.id: draft_li,
            draft_rejected.id: draft_rejected,
        }

        req = _make_request(intent="milestone_drafts")
        resp = await skill.handle(req)
        assert resp.success is True
        assert "Pending Promo Drafts (2)" in resp.message
        assert sample_milestone.title in resp.message
        # Only pending drafts in data
        assert len(resp.data["drafts"]) == 2


# ------------------------------------------------------------------
# 8. milestone_approve
# ------------------------------------------------------------------


class TestMilestoneApprove:
    """Tests for the milestone_approve intent."""

    @pytest.mark.asyncio
    async def test_no_draft_id_returns_error(self, skill):
        await skill.safe_initialize()
        req = _make_request(intent="milestone_approve", context={})
        resp = await skill.handle(req)
        assert resp.success is False
        assert "specify which draft" in resp.error

    @pytest.mark.asyncio
    async def test_approve_found_draft(self, skill, sample_milestone):
        await skill.safe_initialize()

        draft = PromoDraft(
            user_id="user123",
            milestone_id=sample_milestone.id,
            platform="x",
            content="Test tweet",
        )
        skill._drafts_cache["user123"] = {draft.id: draft}

        req = _make_request(
            intent="milestone_approve",
            context={"draft_id": str(draft.id)},
        )
        resp = await skill.handle(req)
        assert resp.success is True
        assert "Approved" in resp.message
        assert "X/Twitter" in resp.message
        assert resp.data["draft"]["status"] == "approved"
        assert draft.status == "approved"

    @pytest.mark.asyncio
    async def test_approve_draft_not_found(self, skill):
        await skill.safe_initialize()

        req = _make_request(
            intent="milestone_approve",
            context={"draft_id": "nonexistent-id"},
        )
        resp = await skill.handle(req)
        assert resp.success is False
        assert "Draft not found" in resp.error


# ------------------------------------------------------------------
# 9. milestone_reject
# ------------------------------------------------------------------


class TestMilestoneReject:
    """Tests for the milestone_reject intent."""

    @pytest.mark.asyncio
    async def test_no_draft_id_returns_error(self, skill):
        await skill.safe_initialize()
        req = _make_request(intent="milestone_reject", context={})
        resp = await skill.handle(req)
        assert resp.success is False
        assert "specify which draft" in resp.error

    @pytest.mark.asyncio
    async def test_reject_found_draft(self, skill, sample_milestone):
        await skill.safe_initialize()

        draft = PromoDraft(
            user_id="user123",
            milestone_id=sample_milestone.id,
            platform="linkedin",
            content="Test LinkedIn post",
        )
        skill._drafts_cache["user123"] = {draft.id: draft}

        req = _make_request(
            intent="milestone_reject",
            context={"draft_id": str(draft.id)},
        )
        resp = await skill.handle(req)
        assert resp.success is True
        assert "Rejected" in resp.message
        assert "LinkedIn" in resp.message
        assert resp.data["draft"]["status"] == "rejected"
        assert draft.status == "rejected"

    @pytest.mark.asyncio
    async def test_reject_draft_not_found(self, skill):
        await skill.safe_initialize()

        req = _make_request(
            intent="milestone_reject",
            context={"draft_id": "nonexistent-id"},
        )
        resp = await skill.handle(req)
        assert resp.success is False
        assert "Draft not found" in resp.error


# ------------------------------------------------------------------
# 10. milestone_detect
# ------------------------------------------------------------------


class TestMilestoneDetect:
    """Tests for the milestone_detect intent."""

    @pytest.mark.asyncio
    async def test_below_threshold_returns_significance(self, skill):
        await skill.safe_initialize()

        req = _make_request(
            intent="milestone_detect",
            message="chore: update readme",
            context={
                "event_type": "commit",
                "title": "chore: update readme",
                "description": "Updated readme with new examples",
            },
        )
        resp = await skill.handle(req)
        assert resp.success is True
        assert resp.data["milestone"] is False
        assert "significance" in resp.data
        assert "below milestone threshold" in resp.message

    @pytest.mark.asyncio
    async def test_above_threshold_creates_milestone_and_drafts(self, skill):
        await skill.safe_initialize()

        req = _make_request(
            intent="milestone_detect",
            message="feat: Add personal understanding layer",
            context={
                "event_type": "commit",
                "title": "feat: Add personal understanding layer",
                "description": "Added Phase 9 personal understanding layer with dev watcher",
                "files_changed": "12",
            },
        )
        resp = await skill.handle(req)
        assert resp.success is True
        assert "Milestone detected" in resp.message
        assert "milestone" in resp.data
        assert "drafts" in resp.data
        # Should generate one draft per platform
        assert len(resp.data["drafts"]) == len(PLATFORMS)
        assert resp.data["milestone"]["category"] == "feature"
        assert resp.data["milestone"]["significance"] == 8

        # Verify the milestone is stored in cache
        assert "user123" in skill._milestones_cache
        assert len(skill._milestones_cache["user123"]) == 1

        # Verify drafts are stored in cache
        assert "user123" in skill._drafts_cache
        assert len(skill._drafts_cache["user123"]) == 3

    @pytest.mark.asyncio
    async def test_detect_tag_event(self, skill):
        await skill.safe_initialize()

        req = _make_request(
            intent="milestone_detect",
            message="v2.0.0",
            context={
                "event_type": "tag",
                "title": "v2.0.0",
                "description": "Major release",
            },
        )
        resp = await skill.handle(req)
        assert resp.success is True
        assert "Milestone detected" in resp.message
        assert resp.data["milestone"]["category"] == "release"
        assert resp.data["milestone"]["significance"] == 8

    @pytest.mark.asyncio
    async def test_detect_truncates_description_to_500(self, skill):
        await skill.safe_initialize()

        long_desc = "x" * 1000
        req = _make_request(
            intent="milestone_detect",
            message="feat: something big",
            context={
                "event_type": "commit",
                "title": "feat: something big",
                "description": long_desc,
                "files_changed": "10",
            },
        )
        resp = await skill.handle(req)
        assert resp.success is True
        # The stored milestone description should be truncated
        milestone_data = resp.data["milestone"]
        assert len(milestone_data["description"]) <= 500


# ------------------------------------------------------------------
# 11. Heartbeat
# ------------------------------------------------------------------


class TestMilestoneHeartbeat:
    """Tests for the on_heartbeat method."""

    @pytest.mark.asyncio
    async def test_pending_drafts_generate_action(self, skill, sample_milestone):
        await skill.safe_initialize()

        draft = PromoDraft(
            user_id="user123",
            milestone_id=sample_milestone.id,
            platform="x",
            content="Tweet",
            status="pending",
        )
        skill._drafts_cache["user123"] = {draft.id: draft}

        actions = await skill.on_heartbeat(["user123"])
        assert len(actions) == 1
        assert actions[0].skill_name == "milestone_tracker"
        assert actions[0].action_type == "milestone_drafts_pending"
        assert actions[0].user_id == "user123"
        assert actions[0].data["count"] == 1
        assert "x" in actions[0].data["platforms"]
        assert actions[0].priority == 4

    @pytest.mark.asyncio
    async def test_no_actions_when_no_pending(self, skill):
        await skill.safe_initialize()

        actions = await skill.on_heartbeat(["user123"])
        assert actions == []

    @pytest.mark.asyncio
    async def test_heartbeat_multiple_users(self, skill, sample_milestone):
        await skill.safe_initialize()

        draft = PromoDraft(
            user_id="user123",
            milestone_id=sample_milestone.id,
            platform="linkedin",
            content="LinkedIn post",
            status="pending",
        )
        skill._drafts_cache["user123"] = {draft.id: draft}
        # user456 has no drafts

        actions = await skill.on_heartbeat(["user123", "user456"])
        assert len(actions) == 1
        assert actions[0].user_id == "user123"

    @pytest.mark.asyncio
    async def test_heartbeat_skips_non_pending_drafts(self, skill, sample_milestone):
        await skill.safe_initialize()

        draft = PromoDraft(
            user_id="user123",
            milestone_id=sample_milestone.id,
            platform="x",
            content="Approved tweet",
            status="approved",
        )
        skill._drafts_cache["user123"] = {draft.id: draft}

        actions = await skill.on_heartbeat(["user123"])
        assert actions == []


# ------------------------------------------------------------------
# 12. Storage helpers
# ------------------------------------------------------------------


class TestMilestoneStorage:
    """Tests for cache-based storage helpers."""

    @pytest.mark.asyncio
    async def test_store_and_get_milestone(self, skill, sample_milestone):
        await skill.safe_initialize()
        await skill._store_milestone(sample_milestone)

        milestones = await skill._get_user_milestones("user123")
        assert len(milestones) == 1
        assert milestones[0].id == sample_milestone.id

    @pytest.mark.asyncio
    async def test_store_draft_in_cache(self, skill, sample_draft):
        await skill.safe_initialize()
        await skill._store_draft(sample_draft)

        drafts = await skill._get_user_drafts("user123")
        assert len(drafts) == 1
        assert drafts[0].id == sample_draft.id

    @pytest.mark.asyncio
    async def test_find_draft_by_prefix(self, skill, sample_draft):
        await skill.safe_initialize()
        await skill._store_draft(sample_draft)

        prefix = str(sample_draft.id)[:8]
        found = await skill._find_draft("user123", prefix)
        assert found is not None
        assert found.id == sample_draft.id

    @pytest.mark.asyncio
    async def test_find_draft_by_full_id(self, skill, sample_draft):
        await skill.safe_initialize()
        await skill._store_draft(sample_draft)

        found = await skill._find_draft("user123", str(sample_draft.id))
        assert found is not None
        assert found.id == sample_draft.id

    @pytest.mark.asyncio
    async def test_find_draft_returns_none_for_unknown(self, skill):
        await skill.safe_initialize()

        found = await skill._find_draft("user123", "nonexistent")
        assert found is None

    @pytest.mark.asyncio
    async def test_get_milestones_empty_user(self, skill):
        await skill.safe_initialize()

        milestones = await skill._get_user_milestones("nobody")
        assert milestones == []

    @pytest.mark.asyncio
    async def test_get_drafts_empty_user(self, skill):
        await skill.safe_initialize()

        drafts = await skill._get_user_drafts("nobody")
        assert drafts == []

    @pytest.mark.asyncio
    async def test_store_milestone_with_memory(self, skill_with_memory, sample_milestone):
        await skill_with_memory.safe_initialize()
        await skill_with_memory._store_milestone(sample_milestone)

        skill_with_memory._memory.store_with_payload.assert_awaited_once()
        call_kwargs = skill_with_memory._memory.store_with_payload.call_args
        assert call_kwargs.kwargs["collection_name"] == MILESTONES_COLLECTION
        assert call_kwargs.kwargs["payload"]["_type"] == "milestone"

    @pytest.mark.asyncio
    async def test_store_draft_with_memory(self, skill_with_memory, sample_draft):
        await skill_with_memory.safe_initialize()
        await skill_with_memory._store_draft(sample_draft)

        skill_with_memory._memory.store_with_payload.assert_awaited_once()
        call_kwargs = skill_with_memory._memory.store_with_payload.call_args
        assert call_kwargs.kwargs["collection_name"] == MILESTONES_COLLECTION
        assert call_kwargs.kwargs["payload"]["_type"] == "draft"


# ------------------------------------------------------------------
# 13. Cleanup
# ------------------------------------------------------------------


class TestMilestoneCleanup:
    """Tests for the cleanup method."""

    @pytest.mark.asyncio
    async def test_cleanup_clears_both_caches(self, skill, sample_milestone, sample_draft):
        await skill.safe_initialize()
        await skill._store_milestone(sample_milestone)
        await skill._store_draft(sample_draft)

        assert len(skill._milestones_cache) > 0
        assert len(skill._drafts_cache) > 0

        await skill.cleanup()

        assert skill._milestones_cache == {}
        assert skill._drafts_cache == {}

    @pytest.mark.asyncio
    async def test_cleanup_on_empty_caches(self, skill):
        await skill.safe_initialize()
        # Should not raise
        await skill.cleanup()
        assert skill._milestones_cache == {}
        assert skill._drafts_cache == {}


# ------------------------------------------------------------------
# 14. _score_significance
# ------------------------------------------------------------------


class TestScoreSignificance:
    """Tests for the heuristic significance scoring function."""

    def test_tag_event(self):
        score, cat = _score_significance("tag", "v1.0.0", "First release", {})
        assert score == 8
        assert cat == "release"

    def test_feat_with_many_files(self):
        score, cat = _score_significance(
            "commit",
            "feat: Add personal understanding layer",
            "Large feature",
            {"files_changed": "12"},
        )
        assert score == 8
        assert cat == "feature"

    def test_feat_with_5_files(self):
        score, cat = _score_significance(
            "commit",
            "feat: Add new endpoint",
            "Medium feature",
            {"files_changed": "5"},
        )
        assert score == 7
        assert cat == "feature"

    def test_feat_with_few_files(self):
        score, cat = _score_significance(
            "commit",
            "feat: small tweak",
            "Small feature",
            {"files_changed": "2"},
        )
        assert score == 6
        assert cat == "feature"

    def test_feat_parenthesis_format(self):
        score, cat = _score_significance(
            "commit",
            "feat(api): Add new route",
            "New route",
            {"files_changed": "1"},
        )
        assert score == 6
        assert cat == "feature"

    def test_feat_with_no_files_changed(self):
        score, cat = _score_significance(
            "commit",
            "feat: Quick fix",
            "Quick",
            {},
        )
        assert score == 6
        assert cat == "feature"

    def test_architecture_keywords(self):
        score, cat = _score_significance(
            "commit",
            "Refactor core module",
            "Restructured the core",
            {"files_changed": "3"},
        )
        assert score == 6
        assert cat == "architecture"

    def test_architecture_with_many_files(self):
        score, cat = _score_significance(
            "commit",
            "Refactor project structure",
            "Major restructure",
            {"files_changed": "10"},
        )
        assert score == 8
        assert cat == "architecture"

    def test_architecture_docker_keyword(self):
        score, cat = _score_significance(
            "commit",
            "Update docker compose",
            "Docker changes",
            {},
        )
        assert score == 6
        assert cat == "architecture"

    def test_architecture_pipeline_keyword(self):
        score, cat = _score_significance(
            "commit",
            "Update ci/cd pipeline",
            "Pipeline changes",
            {},
        )
        assert score == 6
        assert cat == "architecture"

    def test_integration_keywords(self):
        score, cat = _score_significance(
            "commit",
            "Add Gmail integration",
            "Gmail OAuth setup",
            {},
        )
        assert score == 7
        assert cat == "integration"

    def test_integration_api_keyword(self):
        score, cat = _score_significance(
            "commit",
            "Add API endpoint",
            "REST endpoint for users",
            {},
        )
        assert score == 7
        assert cat == "integration"

    def test_integration_webhook_keyword(self):
        score, cat = _score_significance(
            "commit",
            "Add webhook listener",
            "Webhook for events",
            {},
        )
        assert score == 7
        assert cat == "integration"

    def test_security_keywords(self):
        score, cat = _score_significance(
            "commit",
            "Add encryption at rest",
            "Security improvement",
            {},
        )
        assert score == 6
        assert cat == "security"

    def test_security_rbac_keyword(self):
        score, cat = _score_significance(
            "commit",
            "Implement RBAC system",
            "Role-based access control",
            {},
        )
        assert score == 6
        assert cat == "security"

    def test_security_permission_keyword(self):
        score, cat = _score_significance(
            "commit",
            "Add permission checks",
            "Enforce permissions",
            {},
        )
        assert score == 6
        assert cat == "security"

    def test_test_keywords(self):
        score, cat = _score_significance(
            "commit",
            "Add pytest suite",
            "Test coverage improvements",
            {},
        )
        assert score == 5
        assert cat == "coverage"

    def test_coverage_keyword(self):
        score, cat = _score_significance(
            "commit",
            "Improve coverage",
            "More coverage",
            {},
        )
        assert score == 5
        assert cat == "coverage"

    def test_performance_keywords(self):
        score, cat = _score_significance(
            "commit",
            "Optimize query performance",
            "Speed improvements",
            {},
        )
        assert score == 5
        assert cat == "performance"

    def test_performance_cache_keyword(self):
        score, cat = _score_significance(
            "commit",
            "Add cache layer",
            "Caching responses",
            {},
        )
        assert score == 5
        assert cat == "performance"

    def test_default_maintenance(self):
        score, cat = _score_significance(
            "commit",
            "chore: bump version",
            "Bumped version",
            {},
        )
        assert score == 3
        assert cat == "maintenance"

    def test_default_for_unrecognized(self):
        score, cat = _score_significance(
            "commit",
            "misc update",
            "Just some changes",
            {},
        )
        assert score == 3
        assert cat == "maintenance"

    def test_files_changed_as_none(self):
        """files_changed can be None or empty string in context."""
        score, cat = _score_significance(
            "commit",
            "feat: something",
            "Some desc",
            {"files_changed": None},
        )
        assert score == 6
        assert cat == "feature"

    def test_files_changed_as_empty_string(self):
        score, cat = _score_significance(
            "commit",
            "feat: something",
            "Some desc",
            {"files_changed": ""},
        )
        assert score == 6
        assert cat == "feature"


# ------------------------------------------------------------------
# 15. Draft generation
# ------------------------------------------------------------------


class TestGenerateDraft:
    """Tests for the draft generation template functions."""

    def test_x_draft_under_280_chars(self):
        milestone = Milestone(
            title="feat: Add personal understanding layer",
            description="Added Phase 9 personal understanding layer",
            category="feature",
            significance=8,
        )
        draft = _generate_x_draft(milestone)
        assert len(draft) <= 280

    def test_x_draft_removes_commit_prefix_feat(self):
        milestone = Milestone(
            title="feat: Add something cool",
            description="Description",
            category="feature",
            significance=7,
        )
        draft = _generate_x_draft(milestone)
        assert not draft.startswith("feat:")
        # Should capitalize after prefix removal
        assert "Add something cool" in draft or "add something cool" in draft.lower()

    def test_x_draft_removes_commit_prefix_fix(self):
        milestone = Milestone(
            title="fix: Resolve memory leak",
            description="Fixed leak",
            category="maintenance",
            significance=6,
        )
        draft = _generate_x_draft(milestone)
        assert not draft.lower().startswith("fix:")

    def test_x_draft_removes_commit_prefix_refactor(self):
        milestone = Milestone(
            title="refactor: Clean up codebase",
            description="Refactored",
            category="architecture",
            significance=6,
        )
        draft = _generate_x_draft(milestone)
        assert not draft.lower().startswith("refactor:")

    def test_x_draft_includes_hashtags(self):
        milestone = Milestone(
            title="New feature",
            description="Desc",
            category="feature",
            significance=7,
        )
        draft = _generate_x_draft(milestone)
        assert "#buildinpublic" in draft

    def test_x_draft_long_content_truncated(self):
        milestone = Milestone(
            title="A very long title " * 10,
            description="A very long description " * 20,
            category="feature",
            significance=8,
        )
        draft = _generate_x_draft(milestone)
        assert len(draft) <= 280

    def test_linkedin_has_professional_format(self):
        milestone = Milestone(
            title="feat: Add OAuth integration",
            description="Implemented OAuth 2.0 flow",
            category="integration",
            significance=7,
        )
        draft = _generate_linkedin_draft(milestone)
        assert "Excited to share" in draft
        assert milestone.title in draft
        assert milestone.description in draft
        assert "#AI" in draft
        assert "#SoftwareEngineering" in draft
        assert "#BuildInPublic" in draft
        assert milestone.category in draft

    def test_github_has_markdown(self):
        milestone = Milestone(
            title="v2.0.0 Release",
            description="Major release with new features",
            category="release",
            significance=8,
        )
        draft = _generate_github_draft(milestone)
        assert draft.startswith("## ")
        assert milestone.title in draft
        assert milestone.description in draft
        assert f"**Category:** {milestone.category}" in draft
        assert f"**Significance:** {milestone.significance}/10" in draft
        assert "### What changed" in draft
        assert "### What's next" in draft

    def test_unknown_platform_fallback(self):
        milestone = Milestone(
            title="Test milestone",
            description="Test description",
            category="feature",
            significance=6,
        )
        draft = _generate_draft("mastodon", milestone)
        assert "[mastodon]" in draft
        assert milestone.title in draft

    def test_generate_draft_dispatches_x(self):
        milestone = Milestone(title="Test", category="feature", significance=6)
        draft = _generate_draft("x", milestone)
        # X drafts have hashtags
        assert "#" in draft

    def test_generate_draft_dispatches_linkedin(self):
        milestone = Milestone(title="Test", category="feature", significance=6)
        draft = _generate_draft("linkedin", milestone)
        assert "Excited to share" in draft

    def test_generate_draft_dispatches_github(self):
        milestone = Milestone(title="Test", category="feature", significance=6)
        draft = _generate_draft("github", milestone)
        assert "## Test" in draft

    def test_x_draft_no_description(self):
        milestone = Milestone(
            title="feat: Standalone feature",
            description="",
            category="feature",
            significance=6,
        )
        draft = _generate_x_draft(milestone)
        assert len(draft) > 0
        assert len(draft) <= 280

    def test_linkedin_no_description(self):
        milestone = Milestone(
            title="Something",
            description="",
            category="feature",
            significance=6,
        )
        draft = _generate_linkedin_draft(milestone)
        assert "No description" in draft

    def test_github_no_description(self):
        milestone = Milestone(
            title="Something",
            description="",
            category="feature",
            significance=6,
        )
        draft = _generate_github_draft(milestone)
        assert "No description provided." in draft


# ------------------------------------------------------------------
# 16. Helper functions
# ------------------------------------------------------------------


class TestHelperFunctions:
    """Tests for _status_icon and _platform_label."""

    def test_status_icon_detected(self):
        assert _status_icon("detected") == "[new]"

    def test_status_icon_drafts_ready(self):
        assert _status_icon("drafts_ready") == "[drafts]"

    def test_status_icon_posted(self):
        assert _status_icon("posted") == "[posted]"

    def test_status_icon_dismissed(self):
        assert _status_icon("dismissed") == "[dismissed]"

    def test_status_icon_unknown(self):
        assert _status_icon("unknown_status") == "[?]"

    def test_platform_label_x(self):
        assert _platform_label("x") == "X/Twitter"

    def test_platform_label_linkedin(self):
        assert _platform_label("linkedin") == "LinkedIn"

    def test_platform_label_github(self):
        assert _platform_label("github") == "GitHub"

    def test_platform_label_unknown(self):
        assert _platform_label("mastodon") == "mastodon"


# ------------------------------------------------------------------
# Constants verification
# ------------------------------------------------------------------


class TestConstants:
    """Tests for module-level constants."""

    def test_draft_threshold(self):
        assert DRAFT_THRESHOLD == 6

    def test_platforms(self):
        assert PLATFORMS == ["x", "linkedin", "github"]

    def test_milestones_collection(self):
        assert MILESTONES_COLLECTION == "skill_milestones"
