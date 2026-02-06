"""Tests for skills registry module."""

import pytest

from zetherion_ai.skills.base import (
    HeartbeatAction,
    Skill,
    SkillMetadata,
    SkillRequest,
    SkillResponse,
    SkillStatus,
)
from zetherion_ai.skills.permissions import Permission, PermissionSet
from zetherion_ai.skills.registry import (
    SkillPermissionError,
    SkillRegistry,
)


class MockSkill(Skill):
    """Mock skill for testing."""

    def __init__(
        self,
        name: str,
        permissions: PermissionSet | None = None,
        intents: list[str] | None = None,
        heartbeat_actions: list[HeartbeatAction] | None = None,
        prompt_fragment: str | None = None,
    ):
        super().__init__(memory=None)
        self._name = name
        self._permissions = permissions or PermissionSet()
        self._intents = intents or []
        self._heartbeat_actions = heartbeat_actions or []
        self._prompt_fragment = prompt_fragment

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name=self._name,
            description=f"{self._name} skill",
            version="1.0.0",
            permissions=self._permissions,
            intents=self._intents,
        )

    async def initialize(self) -> bool:
        return True

    async def handle(self, request: SkillRequest) -> SkillResponse:
        return SkillResponse(
            request_id=request.id,
            message=f"Handled by {self._name}",
            data={"skill": self._name},
        )

    async def on_heartbeat(self, user_ids: list[str]) -> list[HeartbeatAction]:
        return self._heartbeat_actions

    def get_system_prompt_fragment(self, user_id: str) -> str | None:
        return self._prompt_fragment


