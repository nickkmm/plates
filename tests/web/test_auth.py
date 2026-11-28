"""Tests for app/shared/auth.py (the access-token check) and app/auth/login.py.

Fase 2: the live login flow (authenticate/verify_credentials, app/auth/login.py)
is DB-backed (queries the `users` table via app/auth/repository/users.py), so
those tests need `clean_db`/`db_conn` (see tests/conftest.py). load_users/
load_roles are still pure os.environ parsing -- they're kept only for
app.schema_migrate.backfill_users (the historical, already-applied migration
that originally moved AUTH_USERS/AUTH_ROLES into `users`), so their tests
stay environment-only. is_safe_next is pure string/urlparse logic.

The `permission_required`/`authenticated` decorators read only the
`access_token` cookie (never the database), so they're exercised here
against a minimal standalone Flask app built just for this test file, with
tokens issued by the test itself. Route-level access per role is covered in
tests/web/test_roles.py and tests/web/test_permissions.py.
"""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import flask
import pytest
from werkzeug.security import generate_password_hash

from app.auth import login as auth
from app.auth import repository
from app.shared import auth as shared_auth
from app.shared import db
from app.shared.timezones import utc_now
from tests.web._helpers import set_access_token

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
        assert shared_auth.is_safe_next(None) is False

    def test_empty_string_is_unsafe(self):
        assert shared_auth.is_safe_next("") is False

    def test_relative_path_without_leading_slash_is_unsafe(self):
        assert shared_auth.is_safe_next("relative") is False

    def test_protocol_relative_is_unsafe(self):
        assert shared_auth.is_safe_next("//evil.com") is False

    def test_backslash_trick_is_unsafe(self):
        assert shared_auth.is_safe_next("/\\evil.com") is False

    def test_absolute_url_is_unsafe(self):
        assert shared_auth.is_safe_next("http://evil.com") is False

    def test_absolute_https_url_is_unsafe(self):
        assert shared_auth.is_safe_next("https://evil.com") is False

    def test_simple_path_is_safe(self):
        assert shared_auth.is_safe_next("/inventory") is True

    def test_path_with_query_string_is_safe(self):
        assert shared_auth.is_safe_next("/records/5?x=1") is True

    def test_root_path_is_safe(self):
        assert shared_auth.is_safe_next("/") is True


# --------------------------------------------------------------------------
# permission_required / authenticated -- against a tiny standalone Flask app
# (one dummy view each), with access tokens issued by the test.
# --------------------------------------------------------------------------


@pytest.fixture
def guarded_app():
    """A minimal Flask app with one dummy view behind
    @permission_required("orders.capture"), one behind @authenticated and one
    under /api.

    Also registers stand-in "auth_routes.login", "auth_api.refresh_continue"
    and "auth_api.logout_continue" endpoints: app/shared/auth.py builds its
    redirects with url_for, which would otherwise raise BuildError in an app
    that never registers the real blueprints.
    """
    app = flask.Flask(__name__)
    app.secret_key = "test-only"

    auth_routes = flask.Blueprint("auth_routes", __name__)

    @auth_routes.get("/login")
    def login():
        return "login page"

    auth_api = flask.Blueprint("auth_api", __name__, url_prefix="/api/auth")

    @auth_api.get("/refresh/continue")
    def refresh_continue():
        return "refresh"

    @auth_api.get("/logout/continue")
    def logout_continue():
        return "logout"

    app.register_blueprint(auth_routes)
    app.register_blueprint(auth_api)

    @app.route("/capture-only", methods=["GET", "POST"])
    @shared_auth.permission_required("orders.capture")
    def capture_only():
        return "ok"

    @app.get("/any-user")
    @shared_auth.authenticated
    def any_user():
        return "ok"

    @app.get("/api/thing")
    @shared_auth.permission_required("orders.capture")
    def api_thing():
        return "ok"

    return app


@pytest.fixture
def guarded_client(guarded_app):
    with guarded_app.test_client() as c:
        yield c


def _location(resp):
    assert resp.status_code == 302
    location = urlparse(resp.headers["Location"])
    return location.path, parse_qs(location.query)


