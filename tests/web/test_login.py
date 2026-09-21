"""Integration tests for the login/logout session lifecycle (POST /login,
POST /logout) plus the two bare routes that live directly in
app/web/app.py rather than in a blueprint: / and /healthz. Mirrors
app/web/blueprints/auth_routes.py.

Tests that only need to inspect a redirect's Location header (not the
fully-rendered target page) deliberately avoid the `clean_db` fixture so
they run with zero DB dependency; the ones that assert a real 200 from a
DB-backed page (e.g. /ingest, which queries distinct requested_by /
cities) or that need `authenticate()` to actually query `users` (Fase
2's DB-backed login) request `clean_db` explicitly."""

from __future__ import annotations

import logging
from urllib.parse import parse_qs, urlparse


class TestLoginRequired:
    def test_redirects_to_login_with_next(self, client):
        resp = client.get("/ingest")
        assert resp.status_code == 302
        location = urlparse(resp.headers["Location"])
        assert location.path == "/login"
        assert parse_qs(location.query).get("next") == ["/ingest"]

    def test_different_protected_route_also_redirects(self, client):
        resp = client.get("/records")
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/login"

    def test_no_session_root_redirects_to_login(self, client):
        resp = client.get("/")
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/login"

    def test_next_preserves_query_string(self, client):
        """request.full_path always has a trailing '?' when there's no query
        string (stripped by login_required); with a real query string it
        doesn't end in '?', so nothing should be trimmed off it."""
        resp = client.get("/records/5?x=1")
        location = urlparse(resp.headers["Location"])
        assert location.path == "/login"
        assert parse_qs(location.query).get("next") == ["/records/5?x=1"]



class TestLogin:
    def test_valid_credentials_authenticate_and_reach_ingest(self, client, test_credentials):
        """Fase 2: logging in now DOES touch the DB (it queries `users`), via
        the `test_credentials` fixture's own `clean_db` dependency -- this
        used to be DB-free (AUTH_USERS was an env var). `/` itself still
        never queries the DB on its own (it just redirects to /ingest), so
        this proves the session was accepted using only the Location
        header."""
        username, password = test_credentials
        resp = client.post("/login", data={"username": username, "password": password})
        assert resp.status_code == 302

        resp2 = client.get("/")
        assert urlparse(resp2.headers["Location"]).path == "/ingest"

    def test_valid_credentials_grant_access_to_a_protected_page(self, client, test_credentials, clean_db):
        username, password = test_credentials
        client.post("/login", data={"username": username, "password": password})
        resp = client.get("/ingest")
        assert resp.status_code == 200

    def test_valid_credentials_redirect_to_safe_next(self, client, test_credentials):
        username, password = test_credentials
        resp = client.post(
            "/login",
            data={"username": username, "password": password, "next": "/inventory"},
        )
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/inventory"

    def test_unsafe_next_is_ignored_on_login(self, client, test_credentials):
        username, password = test_credentials
        resp = client.post(
            "/login",
            data={"username": username, "password": password, "next": "http://evil.com"},
        )
        assert resp.status_code == 302
        location = urlparse(resp.headers["Location"])
        assert location.netloc == ""
        assert location.path == "/ingest"

    def test_wrong_password_does_not_authenticate(self, client, test_credentials):
        username, _ = test_credentials
        resp = client.post("/login", data={"username": username, "password": "wrong-password"})
        assert resp.status_code == 401

        resp2 = client.get("/")
        assert urlparse(resp2.headers["Location"]).path == "/login"

    def test_successful_login_is_logged(self, client, test_credentials, caplog):
        """A successful login must produce an INFO-level activity log line
        (see app.web.app's app.logger.setLevel(logging.INFO) -- without it
        this is silently dropped by Flask's default WARNING-level handler),
        the only way to see "who logged in when" outside the DB."""
        username, password = test_credentials
        with caplog.at_level(logging.INFO):
            client.post("/login", data={"username": username, "password": password})
        assert any(
            "login" in r.message and username in r.message and "admin" in r.message
            for r in caplog.records
        )

    def test_failed_login_is_not_logged_as_a_login(self, client, clean_db, caplog):
        with caplog.at_level(logging.INFO):
            client.post("/login", data={"username": "ghost", "password": "whatever"})
        assert not any(r.message.startswith("login:") for r in caplog.records)

    def test_unknown_username_does_not_authenticate(self, client, clean_db):
        """Fase 2: authenticate() queries `users` for this username even
        though it doesn't exist -- needs a real (reachable) DB, hence
        `clean_db` (this test has no credentials fixture to inherit it
        from)."""
        resp = client.post("/login", data={"username": "ghost", "password": "whatever"})
        assert resp.status_code == 401

    def test_login_stores_admin_role_in_session_for_user_with_no_auth_roles_entry(
        self, client, test_credentials
    ):
        """test_credentials' username has no AUTH_ROLES entry (see
        tests/conftest.py) -- role_for() must default it to admin, and
        login_post must persist that into the session."""
        username, password = test_credentials
        client.post("/login", data={"username": username, "password": password})
        with client.session_transaction() as sess:
            assert sess["role"] == "admin"

    def test_login_stores_configured_role_in_session(self, client, produccion_credentials):
        username, password = produccion_credentials
        client.post("/login", data={"username": username, "password": password})
        with client.session_transaction() as sess:
            assert sess["role"] == "production"

    def test_production_role_lands_on_records_not_ingest(self, client, produccion_credentials):
        """Regression guard: login (and '/') used to always redirect to
        /ingest regardless of role -- for 'production' that route 403s, so the
        person landed straight on an error page right after logging in. The
        default landing page must be role-aware."""
        username, password = produccion_credentials
        resp = client.post("/login", data={"username": username, "password": password})
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/records"

    def test_root_redirects_production_role_to_records(self, client, produccion_credentials):
        username, password = produccion_credentials
        client.post("/login", data={"username": username, "password": password})
        resp = client.get("/")
        assert urlparse(resp.headers["Location"]).path == "/records"