class TestSkillRegistry:
    """Tests for SkillRegistry class."""

    def test_empty_registry(self) -> None:
        """New registry should be empty."""
        registry = SkillRegistry()
        assert registry.skill_count == 0
        assert registry.list_skills() == []

    def test_register_skill(self) -> None:
        """register() should add skill to registry."""
        registry = SkillRegistry()
        skill = MockSkill("test_skill", intents=["test_intent"])
        result = registry.register(skill)
        assert result is True
        assert registry.skill_count == 1

    def test_register_duplicate_skill(self) -> None:
        """register() should reject duplicate skill names."""
        registry = SkillRegistry()
        skill1 = MockSkill("duplicate")
        skill2 = MockSkill("duplicate")
        registry.register(skill1)
        result = registry.register(skill2)
        assert result is False
        assert registry.skill_count == 1

    def test_register_with_max_permissions_allowed(self) -> None:
        """register() should allow skills within max permissions."""
        max_perms = PermissionSet({Permission.READ_PROFILE, Permission.WRITE_PROFILE})
        registry = SkillRegistry(max_permissions=max_perms)
        skill_perms = PermissionSet({Permission.READ_PROFILE})
        skill = MockSkill("allowed_skill", permissions=skill_perms)
        result = registry.register(skill)
        assert result is True

    def test_register_with_max_permissions_denied(self) -> None:
        """register() should reject skills exceeding max permissions."""
        max_perms = PermissionSet({Permission.READ_PROFILE})
        registry = SkillRegistry(max_permissions=max_perms)
        skill_perms = PermissionSet({Permission.READ_PROFILE, Permission.ADMIN})
        skill = MockSkill("denied_skill", permissions=skill_perms)
        with pytest.raises(SkillPermissionError, match="excessive permissions"):
            registry.register(skill)

    def test_get_skill_by_name(self) -> None:
        """get_skill() should return skill by name."""
        registry = SkillRegistry()
        skill = MockSkill("my_skill")
        registry.register(skill)
        retrieved = registry.get_skill("my_skill")
        assert retrieved is skill

    def test_get_skill_not_found(self) -> None:
        """get_skill() should return None for unknown skill."""
        registry = SkillRegistry()
        retrieved = registry.get_skill("unknown")
        assert retrieved is None

    def test_get_skill_for_intent(self) -> None:
        """get_skill_for_intent() should return skill handling intent."""
        registry = SkillRegistry()
        skill = MockSkill("task_skill", intents=["create_task", "list_tasks"])
        registry.register(skill)
        retrieved = registry.get_skill_for_intent("create_task")
        assert retrieved is skill
        retrieved = registry.get_skill_for_intent("list_tasks")
        assert retrieved is skill

    def test_get_skill_for_unknown_intent(self) -> None:
        """get_skill_for_intent() should return None for unknown intent."""
        registry = SkillRegistry()
        retrieved = registry.get_skill_for_intent("unknown_intent")
        assert retrieved is None

    def test_intent_mapping_conflict(self) -> None:
        """Multiple skills mapping same intent should warn but allow."""
        registry = SkillRegistry()
        skill1 = MockSkill("skill1", intents=["shared_intent"])
        skill2 = MockSkill("skill2", intents=["shared_intent"])
        registry.register(skill1)
        registry.register(skill2)
        # Last registered wins
        retrieved = registry.get_skill_for_intent("shared_intent")
        assert retrieved is skill2

    def test_unregister_skill(self) -> None:
        """unregister() should remove skill."""
        registry = SkillRegistry()
        skill = MockSkill("to_remove", intents=["some_intent"])
        registry.register(skill)
        assert registry.skill_count == 1
        result = registry.unregister("to_remove")
        assert result is True
        assert registry.skill_count == 0
        assert registry.get_skill("to_remove") is None
        assert registry.get_skill_for_intent("some_intent") is None

    def test_unregister_unknown_skill(self) -> None:
        """unregister() should return False for unknown skill."""
        registry = SkillRegistry()
        result = registry.unregister("unknown")
        assert result is False

    @pytest.mark.asyncio
    async def test_initialize_all(self) -> None:
        """initialize_all() should initialize all skills."""
        registry = SkillRegistry()
        skill1 = MockSkill("skill1")
        skill2 = MockSkill("skill2")
        registry.register(skill1)
        registry.register(skill2)
        results = await registry.initialize_all()
        assert results["skill1"] is True
        assert results["skill2"] is True
        assert skill1.status == SkillStatus.READY
        assert skill2.status == SkillStatus.READY

    @pytest.mark.asyncio
    async def test_initialize_all_with_failure(self) -> None:
        """initialize_all() should track failures."""

        class FailingSkill(MockSkill):
            async def initialize(self) -> bool:
                return False

        registry = SkillRegistry()
        good_skill = MockSkill("good")
        bad_skill = FailingSkill("bad")
        registry.register(good_skill)
        registry.register(bad_skill)
        results = await registry.initialize_all()
        assert results["good"] is True
        assert results["bad"] is False

    @pytest.mark.asyncio
    async def test_handle_request_by_intent(self) -> None:
        """handle_request() should route by intent."""
        registry = SkillRegistry()
        skill = MockSkill("handler", intents=["handle_this"])
        registry.register(skill)
        await registry.initialize_all()

        request = SkillRequest(intent="handle_this", message="test")
        response = await registry.handle_request(request)
        assert response.success is True
        assert response.data["skill"] == "handler"

    @pytest.mark.asyncio
    async def test_handle_request_by_skill_name(self) -> None:
        """handle_request() should route by explicit skill_name in context."""
        registry = SkillRegistry()
        skill = MockSkill("explicit_skill")
        registry.register(skill)
        await registry.initialize_all()

        request = SkillRequest(
            intent="unknown_intent",
            context={"skill_name": "explicit_skill"},
        )
        response = await registry.handle_request(request)
        assert response.success is True
        assert response.data["skill"] == "explicit_skill"

    @pytest.mark.asyncio
    async def test_handle_request_no_skill(self) -> None:
        """handle_request() should error when no skill found."""
        registry = SkillRegistry()
        request = SkillRequest(intent="no_handler")
        response = await registry.handle_request(request)
        assert response.success is False
        assert "No skill found" in response.error

    @pytest.mark.asyncio
    async def test_run_heartbeat(self) -> None:
        """run_heartbeat() should collect actions from ready skills."""
        registry = SkillRegistry()
        action1 = HeartbeatAction(
            skill_name="skill1",
            action_type="reminder",
            user_id="user1",
            priority=5,
        )
        action2 = HeartbeatAction(
            skill_name="skill2",
            action_type="briefing",
            user_id="user1",
            priority=10,
        )
        skill1 = MockSkill(
            "skill1",
            permissions=PermissionSet({Permission.SEND_MESSAGES}),
            heartbeat_actions=[action1],
        )
        skill2 = MockSkill(
            "skill2",
            permissions=PermissionSet({Permission.SEND_MESSAGES}),
            heartbeat_actions=[action2],
        )
        registry.register(skill1)
        registry.register(skill2)
        await registry.initialize_all()

        actions = await registry.run_heartbeat(["user1"])
        assert len(actions) == 2
        # Should be sorted by priority (highest first)
        assert actions[0].priority == 10
        assert actions[1].priority == 5

    @pytest.mark.asyncio
    async def test_run_heartbeat_skips_non_ready(self) -> None:
        """run_heartbeat() should skip non-ready skills."""
        registry = SkillRegistry()
        skill = MockSkill(
            "not_ready",
            permissions=PermissionSet({Permission.SEND_MESSAGES}),
            heartbeat_actions=[
                HeartbeatAction(
                    skill_name="not_ready",
                    action_type="test",
                    user_id="user1",
                )
            ],
        )
        registry.register(skill)
        # Don't initialize - status is UNINITIALIZED
        actions = await registry.run_heartbeat(["user1"])
        assert len(actions) == 0

    @pytest.mark.asyncio
    async def test_run_heartbeat_skips_no_permission(self) -> None:
        """run_heartbeat() should skip skills without SEND_MESSAGES."""
        registry = SkillRegistry()
        skill = MockSkill(
            "no_send",
            permissions=PermissionSet({Permission.READ_PROFILE}),
            heartbeat_actions=[
                HeartbeatAction(
                    skill_name="no_send",
                    action_type="test",
                    user_id="user1",
                )
            ],
        )
        registry.register(skill)
        await registry.initialize_all()
        actions = await registry.run_heartbeat(["user1"])
        assert len(actions) == 0

    def test_get_system_prompt_fragments(self) -> None:
        """get_system_prompt_fragments() should collect fragments."""
        registry = SkillRegistry()
        skill1 = MockSkill("skill1", prompt_fragment="Fragment 1")
        skill2 = MockSkill("skill2", prompt_fragment="Fragment 2")
        skill3 = MockSkill("skill3", prompt_fragment=None)  # No fragment
        registry.register(skill1)
        registry.register(skill2)
        registry.register(skill3)

        # Set ready status manually for this test
        skill1._status = SkillStatus.READY
        skill2._status = SkillStatus.READY
        skill3._status = SkillStatus.READY

        fragments = registry.get_system_prompt_fragments("user1")
        assert len(fragments) == 2
        assert "Fragment 1" in fragments
        assert "Fragment 2" in fragments

    def test_list_skills(self) -> None:
        """list_skills() should return all skill metadata."""
        registry = SkillRegistry()
        skill1 = MockSkill("skill1")
        skill2 = MockSkill("skill2")
        registry.register(skill1)
        registry.register(skill2)
        metadata_list = registry.list_skills()
        assert len(metadata_list) == 2
        names = [m.name for m in metadata_list]
        assert "skill1" in names
        assert "skill2" in names

    def test_list_ready_skills(self) -> None:
        """list_ready_skills() should return only ready skills."""
        registry = SkillRegistry()
        skill1 = MockSkill("ready_skill")
        skill2 = MockSkill("not_ready")
        registry.register(skill1)
        registry.register(skill2)
        skill1._status = SkillStatus.READY
        # skill2 stays UNINITIALIZED

        ready = registry.list_ready_skills()
        assert len(ready) == 1
        assert ready[0].name == "ready_skill"

    def test_list_intents(self) -> None:
        """list_intents() should return intent-to-skill mapping."""
        registry = SkillRegistry()
        skill = MockSkill("task_skill", intents=["create_task", "list_tasks"])
        registry.register(skill)
        intents = registry.list_intents()
        assert intents == {"create_task": "task_skill", "list_tasks": "task_skill"}

    def test_get_status_summary(self) -> None:
        """get_status_summary() should return registry status."""
        registry = SkillRegistry()
        skill1 = MockSkill("ready1", intents=["intent1"])
        skill2 = MockSkill("ready2", intents=["intent2"])
        skill3 = MockSkill("error_skill", intents=["intent3"])
        registry.register(skill1)
        registry.register(skill2)
        registry.register(skill3)
        skill1._status = SkillStatus.READY
        skill2._status = SkillStatus.READY
        skill3._status = SkillStatus.ERROR

        summary = registry.get_status_summary()
        assert summary["total_skills"] == 3
        assert summary["total_intents"] == 3
        assert summary["ready_count"] == 2
        assert summary["error_count"] == 1
        assert "ready" in summary["by_status"]
        assert "error" in summary["by_status"]

    @pytest.mark.asyncio
    async def test_cleanup_all(self) -> None:
        """cleanup_all() should cleanup all skills."""

        class CleanupTrackingSkill(MockSkill):
            cleaned_up = False

            async def cleanup(self) -> None:
                CleanupTrackingSkill.cleaned_up = True

        registry = SkillRegistry()
        skill = CleanupTrackingSkill("cleanup_skill")
        registry.register(skill)
        await registry.cleanup_all()
        assert CleanupTrackingSkill.cleaned_up is True
