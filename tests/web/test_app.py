"""Integration tests for app.web.app via Flask's test_client().

Scope: login_required redirect behavior, the login/logout session lifecycle,
and /healthz. Tests that only need to inspect a redirect's Location header
(not the fully-rendered target page) deliberately avoid the `clean_db`
fixture so they run with zero DB dependency; the couple that assert a real
200 from a DB-backed page (e.g. /ingest, which queries distinct requested_by
/ cities) request `clean_db` explicitly.
"""

from __future__ import annotations

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
    def test_valid_credentials_authenticate_without_touching_db(self, client, test_credentials):
        """`/` itself never queries the DB (it just redirects to /ingest) --
        so this proves the session was accepted using only the Location
        header, no DB fixture required."""
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

    def test_unknown_username_does_not_authenticate(self, client):
        resp = client.post("/login", data={"username": "ghost", "password": "whatever"})
        assert resp.status_code == 401


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
