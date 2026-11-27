"""Tests for app.auth.decorators.

Fase 2: the live login flow (authenticate/verify_credentials) is now
DB-backed (queries the `users` table via app/auth/repository/users.py), so those
tests need `clean_db`/`db_conn` (see tests/conftest.py). load_users/
load_roles are UNCHANGED and still pure os.environ parsing -- they're kept
only for app.schema_migrate.backfill_users (the historical, already-applied
migration that originally moved AUTH_USERS/AUTH_ROLES into `users`), so
their tests stay environment-only, no DB/Flask context needed. is_safe_next
is pure string/urlparse logic, also no context needed.

The `permission_required`/`authenticated` decorators need a real Flask
request/session context and real `users`/`role_permissions` rows (they read
both on every request), so they're exercised here against a minimal
standalone Flask app built just for this test file, on `db_conn`. Route-level
access per role is covered in tests/web/test_roles.py and
tests/web/test_permissions.py.
"""

from __future__ import annotations

import flask
import pytest
from werkzeug.security import generate_password_hash

from app.auth import decorators as auth
from app.auth import repository

# --------------------------------------------------------------------------
# load_users -- ONLY used by app.schema_migrate.backfill_users now
# --------------------------------------------------------------------------


class TestLoadUsers:
    def test_parses_multiple_users_splitting_on_first_colon_only(self, monkeypatch):
        """The hash itself contains ':' (pbkdf2:sha256:...) -- must split on
        the FIRST ':' only, keeping the rest of the hash intact.

        Uses monkeypatch (like every other test here) rather than mutating
        os.environ directly: a bare `del os.environ[...]` in a finally block
        discards whatever AUTH_USERS was set to by tests/conftest.py instead
        of restoring it, which is invisible under a single plain pytest
        invocation (file collection order happens to run test_login.py's
        login tests first) but corrupts later runs whenever the test suite
        executes more than once in the same process -- e.g. under mutmut,
        which reruns pytest in-process per mutant.
        """
        raw = "alice:pbkdf2:sha256:abc$def,bob:pbkdf2:sha256:xyz"
        monkeypatch.setenv("AUTH_USERS", raw)
        users = auth.load_users()
        assert users == {
            "alice": "pbkdf2:sha256:abc$def",
            "bob": "pbkdf2:sha256:xyz",
        }

    def test_ignores_blank_entries(self, monkeypatch):
        monkeypatch.setenv("AUTH_USERS", "alice:hash1,,   ,bob:hash2")
        users = auth.load_users()
        assert users == {"alice": "hash1", "bob": "hash2"}

    def test_ignores_malformed_entry_without_colon(self, monkeypatch):
        monkeypatch.setenv("AUTH_USERS", "alice:hash1,malformed,bob:hash2")
        users = auth.load_users()
        assert users == {"alice": "hash1", "bob": "hash2"}

    def test_ignores_entry_with_empty_username(self, monkeypatch):
        monkeypatch.setenv("AUTH_USERS", ":hash1,bob:hash2")
        users = auth.load_users()
        assert users == {"bob": "hash2"}

    def test_ignores_entry_with_empty_hash(self, monkeypatch):
        monkeypatch.setenv("AUTH_USERS", "alice:,bob:hash2")
        users = auth.load_users()
        assert users == {"bob": "hash2"}

    def test_unset_env_var_returns_empty_dict(self, monkeypatch):
        monkeypatch.delenv("AUTH_USERS", raising=False)
        assert auth.load_users() == {}

    def test_strips_whitespace_around_username_and_hash(self, monkeypatch):
        monkeypatch.setenv("AUTH_USERS", " alice : hash1 ")
        users = auth.load_users()
        assert users == {"alice": "hash1"}


# --------------------------------------------------------------------------
# load_roles -- ONLY used by app.schema_migrate.backfill_users now
# --------------------------------------------------------------------------


