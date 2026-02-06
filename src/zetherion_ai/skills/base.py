"""Abstract base class for skills.

Skills are modular capabilities that the bot can use to perform specific tasks.
Each skill declares its permissions, handles requests, and can contribute to
heartbeat cycles for proactive behavior.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from zetherion_ai.logging import get_logger
from zetherion_ai.skills.permissions import Permission, PermissionSet

if TYPE_CHECKING:
    from zetherion_ai.memory.qdrant import QdrantMemory

log = get_logger("zetherion_ai.skills.base")


class SkillStatus(Enum):
    """Status of a skill."""

    UNINITIALIZED = "uninitialized"
    INITIALIZING = "initializing"
    READY = "ready"
    ERROR = "error"
    DISABLED = "disabled"


@dataclass
class SkillRequest:
    """A request to a skill."""

    id: UUID = field(default_factory=uuid4)
    user_id: str = ""
    intent: str = ""  # The classified intent (e.g., "create_task", "list_tasks")
    message: str = ""  # The original user message
    context: dict[str, Any] = field(default_factory=dict)  # Additional context
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": str(self.id),
            "user_id": self.user_id,
            "intent": self.intent,
            "message": self.message,
            "context": self.context,
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SkillRequest":
        """Create from dictionary."""
        return cls(
            id=UUID(data["id"]) if data.get("id") else uuid4(),
            user_id=data.get("user_id", ""),
            intent=data.get("intent", ""),
            message=data.get("message", ""),
            context=data.get("context", {}),
            timestamp=datetime.fromisoformat(data["timestamp"])
            if data.get("timestamp")
            else datetime.now(),
        )


@dataclass
class SkillResponse:
    """A response from a skill."""

    request_id: UUID
    success: bool = True
    message: str = ""  # Response message to show user
    data: dict[str, Any] = field(default_factory=dict)  # Structured data
    error: str | None = None
    actions: list[dict[str, Any]] = field(default_factory=list)  # Actions to take

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "request_id": str(self.request_id),
            "success": self.success,
            "message": self.message,
            "data": self.data,
            "error": self.error,
            "actions": self.actions,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SkillResponse":
        """Create from dictionary."""
        return cls(
            request_id=UUID(data["request_id"]),
            success=data.get("success", True),
            message=data.get("message", ""),
            data=data.get("data", {}),
            error=data.get("error"),
            actions=data.get("actions", []),
        )

    @classmethod
    def error_response(cls, request_id: UUID, error: str) -> "SkillResponse":
        """Create an error response."""
        return cls(
            request_id=request_id,
            success=False,
            error=error,
        )


@dataclass
class HeartbeatAction:
    """An action to take during a heartbeat cycle."""

    skill_name: str
    action_type: str  # "send_message", "update_memory", "schedule", etc.
    user_id: str
    data: dict[str, Any] = field(default_factory=dict)
    priority: int = 0  # Higher = more important

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "skill_name": self.skill_name,
            "action_type": self.action_type,
            "user_id": self.user_id,
            "data": self.data,
            "priority": self.priority,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HeartbeatAction":
        """Create from dictionary."""
        return cls(
            skill_name=data["skill_name"],
            action_type=data["action_type"],
            user_id=data["user_id"],
            data=data.get("data", {}),
            priority=data.get("priority", 0),
        )


@dataclass
class SkillMetadata:
    """Metadata about a skill."""

    name: str
    description: str
    version: str
    author: str = "SecureClaw"
    permissions: PermissionSet = field(default_factory=PermissionSet)
    collections: list[str] = field(default_factory=list)  # Qdrant collections used
    intents: list[str] = field(default_factory=list)  # Intents this skill handles

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "author": self.author,
            "permissions": self.permissions.to_list(),
            "collections": self.collections,
            "intents": self.intents,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SkillMetadata":
        """Create from dictionary."""
        return cls(
            name=data["name"],
            description=data["description"],
            version=data["version"],
            author=data.get("author", "SecureClaw"),
            permissions=PermissionSet.from_list(data.get("permissions", [])),
            collections=data.get("collections", []),
            intents=data.get("intents", []),
        )


class Skill(ABC):
    """Abstract base class for all skills.

    Skills must implement:
    - metadata: Property returning SkillMetadata
    - handle(): Process a skill request
    - initialize(): Set up the skill (create collections, etc.)

    Skills may optionally implement:
    - on_heartbeat(): Return actions for proactive behavior
    - get_system_prompt_fragment(): Contribute to agent's system prompt
    - cleanup(): Clean up resources
    """

    def __init__(self, memory: "QdrantMemory | None" = None):
        """Initialize the skill.

        Args:
            memory: Optional Qdrant memory for storage.
        """
        self._memory = memory
        self._status = SkillStatus.UNINITIALIZED
        self._error: str | None = None

    @property
    @abstractmethod
    def metadata(self) -> SkillMetadata:
        """Return skill metadata.

        Must be implemented by subclasses to declare the skill's
        name, description, version, permissions, and collections.
        """
        ...

    @property
    def name(self) -> str:
        """Return the skill name."""
        return self.metadata.name

    @property
    def status(self) -> SkillStatus:
        """Return the current skill status."""
        return self._status

    @property
    def error(self) -> str | None:
        """Return the last error, if any."""
        return self._error

    def has_permission(self, permission: Permission) -> bool:
        """Check if this skill has a specific permission.

        Args:
            permission: The permission to check.

        Returns:
            True if the skill has the permission.
        """
        return permission in self.metadata.permissions

    def requires_permission(self, permission: Permission) -> None:
        """Raise an error if the skill doesn't have a permission.

        Args:
            permission: The required permission.

        Raises:
            PermissionError: If the skill lacks the permission.
        """
        if not self.has_permission(permission):
            raise PermissionError(
                f"Skill '{self.name}' lacks required permission: {permission.name}"
            )

    @abstractmethod
    async def initialize(self) -> bool:
        """Initialize the skill.

        This is called when the skill is first loaded. Use this to:
        - Create Qdrant collections
        - Set up any required state
        - Validate configuration

        Returns:
            True if initialization succeeded.
        """
        ...

    @abstractmethod
    async def handle(self, request: SkillRequest) -> SkillResponse:
        """Handle a skill request.

        Args:
            request: The incoming request.

        Returns:
            Response with results or error.
        """
        ...

    async def on_heartbeat(self, user_ids: list[str]) -> list[HeartbeatAction]:
        """Called during heartbeat cycles for proactive behavior.

        Override this to implement proactive features like:
        - Sending reminders
        - Checking for stale data
        - Generating summaries

        Args:
            user_ids: List of user IDs to check.

        Returns:
            List of actions to take.
        """
        return []

    def get_system_prompt_fragment(self, user_id: str) -> str | None:
        """Return a fragment to include in the agent's system prompt.

        Override this to inject context about the skill's state
        into the agent's system prompt.

        Args:
            user_id: The user ID for personalization.

        Returns:
            String to include in system prompt, or None.
        """
        return None

    async def cleanup(self) -> None:  # noqa: B027
        """Clean up skill resources.

        Override this to release any resources when the skill
        is being shut down. Default implementation does nothing.
        """

    def _set_status(self, status: SkillStatus, error: str | None = None) -> None:
        """Set the skill status.

        Args:
            status: New status.
            error: Optional error message.
        """
        self._status = status
        self._error = error
        log.debug(
            "skill_status_changed",
            skill=self.name,
            status=status.value,
            error=error,
        )

    async def safe_initialize(self) -> bool:
        """Safely initialize the skill, catching errors.

        Returns:
            True if initialization succeeded.
        """
        self._set_status(SkillStatus.INITIALIZING)
        try:
            result = await self.initialize()
            if result:
                self._set_status(SkillStatus.READY)
            else:
                self._set_status(SkillStatus.ERROR, "Initialization returned False")
            return result
        except Exception as e:
            self._set_status(SkillStatus.ERROR, str(e))
            log.error("skill_init_failed", skill=self.name, error=str(e))
            return False

    async def safe_handle(self, request: SkillRequest) -> SkillResponse:
        """Safely handle a request, catching errors.

        Args:
            request: The incoming request.

        Returns:
            Response with results or error.
        """
        if self._status != SkillStatus.READY:
            return SkillResponse.error_response(
                request.id,
                f"Skill '{self.name}' is not ready (status: {self._status.value})",
            )

        try:
            return await self.handle(request)
        except PermissionError as e:
            log.warning("skill_permission_denied", skill=self.name, error=str(e))
            return SkillResponse.error_response(request.id, f"Permission denied: {e}")
        except Exception as e:
            log.error("skill_handle_failed", skill=self.name, error=str(e))
            return SkillResponse.error_response(request.id, f"Skill error: {e}")