def _expired(client, perms=("orders.capture",)):
    set_access_token(client, 7, "someone", perms, now=utc_now() - timedelta(minutes=6))


class TestPermissionRequired:
    def test_no_cookie_redirects_to_login(self, guarded_client):
        assert _location(guarded_client.get("/capture-only")) == ("/login", {"next": ["/capture-only"]})

    def test_invalid_token_redirects_to_login(self, guarded_client):
        guarded_client.set_cookie("access_token", "not-a-jwt", path="/")
        assert _location(guarded_client.get("/capture-only"))[0] == "/login"

    def test_forged_token_redirects_to_login(self, guarded_client, monkeypatch):
        """Signed with another secret: InvalidTokenError, not a refresh."""
        monkeypatch.setenv("JWT_SECRET", "another-secret-at-least-32-bytes-long")
        set_access_token(guarded_client, 7, "someone", ["orders.capture"])
        monkeypatch.undo()
        assert _location(guarded_client.get("/capture-only"))[0] == "/login"

    def test_expired_token_redirects_through_refresh(self, guarded_client):
        _expired(guarded_client)
        resp = guarded_client.get("/capture-only?x=1")
        assert _location(resp) == ("/api/auth/refresh/continue", {"next": ["/capture-only?x=1"]})

    def test_non_get_with_an_expired_token_comes_back_to_root(self, guarded_client):
        _expired(guarded_client)
        assert _location(guarded_client.post("/capture-only")) == ("/api/auth/refresh/continue", {"next": ["/"]})

    def test_non_get_without_a_token_comes_back_to_root(self, guarded_client):
        assert _location(guarded_client.post("/capture-only")) == ("/login", {"next": ["/"]})

    def test_api_path_answers_a_json_401(self, guarded_client):
        for set_up in (lambda: None, lambda: _expired(guarded_client)):
            set_up()
            resp = guarded_client.get("/api/thing")
            assert resp.status_code == 401
            assert resp.get_json()["error"]["code"] == "not_authenticated"

    def test_token_with_the_code_is_allowed(self, guarded_client):
        set_access_token(guarded_client, 7, "someone", ["orders.capture"])
        assert guarded_client.get("/capture-only").status_code == 200

    def test_token_without_the_code_is_forbidden(self, guarded_client):
        set_access_token(guarded_client, 7, "someone", ["records.view"])
        assert guarded_client.get("/capture-only").status_code == 403

    def test_never_reads_the_database(self, guarded_client, monkeypatch):
        def no_db(*_args, **_kwargs):
            raise AssertionError("the access check must not read the database")

        monkeypatch.setattr(db, "get_conn", no_db)
        set_access_token(guarded_client, 7, "someone", ["orders.capture"])
        assert guarded_client.get("/capture-only").status_code == 200

    def test_exposes_its_codes(self, guarded_app):
        assert guarded_app.view_functions["capture_only"].required_permissions == ("orders.capture",)


class TestAuthenticated:
    def test_no_cookie_redirects_to_login(self, guarded_client):
        assert _location(guarded_client.get("/any-user"))[0] == "/login"

    def test_expired_token_redirects_through_refresh(self, guarded_client):
        _expired(guarded_client)
        assert _location(guarded_client.get("/any-user"))[0] == "/api/auth/refresh/continue"

    def test_any_token_is_allowed_whatever_its_permissions(self, guarded_client):
        set_access_token(guarded_client, 7, "someone", [])
        assert guarded_client.get("/any-user").status_code == 200


class TestCurrentUser:
    def test_reads_the_token_claims(self, guarded_app):
        with guarded_app.test_client() as client:
            set_access_token(client, 7, "someone", ["records.view"])
            client.get("/any-user")
            user = shared_auth.current_user()
            assert user is not None
            assert (user.user_id, user.username, user.perms) == (7, "someone", ("records.view",))


class TestHasPermission:
    def test_false_with_no_token(self, guarded_app):
        with guarded_app.test_request_context("/"):
            assert shared_auth.has_permission("records.view") is False
