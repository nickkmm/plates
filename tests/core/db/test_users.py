"""Integration tests for app/core/db/users.py -- create_user,
get_user_by_username, list_users. Needs a real Postgres connection (see
tests/conftest.py's `db_conn`/`clean_db`); `users` is never actually
empty under it (Fase 2's conftest.py pre-seeds its own 3 test
identities into it on every test -- see tests/conftest.py's clean_db
docstring)."""

from __future__ import annotations

import psycopg
import pytest

from app.core import db


class TestCreateUser:
    def test_creates_and_returns_a_lookup_up_record(self, db_conn):
        user_id = db.create_user(db_conn, username="alice", password_hash="hash1", role="admin")
        user = db.get_user_by_id(db_conn, user_id)
        assert user is not None
        assert (user.username, user.password_hash, user.role, user.active) == (
            "alice", "hash1", "admin", True,
        )

    def test_active_defaults_to_true(self, db_conn):
        user_id = db.create_user(db_conn, username="bob", password_hash="hash2", role="production")
        user = db.get_user_by_id(db_conn, user_id)
        assert user is not None
        assert user.active is True

    def test_active_can_be_set_false(self, db_conn):
        user_id = db.create_user(
            db_conn, username="carol", password_hash="hash3", role="production", active=False
        )
        user = db.get_user_by_id(db_conn, user_id)
        assert user is not None
        assert user.active is False

    def test_duplicate_username_raises(self, db_conn):
        db.create_user(db_conn, username="dana", password_hash="hash4", role="admin")
        with pytest.raises(psycopg.errors.UniqueViolation):
            db.create_user(db_conn, username="dana", password_hash="hash5", role="admin")



class TestGetUserByUsername:
    def test_finds_an_existing_user(self, db_conn):
        db.create_user(db_conn, username="erin", password_hash="hash6", role="point_of_sale")
        user = db.get_user_by_username(db_conn, "erin")
        assert user is not None
        assert user.role == "point_of_sale"

    def test_unknown_username_returns_none(self, db_conn):
        assert db.get_user_by_username(db_conn, "ghost") is None

    def test_returns_inactive_user_too(self, db_conn):
        """Deactivation is the caller's business rule (app.web.auth), not
        this lookup's -- it must hand back an inactive row rather than
        hiding it."""
        db.create_user(db_conn, username="frank", password_hash="hash7", role="admin", active=False)
        user = db.get_user_by_username(db_conn, "frank")
        assert user is not None
        assert user.active is False



class TestListUsers:
    """`users` is never actually empty under `db_conn`/`clean_db` (Fase 2's
    conftest.py pre-seeds its own 3 test identities into it on every test --
    see tests/conftest.py's clean_db docstring) -- these tests check the two
    NEW ones added here appear, not the table's total contents."""

    def test_returns_every_user_ordered_by_id(self, db_conn):
        first = db.create_user(db_conn, username="a1", password_hash="h", role="admin")
        second = db.create_user(db_conn, username="a2", password_hash="h", role="production")
        users = db.list_users(db_conn)
        ids = [u.id for u in users]
        assert ids == sorted(ids)  # ordered by id
        assert ids[-2:] == [first, second]  # the two just-created users are last

    def test_seeded_test_users_are_present(self, db_conn):
        usernames = {u.username for u in db.list_users(db_conn)}
        assert {"testuser", "produccionuser", "puntoventauser"} <= usernames



class TestCreatedAt:
    def test_lookups_return_created_at(self, db_conn):
        user_id = db.create_user(db_conn, username="gina", password_hash="h", role="admin")
        user = db.get_user_by_id(db_conn, user_id)
        assert user is not None
        assert user.created_at is not None
        assert all(u.created_at is not None for u in db.list_users(db_conn))


class TestUpdateUser:
    def test_sets_role_and_active(self, db_conn):
        user_id = db.create_user(db_conn, username="hank", password_hash="h", role="admin")
        assert db.update_user(db_conn, user_id, role="production", active=False) is True
        user = db.get_user_by_id(db_conn, user_id)
        assert user is not None
        assert (user.role, user.active) == ("production", False)

    def test_unknown_id_returns_false(self, db_conn):
        assert db.update_user(db_conn, 999_999, role="admin", active=True) is False


class TestSetUserPassword:
    def test_replaces_the_hash(self, db_conn):
        user_id = db.create_user(db_conn, username="iris", password_hash="old", role="admin")
        assert db.set_user_password(db_conn, user_id, "new") is True
        user = db.get_user_by_id(db_conn, user_id)
        assert user is not None
        assert user.password_hash == "new"

    def test_unknown_id_returns_false(self, db_conn):
        assert db.set_user_password(db_conn, 999_999, "new") is False


class TestCountActiveUsersWithRole:
    def test_counts_only_active_users_with_that_role(self, db_conn):
        # conftest seeds one active user per system role.
        db.create_user(db_conn, username="j1", password_hash="h", role="production")
        db.create_user(db_conn, username="j2", password_hash="h", role="production", active=False)
        db.create_user(db_conn, username="j3", password_hash="h", role="admin")
        assert db.count_active_users_with_role(db_conn, "production") == 2
        assert db.count_active_users_with_role(db_conn, "admin") == 2

    def test_role_with_no_users_is_zero(self, db_conn):
        assert db.count_active_users_with_role(db_conn, "ghost") == 0
