"""Skill permissions for access control.

Defines the permission model that controls what resources each skill can access.
Skills must declare their required permissions upfront, and the registry enforces
that skills only access their declared resources.
"""

import contextlib
from collections.abc import Iterator
from enum import Enum, auto


class Permission(Enum):
    """Permissions that skills can request."""

    # Profile permissions
    READ_PROFILE = auto()  # Read user profile entries
    WRITE_PROFILE = auto()  # Create/update profile entries
    DELETE_PROFILE = auto()  # Delete profile entries

    # Memory permissions
    READ_MEMORIES = auto()  # Read from conversation/long-term memory
    WRITE_MEMORIES = auto()  # Store new memories
    DELETE_MEMORIES = auto()  # Delete memories

    # Communication permissions
    SEND_MESSAGES = auto()  # Send proactive messages to user
    SEND_DM = auto()  # Send direct messages (for proactive features)

    # Scheduling permissions
    SCHEDULE_TASKS = auto()  # Schedule future actions
    READ_SCHEDULE = auto()  # Read scheduled events

    # Skill-specific storage
    READ_OWN_COLLECTION = auto()  # Read from skill's own Qdrant collection
    WRITE_OWN_COLLECTION = auto()  # Write to skill's own Qdrant collection

    # Cross-skill permissions
    INVOKE_OTHER_SKILLS = auto()  # Call other skills

    # System permissions (restricted)
    READ_CONFIG = auto()  # Read configuration values
    ADMIN = auto()  # Full administrative access (rarely granted)


class PermissionSet:
    """A set of permissions with helper methods."""

    def __init__(self, permissions: set[Permission] | None = None):
        """Initialize with a set of permissions.

        Args:
            permissions: Initial set of permissions.
        """
        self._permissions = permissions or set()

    def add(self, permission: Permission) -> "PermissionSet":
        """Add a permission to the set.

        Args:
            permission: Permission to add.

        Returns:
            Self for chaining.
        """
        self._permissions.add(permission)
        return self

    def remove(self, permission: Permission) -> "PermissionSet":
        """Remove a permission from the set.

        Args:
            permission: Permission to remove.

        Returns:
            Self for chaining.
        """
        self._permissions.discard(permission)
        return self

    def has(self, permission: Permission) -> bool:
        """Check if a permission is in the set.

        Args:
            permission: Permission to check.

        Returns:
            True if the permission is present.
        """
        return permission in self._permissions

    def has_all(self, *permissions: Permission) -> bool:
        """Check if all specified permissions are in the set.

        Args:
            permissions: Permissions to check.

        Returns:
            True if all permissions are present.
        """
        return all(p in self._permissions for p in permissions)

    def has_any(self, *permissions: Permission) -> bool:
        """Check if any of the specified permissions are in the set.

        Args:
            permissions: Permissions to check.

        Returns:
            True if any permission is present.
        """
        return any(p in self._permissions for p in permissions)

    def is_subset_of(self, other: "PermissionSet") -> bool:
        """Check if this permission set is a subset of another.

        Args:
            other: The other permission set.

        Returns:
            True if all permissions in this set are in the other set.
        """
        return self._permissions.issubset(other._permissions)

    def __contains__(self, permission: Permission) -> bool:
        """Support 'in' operator."""
        return permission in self._permissions

    def __iter__(self) -> Iterator[Permission]:
        """Support iteration."""
        return iter(self._permissions)

    def __len__(self) -> int:
        """Return number of permissions."""
        return len(self._permissions)

    def __repr__(self) -> str:
        """String representation."""
        perms = ", ".join(p.name for p in sorted(self._permissions, key=lambda p: p.name))
        return f"PermissionSet({{{perms}}})"

    def to_list(self) -> list[str]:
        """Convert to list of permission names.

        Returns:
            List of permission names as strings.
        """
        return [p.name for p in self._permissions]

    @classmethod
    def from_list(cls, names: list[str]) -> "PermissionSet":
        """Create from list of permission names.

        Args:
            names: List of permission names.

        Returns:
            PermissionSet with the specified permissions.
        """
        permissions = set()
        for name in names:
            with contextlib.suppress(KeyError):
                permissions.add(Permission[name])
        return cls(permissions)


# Pre-defined permission sets for common use cases
READONLY_PERMISSIONS = PermissionSet(
    {
        Permission.READ_PROFILE,
        Permission.READ_MEMORIES,
        Permission.READ_OWN_COLLECTION,
        Permission.READ_SCHEDULE,
    }
)

STANDARD_PERMISSIONS = PermissionSet(
    {
        Permission.READ_PROFILE,
        Permission.WRITE_PROFILE,
        Permission.READ_MEMORIES,
        Permission.WRITE_MEMORIES,
        Permission.READ_OWN_COLLECTION,
        Permission.WRITE_OWN_COLLECTION,
        Permission.SEND_MESSAGES,
    }
)

PROACTIVE_PERMISSIONS = PermissionSet(
    {
        Permission.READ_PROFILE,
        Permission.WRITE_PROFILE,
        Permission.READ_MEMORIES,
        Permission.WRITE_MEMORIES,
        Permission.READ_OWN_COLLECTION,
        Permission.WRITE_OWN_COLLECTION,
        Permission.SEND_MESSAGES,
        Permission.SEND_DM,
        Permission.SCHEDULE_TASKS,
        Permission.READ_SCHEDULE,
    }
)
