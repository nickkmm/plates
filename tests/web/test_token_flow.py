"""The Jinja pages on the JWT cookie login, end to end through app.web.app.

The contract under test:
- A genuine but expired access token sends a page through
  /api/auth/refresh/continue, which rotates the tokens and comes back.
- refresh/continue never redirects off-site, with or without a refresh cookie.
- Every route behind @permission_required refuses a request without a token
  (302 to /login, or 401 under /api) and one without its code (403).
- Jinja POST /login sets the same cookies as /api/auth/login and writes no
  user to the session; POST /logout ends at /api/auth/logout/continue, which
  revokes the refresh token.

Needs a real Postgres connection for the login tests (`test_credentials`).
"""

from __future__ import annotations

import hashlib
import re
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from werkzeug.security import generate_password_hash

from app.auth import repository
from app.auth.permissions import PERMISSION_CODES
from app.shared import db
from app.shared.timezones import utc_now
from app.web.app import app as flask_app
from tests.conftest import TEST_PASSWORD, TEST_USERNAME
from tests.web._helpers import expire_access_token, log_in, set_access_token

_TOKEN_RE = re.compile(r'name="csrf_token" value="([^"]+)"')
EVIL_NEXTS = ("https://evil.example", "//evil.example", "/\\evil.example", "http:evil.example")


def _set_cookies(resp) -> dict[str, str]:
    return {header.split("=", 1)[0]: header for header in resp.headers.getlist("Set-Cookie")}


def _attributes(header: str) -> set[str]:
    return {part.strip() for part in header.split(";")[1:]}


def _cookie_value(header: str) -> str:
    return header.split(";", 1)[0].split("=", 1)[1]


def _assert_local(location: str) -> None:
    parsed = urlparse(location)
    assert parsed.netloc in ("", "localhost"), location
    assert parsed.path.startswith("/") and not parsed.path.startswith("//"), location
    assert "\\" not in parsed.path, location


class TestExpiredTokenRefreshes:
    def test_records_goes_through_refresh_and_comes_back(self, client, test_credentials):
        log_in(client, *test_credentials)
        expire_access_token(client)

        resp = client.get("/records")
        assert resp.status_code == 302
        location = urlparse(resp.headers["Location"])
        assert location.path == "/api/auth/refresh/continue"
        assert parse_qs(location.query) == {"next": ["/records"]}

        resp = client.get(resp.headers["Location"])
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/records"
        cookies = _set_cookies(resp)
        assert _cookie_value(cookies["access_token"])
        assert _cookie_value(cookies["refresh_token"])

        assert client.get(resp.headers["Location"]).status_code == 200
        assert client.get("/records").status_code == 200

    def test_me_with_an_expired_token_is_still_a_json_401(self, client, test_credentials):
        log_in(client, *test_credentials)
        expire_access_token(client)
        resp = client.get("/api/auth/me")
        assert resp.status_code == 401
        assert resp.content_type == "application/json"
        assert resp.get_json()["error"]["code"] == "not_authenticated"

    def test_refresh_continue_is_never_cached(self, client, test_credentials):
        assert client.get("/api/auth/refresh/continue?next=/records").headers["Cache-Control"] == "no-store"
        log_in(client, *test_credentials)
        assert client.get("/api/auth/refresh/continue?next=/records").headers["Cache-Control"] == "no-store"


class TestRefreshContinueStaysOnSite:
    @pytest.mark.parametrize("next_url", EVIL_NEXTS)
    def test_with_a_refresh_cookie(self, client, test_credentials, next_url):
        log_in(client, *test_credentials)
        resp = client.get("/api/auth/refresh/continue", query_string={"next": next_url})
        assert resp.status_code == 302
        _assert_local(resp.headers["Location"])
        assert urlparse(resp.headers["Location"]).path == "/"
        assert _cookie_value(_set_cookies(resp)["access_token"])

    @pytest.mark.parametrize("next_url", EVIL_NEXTS)
    def test_without_a_refresh_cookie(self, client, next_url):
        resp = client.get("/api/auth/refresh/continue", query_string={"next": next_url})
        assert resp.status_code == 302
        location = urlparse(resp.headers["Location"])
        _assert_local(resp.headers["Location"])
        assert location.path == "/login"
        assert "evil" not in location.query
        cookies = _set_cookies(resp)
        assert _cookie_value(cookies["access_token"]) == ""
        assert _cookie_value(cookies["refresh_token"]) == ""

    def test_without_a_refresh_cookie_keeps_a_safe_next(self, client):
        resp = client.get("/api/auth/refresh/continue", query_string={"next": "/records?x=1"})
        location = urlparse(resp.headers["Location"])
        assert location.path == "/login"
        assert parse_qs(location.query) == {"next": ["/records?x=1"]}


