"""Who the request's user is and what they may open, from the access token.

Every protected route is decorated with @permission_required("<code>") (or
@authenticated for `/` alone). Both read only the `access_token` cookie: a
JWT of app/shared/tokens.py whose claims carry the user id, the username and
the effective permissions computed at login or refresh. Nothing here reads the
database, so a deactivation or a role change takes effect when the user's
access token expires (5 minutes) and the refresh reloads them.

Without a valid token a request is refused: an `/api` request with the JSON
401, a genuine but expired token with a redirect through
`auth_api.refresh_continue` (which rotates the tokens and comes back), and
anything else with a redirect to `auth_routes.login`. Endpoints are named
through url_for, never imported, so this module depends on no domain package.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any
from urllib.parse import urlparse

from flask import abort, g, redirect, request, session, url_for
from werkzeug.wrappers import Response

from app.shared.api import http_error_response, is_api_request
from app.shared.timezones import utc_now
from app.shared.tokens import ACCESS_COOKIE, AccessClaims, ExpiredTokenError, TokenError, decode_access_token

View = Callable[..., Any]


def _load() -> None:
    """Decode the access cookie once per request, caching the claims (or
    None) and whether the token was genuine but expired on flask.g."""
    if "_access_claims_loaded" in g:
        return
    claims: AccessClaims | None = None
    expired = False
    token = request.cookies.get(ACCESS_COOKIE)
    if token:
        try:
            claims = decode_access_token(token, utc_now())
        except ExpiredTokenError:
            expired = True
        except TokenError:
            pass
    g._access_claims_loaded = True
    g.access_claims = claims
    g.access_token_expired = expired


def current_user() -> AccessClaims | None:
    """The verified claims of the request's access token, or None when the
    cookie is missing, invalid or expired."""
    _load()
    claims: AccessClaims | None = g.access_claims
    return claims


def signed_in_user() -> AccessClaims:
    """current_user() in a view behind @authenticated or
    @permission_required, where it is never None."""
    user = current_user()
    if user is None:
        raise RuntimeError("signed_in_user() called without a valid access token")
    return user


def has_permission(code: str) -> bool:
    """True only when the access token's permissions hold `code`. False
    (never an error) without a valid token."""
    user = current_user()
    return user is not None and code in user.perms


def is_safe_next(target: str | None) -> bool:
    """True if `target` is a safe local relative path (guards open redirects)."""
    if not target or not target.startswith("/"):
        return False
    # Reject protocol-relative ("//host") and backslash tricks ("/\\host").
    if target.startswith("//") or target.startswith("/\\"):
        return False
    parsed = urlparse(target)
    return not parsed.scheme and not parsed.netloc


def _next_target() -> str:
    """Where to come back to: the requested path for GET and HEAD. Any other
    method comes back to `/`, because the redirect turns it into a GET,
    which a POST-only URL would answer with a 405."""
    if request.method not in ("GET", "HEAD"):
        return "/"
    next_url = request.full_path
    if next_url.endswith("?"):
        next_url = next_url[:-1]
    return next_url


def _refuse() -> Response:
    """The answer to a request without a valid access token."""
    if is_api_request():
        return http_error_response(401)
    _load()
    if g.access_token_expired:
        return redirect(url_for("auth_api.refresh_continue", next=_next_target()))
    # A session cookie from before the token login may still carry `user`;
    # it means nothing now, so drop it with the redirect.
    session.clear()
    return redirect(url_for("auth_routes.login", next=_next_target()))


def authenticated(view: View) -> View:
    """Any valid access token, whatever its permissions. Only for `/`,
    which itself decides where (or whether) the user may go."""

    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        if current_user() is None:
            return _refuse()
        return view(*args, **kwargs)

    return wrapped


def permission_required(*codes: str) -> Callable[[View], View]:
    """Like @authenticated, then 403 unless the token's permissions hold at
    least one of `codes`. Every protected route uses this with one code; no
    route that writes may accept 'records.view'. The wrapper exposes the codes
    as `required_permissions`."""

    def decorator(view: View) -> View:
        @wraps(view)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            if current_user() is None:
                return _refuse()
            if not any(has_permission(code) for code in codes):
                abort(403)
            return view(*args, **kwargs)

        wrapped.required_permissions = codes  # type: ignore[attr-defined]
        return wrapped

    return decorator
