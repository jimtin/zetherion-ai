"""Tests for skills base module."""

from datetime import datetime
from uuid import UUID, uuid4

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


class TestSkillStatus:
    """Tests for SkillStatus enum."""

    def test_skill_status_values(self) -> None:
        """SkillStatus should have expected values."""
        assert SkillStatus.UNINITIALIZED.value == "uninitialized"
        assert SkillStatus.INITIALIZING.value == "initializing"
        assert SkillStatus.READY.value == "ready"
        assert SkillStatus.ERROR.value == "error"
        assert SkillStatus.DISABLED.value == "disabled"

    def test_skill_status_count(self) -> None:
        """SkillStatus should have 5 states."""
        assert len(SkillStatus) == 5


class TestSkillRequest:
    """Tests for SkillRequest dataclass."""

    def test_default_values(self) -> None:
        """SkillRequest should have sensible defaults."""
        req = SkillRequest()
        assert isinstance(req.id, UUID)
        assert req.user_id == ""
        assert req.intent == ""
        assert req.message == ""
        assert req.context == {}
        assert isinstance(req.timestamp, datetime)

    def test_custom_values(self) -> None:
        """SkillRequest should accept custom values."""
        custom_id = uuid4()
        custom_time = datetime(2026, 1, 15, 10, 30)
        req = SkillRequest(
            id=custom_id,
            user_id="user123",
            intent="create_task",
            message="Create a task for tomorrow",
            context={"channel_id": "ch123"},
            timestamp=custom_time,
        )
        assert req.id == custom_id
        assert req.user_id == "user123"
        assert req.intent == "create_task"
        assert req.message == "Create a task for tomorrow"
        assert req.context == {"channel_id": "ch123"}
        assert req.timestamp == custom_time

    def test_to_dict(self) -> None:
        """to_dict() should serialize properly."""
        req = SkillRequest(
            user_id="user123",
            intent="test_intent",
            message="test message",
            context={"key": "value"},
        )
        data = req.to_dict()
        assert data["user_id"] == "user123"
        assert data["intent"] == "test_intent"
        assert data["message"] == "test message"
        assert data["context"] == {"key": "value"}
        assert "id" in data
        assert "timestamp" in data

    def test_from_dict(self) -> None:
        """from_dict() should deserialize properly."""
        data = {
            "id": str(uuid4()),
            "user_id": "user456",
            "intent": "list_tasks",
            "message": "Show my tasks",
            "context": {"project": "SecureClaw"},
            "timestamp": "2026-02-06T14:30:00",
        }
        req = SkillRequest.from_dict(data)
        assert req.user_id == "user456"
        assert req.intent == "list_tasks"
        assert req.message == "Show my tasks"
        assert req.context == {"project": "SecureClaw"}
        assert req.timestamp == datetime(2026, 2, 6, 14, 30)

    def test_from_dict_minimal(self) -> None:
        """from_dict() should handle minimal data."""
        data = {}
        req = SkillRequest.from_dict(data)
        assert req.user_id == ""
        assert req.intent == ""
        assert isinstance(req.id, UUID)


class TestSkillResponse:
    """Tests for SkillResponse dataclass."""

    def test_success_response(self) -> None:
        """SkillResponse should default to success."""
        req_id = uuid4()
        resp = SkillResponse(request_id=req_id, message="Task created")
        assert resp.request_id == req_id
        assert resp.success is True
        assert resp.message == "Task created"
        assert resp.data == {}
        assert resp.error is None
        assert resp.actions == []

    def test_error_response_factory(self) -> None:
        """error_response() should create error response."""
        req_id = uuid4()
        resp = SkillResponse.error_response(req_id, "Something went wrong")
        assert resp.request_id == req_id
        assert resp.success is False
        assert resp.error == "Something went wrong"

    def test_response_with_data_and_actions(self) -> None:
        """SkillResponse should accept data and actions."""
        req_id = uuid4()
        resp = SkillResponse(
            request_id=req_id,
            message="Tasks found",
            data={"tasks": [{"id": 1, "title": "Test"}]},
            actions=[{"type": "update_memory", "data": {}}],
        )
        assert resp.data == {"tasks": [{"id": 1, "title": "Test"}]}
        assert len(resp.actions) == 1

    def test_to_dict(self) -> None:
        """to_dict() should serialize properly."""
        req_id = uuid4()
        resp = SkillResponse(
            request_id=req_id,
            success=True,
            message="Done",
            data={"key": "value"},
            actions=[{"type": "action1"}],
        )
        data = resp.to_dict()
        assert data["request_id"] == str(req_id)
        assert data["success"] is True
        assert data["message"] == "Done"
        assert data["data"] == {"key": "value"}
        assert data["actions"] == [{"type": "action1"}]

    def test_from_dict(self) -> None:
        """from_dict() should deserialize properly."""
        req_id = uuid4()
        data = {
            "request_id": str(req_id),
            "success": False,
            "message": "Error occurred",
            "error": "Test error",
            "data": {},
            "actions": [],
        }
        resp = SkillResponse.from_dict(data)
        assert resp.request_id == req_id
        assert resp.success is False
        assert resp.error == "Test error"


