"""Skills framework for SecureClaw.

This package provides:
- Abstract Skill interface for building modular capabilities
- Permission system for access control
- Registry for skill management and routing
- Client for bot-to-skills communication
- Server for running skills as a separate service
"""

from zetherion_ai.skills.base import (
    HeartbeatAction,
    Skill,
    SkillMetadata,
    SkillRequest,
    SkillResponse,
    SkillStatus,
)
from zetherion_ai.skills.client import (
    SkillsClient,
    SkillsClientError,
    SkillsConnectionError,
    create_skills_client,
)
from zetherion_ai.skills.permissions import (
    PROACTIVE_PERMISSIONS,
    READONLY_PERMISSIONS,
    STANDARD_PERMISSIONS,
    Permission,
    PermissionSet,
)
from zetherion_ai.skills.registry import (
    SkillNotFoundError,
    SkillPermissionError,
    SkillRegistry,
)

__all__ = [
    # Base
    "Skill",
    "SkillMetadata",
    "SkillRequest",
    "SkillResponse",
    "SkillStatus",
    "HeartbeatAction",
    # Permissions
    "Permission",
    "PermissionSet",
    "READONLY_PERMISSIONS",
    "STANDARD_PERMISSIONS",
    "PROACTIVE_PERMISSIONS",
    # Registry
    "SkillRegistry",
    "SkillNotFoundError",
    "SkillPermissionError",
    # Client
    "SkillsClient",
    "SkillsClientError",
    "SkillsConnectionError",
    "create_skills_client",
]
