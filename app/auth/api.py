"""/api/auth -- the JSON auth API: login, refresh, logout and me.

Login sets two httpOnly, SameSite=Strict cookies (Secure when COOKIE_SECURE
is on): `access_token`, the 5-minute JWT of app/shared/tokens.py on path `/`,
and `refresh_token`, the opaque 30-day token of app/auth/refresh_tokens.py
on path `/api/auth` (logout must receive it to revoke it). Refresh rotates the
refresh token and reloads the user's current roles and permissions; me reads
only the access token, never the database. Login, refresh and logout are
public; me answers 401 without a valid access token.

The blueprint is exempt from Flask-WTF CSRF (app/web/app.py): every POST must
send `X-Requested-With: XMLHttpRequest` instead (app/shared/api.py). This API
never touches the Flask session. See docs/WEB.md, "JSON API (`/api`)".
"""

from __future__ import annotations

from flask import Blueprint, Response, current_app, jsonify, request
from pydantic import ValidationError

from app.auth import decorators as auth
from app.auth import permissions, refresh_tokens, repository
from app.auth.repository import UserRecord
from app.auth.schemas import LoginRequest, LoginResponse, MeResponse, MeUser, UserOut
from app.shared import db
from app.shared.api import error_response, http_error_response
from app.shared.timezones import utc_now
from app.shared.tokens import (
    ACCESS_TOKEN_LIFETIME,
    REFRESH_TOKEN_LIFETIME,
    TokenError,
    decode_access_token,
    issue_access_token,
)

bp = Blueprint("auth_api", __name__, url_prefix="/api/auth")

ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"
_ACCESS_PATH = "/"
_REFRESH_PATH = "/api/auth"
MSG_INVALID_CREDENTIALS = "Usuario o contraseña incorrectos."
MSG_INVALID_BODY = "Escribe tu usuario y tu contraseña."


@bp.after_request
def _no_store(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    return response


def _set_cookie(response: Response, name: str, value: str, *, max_age: int, path: str) -> None:
    response.set_cookie(
        name, value, max_age=max_age, path=path, httponly=True, samesite="Strict",
        secure=current_app.config["COOKIE_SECURE"],
    )


def _delete_cookie(response: Response, name: str, path: str) -> None:
    response.delete_cookie(
        name, path=path, httponly=True, samesite="Strict", secure=current_app.config["COOKIE_SECURE"],
    )


def _clear_cookies(response: Response) -> Response:
    _delete_cookie(response, ACCESS_COOKIE, _ACCESS_PATH)
    _delete_cookie(response, REFRESH_COOKIE, _REFRESH_PATH)
    return response


def _signed_in(user: UserRecord, perms: list[str], raw_refresh: str) -> Response:
    """200 with LoginResponse and both cookies freshly set."""
    body = LoginResponse(
        user=UserOut(id=user.id, username=user.username, roles=user.roles), permissions=perms
    )
    response = jsonify(body.model_dump())
    access = issue_access_token(user.id, user.username, perms, utc_now())
    _set_cookie(
        response, ACCESS_COOKIE, access, max_age=int(ACCESS_TOKEN_LIFETIME.total_seconds()),
        path=_ACCESS_PATH,
    )
    _set_cookie(
        response, REFRESH_COOKIE, raw_refresh, max_age=int(REFRESH_TOKEN_LIFETIME.total_seconds()),
        path=_REFRESH_PATH,
    )
    return response


@bp.post("/login")
def login() -> Response:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error_response(400, "invalid_body", MSG_INVALID_BODY)
    try:
        body = LoginRequest.model_validate(data)
    except ValidationError:
        return error_response(400, "invalid_body", MSG_INVALID_BODY)

    user = auth.authenticate(body.username.strip(), body.password)
    if user is None:
        return error_response(401, "invalid_credentials", MSG_INVALID_CREDENTIALS)
    with db.get_conn() as conn:
        perms = sorted(
            permissions.effective_permissions(user.roles, repository.get_user_permissions(conn, user.id))
        )
        raw_refresh = refresh_tokens.issue(conn, user.id)
        conn.commit()
    current_app.logger.info("login: user=%s roles=%s", user.username, ",".join(user.roles))
    return _signed_in(user, perms, raw_refresh)


@bp.post("/refresh")
def refresh() -> Response:
    raw = request.cookies.get(REFRESH_COOKIE)
    if not raw:
        return _clear_cookies(http_error_response(401))
    with db.get_conn() as conn:
        rotated = refresh_tokens.rotate(conn, raw)
        if rotated is None:
            # Commit anyway: on reuse, rotate revoked the whole family.
            conn.commit()
            return _clear_cookies(http_error_response(401))
        user_id, new_raw = rotated
        user = repository.get_user_by_id(conn, user_id)
        if user is None or not user.active:
            refresh_tokens.revoke_user(conn, user_id)
            conn.commit()
            return _clear_cookies(http_error_response(401))
        perms = sorted(
            permissions.effective_permissions(user.roles, repository.get_user_permissions(conn, user.id))
        )
        conn.commit()
    return _signed_in(user, perms, new_raw)


@bp.post("/logout")
def logout() -> Response:
    raw = request.cookies.get(REFRESH_COOKIE)
    if raw:
        with db.get_conn() as conn:
            refresh_tokens.revoke(conn, raw)
            conn.commit()
    return _clear_cookies(Response(status=204))


@bp.get("/me")
def me() -> Response:
    token = request.cookies.get(ACCESS_COOKIE)
    if not token:
        return http_error_response(401)
    try:
        claims = decode_access_token(token, utc_now())
    except TokenError:
        return http_error_response(401)
    body = MeResponse(user=MeUser(id=claims.user_id, username=claims.username), permissions=list(claims.perms))
    return jsonify(body.model_dump())