class TestLoadRoles:
    def test_parses_multiple_roles(self, monkeypatch):
        monkeypatch.setenv("AUTH_ROLES", "alice:admin,bob:production")
        roles = auth.load_roles()
        assert roles == {"alice": "admin", "bob": "production"}

    def test_ignores_blank_entries(self, monkeypatch):
        monkeypatch.setenv("AUTH_ROLES", "alice:admin,,   ,bob:production")
        roles = auth.load_roles()
        assert roles == {"alice": "admin", "bob": "production"}

    def test_ignores_malformed_entry_without_colon(self, monkeypatch):
        monkeypatch.setenv("AUTH_ROLES", "alice:production,malformed,bob:admin")
        roles = auth.load_roles()
        assert roles == {"alice": "production", "bob": "admin"}

    def test_ignores_entry_with_empty_username(self, monkeypatch):
        monkeypatch.setenv("AUTH_ROLES", ":production,bob:admin")
        roles = auth.load_roles()
        assert roles == {"bob": "admin"}

    def test_ignores_entry_with_empty_role(self, monkeypatch):
        monkeypatch.setenv("AUTH_ROLES", "alice:,bob:admin")
        roles = auth.load_roles()
        assert roles == {"bob": "admin"}

    def test_unset_env_var_returns_empty_dict(self, monkeypatch):
        monkeypatch.delenv("AUTH_ROLES", raising=False)
        assert auth.load_roles() == {}

    def test_strips_whitespace_around_username_and_role(self, monkeypatch):
        monkeypatch.setenv("AUTH_ROLES", " alice : production ")
        roles = auth.load_roles()
        assert roles == {"alice": "production"}


# --------------------------------------------------------------------------
# authenticate / verify_credentials -- Fase 2: DB-backed (queries `users`)
# --------------------------------------------------------------------------


class TestAuthenticate:
    """auth.authenticate() opens its OWN connection (db.get_conn(), see its
    docstring) -- a SEPARATE session from the `db_conn` fixture's, so setup
    done through `db_conn` must be committed before authenticate() can see
    it (both run at the default READ COMMITTED isolation level)."""

    def test_correct_password_returns_the_user_record(self, db_conn):
        repository.create_user(
            db_conn, username="alice", password_hash=generate_password_hash("correct-horse"),
            roles=["admin"],
        )
        db_conn.commit()
        user = auth.authenticate("alice", "correct-horse")
        assert user is not None
        assert user.username == "alice"
        assert user.roles == ["admin"]

    def test_wrong_password_returns_none(self, db_conn):
        repository.create_user(
            db_conn, username="alice", password_hash=generate_password_hash("correct-horse"),
            roles=["admin"],
        )
        db_conn.commit()
        assert auth.authenticate("alice", "wrong-password") is None

    def test_unknown_username_returns_none(self, db_conn):
        assert auth.authenticate("ghost", "whatever") is None

    def test_empty_password_returns_none(self, db_conn):
        repository.create_user(
            db_conn, username="alice", password_hash=generate_password_hash("correct-horse"),
            roles=["admin"],
        )
        db_conn.commit()
        assert auth.authenticate("alice", "") is None

    def test_inactive_user_returns_none_even_with_the_right_password(self, db_conn):
        """A deactivated account must not be able to log in, even though its
        hash still checks out -- get_user_by_username deliberately returns
        inactive rows too (that's authenticate's job to filter, not the
        lookup's)."""
        repository.create_user(
            db_conn, username="alice", password_hash=generate_password_hash("correct-horse"),
            roles=["admin"], active=False,
        )
        db_conn.commit()
        assert auth.authenticate("alice", "correct-horse") is None

    def test_returns_the_real_roles_not_a_default(self, db_conn):
        repository.create_user(
            db_conn, username="ptov", password_hash=generate_password_hash("pw"), roles=["point_of_sale"],
        )
        db_conn.commit()
        user = auth.authenticate("ptov", "pw")
        assert user is not None
        assert user.roles == ["point_of_sale"]


