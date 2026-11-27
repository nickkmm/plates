"""Integration tests for app/core/db/roles.py. Needs a real Postgres
connection (see tests/conftest.py's `db_conn`/`clean_db`). `roles`,
`role_permissions` and `permissions` hold the three seeded system roles and
the permission catalog (static seed data, not truncated by clean_db); every
write here is rolled back after each test."""

from __future__ import annotations

import psycopg
import pytest
from psycopg.rows import tuple_row

from app.auth import repository
from app.auth.permissions import PERMISSIONS


def _permission_rows(conn, name: str) -> int:
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute("SELECT count(*) FROM role_permissions WHERE role = %s", (name,))
        return cur.fetchone()[0]


class TestListRoles:
    def test_system_roles_first_then_by_label(self, db_conn):
        repository.create_role(db_conn, name="zeta", label="Bodega", permissions=[])
        repository.create_role(db_conn, name="alpha", label="Auditoría", permissions=[])
        names = [r.name for r in repository.list_roles(db_conn)]
        # System roles by label: Administrador, Producción, Punto de venta.
        assert names == ["admin", "production", "point_of_sale", "alpha", "zeta"]

    def test_permissions_sorted_and_user_count_includes_inactive(self, db_conn):
        repository.create_role(db_conn, name="clerk", label="Auxiliar", permissions=["records.view", "orders.capture"])
        repository.create_user(db_conn, username="c1", password_hash="h", roles=["clerk"])
        repository.create_user(db_conn, username="c2", password_hash="h", roles=["clerk"], active=False)
        clerk = next(r for r in repository.list_roles(db_conn) if r.name == "clerk")
        assert clerk.permissions == ["orders.capture", "records.view"]
        assert clerk.user_count == 2
        assert clerk.is_system is False


class TestListPermissions:
    def test_returns_the_catalog_in_order(self, db_conn):
        permissions = repository.list_permissions(db_conn)
        assert len(permissions) == 13
        assert all(isinstance(p, repository.PermissionRecord) for p in permissions)
        assert [(p.code, p.label) for p in permissions] == list(PERMISSIONS)


class TestRolePermissionsForeignKey:
    def test_unknown_code_inserted_directly_raises_foreign_key_violation(self, db_conn):
        with db_conn.cursor() as cur, pytest.raises(psycopg.errors.ForeignKeyViolation):
            cur.execute("INSERT INTO role_permissions (role, permission) VALUES ('production', 'nope.code')")


class TestGetRole:
    def test_returns_a_seeded_role(self, db_conn):
        role = repository.get_role(db_conn, "production")
        assert role is not None
        assert (role.label, role.is_system) == ("Producción", True)
        assert role.permissions == ["production.deliver", "production.manage", "production.view", "records.view"]
        assert role.user_count == 1  # conftest's produccionuser

    def test_role_without_permissions_has_an_empty_list(self, db_conn):
        repository.create_role(db_conn, name="empty", label="Vacío", permissions=[])
        role = repository.get_role(db_conn, "empty")
        assert role is not None
        assert (role.permissions, role.user_count) == ([], 0)

    def test_unknown_role_returns_none(self, db_conn):
        assert repository.get_role(db_conn, "ghost") is None


class TestGetRolePermissions:
    def test_returns_the_stored_codes(self, db_conn):
        assert repository.get_role_permissions(db_conn, "production") == frozenset(
            {"records.view", "production.view", "production.manage", "production.deliver"}
        )

    def test_unknown_role_returns_an_empty_set(self, db_conn):
        assert repository.get_role_permissions(db_conn, "ghost") == frozenset()


class TestCreateRole:
    def test_creates_a_non_system_role_with_its_permissions(self, db_conn):
        repository.create_role(db_conn, name="cashier", label="Caja", permissions=["cash_summary.view"])
        role = repository.get_role(db_conn, "cashier")
        assert role is not None
        assert (role.label, role.is_system, role.permissions) == ("Caja", False, ["cash_summary.view"])

    def test_unknown_code_raises_value_error_and_writes_nothing(self, db_conn):
        with pytest.raises(ValueError):
            repository.create_role(db_conn, name="bad", label="Malo", permissions=["records.view", "nope"])
        assert repository.get_role(db_conn, "bad") is None
        assert _permission_rows(db_conn, "bad") == 0

    def test_duplicate_name_raises_unique_violation(self, db_conn):
        with pytest.raises(psycopg.errors.UniqueViolation):
            repository.create_role(db_conn, name="admin", label="Otro admin", permissions=[])

    def test_duplicate_label_raises_unique_violation(self, db_conn):
        with pytest.raises(psycopg.errors.UniqueViolation):
            repository.create_role(db_conn, name="admin2", label="Administrador", permissions=[])


class TestUpdateRole:
    def test_sets_the_label_and_replaces_the_permissions(self, db_conn):
        repository.create_role(db_conn, name="clerk", label="Auxiliar", permissions=["records.view", "orders.capture"])
        assert repository.update_role(db_conn, "clerk", label="Auxiliar de caja", permissions=["cash_summary.view"])
        role = repository.get_role(db_conn, "clerk")
        assert role is not None
        assert (role.label, role.permissions) == ("Auxiliar de caja", ["cash_summary.view"])

    def test_unknown_code_raises_value_error_and_writes_nothing(self, db_conn):
        repository.create_role(db_conn, name="clerk", label="Auxiliar", permissions=["records.view"])
        with pytest.raises(ValueError):
            repository.update_role(db_conn, "clerk", label="Cambiado", permissions=["nope"])
        role = repository.get_role(db_conn, "clerk")
        assert role is not None
        assert (role.label, role.permissions) == ("Auxiliar", ["records.view"])

    def test_unknown_role_returns_false_and_writes_nothing(self, db_conn):
        assert repository.update_role(db_conn, "ghost", label="Fantasma", permissions=["records.view"]) is False
        assert _permission_rows(db_conn, "ghost") == 0


class TestDeleteRole:
    def test_deletes_the_role_and_cascades_its_permissions(self, db_conn):
        repository.create_role(db_conn, name="clerk", label="Auxiliar", permissions=["records.view"])
        assert repository.delete_role(db_conn, "clerk") is True
        assert repository.get_role(db_conn, "clerk") is None
        assert _permission_rows(db_conn, "clerk") == 0

    def test_unknown_role_returns_false(self, db_conn):
        assert repository.delete_role(db_conn, "ghost") is False

    def test_role_still_held_by_a_user_raises_foreign_key_violation(self, db_conn):
        repository.create_role(db_conn, name="clerk", label="Auxiliar", permissions=[])
        repository.create_user(db_conn, username="c1", password_hash="h", roles=["clerk"], active=False)
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            repository.delete_role(db_conn, "clerk")
