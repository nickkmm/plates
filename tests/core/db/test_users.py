"""Integration tests for app/auth/repository/users.py -- create_user, the lookups,
list_users and the user_roles functions. Needs a real Postgres connection (see
tests/conftest.py's `db_conn`/`clean_db`); `users` is never actually
empty under it (Fase 2's conftest.py pre-seeds its own 3 test
identities into it on every test -- see tests/conftest.py's clean_db
docstring)."""

from __future__ import annotations

import psycopg
import pytest

from app.auth import repository


def _assignments(conn, user_id: int) -> dict[str, int | None]:
    """{role: assigned_by} of a user's user_roles rows."""
    with conn.cursor() as cur:
        cur.execute("SELECT role, assigned_by FROM user_roles WHERE user_id = %s", (user_id,))
        return {r["role"]: r["assigned_by"] for r in cur.fetchall()}


def _assigned_at(conn, user_id: int, role: str):
    with conn.cursor() as cur:
        cur.execute("SELECT assigned_at FROM user_roles WHERE user_id = %s AND role = %s", (user_id, role))
        row = cur.fetchone()
        return row["assigned_at"] if row else None


class TestCreateUser:
    def test_creates_and_returns_a_lookup_up_record(self, db_conn):
        user_id = repository.create_user(db_conn, username="alice", password_hash="hash1", roles=["admin"])
        user = repository.get_user_by_id(db_conn, user_id)
        assert user is not None
        assert (user.username, user.password_hash, user.roles, user.active) == (
            "alice", "hash1", ["admin"], True,
        )

    def test_active_defaults_to_true(self, db_conn):
        user_id = repository.create_user(db_conn, username="bob", password_hash="hash2", roles=["production"])
        user = repository.get_user_by_id(db_conn, user_id)
        assert user is not None
        assert user.active is True

    def test_active_can_be_set_false(self, db_conn):
        user_id = repository.create_user(
            db_conn, username="carol", password_hash="hash3", roles=["production"], active=False
        )
        user = repository.get_user_by_id(db_conn, user_id)
        assert user is not None
        assert user.active is False

    def test_duplicate_username_raises(self, db_conn):
        repository.create_user(db_conn, username="dana", password_hash="hash4", roles=["admin"])
        with pytest.raises(psycopg.errors.UniqueViolation):
            repository.create_user(db_conn, username="dana", password_hash="hash5", roles=["admin"])

    def test_unknown_role_raises_foreign_key_violation(self, db_conn):
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            repository.create_user(db_conn, username="ghosty", password_hash="h", roles=["ghost"])

    def test_several_roles_come_back_sorted_and_deduplicated(self, db_conn):
        user_id = repository.create_user(
            db_conn, username="multi", password_hash="h", roles=["production", "admin", "production"]
        )
        user = repository.get_user_by_id(db_conn, user_id)
        assert user is not None
        assert user.roles == ["admin", "production"]

    def test_no_roles_gives_an_empty_list(self, db_conn):
        user_id = repository.create_user(db_conn, username="none", password_hash="h", roles=[])
        user = repository.get_user_by_id(db_conn, user_id)
        assert user is not None
        assert user.roles == []

    def test_assigned_by_is_recorded(self, db_conn):
        admin_id = repository.create_user(db_conn, username="boss", password_hash="h", roles=["admin"])
        user_id = repository.create_user(
            db_conn, username="hired", password_hash="h", roles=["production"], assigned_by=admin_id
        )
        assert _assignments(db_conn, user_id) == {"production": admin_id}



class TestGetUserByUsername:
    def test_finds_an_existing_user(self, db_conn):
        repository.create_user(db_conn, username="erin", password_hash="hash6", roles=["point_of_sale"])
        user = repository.get_user_by_username(db_conn, "erin")
        assert user is not None
        assert user.roles == ["point_of_sale"]

    def test_unknown_username_returns_none(self, db_conn):
        assert repository.get_user_by_username(db_conn, "ghost") is None

    def test_returns_inactive_user_too(self, db_conn):
        """Deactivation is the caller's business rule (app.auth.decorators), not
        this lookup's -- it must hand back an inactive row rather than
        hiding it."""
        repository.create_user(db_conn, username="frank", password_hash="hash7", roles=["admin"], active=False)
        user = repository.get_user_by_username(db_conn, "frank")
        assert user is not None
        assert user.active is False



class TestListUsers:
    """`users` is never actually empty under `db_conn`/`clean_db` (Fase 2's
    conftest.py pre-seeds its own 3 test identities into it on every test --
    see tests/conftest.py's clean_db docstring) -- these tests check the two
    NEW ones added here appear, not the table's total contents."""

    def test_returns_every_user_ordered_by_id(self, db_conn):
        first = repository.create_user(db_conn, username="a1", password_hash="h", roles=["admin"])
        second = repository.create_user(db_conn, username="a2", password_hash="h", roles=["production"])
        users = repository.list_users(db_conn)
        ids = [u.id for u in users]
        assert ids == sorted(ids)  # ordered by id
        assert ids[-2:] == [first, second]  # the two just-created users are last

    def test_seeded_test_users_are_present(self, db_conn):
        usernames = {u.username for u in repository.list_users(db_conn)}
        assert {"testuser", "produccionuser", "puntoventauser"} <= usernames



class TestCreatedAt:
    def test_lookups_return_created_at(self, db_conn):
        user_id = repository.create_user(db_conn, username="gina", password_hash="h", roles=["admin"])
        user = repository.get_user_by_id(db_conn, user_id)
        assert user is not None
        assert user.created_at is not None
        assert all(u.created_at is not None for u in repository.list_users(db_conn))