class TestVerifyCredentials:
    """Thin bool wrapper over authenticate() -- same DB-backed behavior (and
    same commit-before-checking requirement, see TestAuthenticate)."""

    def test_correct_password_is_true(self, db_conn):
        repository.create_user(
            db_conn, username="alice", password_hash=generate_password_hash("correct-horse-battery-staple"),
            roles=["admin"],
        )
        db_conn.commit()
        assert auth.verify_credentials("alice", "correct-horse-battery-staple") is True

    def test_wrong_password_is_false(self, db_conn):
        repository.create_user(
            db_conn, username="alice", password_hash=generate_password_hash("correct-horse-battery-staple"),
            roles=["admin"],
        )
        db_conn.commit()
        assert auth.verify_credentials("alice", "wrong-password") is False

    def test_unknown_username_is_false(self, db_conn):
        assert auth.verify_credentials("bob", "correct-horse-battery-staple") is False

    def test_empty_password_is_false(self, db_conn):
        repository.create_user(
            db_conn, username="alice", password_hash=generate_password_hash("correct-horse-battery-staple"),
            roles=["admin"],
        )
        db_conn.commit()
        assert auth.verify_credentials("alice", "") is False


# --------------------------------------------------------------------------
# is_safe_next -- security-critical open-redirect guard
# --------------------------------------------------------------------------


class TestIsSafeNext:
    def test_none_is_unsafe(self):
        assert auth.is_safe_next(None) is False

    def test_empty_string_is_unsafe(self):
        assert auth.is_safe_next("") is False

    def test_relative_path_without_leading_slash_is_unsafe(self):
        assert auth.is_safe_next("relative") is False

    def test_protocol_relative_is_unsafe(self):
        assert auth.is_safe_next("//evil.com") is False

    def test_backslash_trick_is_unsafe(self):
        assert auth.is_safe_next("/\\evil.com") is False

    def test_absolute_url_is_unsafe(self):
        assert auth.is_safe_next("http://evil.com") is False

    def test_absolute_https_url_is_unsafe(self):
        assert auth.is_safe_next("https://evil.com") is False

    def test_simple_path_is_safe(self):
        assert auth.is_safe_next("/inventory") is True

    def test_path_with_query_string_is_safe(self):
        assert auth.is_safe_next("/records/5?x=1") is True

    def test_root_path_is_safe(self):
        assert auth.is_safe_next("/") is True


# --------------------------------------------------------------------------
# permission_required / authenticated -- against a tiny standalone Flask app
# (one dummy view each), with the real test users from tests/conftest.py.
# --------------------------------------------------------------------------


@pytest.fixture
def guarded_app():
    """A minimal Flask app with one dummy view behind
    @permission_required("orders.capture") and one behind @authenticated.

    Also registers a stand-in "auth_routes.login" endpoint: auth._redirect_to_login
    (used for a stale/missing session) builds its redirect with
    `url_for("auth_routes.login", ...)`, which would otherwise raise
    BuildError in an app that never registers the real blueprint.
    """
    app = flask.Flask(__name__)
    app.secret_key = "test-only"

    auth_routes = flask.Blueprint("auth_routes", __name__)

    @auth_routes.get("/login")
    def login():
        return "login page"

    app.register_blueprint(auth_routes)

    @app.get("/capture-only")
    @auth.permission_required("orders.capture")
    def capture_only():
        return "ok"

    @app.get("/any-user")
    @auth.authenticated
    def any_user():
        return "ok"

    return app


@pytest.fixture
def guarded_client(guarded_app):
    with guarded_app.test_client() as c:
        yield c


def _log_in_as(client, conn, username):
    """Put a real `users` row into the client's session, the way POST /login
    does."""
    user = repository.get_user_by_username(conn, username)
    assert user is not None
    with client.session_transaction() as sess:
        sess["user"] = user.username
        sess["user_id"] = user.id
    return user


