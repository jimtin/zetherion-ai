"""User profile system for SecureClaw.

This package provides:
- ProfileEntry and ProfileCategory for structured user knowledge
- ProfileUpdate for proposed changes with confidence scoring
- Tiered inference engines for cost-conscious profile extraction
- ProfileBuilder for passive extraction from conversations
- ProfileCache for fast in-memory access
"""

from secureclaw.profile.builder import (
    ProfileBuilder,
    extract_profile_updates_background,
    schedule_profile_extraction,
)
from secureclaw.profile.cache import (
    EmploymentProfileSummary,
    ProfileCache,
    UserProfileSummary,
)
from secureclaw.profile.models import (
    ProfileCategory,
    ProfileEntry,
    ProfileSource,
    ProfileUpdate,
)
from secureclaw.profile.storage import (
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
]
