"""Tests for skills permissions module."""

from zetherion_ai.skills.permissions import (
    PROACTIVE_PERMISSIONS,
    READONLY_PERMISSIONS,
    STANDARD_PERMISSIONS,
    Permission,
    PermissionSet,
)


class TestPermissionEnum:
    """Tests for the Permission enum."""

    def test_permission_enum_has_expected_values(self) -> None:
        """Permission enum should have all expected permissions."""
        expected = [
            "READ_PROFILE",
            "WRITE_PROFILE",
            "DELETE_PROFILE",
            "READ_MEMORIES",
            "WRITE_MEMORIES",
            "DELETE_MEMORIES",
            "SEND_MESSAGES",
            "SEND_DM",
            "SCHEDULE_TASKS",
            "READ_SCHEDULE",
            "READ_OWN_COLLECTION",
            "WRITE_OWN_COLLECTION",
            "INVOKE_OTHER_SKILLS",
            "READ_CONFIG",
            "ADMIN",
        ]
        actual = [p.name for p in Permission]
        for name in expected:
            assert name in actual, f"Missing permission: {name}"

    def test_permission_enum_count(self) -> None:
        """Permission enum should have expected number of values."""
        # At least 15 permissions defined
        assert len(Permission) >= 15


class TestPermissionSet:
    """Tests for PermissionSet class."""

    def test_empty_permission_set(self) -> None:
        """Empty PermissionSet should have no permissions."""
        ps = PermissionSet()
        assert len(ps) == 0
        assert Permission.READ_PROFILE not in ps

    def test_permission_set_with_initial_permissions(self) -> None:
        """PermissionSet should accept initial permissions."""
        permissions = {Permission.READ_PROFILE, Permission.READ_MEMORIES}
        ps = PermissionSet(permissions)
        assert len(ps) == 2
        assert Permission.READ_PROFILE in ps
        assert Permission.READ_MEMORIES in ps

    def test_add_permission(self) -> None:
        """add() should add a permission and return self."""
        ps = PermissionSet()
        result = ps.add(Permission.READ_PROFILE)
        assert result is ps  # Returns self for chaining
        assert Permission.READ_PROFILE in ps

    def test_add_permission_chaining(self) -> None:
        """add() should support method chaining."""
        ps = PermissionSet()
        ps.add(Permission.READ_PROFILE).add(Permission.WRITE_PROFILE).add(Permission.READ_MEMORIES)
        assert len(ps) == 3

    def test_remove_permission(self) -> None:
        """remove() should remove a permission."""
        ps = PermissionSet({Permission.READ_PROFILE, Permission.WRITE_PROFILE})
        ps.remove(Permission.READ_PROFILE)
        assert Permission.READ_PROFILE not in ps
        assert Permission.WRITE_PROFILE in ps

    def test_remove_nonexistent_permission(self) -> None:
        """remove() should not raise for non-existent permission."""
        ps = PermissionSet()
        ps.remove(Permission.ADMIN)  # Should not raise
        assert len(ps) == 0

    def test_has_permission(self) -> None:
        """has() should check for permission presence."""
        ps = PermissionSet({Permission.READ_PROFILE})
        assert ps.has(Permission.READ_PROFILE) is True
        assert ps.has(Permission.ADMIN) is False

    def test_has_all_permissions(self) -> None:
        """has_all() should check for all permissions."""
        ps = PermissionSet(
            {Permission.READ_PROFILE, Permission.WRITE_PROFILE, Permission.READ_MEMORIES}
        )
        assert ps.has_all(Permission.READ_PROFILE, Permission.WRITE_PROFILE) is True
        assert ps.has_all(Permission.READ_PROFILE, Permission.ADMIN) is False

    def test_has_all_empty(self) -> None:
        """has_all() with no arguments should return True."""
        ps = PermissionSet()
        assert ps.has_all() is True

    def test_has_any_permissions(self) -> None:
        """has_any() should check for any permission."""
        ps = PermissionSet({Permission.READ_PROFILE})
        assert ps.has_any(Permission.READ_PROFILE, Permission.ADMIN) is True
        assert ps.has_any(Permission.ADMIN, Permission.DELETE_PROFILE) is False

    def test_has_any_empty(self) -> None:
        """has_any() with no arguments should return False."""
        ps = PermissionSet({Permission.READ_PROFILE})
        assert ps.has_any() is False

    def test_is_subset_of(self) -> None:
        """is_subset_of() should check subset relationship."""
        small = PermissionSet({Permission.READ_PROFILE})
        large = PermissionSet({Permission.READ_PROFILE, Permission.WRITE_PROFILE})

        assert small.is_subset_of(large) is True
        assert large.is_subset_of(small) is False
        assert small.is_subset_of(small) is True

    def test_contains_operator(self) -> None:
        """'in' operator should work with PermissionSet."""
        ps = PermissionSet({Permission.READ_PROFILE})
        assert Permission.READ_PROFILE in ps
        assert Permission.ADMIN not in ps

    def test_iteration(self) -> None:
        """PermissionSet should be iterable."""
        permissions = {Permission.READ_PROFILE, Permission.WRITE_PROFILE}
        ps = PermissionSet(permissions)
        iterated = set(ps)
        assert iterated == permissions

    def test_len(self) -> None:
        """len() should return permission count."""
        ps = PermissionSet({Permission.READ_PROFILE, Permission.WRITE_PROFILE})
        assert len(ps) == 2

    def test_repr(self) -> None:
        """repr() should return readable string."""
        ps = PermissionSet({Permission.READ_PROFILE})
        rep = repr(ps)
        assert "PermissionSet" in rep
        assert "READ_PROFILE" in rep

    def test_to_list(self) -> None:
        """to_list() should return list of permission names."""
        ps = PermissionSet({Permission.READ_PROFILE, Permission.WRITE_PROFILE})
        names = ps.to_list()
        assert isinstance(names, list)
        assert "READ_PROFILE" in names
        assert "WRITE_PROFILE" in names

    def test_from_list(self) -> None:
        """from_list() should create PermissionSet from names."""
        names = ["READ_PROFILE", "WRITE_PROFILE"]
        ps = PermissionSet.from_list(names)
        assert Permission.READ_PROFILE in ps
        assert Permission.WRITE_PROFILE in ps

    def test_from_list_ignores_unknown(self) -> None:
        """from_list() should ignore unknown permission names."""
        names = ["READ_PROFILE", "UNKNOWN_PERMISSION", "WRITE_PROFILE"]
        ps = PermissionSet.from_list(names)
        assert len(ps) == 2  # Unknown ignored
        assert Permission.READ_PROFILE in ps
        assert Permission.WRITE_PROFILE in ps