class TestHeartbeatAction:
    """Tests for HeartbeatAction dataclass."""

    def test_default_values(self) -> None:
        """HeartbeatAction should have sensible defaults."""
        action = HeartbeatAction(
            skill_name="task_manager",
            action_type="send_reminder",
            user_id="user123",
        )
        assert action.skill_name == "task_manager"
        assert action.action_type == "send_reminder"
        assert action.user_id == "user123"
        assert action.data == {}
        assert action.priority == 0

    def test_with_data_and_priority(self) -> None:
        """HeartbeatAction should accept data and priority."""
        action = HeartbeatAction(
            skill_name="calendar",
            action_type="morning_briefing",
            user_id="user456",
            data={"summary": "3 meetings today"},
            priority=10,
        )
        assert action.data == {"summary": "3 meetings today"}
        assert action.priority == 10

    def test_to_dict(self) -> None:
        """to_dict() should serialize properly."""
        action = HeartbeatAction(
            skill_name="test_skill",
            action_type="test_action",
            user_id="user789",
            data={"key": "value"},
            priority=5,
        )
        data = action.to_dict()
        assert data["skill_name"] == "test_skill"
        assert data["action_type"] == "test_action"
        assert data["user_id"] == "user789"
        assert data["data"] == {"key": "value"}
        assert data["priority"] == 5

    def test_from_dict(self) -> None:
        """from_dict() should deserialize properly."""
        data = {
            "skill_name": "profile",
            "action_type": "confirm_entry",
            "user_id": "user000",
            "data": {"entry_id": "abc123"},
            "priority": 3,
        }
        action = HeartbeatAction.from_dict(data)
        assert action.skill_name == "profile"
        assert action.action_type == "confirm_entry"
        assert action.user_id == "user000"
        assert action.data == {"entry_id": "abc123"}
        assert action.priority == 3


class TestSkillMetadata:
    """Tests for SkillMetadata dataclass."""

    def test_minimal_metadata(self) -> None:
        """SkillMetadata should work with minimal fields."""
        meta = SkillMetadata(
            name="test_skill",
            description="A test skill",
            version="1.0.0",
        )
        assert meta.name == "test_skill"
        assert meta.description == "A test skill"
        assert meta.version == "1.0.0"
        assert meta.author == "SecureClaw"
        assert len(meta.permissions) == 0
        assert meta.collections == []
        assert meta.intents == []

    def test_full_metadata(self) -> None:
        """SkillMetadata should accept all fields."""
        permissions = PermissionSet({Permission.READ_PROFILE, Permission.WRITE_PROFILE})
        meta = SkillMetadata(
            name="task_manager",
            description="Manage tasks and projects",
            version="2.1.0",
            author="Custom Author",
            permissions=permissions,
            collections=["skill_tasks"],
            intents=["create_task", "list_tasks", "complete_task"],
        )
        assert meta.author == "Custom Author"
        assert Permission.READ_PROFILE in meta.permissions
        assert "skill_tasks" in meta.collections
        assert "create_task" in meta.intents

    def test_to_dict(self) -> None:
        """to_dict() should serialize properly."""
        permissions = PermissionSet({Permission.READ_PROFILE})
        meta = SkillMetadata(
            name="test",
            description="Test",
            version="1.0.0",
            permissions=permissions,
            intents=["test_intent"],
        )
        data = meta.to_dict()
        assert data["name"] == "test"
        assert data["description"] == "Test"
        assert data["version"] == "1.0.0"
        assert "READ_PROFILE" in data["permissions"]
        assert "test_intent" in data["intents"]

    def test_from_dict(self) -> None:
        """from_dict() should deserialize properly."""
        data = {
            "name": "calendar",
            "description": "Calendar skill",
            "version": "1.5.0",
            "author": "Team",
            "permissions": ["READ_SCHEDULE", "SEND_MESSAGES"],
            "collections": ["skill_calendar"],
            "intents": ["schedule_event"],
        }
        meta = SkillMetadata.from_dict(data)
        assert meta.name == "calendar"
        assert meta.version == "1.5.0"
        assert Permission.READ_SCHEDULE in meta.permissions
        assert "skill_calendar" in meta.collections


class ConcreteSkill(Skill):
    """Concrete implementation for testing abstract Skill class."""

    def __init__(self, name: str = "test_skill", permissions: PermissionSet | None = None):
        super().__init__(memory=None)
        self._name = name
        self._permissions = permissions or PermissionSet()
        self._init_called = False
        self._should_init_succeed = True

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name=self._name,
            description="Test skill for unit tests",
            version="1.0.0",
            permissions=self._permissions,
            intents=["test_intent"],
        )

    async def initialize(self) -> bool:
        self._init_called = True
        return self._should_init_succeed

    async def handle(self, request: SkillRequest) -> SkillResponse:
        return SkillResponse(
            request_id=request.id,
            message=f"Handled: {request.message}",
        )