class TestUpdateUser:
    def test_sets_active_and_keeps_the_roles(self, db_conn):
        user_id = repository.create_user(db_conn, username="hank", password_hash="h", roles=["admin"])
        assert repository.update_user(db_conn, user_id, active=False) is True
        user = repository.get_user_by_id(db_conn, user_id)
        assert user is not None
        assert (user.roles, user.active) == (["admin"], False)

    def test_unknown_id_returns_false(self, db_conn):
        assert repository.update_user(db_conn, 999_999, active=True) is False


class TestSetUserPassword:
    def test_replaces_the_hash(self, db_conn):
        user_id = repository.create_user(db_conn, username="iris", password_hash="old", roles=["admin"])
        assert repository.set_user_password(db_conn, user_id, "new") is True
        user = repository.get_user_by_id(db_conn, user_id)
        assert user is not None
        assert user.password_hash == "new"

    def test_unknown_id_returns_false(self, db_conn):
        assert repository.set_user_password(db_conn, 999_999, "new") is False


class TestCountActiveUsersWithRole:
    def test_counts_only_active_users_with_that_role(self, db_conn):
        # conftest seeds one active user per system role.
        repository.create_user(db_conn, username="j1", password_hash="h", roles=["production"])
        repository.create_user(db_conn, username="j2", password_hash="h", roles=["production"], active=False)
        repository.create_user(db_conn, username="j3", password_hash="h", roles=["admin"])
        repository.create_user(db_conn, username="j4", password_hash="h", roles=["admin", "production"])
        repository.create_user(db_conn, username="j5", password_hash="h", roles=[])
        assert repository.count_active_users_with_role(db_conn, "production") == 3
        assert repository.count_active_users_with_role(db_conn, "admin") == 3

    def test_role_with_no_users_is_zero(self, db_conn):
        assert repository.count_active_users_with_role(db_conn, "ghost") == 0


class TestSetUserRoles:
    def test_replaces_the_set_and_keeps_assigned_at_of_kept_rows(self, db_conn):
        user_id = repository.create_user(db_conn, username="k1", password_hash="h", roles=["production", "point_of_sale"])
        with db_conn.cursor() as cur:
            cur.execute(
                "UPDATE user_roles SET assigned_at = '2020-01-01' WHERE user_id = %s AND role = 'production'",
                (user_id,),
            )
        kept_at = _assigned_at(db_conn, user_id, "production")
        admin_id = repository.create_user(db_conn, username="k-boss", password_hash="h", roles=["admin"])

        assert repository.set_user_roles(db_conn, user_id, ["production", "admin"], assigned_by=admin_id) is True

        user = repository.get_user_by_id(db_conn, user_id)
        assert user is not None
        assert user.roles == ["admin", "production"]
        assert _assigned_at(db_conn, user_id, "production") == kept_at
        assert _assignments(db_conn, user_id) == {"production": None, "admin": admin_id}

    def test_empty_list_removes_every_role(self, db_conn):
        user_id = repository.create_user(db_conn, username="k2", password_hash="h", roles=["production"])
        assert repository.set_user_roles(db_conn, user_id, []) is True
        user = repository.get_user_by_id(db_conn, user_id)
        assert user is not None
        assert user.roles == []

    def test_unknown_user_returns_false(self, db_conn):
        assert repository.set_user_roles(db_conn, 999_999, ["admin"]) is False


class TestAssignAndUnassignRole:
    def test_assign_role_true_then_false(self, db_conn):
        user_id = repository.create_user(db_conn, username="l1", password_hash="h", roles=["production"])
        assert repository.assign_role(db_conn, user_id, "admin") is True
        assert repository.assign_role(db_conn, user_id, "admin") is False
        user = repository.get_user_by_id(db_conn, user_id)
        assert user is not None
        assert user.roles == ["admin", "production"]

    def test_unassign_role_true_then_false(self, db_conn):
        user_id = repository.create_user(db_conn, username="l2", password_hash="h", roles=["production", "admin"])
        assert repository.unassign_role(db_conn, user_id, "admin") is True
        assert repository.unassign_role(db_conn, user_id, "admin") is False
        user = repository.get_user_by_id(db_conn, user_id)
        assert user is not None
        assert user.roles == ["production"]


class TestGetUserPermissions:
    def test_is_the_union_of_the_roles_stored_codes(self, db_conn):
        repository.create_role(db_conn, name="stock", label="Bodega", permissions=["inventory.manage", "records.view"])
        user_id = repository.create_user(db_conn, username="m1", password_hash="h", roles=["production", "stock"])
        assert repository.get_user_permissions(db_conn, user_id) == (
            repository.get_role_permissions(db_conn, "production") | repository.get_role_permissions(db_conn, "stock")
        )
        assert "inventory.manage" in repository.get_user_permissions(db_conn, user_id)

    def test_user_without_roles_has_none(self, db_conn):
        user_id = repository.create_user(db_conn, username="m2", password_hash="h", roles=[])
        assert repository.get_user_permissions(db_conn, user_id) == frozenset()


class TestListUsersWithRole:
    def test_returns_only_holders_by_id(self, db_conn):
        first = repository.create_user(db_conn, username="n1", password_hash="h", roles=["production", "admin"])
        repository.create_user(db_conn, username="n2", password_hash="h", roles=["point_of_sale"])
        third = repository.create_user(db_conn, username="n3", password_hash="h", roles=["admin"], active=False)
        users = repository.list_users_with_role(db_conn, "admin")
        seeded_admin = repository.get_user_by_username(db_conn, "testuser")
        assert seeded_admin is not None
        assert [u.id for u in users] == [seeded_admin.id, first, third]
        assert all("admin" in u.roles for u in users)

    def test_role_nobody_holds_is_empty(self, db_conn):
        assert repository.list_users_with_role(db_conn, "ghost") == []