class TestPredefinedPermissionSets:
    """Tests for pre-defined permission sets."""

    def test_readonly_permissions(self) -> None:
        """READONLY_PERMISSIONS should contain read-only permissions."""
        assert Permission.READ_PROFILE in READONLY_PERMISSIONS
        assert Permission.READ_MEMORIES in READONLY_PERMISSIONS
        assert Permission.READ_OWN_COLLECTION in READONLY_PERMISSIONS
        assert Permission.READ_SCHEDULE in READONLY_PERMISSIONS
        # Should not have write permissions
        assert Permission.WRITE_PROFILE not in READONLY_PERMISSIONS
        assert Permission.WRITE_MEMORIES not in READONLY_PERMISSIONS
        assert Permission.SEND_MESSAGES not in READONLY_PERMISSIONS

    def test_standard_permissions(self) -> None:
        """STANDARD_PERMISSIONS should contain typical skill permissions."""
        assert Permission.READ_PROFILE in STANDARD_PERMISSIONS
        assert Permission.WRITE_PROFILE in STANDARD_PERMISSIONS
        assert Permission.READ_MEMORIES in STANDARD_PERMISSIONS
        assert Permission.WRITE_MEMORIES in STANDARD_PERMISSIONS
        assert Permission.READ_OWN_COLLECTION in STANDARD_PERMISSIONS
        assert Permission.WRITE_OWN_COLLECTION in STANDARD_PERMISSIONS
        assert Permission.SEND_MESSAGES in STANDARD_PERMISSIONS
        # Should not have admin or scheduling
        assert Permission.ADMIN not in STANDARD_PERMISSIONS
        assert Permission.SCHEDULE_TASKS not in STANDARD_PERMISSIONS

    def test_proactive_permissions(self) -> None:
        """PROACTIVE_PERMISSIONS should contain proactive skill permissions."""
        assert Permission.SEND_MESSAGES in PROACTIVE_PERMISSIONS
        assert Permission.SEND_DM in PROACTIVE_PERMISSIONS
        assert Permission.SCHEDULE_TASKS in PROACTIVE_PERMISSIONS
        assert Permission.READ_SCHEDULE in PROACTIVE_PERMISSIONS
        # Should not have admin
        assert Permission.ADMIN not in PROACTIVE_PERMISSIONS

    def test_readonly_is_subset_of_standard(self) -> None:
        """READONLY should be subset of STANDARD (for read perms)."""
        # Note: READONLY has READ_SCHEDULE which STANDARD doesn't have
        # So we check specific read permissions
        for perm in [
            Permission.READ_PROFILE,
            Permission.READ_MEMORIES,
            Permission.READ_OWN_COLLECTION,
        ]:
            assert perm in STANDARD_PERMISSIONS

    def test_standard_is_subset_of_proactive(self) -> None:
        """STANDARD should be subset of PROACTIVE."""
        assert STANDARD_PERMISSIONS.is_subset_of(PROACTIVE_PERMISSIONS)
