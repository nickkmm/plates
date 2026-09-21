"""Tests for app.web.auth.

Fase 2: the live login flow (authenticate/verify_credentials) is now
DB-backed (queries the `users` table via app/core/db/users.py), so those
tests need `clean_db`/`db_conn` (see tests/conftest.py). load_users/
load_roles are UNCHANGED and still pure os.environ parsing -- they're kept
only for app.schema_migrate.backfill_users (the historical, already-applied
migration that originally moved AUTH_USERS/AUTH_ROLES into `users`), so
their tests stay environment-only, no DB/Flask context needed. is_safe_next
is pure string/urlparse logic, also no context needed.

The `pos_allowed`/`login_required`/`consulta_allowed` decorators need a real
Flask request/session context to exercise -- login_required/consulta_allowed
already get end-to-end coverage through real routes in tests/web/test_app.py
(/ingest, /records); pos_allowed has no route wired to it yet (that's
Fase 3), so it's exercised here against a minimal standalone Flask app built
just for this test file.
"""

from __future__ import annotations

import flask
import pytest
from werkzeug.security import generate_password_hash

from app.core import db
from app.web import auth

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
        invocation (file collection order happens to run test_app.py's
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
        db.create_user(
            db_conn, username="alice", password_hash=generate_password_hash("correct-horse"),
            role="admin",
        )
        db_conn.commit()
        user = auth.authenticate("alice", "correct-horse")
        assert user is not None
        assert user.username == "alice"
        assert user.role == "admin"

    def test_wrong_password_returns_none(self, db_conn):
        db.create_user(
            db_conn, username="alice", password_hash=generate_password_hash("correct-horse"),
            role="admin",
        )
        db_conn.commit()
        assert auth.authenticate("alice", "wrong-password") is None

    def test_unknown_username_returns_none(self, db_conn):
        assert auth.authenticate("ghost", "whatever") is None

    def test_empty_password_returns_none(self, db_conn):
        db.create_user(
            db_conn, username="alice", password_hash=generate_password_hash("correct-horse"),
            role="admin",
        )
        db_conn.commit()
        assert auth.authenticate("alice", "") is None

    def test_inactive_user_returns_none_even_with_the_right_password(self, db_conn):
        """A deactivated account must not be able to log in, even though its
        hash still checks out -- get_user_by_username deliberately returns
        inactive rows too (that's authenticate's job to filter, not the
        lookup's)."""
        db.create_user(
            db_conn, username="alice", password_hash=generate_password_hash("correct-horse"),
            role="admin", active=False,
        )
        db_conn.commit()
        assert auth.authenticate("alice", "correct-horse") is None

    def test_returns_the_real_role_not_a_default(self, db_conn):
        db.create_user(
            db_conn, username="ptov", password_hash=generate_password_hash("pw"), role="point_of_sale",
        )
        db_conn.commit()
        user = auth.authenticate("ptov", "pw")
        assert user is not None
        assert user.role == "point_of_sale"


class TestVerifyCredentials:
    """Thin bool wrapper over authenticate() -- same DB-backed behavior (and
    same commit-before-checking requirement, see TestAuthenticate)."""

    def test_correct_password_is_true(self, db_conn):
        db.create_user(
            db_conn, username="alice", password_hash=generate_password_hash("correct-horse-battery-staple"),
            role="admin",
        )
        db_conn.commit()
        assert auth.verify_credentials("alice", "correct-horse-battery-staple") is True

    def test_wrong_password_is_false(self, db_conn):
        db.create_user(
            db_conn, username="alice", password_hash=generate_password_hash("correct-horse-battery-staple"),
            role="admin",
        )
        db_conn.commit()
        assert auth.verify_credentials("alice", "wrong-password") is False

    def test_unknown_username_is_false(self, db_conn):
        assert auth.verify_credentials("bob", "correct-horse-battery-staple") is False

    def test_empty_password_is_false(self, db_conn):
        db.create_user(
            db_conn, username="alice", password_hash=generate_password_hash("correct-horse-battery-staple"),
            role="admin",
        )
        db_conn.commit()
        assert auth.verify_credentials("alice", "") is False


# --------------------------------------------------------------------------
# Role constants
# --------------------------------------------------------------------------


class TestRoles:
    def test_roles_tuple_has_the_three_exact_literals(self):
        """⚠️ Load-bearing spelling: these are the exact literals already
        live in production's `users.role` column. 'production' has NO
        accent -- writing 'producción' here would silently lock out every
        real user with that role. 'point_of_sale' is lowercase-with-underscore,
        no accent either."""
        assert auth.ROLES == ("admin", "production", "point_of_sale")

    def test_no_role_value_has_an_accent(self):
        for role in auth.ROLES:
            assert role == role.encode("ascii", "ignore").decode("ascii"), (
                f"role {role!r} contains a non-ASCII character (accent?)"
            )


class TestFullAccessRoles:
    def test_production_is_not_full_access(self):
        """'production' was demoted to read-only once real usage showed it
        should only ever look things up -- regression guard against it
        silently regaining full access."""
        assert auth.ROLE_PRODUCCION not in auth.FULL_ACCESS_ROLES

    def test_admin_is_full_access(self):
        assert auth.ROLE_ADMIN in auth.FULL_ACCESS_ROLES

    def test_point_of_sale_is_not_full_access(self):
        assert auth.ROLE_PUNTO_VENTA not in auth.FULL_ACCESS_ROLES


class TestPosRoles:
    def test_admin_is_pos_allowed(self):
        assert auth.ROLE_ADMIN in auth.POS_ROLES

    def test_point_of_sale_is_pos_allowed(self):
        assert auth.ROLE_PUNTO_VENTA in auth.POS_ROLES

    def test_production_is_not_pos_allowed(self):
        """The existing 'production' role does NOT change -- it stays
        read-only and must NOT gain POS access."""
        assert auth.ROLE_PRODUCCION not in auth.POS_ROLES


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
# pos_allowed -- no real route uses this decorator yet (Fase 3's job), so
# it's exercised here against a tiny standalone Flask app built just for
# this test file. login_required/consulta_allowed already get real
# end-to-end route coverage in tests/web/test_app.py.
# --------------------------------------------------------------------------


@pytest.fixture
def pos_app():
    """A minimal Flask app with one dummy view behind @pos_allowed.

    Also registers a stand-in "auth_routes.login" endpoint: auth._redirect_to_login
    (used internally by pos_allowed for a stale/missing session) builds its
    redirect with `url_for("auth_routes.login", ...)`, which would otherwise
    raise BuildError in an app that never registers the real blueprint.
    """
    app = flask.Flask(__name__)
    app.secret_key = "test-only"

    auth_routes = flask.Blueprint("auth_routes", __name__)

    @auth_routes.get("/login")
    def login():
        return "login page"

    app.register_blueprint(auth_routes)

    @app.get("/pos-only")
    @auth.pos_allowed
    def pos_only():
        return "ok"

    return app


@pytest.fixture
def pos_client(pos_app):
    with pos_app.test_client() as c:
        yield c


class TestPosAllowed:
    def test_no_session_redirects_to_login(self, pos_client):
        resp = pos_client.get("/pos-only")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_session_missing_role_redirects_to_login(self, pos_client):
        with pos_client.session_transaction() as sess:
            sess["user"] = "someone"
        resp = pos_client.get("/pos-only")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_session_missing_user_id_redirects_to_login(self, pos_client):
        """Fase 2: a session predating user_id (role+user but no user_id,
        e.g. a cookie from before this deploy) must be treated as stale,
        same as a pre-roles cookie -- not crash later when something reads
        session['user_id']."""
        with pos_client.session_transaction() as sess:
            sess["user"] = "someone"
            sess["role"] = "admin"
        resp = pos_client.get("/pos-only")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_admin_role_is_allowed(self, pos_client):
        with pos_client.session_transaction() as sess:
            sess["user"] = "admin1"
            sess["role"] = "admin"
            sess["user_id"] = 1
        resp = pos_client.get("/pos-only")
        assert resp.status_code == 200

    def test_point_of_sale_role_is_allowed(self, pos_client):
        with pos_client.session_transaction() as sess:
            sess["user"] = "ptov1"
            sess["role"] = "point_of_sale"
            sess["user_id"] = 2
        resp = pos_client.get("/pos-only")
        assert resp.status_code == 200

    def test_production_role_is_forbidden(self, pos_client):
        with pos_client.session_transaction() as sess:
            sess["user"] = "production1"
            sess["role"] = "production"
            sess["user_id"] = 4
        resp = pos_client.get("/pos-only")
        assert resp.status_code == 403