class TestPermissionRequired:
    def test_no_session_redirects_to_login(self, guarded_client):
        resp = guarded_client.get("/capture-only")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_session_without_role_is_accepted(self, guarded_client, db_conn):
        """The session stores `user` and `user_id` only; the roles come from
        the database on every request."""
        user = repository.get_user_by_username(db_conn, "puntoventauser")
        assert user is not None
        with guarded_client.session_transaction() as sess:
            sess["user"] = user.username
            sess["user_id"] = user.id
        assert guarded_client.get("/capture-only").status_code == 200

    def test_session_missing_user_id_redirects_to_login(self, guarded_client):
        """A session predating user_id (role+user but no user_id, e.g. a
        cookie from before Fase 2) must be treated as stale -- not crash
        when current_user() reads session['user_id']."""
        with guarded_client.session_transaction() as sess:
            sess["user"] = "someone"
            sess["role"] = "admin"
        resp = guarded_client.get("/capture-only")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_user_id_of_no_user_redirects_to_login(self, guarded_client, db_conn):
        with guarded_client.session_transaction() as sess:
            sess["user"] = "ghost"
            sess["user_id"] = 999999
        resp = guarded_client.get("/capture-only")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_user_with_the_permission_is_allowed(self, guarded_client, db_conn):
        _log_in_as(guarded_client, db_conn, "puntoventauser")
        assert guarded_client.get("/capture-only").status_code == 200

    def test_admin_is_allowed(self, guarded_client, db_conn):
        _log_in_as(guarded_client, db_conn, "testuser")
        assert guarded_client.get("/capture-only").status_code == 200

    def test_user_without_the_permission_is_forbidden(self, guarded_client, db_conn):
        _log_in_as(guarded_client, db_conn, "produccionuser")
        assert guarded_client.get("/capture-only").status_code == 403

    def test_inactive_user_redirects_to_login_and_clears_the_session(self, guarded_client, db_conn):
        user = _log_in_as(guarded_client, db_conn, "puntoventauser")
        repository.update_user(db_conn, user.id, active=False)
        db_conn.commit()
        resp = guarded_client.get("/capture-only")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]
        with guarded_client.session_transaction() as sess:
            assert "user_id" not in sess

    def test_role_change_applies_on_the_next_request(self, guarded_client, db_conn):
        user = _log_in_as(guarded_client, db_conn, "produccionuser")
        assert guarded_client.get("/capture-only").status_code == 403
        repository.set_user_roles(db_conn, user.id, ["point_of_sale"])
        db_conn.commit()
        assert guarded_client.get("/capture-only").status_code == 200


class TestAuthenticated:
    def test_no_session_redirects_to_login(self, guarded_client):
        resp = guarded_client.get("/any-user")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_session_without_role_is_accepted(self, guarded_client, db_conn):
        user = repository.get_user_by_username(db_conn, "produccionuser")
        assert user is not None
        with guarded_client.session_transaction() as sess:
            sess["user"] = user.username
            sess["user_id"] = user.id
        assert guarded_client.get("/any-user").status_code == 200

    def test_session_missing_user_id_redirects_to_login(self, guarded_client):
        with guarded_client.session_transaction() as sess:
            sess["user"] = "someone"
            sess["role"] = "admin"
        resp = guarded_client.get("/any-user")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_any_active_user_is_allowed_whatever_their_permissions(self, guarded_client, db_conn):
        _log_in_as(guarded_client, db_conn, "produccionuser")
        assert guarded_client.get("/any-user").status_code == 200

    def test_inactive_user_redirects_to_login(self, guarded_client, db_conn):
        user = _log_in_as(guarded_client, db_conn, "produccionuser")
        repository.update_user(db_conn, user.id, active=False)
        db_conn.commit()
        resp = guarded_client.get("/any-user")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]


class TestHasPermission:
    def test_false_with_no_session(self, guarded_app):
        with guarded_app.test_request_context("/"):
            assert auth.has_permission("records.view") is False