def _guarded_rules():
    """(url, method, required codes) for every rule whose view has required_permissions."""
    for rule in flask_app.url_map.iter_rules():
        codes = getattr(flask_app.view_functions[rule.endpoint], "required_permissions", None)
        if codes is None:
            continue
        url = re.sub(r"<[^>]+>", "1", rule.rule)
        for method in sorted((rule.methods or set()) - {"HEAD", "OPTIONS"}):
            yield url, method, codes


class TestEveryGuardedRoute:
    def test_refuses_without_a_token_and_without_its_code(self, client):
        visited = 0
        for url, method, codes in _guarded_rules():
            client.delete_cookie("access_token", path="/")
            resp = client.open(url, method=method)
            if url.startswith("/api/"):
                assert resp.status_code == 401, (method, url)
            else:
                assert resp.status_code == 302, (method, url)
                assert urlparse(resp.headers["Location"]).path == "/login", (method, url)

            set_access_token(client, 1, "someone", PERMISSION_CODES - set(codes))
            assert client.open(url, method=method).status_code == 403, (method, url)
            visited += 1
        assert visited >= 50


class TestJinjaLogin:
    def test_sets_the_api_cookies_and_no_session_user(self, client, test_credentials, db_conn):
        resp = log_in(client, *test_credentials)
        assert resp.status_code == 302
        cookies = _set_cookies(resp)
        assert {"HttpOnly", "SameSite=Strict", "Max-Age=2592000", "Path=/"} <= _attributes(cookies["access_token"])
        assert {"HttpOnly", "SameSite=Strict", "Max-Age=2592000", "Path=/api/auth"} <= _attributes(
            cookies["refresh_token"]
        )
        with client.session_transaction() as sess:
            assert "user" not in sess
            assert "user_id" not in sess

        user = repository.get_user_by_username(db_conn, TEST_USERNAME)
        assert user is not None
        me = client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.get_json()["user"] == {"id": user.id, "username": TEST_USERNAME}

    def test_failures_are_identical_and_set_no_cookie(self, client, db_conn):
        repository.create_user(
            db_conn, username="inactivo", password_hash=generate_password_hash("clave-correcta"),
            roles=["admin"], active=False,
        )
        db_conn.commit()
        bodies = set()
        for username, password in (("ghost", "whatever"), (TEST_USERNAME, "wrong-password"),
                                   ("inactivo", "clave-correcta")):
            resp = log_in(client, username, password)
            assert resp.status_code == 401
            assert "access_token" not in _set_cookies(resp)
            # The CSRF token is signed with a timestamp, so it can differ between
            # two responses a second apart; everything else must be identical.
            bodies.add(_TOKEN_RE.sub("", resp.get_data(as_text=True)))
        assert len(bodies) == 1


class TestJinjaLogout:
    def test_logout_revokes_the_refresh_token_and_clears_both_cookies(self, client, test_credentials, db_conn):
        previous = flask_app.config["WTF_CSRF_ENABLED"]
        flask_app.config["WTF_CSRF_ENABLED"] = True
        try:
            login_page = client.get("/login").get_data(as_text=True)
            match = _TOKEN_RE.search(login_page)
            assert match is not None
            username, password = test_credentials
            client.post("/login", data={"username": username, "password": password, "csrf_token": match.group(1)})
            refresh = client.get_cookie("refresh_token", path="/api/auth")
            assert refresh is not None

            match = _TOKEN_RE.search(client.get("/records").get_data(as_text=True))
            assert match is not None
            resp = client.post("/logout", data={"csrf_token": match.group(1)}, follow_redirects=True)
        finally:
            flask_app.config["WTF_CSRF_ENABLED"] = previous

        assert resp.request.path == "/login"
        assert [r.request.path for r in resp.history] == ["/logout", "/api/auth/logout/continue"]
        assert client.get_cookie("access_token") is None
        assert client.get_cookie("refresh_token", path="/api/auth") is None
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT revoked_at FROM refresh_tokens WHERE token_hash = %s",
                (hashlib.sha256(refresh.value.encode()).hexdigest(),),
            )
            row = cur.fetchone()
        assert row is not None and row["revoked_at"] is not None

        resp = client.get("/records")
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/login"

    def test_logout_continue_without_a_refresh_cookie_just_clears(self, client):
        resp = client.get("/api/auth/logout/continue")
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/login"
        assert resp.headers["Cache-Control"] == "no-store"


class TestNoDatabaseOnGuardedPages:
    def test_a_guarded_view_answers_with_the_database_down(self, client, monkeypatch):
        """The access check reads only the cookie, and the /ingest/pdf view
        renders without a query, so the request succeeds with get_conn
        patched to raise."""

        def no_db(*_args, **_kwargs):
            raise AssertionError("no database read expected")

        monkeypatch.setattr(db, "get_conn", no_db)
        set_access_token(client, 1, "someone", ["orders.capture"], now=utc_now() - timedelta(seconds=10))
        assert client.get("/ingest/pdf").status_code == 200
