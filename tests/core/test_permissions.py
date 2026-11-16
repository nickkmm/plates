"""Tests for app.core.permissions -- pure, no database."""

from __future__ import annotations

from app.core.permissions import ADMIN_ROLE, PERMISSION_CODES, PERMISSIONS, effective_permissions

_ISSUE_ORDER = [
    "orders.capture",
    "cash_summary.view",
    "declarations.review",
    "production.view",
    "production.manage",
    "production.deliver",
    "records.view",
    "records.delete",
    "duplicates.review",
    "inventory.manage",
    "pricing.manage",
    "customers.manage",
    "users.manage",
]


class TestCatalog:
    def test_thirteen_unique_codes_in_issue_order(self):
        codes = [code for code, _ in PERMISSIONS]
        assert codes == _ISSUE_ORDER
        assert len(set(codes)) == 13

    def test_permission_codes_matches_the_catalog(self):
        assert frozenset(_ISSUE_ORDER) == PERMISSION_CODES

    def test_labels_are_the_spanish_text(self):
        labels = dict(PERMISSIONS)
        assert labels["orders.capture"] == "Registrar y editar órdenes"
        assert labels["users.manage"] == "Gestionar usuarios y roles"


class TestEffectivePermissions:
    def test_admin_with_no_stored_rows_gets_every_code(self):
        assert effective_permissions([ADMIN_ROLE], []) == PERMISSION_CODES
        assert len(effective_permissions(["admin"], [])) == 13

    def test_admin_with_junk_stored_still_gets_exactly_the_catalog(self):
        assert effective_permissions([ADMIN_ROLE], ["junk", "records.view"]) == PERMISSION_CODES

    def test_admin_among_several_roles_gets_every_code(self):
        assert effective_permissions(["production", ADMIN_ROLE, "clerk"], ["records.view"]) == PERMISSION_CODES

    def test_roles_can_be_a_generator(self):
        roles = (name for name in ["production", ADMIN_ROLE])
        assert effective_permissions(roles, []) == PERMISSION_CODES

    def test_other_role_gets_stored_minus_unknown_codes(self):
        result = effective_permissions(["point_of_sale"], ["records.view", "orders.capture", "gone.code"])
        assert result == frozenset({"records.view", "orders.capture"})

    def test_several_roles_get_the_stored_union_minus_unknown_codes(self):
        result = effective_permissions(
            ["production", "clerk"], ["records.view", "inventory.manage", "gone.code"]
        )
        assert result == frozenset({"records.view", "inventory.manage"})

    def test_other_role_with_no_stored_rows_gets_nothing(self):
        assert effective_permissions(["production"], []) == frozenset()

    def test_no_roles_and_no_stored_rows_gets_nothing(self):
        assert effective_permissions([], []) == frozenset()
