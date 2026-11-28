"""/api/auth -- the JSON auth API: login, refresh, logout and me.

Login sets two httpOnly, SameSite=Strict cookies (Secure when COOKIE_SECURE
is on, app/auth/login.py): `access_token`, the 5-minute JWT of
app/shared/tokens.py on path `/`, and `refresh_token`, the opaque 30-day
token of app/auth/refresh_tokens.py on path `/api/auth` (logout must receive
it to revoke it). Both cookies live 30 days; the JWT inside `access_token`
still expires after 5 minutes. Refresh rotates the refresh token and reloads
the user's current roles and permissions; me reads only the access token,
never the database. Login, refresh and logout are public; me answers 401
without a valid access token.

Two GET endpoints, refresh/continue and logout/continue, exist only for the
Jinja pages, which can't see the refresh cookie (see their docstrings).

The blueprint is exempt from Flask-WTF CSRF (app/web/app.py): every POST must
send `X-Requested-With: XMLHttpRequest` instead (app/shared/api.py). This API
never touches the Flask session. See docs/WEB.md, "JSON API (`/api`)".
"""

from __future__ import annotations

from flask import Blueprint, Response, jsonify, redirect, request, url_for
from pydantic import ValidationError
from werkzeug.wrappers import Response as BaseResponse

from app.auth import login as auth_login
from app.auth.schemas import LoginRequest, LoginResponse, MeResponse, MeUser, UserOut
from app.shared.api import error_response, http_error_response
from app.shared.auth import current_user, is_safe_next
from app.shared.tokens import REFRESH_COOKIE

bp = Blueprint("auth_api", __name__, url_prefix="/api/auth")

MSG_INVALID_CREDENTIALS = "Usuario o contraseña incorrectos."
MSG_INVALID_BODY = "Escribe tu usuario y tu contraseña."


@bp.after_request
def _no_store(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    return response


def _signed_in(signed_in: auth_login.SignedIn) -> Response:
    """200 with LoginResponse and both cookies freshly set."""
    user = signed_in.user
    body = LoginResponse(
        user=UserOut(id=user.id, username=user.username, roles=user.roles), permissions=signed_in.permissions
    )
    response = jsonify(body.model_dump())
    auth_login.set_token_cookies(response, signed_in)
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

    signed_in = auth_login.sign_in(body.username.strip(), body.password)
    if signed_in is None:
        return error_response(401, "invalid_credentials", MSG_INVALID_CREDENTIALS)
    return _signed_in(signed_in)


@bp.post("/refresh")
def refresh() -> Response:
    raw = request.cookies.get(REFRESH_COOKIE)
    signed_in = auth_login.refresh(raw) if raw else None
    if signed_in is None:
        return auth_login.clear_token_cookies(http_error_response(401))
    return _signed_in(signed_in)


@bp.post("/logout")
def logout() -> Response:
    raw = request.cookies.get(REFRESH_COOKIE)
    if raw:
        auth_login.sign_out(raw)
    return auth_login.clear_token_cookies(Response(status=204))


@bp.get("/me")
def me() -> Response:
    claims = current_user()
    if claims is None:
        return http_error_response(401)
    body = MeResponse(user=MeUser(id=claims.user_id, username=claims.username), permissions=list(claims.perms))
    return jsonify(body.model_dump())


@bp.get("/refresh/continue")
def refresh_continue() -> BaseResponse:
    """A Jinja page whose access token expired lands here (app/shared/auth.py):
    the refresh cookie only reaches /api/auth. Rotates the tokens and sends the
    user back to the safe `next` (or `/`), or, when there is no refresh cookie
    or it can't be rotated, clears both cookies and sends them to /login.
    Exists only while Jinja pages exist; phase 6 of docs/MIGRATION_PLAN.md
    removes it."""
    next_url = request.args.get("next", "")
    safe_next = next_url if is_safe_next(next_url) else ""
    raw = request.cookies.get(REFRESH_COOKIE)
    signed_in = auth_login.refresh(raw) if raw else None
    if signed_in is None:
        login_url = url_for("auth_routes.login", next=safe_next) if safe_next else url_for("auth_routes.login")
        return auth_login.clear_token_cookies(redirect(login_url))
    return auth_login.set_token_cookies(redirect(safe_next or url_for("index")), signed_in)


@bp.get("/logout/continue")
def logout_continue() -> BaseResponse:
    """Where Jinja POST /logout ends: that request never carries the refresh
    cookie (it only reaches /api/auth), so the token is revoked here. Clears
    both cookies and sends the user to /login. Exists only while Jinja pages
    exist; phase 6 of docs/MIGRATION_PLAN.md removes it."""
    raw = request.cookies.get(REFRESH_COOKIE)
    if raw:
        auth_login.sign_out(raw)
    return auth_login.clear_token_cookies(redirect(url_for("auth_routes.login")))