class TestSkillBaseClass:
    """Tests for abstract Skill base class."""

    def test_skill_initial_status(self) -> None:
        """Skill should start uninitialized."""
        skill = ConcreteSkill()
        assert skill.status == SkillStatus.UNINITIALIZED
        assert skill.error is None

    def test_skill_name_property(self) -> None:
        """name property should return metadata name."""
        skill = ConcreteSkill(name="custom_name")
        assert skill.name == "custom_name"

    def test_has_permission(self) -> None:
        """has_permission() should check permissions."""
        permissions = PermissionSet({Permission.READ_PROFILE})
        skill = ConcreteSkill(permissions=permissions)
        assert skill.has_permission(Permission.READ_PROFILE) is True
        assert skill.has_permission(Permission.ADMIN) is False

    def test_requires_permission_success(self) -> None:
        """requires_permission() should not raise for present permission."""
        permissions = PermissionSet({Permission.READ_PROFILE})
        skill = ConcreteSkill(permissions=permissions)
        skill.requires_permission(Permission.READ_PROFILE)  # Should not raise

    def test_requires_permission_failure(self) -> None:
        """requires_permission() should raise for missing permission."""
        skill = ConcreteSkill()  # No permissions
        with pytest.raises(PermissionError, match="lacks required permission"):
            skill.requires_permission(Permission.ADMIN)

    @pytest.mark.asyncio
    async def test_safe_initialize_success(self) -> None:
        """safe_initialize() should set READY status on success."""
        skill = ConcreteSkill()
        skill._should_init_succeed = True
        result = await skill.safe_initialize()
        assert result is True
        assert skill.status == SkillStatus.READY
        assert skill._init_called is True

    @pytest.mark.asyncio
    async def test_safe_initialize_failure(self) -> None:
        """safe_initialize() should set ERROR status on failure."""
        skill = ConcreteSkill()
        skill._should_init_succeed = False
        result = await skill.safe_initialize()
        assert result is False
        assert skill.status == SkillStatus.ERROR

    @pytest.mark.asyncio
    async def test_safe_initialize_exception(self) -> None:
        """safe_initialize() should handle exceptions."""

        class FailingSkill(ConcreteSkill):
            async def initialize(self) -> bool:
                raise RuntimeError("Init failed")

        skill = FailingSkill()
        result = await skill.safe_initialize()
        assert result is False
        assert skill.status == SkillStatus.ERROR
        assert "Init failed" in str(skill.error)

    @pytest.mark.asyncio
    async def test_safe_handle_not_ready(self) -> None:
        """safe_handle() should error if skill not ready."""
        skill = ConcreteSkill()
        # Not initialized, status is UNINITIALIZED
        request = SkillRequest(message="test")
        response = await skill.safe_handle(request)
        assert response.success is False
        assert "not ready" in response.error

    @pytest.mark.asyncio
    async def test_safe_handle_success(self) -> None:
        """safe_handle() should delegate to handle() when ready."""
        skill = ConcreteSkill()
        await skill.safe_initialize()
        request = SkillRequest(message="Hello World")
        response = await skill.safe_handle(request)
        assert response.success is True
        assert "Handled: Hello World" in response.message

    @pytest.mark.asyncio
    async def test_safe_handle_permission_error(self) -> None:
        """safe_handle() should catch permission errors."""

        class PermissionCheckingSkill(ConcreteSkill):
            async def handle(self, request: SkillRequest) -> SkillResponse:
                self.requires_permission(Permission.ADMIN)
                return SkillResponse(request_id=request.id)

        skill = PermissionCheckingSkill()
        await skill.safe_initialize()
        request = SkillRequest()
        response = await skill.safe_handle(request)
        assert response.success is False
        assert "Permission denied" in response.error

    @pytest.mark.asyncio
    async def test_safe_handle_general_exception(self) -> None:
        """safe_handle() should catch general exceptions."""

        class FailingHandleSkill(ConcreteSkill):
            async def handle(self, request: SkillRequest) -> SkillResponse:
                raise ValueError("Handle failed")

        skill = FailingHandleSkill()
        await skill.safe_initialize()
        request = SkillRequest()
        response = await skill.safe_handle(request)
        assert response.success is False
        assert "Handle failed" in response.error

    @pytest.mark.asyncio
    async def test_on_heartbeat_default(self) -> None:
        """Default on_heartbeat() should return empty list."""
        skill = ConcreteSkill()
        actions = await skill.on_heartbeat(["user1", "user2"])
        assert actions == []

    def test_get_system_prompt_fragment_default(self) -> None:
        """Default get_system_prompt_fragment() should return None."""
        skill = ConcreteSkill()
        fragment = skill.get_system_prompt_fragment("user1")
        assert fragment is None

    @pytest.mark.asyncio
    async def test_cleanup_default(self) -> None:
        """Default cleanup() should not raise."""
        skill = ConcreteSkill()
        await skill.cleanup()  # Should not raise
