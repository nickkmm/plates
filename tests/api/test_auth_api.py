"""Integration tests for the JSON auth API (app/auth/api.py) and the shared
`/api` plumbing (app/shared/api.py).

The contract under test:
- Login sets the httpOnly, SameSite=Strict `access_token` (path /, a JWT that
  expires after 5 min in a 30-day cookie) and `refresh_token` (path
  /api/auth, 30 days) cookies, Secure when
  COOKIE_SECURE is on; its three failures are byte-identical 401s with no cookie.
- /me reads only the access token; refresh rotates the refresh token, detects
  reuse and reloads the user's current permissions; logout revokes it.
- Role changes, deactivations and password resets in /usuarios revoke the
  user's refresh tokens.
- Every error under /api/ is JSON; every POST needs X-Requested-With; the
  API is exempt from Flask-WTF CSRF (the client fixture keeps it on).

Needs a real Postgres connection (`clean_db`/`db_conn`).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import uuid
from datetime import timedelta

import jwt

from app.auth import repository
from app.auth.permissions import PERMISSION_CODES
from app.shared.timezones import utc_now
from app.shared.tokens import issue_access_token
from app.web.app import app as flask_app
from tests.api._helpers import api_login, api_post
from tests.conftest import PRODUCCION_PASSWORD, PRODUCCION_USERNAME, TEST_PASSWORD, TEST_USERNAME

INVALID_CREDENTIALS = {"error": {"code": "invalid_credentials", "message": "Usuario o contraseña incorrectos."}}
_TOKEN_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def _set_cookies(resp) -> dict[str, str]:
    """Set-Cookie headers of a response, by cookie name."""
    return {header.split("=", 1)[0]: header for header in resp.headers.getlist("Set-Cookie")}


def _cookie_value(header: str) -> str:
    return header.split(";", 1)[0].split("=", 1)[1]


def _attributes(header: str) -> set[str]:
    return {part.strip() for part in header.split(";")[1:]}


def _assert_cleared(resp) -> None:
    cookies = _set_cookies(resp)
    assert set(cookies) == {"access_token", "refresh_token"}
    for header in cookies.values():
        assert _cookie_value(header) == ""
        assert "Max-Age=0" in _attributes(header)
    assert "Path=/" in _attributes(cookies["access_token"])
    assert "Path=/api/auth" in _attributes(cookies["refresh_token"])


def _assert_access_lifetime(header: str) -> None:
    """The JWT inside the 30-day cookie still expires 5 minutes after it was issued."""
    claims = jwt.decode(_cookie_value(header), options={"verify_signature": False})
    assert claims["exp"] - claims["iat"] == 300


def _assert_error(resp, status: int, code: str) -> None:
    assert resp.status_code == status
    assert resp.content_type == "application/json"
    assert resp.get_json()["error"]["code"] == code
    assert "<" not in resp.get_data(as_text=True)


def _user(conn, username):
    user = repository.get_user_by_username(conn, username)
    assert user is not None
    return user


def _refresh(client):
    return api_post(client, "/api/auth/refresh")


def _csrf_token(client, path: str) -> str:
    match = _TOKEN_RE.search(client.get(path).get_data(as_text=True))
    assert match is not None, f"no csrf_token on {path}"
    return match.group(1)


def _admin_web_client(user_id: int):
    """A browser of its own, logged in to the Jinja app as the admin through
    /login, and the CSRF token of its session read from the edit page of
    `user_id` (CSRF stays on; login clears the session, so the token of the
    login page no longer counts)."""
    admin = flask_app.test_client()
    resp = admin.post(
        "/login",
        data={"username": TEST_USERNAME, "password": TEST_PASSWORD, "csrf_token": _csrf_token(admin, "/login")},
    )
    assert resp.status_code == 302
    return admin, _csrf_token(admin, f"/usuarios/{user_id}")


class TestLogin:
    def test_login_sets_both_cookies_and_me_reads_them(self, client, db_conn, caplog):
        caplog.set_level(logging.INFO)
        admin = _user(db_conn, TEST_USERNAME)
        resp = api_login(client, TEST_USERNAME, TEST_PASSWORD)
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["user"] == {"id": admin.id, "username": TEST_USERNAME, "roles": ["admin"]}
        assert body["permissions"] == sorted(PERMISSION_CODES)

        cookies = _set_cookies(resp)
        assert {"HttpOnly", "SameSite=Strict", "Max-Age=2592000", "Path=/"} <= _attributes(cookies["access_token"])
        _assert_access_lifetime(cookies["access_token"])
        assert {"HttpOnly", "SameSite=Strict", "Max-Age=2592000", "Path=/api/auth"} <= _attributes(
            cookies["refresh_token"]
        )
        assert "login: user=testuser roles=admin" in "\n".join(r.getMessage() for r in caplog.records)

        me = client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.get_json() == {
            "user": {"id": admin.id, "username": TEST_USERNAME},
            "permissions": sorted(PERMISSION_CODES),
        }

    def test_login_strips_the_username(self, client, clean_db):
        assert api_login(client, f"  {TEST_USERNAME}  ", TEST_PASSWORD).status_code == 200

    def test_login_does_not_touch_the_flask_session(self, client, clean_db):
        """And its access token opens the Jinja pages too: one login for both."""
        resp = api_login(client, TEST_USERNAME, TEST_PASSWORD)
        assert "session" not in _set_cookies(resp)
        assert client.get("/records").status_code == 200

    def test_the_three_login_failures_are_identical(self, client, db_conn):
        repository.create_user(
            db_conn, username="inactivo", password_hash=repository.get_user_by_username(
                db_conn, TEST_USERNAME
            ).password_hash, roles=["admin"], active=False,
        )
        db_conn.commit()
        responses = [
            api_login(client, TEST_USERNAME, "wrong-password"),
            api_login(client, "nobody", TEST_PASSWORD),
            api_login(client, "inactivo", TEST_PASSWORD),
        ]
        for resp in responses:
            assert resp.status_code == 401
            assert json.loads(resp.data) == INVALID_CREDENTIALS
            assert resp.headers.getlist("Set-Cookie") == []
        assert len({resp.data for resp in responses}) == 1

    def test_bad_body_is_400_invalid_body(self, client, clean_db):
        for kwargs in (
            {"data": "username=testuser", "content_type": "application/x-www-form-urlencoded"},
            {"json": ["testuser", TEST_PASSWORD]},
            {"json": {"username": TEST_USERNAME}},
            {"json": {"username": TEST_USERNAME, "password": 123}},
        ):
            resp = api_post(client, "/api/auth/login", **kwargs)
            _assert_error(resp, 400, "invalid_body")
            assert resp.headers.getlist("Set-Cookie") == []

    def test_get_login_is_405_json(self, client):
        resp = client.get("/api/auth/login")
        _assert_error(resp, 405, "method_not_allowed")
        assert "POST" in resp.headers["Allow"]

    def test_csrf_stays_on_but_the_api_is_exempt(self, client, clean_db):
        assert flask_app.config["WTF_CSRF_ENABLED"]
        assert api_login(client, TEST_USERNAME, TEST_PASSWORD).status_code == 200
        resp = client.post("/login", data={"username": TEST_USERNAME, "password": TEST_PASSWORD})
        assert resp.status_code == 400
        assert "csrf" not in resp.content_type and "text/html" in resp.content_type


class TestSecureFlag:
    def test_secure_on_both_cookies_when_cookie_secure_is_on(self, client, clean_db):
        flask_app.config["COOKIE_SECURE"] = True
        cookies = _set_cookies(api_login(client, TEST_USERNAME, TEST_PASSWORD))
        assert all("Secure" in _attributes(header) for header in cookies.values())
        assert len(cookies) == 2

    def test_no_secure_when_cookie_secure_is_off(self, client, clean_db):
        cookies = _set_cookies(api_login(client, TEST_USERNAME, TEST_PASSWORD))
        assert not any("Secure" in _attributes(header) for header in cookies.values())
        assert len(cookies) == 2


class TestMe:
    def test_without_a_cookie_is_401(self, client):
        _assert_error(client.get("/api/auth/me"), 401, "not_authenticated")

    def test_with_an_expired_token_is_401(self, client):
        token = issue_access_token(1, TEST_USERNAME, ["users.manage"], utc_now() - timedelta(minutes=10))
        client.set_cookie("access_token", token)
        _assert_error(client.get("/api/auth/me"), 401, "not_authenticated")

    def test_with_a_forged_token_is_401(self, client):
        client.set_cookie("access_token", "not.a.jwt")
        _assert_error(client.get("/api/auth/me"), 401, "not_authenticated")

    def test_permissions_change_only_after_refresh(self, client, db_conn):
        api_login(client, PRODUCCION_USERNAME, PRODUCCION_PASSWORD)
        before = client.get("/api/auth/me").get_json()["permissions"]
        assert "orders.capture" not in before
        repository.update_role(
            db_conn, "production", label="Producción", permissions=[*before, "orders.capture"]
        )
        db_conn.commit()
        assert client.get("/api/auth/me").get_json()["permissions"] == before

        resp = _refresh(client)
        assert resp.status_code == 200
        assert resp.get_json()["permissions"] == sorted([*before, "orders.capture"])
        assert client.get("/api/auth/me").get_json()["permissions"] == sorted([*before, "orders.capture"])


class TestRefresh:
    def test_rotates_and_detects_reuse(self, client, clean_db):
        login = api_login(client, TEST_USERNAME, TEST_PASSWORD)
        old_refresh = _cookie_value(_set_cookies(login)["refresh_token"])

        resp = _refresh(client)
        assert resp.status_code == 200
        assert resp.get_json()["user"]["username"] == TEST_USERNAME
        cookies = _set_cookies(resp)
        new_refresh = _cookie_value(cookies["refresh_token"])
        assert new_refresh and new_refresh != old_refresh
        assert _cookie_value(cookies["access_token"])
        assert {"HttpOnly", "SameSite=Strict", "Max-Age=2592000", "Path=/api/auth"} <= _attributes(
            cookies["refresh_token"]
        )
        assert {"HttpOnly", "SameSite=Strict", "Max-Age=2592000", "Path=/"} <= _attributes(cookies["access_token"])
        _assert_access_lifetime(cookies["access_token"])

        client.set_cookie("refresh_token", old_refresh, path="/api/auth")
        reused = _refresh(client)
        _assert_error(reused, 401, "not_authenticated")
        _assert_cleared(reused)

        client.set_cookie("refresh_token", new_refresh, path="/api/auth")
        _assert_error(_refresh(client), 401, "not_authenticated")

    def test_without_a_cookie_is_401(self, client):
        resp = _refresh(client)
        _assert_error(resp, 401, "not_authenticated")
        _assert_cleared(resp)

    def test_with_an_expired_token_is_401(self, client, db_conn):
        raw = secrets.token_urlsafe(32)
        repository.insert_refresh_token(
            db_conn, user_id=_user(db_conn, TEST_USERNAME).id,
            token_hash=hashlib.sha256(raw.encode()).hexdigest(), family_id=uuid.uuid4(),
            expires_at=utc_now() - timedelta(seconds=1),
        )
        db_conn.commit()
        client.set_cookie("refresh_token", raw, path="/api/auth")
        resp = _refresh(client)
        _assert_error(resp, 401, "not_authenticated")
        _assert_cleared(resp)

    def test_inactive_user_is_401(self, client, db_conn):
        api_login(client, PRODUCCION_USERNAME, PRODUCCION_PASSWORD)
        repository.update_user(db_conn, _user(db_conn, PRODUCCION_USERNAME).id, active=False)
        db_conn.commit()
        resp = _refresh(client)
        _assert_error(resp, 401, "not_authenticated")
        _assert_cleared(resp)

    def test_needs_the_header(self, client, clean_db):
        api_login(client, TEST_USERNAME, TEST_PASSWORD)
        _assert_error(client.post("/api/auth/refresh"), 403, "missing_requested_with")


class TestLogout:
    def test_needs_the_header_then_revokes(self, client, clean_db):
        login = api_login(client, TEST_USERNAME, TEST_PASSWORD)
        refresh = _cookie_value(_set_cookies(login)["refresh_token"])

        resp = client.post("/api/auth/logout")
        _assert_error(resp, 403, "missing_requested_with")
        assert resp.headers.getlist("Set-Cookie") == []

        resp = api_post(client, "/api/auth/logout")
        assert resp.status_code == 204
        assert resp.data == b""
        _assert_cleared(resp)

        client.set_cookie("refresh_token", refresh, path="/api/auth")
        _assert_error(_refresh(client), 401, "not_authenticated")

    def test_without_a_cookie_is_204(self, client, clean_db):
        resp = api_post(client, "/api/auth/logout")
        assert resp.status_code == 204
        _assert_cleared(resp)

    def test_with_an_unknown_token_is_204(self, client, clean_db):
        client.set_cookie("refresh_token", "not-a-token", path="/api/auth")
        assert api_post(client, "/api/auth/logout").status_code == 204


class TestRevocationFromUsuarios:
    def _logged_in_production(self, client, db_conn):
        assert api_login(client, PRODUCCION_USERNAME, PRODUCCION_PASSWORD).status_code == 200
        return _user(db_conn, PRODUCCION_USERNAME)

    def test_deactivation_revokes(self, client, db_conn):
        target = self._logged_in_production(client, db_conn)
        admin, token = _admin_web_client(target.id)
        resp = admin.post(f"/usuarios/{target.id}", data={"roles": ["production"], "csrf_token": token})
        assert resp.status_code == 302
        _assert_error(_refresh(client), 401, "not_authenticated")

    def test_role_change_revokes(self, client, db_conn):
        target = self._logged_in_production(client, db_conn)
        admin, token = _admin_web_client(target.id)
        resp = admin.post(
            f"/usuarios/{target.id}", data={"roles": ["point_of_sale"], "active": "1", "csrf_token": token}
        )
        assert resp.status_code == 302
        _assert_error(_refresh(client), 401, "not_authenticated")

    def test_password_reset_revokes(self, client, db_conn):
        target = self._logged_in_production(client, db_conn)
        admin, token = _admin_web_client(target.id)
        resp = admin.post(
            f"/usuarios/{target.id}/password",
            data={"password": "nuevaclave123", "password_confirm": "nuevaclave123", "csrf_token": token},
        )
        assert resp.status_code == 302
        _assert_error(_refresh(client), 401, "not_authenticated")

    def test_saving_unchanged_does_not_revoke(self, client, db_conn):
        target = self._logged_in_production(client, db_conn)
        admin, token = _admin_web_client(target.id)
        resp = admin.post(
            f"/usuarios/{target.id}", data={"roles": ["production"], "active": "1", "csrf_token": token}
        )
        assert resp.status_code == 302
        assert _refresh(client).status_code == 200


class TestApiErrors:
    def test_unknown_api_url_is_json_404(self, client):
        resp = client.get("/api/nope")
        _assert_error(resp, 404, "not_found")
        assert resp.headers["Cache-Control"] == "no-store"

    def test_unknown_url_elsewhere_is_still_the_html_page(self, client):
        resp = client.get("/no-existe")
        assert resp.status_code == 404
        assert "Página no encontrada" in resp.get_data(as_text=True)

    def test_unknown_api_post_needs_the_header_first(self, client):
        _assert_error(client.post("/api/nope"), 403, "missing_requested_with")
        _assert_error(api_post(client, "/api/nope"), 404, "not_found")
