"""User profile system for SecureClaw.

This package provides:
- ProfileEntry and ProfileCategory for structured user knowledge
- ProfileUpdate for proposed changes with confidence scoring
- Tiered inference engines for cost-conscious profile extraction
- ProfileBuilder for passive extraction from conversations
- ProfileCache for fast in-memory access
- EmploymentProfile for modeling bot's role and relationship with user
- RelationshipTracker for managing relationship evolution
"""

from zetherion_ai.profile.builder import (
    ProfileBuilder,
    extract_profile_updates_background,
    schedule_profile_extraction,
)
from zetherion_ai.profile.cache import (
    EmploymentProfileSummary,
    ProfileCache,
    UserProfileSummary,
)
from zetherion_ai.profile.employment import (
    CommunicationStyle,
    EmploymentProfile,
    Milestone,
    RoleDefinition,
    SkillUsage,
    TrustLevel,
    create_default_profile,
)
from zetherion_ai.profile.models import (
    ProfileCategory,
    ProfileEntry,
    ProfileSource,
    ProfileUpdate,
)
from zetherion_ai.profile.relationship import (
    MilestoneProgress,
    RelationshipEvent,
    RelationshipState,
    RelationshipTracker,
)
from zetherion_ai.profile.storage import (
    PendingConfirmation,
    ProfileStats,
    ProfileStorage,
    ProfileUpdateRecord,
)

__all__ = [
    # Models
    "ProfileCategory",
    "ProfileEntry",
    "ProfileSource",
    "ProfileUpdate",
    # Builder
    "ProfileBuilder",
    "extract_profile_updates_background",
    "schedule_profile_extraction",
    # Cache
    "ProfileCache",
    "UserProfileSummary",
    "EmploymentProfileSummary",
    # Storage
    "ProfileStorage",
    "ProfileStats",
    "ProfileUpdateRecord",
    "PendingConfirmation",
    # Employment Profile
    "EmploymentProfile",
    "RoleDefinition",
    "CommunicationStyle",
    "SkillUsage",
    "TrustLevel",
    "Milestone",
    "create_default_profile",
    # Relationship
    "RelationshipTracker",
    "RelationshipEvent",
    "RelationshipState",
    "MilestoneProgress",
]