class TestStaleSessionPredatingRoles:
    """Regression guard for a real production incident (2026-08-22): a session
    cookie created before roles existed has 'user' but no 'role' key. Both
    decorators used to treat that as "logged in, role is None, None isn't a
    full-access role" -> a dead-end 403 with no way to recover short of
    manually clearing cookies. It must instead be treated like "not logged
    in" -- bounce to /login so a fresh login (which does set 'role') fixes
    it themselves."""

    def test_full_access_route_redirects_to_login_instead_of_403(self, client):
        with client.session_transaction() as sess:
            sess["user"] = "someone"  # no "role" key -- simulates a pre-roles cookie
        resp = client.get("/ingest")
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/login"

    def test_consulta_allowed_route_also_redirects_to_login(self, client):
        with client.session_transaction() as sess:
            sess["user"] = "someone"
        resp = client.get("/records")
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/login"

    def test_the_stale_session_is_cleared(self, client):
        with client.session_transaction() as sess:
            sess["user"] = "someone"
        client.get("/ingest")
        with client.session_transaction() as sess:
            assert "user" not in sess

    def test_a_fresh_login_recovers_full_access(self, client, test_credentials):
        """The whole point: logging out/in again (not clearing cookies by
        hand) is enough to fix it, since a real login always sets 'role'."""
        with client.session_transaction() as sess:
            sess["user"] = "someone"
        client.get("/ingest")  # bounced, session cleared

        username, password = test_credentials
        client.post("/login", data={"username": username, "password": password})
        resp = client.get("/ingest")
        assert resp.status_code == 200



class TestLogout:
    def test_logout_clears_session(self, client, test_credentials):
        username, password = test_credentials
        client.post("/login", data={"username": username, "password": password})
        resp = client.get("/")
        assert urlparse(resp.headers["Location"]).path == "/ingest"  # logged in

        client.post("/logout")
        resp2 = client.get("/")
        assert urlparse(resp2.headers["Location"]).path == "/login"  # logged out



class TestHealthz:
    def test_returns_200_without_session(self, client):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.get_json() == {"status": "ok"}



class TestLoginAlreadyAuthenticated:
    def test_get_login_while_logged_in_redirects_to_ingest(self, authenticated_client):
        resp = authenticated_client.get("/login")
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/ingest"



class TestLoginPageRendersWhenNotAuthenticated:
    def test_plain_get_renders_the_login_form(self, client):
        resp = client.get("/login")
        assert resp.status_code == 200
        assert b"<form" in resp.data

    def test_unsafe_next_query_param_is_dropped_from_the_rendered_form(self, client):
        """is_safe_next() is unit-tested directly in test_auth.py; this wires
        the same guard through the actual GET /login route -- an open-redirect
        payload in ?next= must not end up echoed into the page."""
        resp = client.get("/login?next=http://evil.com")
        assert resp.status_code == 200
        assert b"evil.com" not in resp.data

