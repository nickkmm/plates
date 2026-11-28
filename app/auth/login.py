"""Sign-in, refresh and sign-out, shared by the Jinja `/login` and `/logout`
(app/auth/routes.py) and the JSON auth API (app/auth/api.py).

`authenticate` checks a username and password against the `users` table.
`sign_in` and `refresh` return the user, their effective permissions (the
union over their roles, app/auth/permissions.py) and a raw refresh token;
`set_token_cookies` turns that into the two httpOnly, SameSite=Strict
cookies (Secure when COOKIE_SECURE is on): `access_token`, the 5-minute JWT
of app/shared/tokens.py on path `/`, and `refresh_token`, the opaque token
of app/auth/refresh_tokens.py on path `/api/auth`. Both cookies live
REFRESH_TOKEN_LIFETIME, so a page still receives an expired access token and
can send the user through a refresh (app/shared/auth.py); the JWT inside
still expires after ACCESS_TOKEN_LIFETIME.

load_users()/load_roles() parse the old AUTH_USERS/AUTH_ROLES env vars. They
are used ONLY by app.schema_migrate.backfill_users, the one-time,
already-applied-in-production migration that moved those entries into the
`users` table with the same werkzeug hashes. Nothing in the live login flow
calls them.

Generate a password hash for a new user with:

    python -m app.auth.hash_password 'the-password'
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TypeVar

from flask import current_app
from werkzeug.security import check_password_hash
from werkzeug.wrappers import Response

from app.auth import permissions, refresh_tokens, repository
from app.auth.repository import UserRecord
from app.shared import db
from app.shared.timezones import utc_now
from app.shared.tokens import ACCESS_COOKIE, REFRESH_COOKIE, REFRESH_TOKEN_LIFETIME, issue_access_token

ROLE_ADMIN = "admin"

# Any response type: the cookie helpers hand back the response they were given.
R = TypeVar("R", bound=Response)

_ACCESS_PATH = "/"
_REFRESH_PATH = "/api/auth"


@dataclass(frozen=True)
class SignedIn:
    """A successful sign-in or refresh: the user, their sorted effective
    permissions and the raw refresh token to hand to the browser."""

    user: UserRecord
    permissions: list[str]
    raw_refresh: str


def load_users() -> dict[str, str]:
    """Parse AUTH_USERS into {username: password_hash}.

    ONLY still used by app.schema_migrate.backfill_users -- the one-time,
    already-applied-in-production migration that moved these env vars into
    the `users` table (see the module docstring). The live login flow below
    (authenticate/verify_credentials) queries `users` instead.

    The hash itself contains ':' separators, so each pair is split on the
    FIRST ':' only. Blank or malformed entries are ignored.
    """
    raw = os.environ.get("AUTH_USERS", "")
    users: dict[str, str] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        username, pw_hash = entry.split(":", 1)
        username = username.strip()
        pw_hash = pw_hash.strip()
        if username and pw_hash:
            users[username] = pw_hash
    return users


def load_roles() -> dict[str, str]:
    """Parse AUTH_ROLES into {username: role}.

    ONLY still used by app.schema_migrate.backfill_users (see load_users'
    docstring and the module docstring above) -- not by the live login flow.

    Separate env var from AUTH_USERS on purpose: AUTH_USERS values are werkzeug
    hashes that themselves contain ':' (e.g. 'pbkdf2:sha256:600000$salt$hash'),
    so a third ':role' suffix on that line would be ambiguous to parse. Any
    username with no entry here defaults to ROLE_ADMIN -- this is what made the
    original single-role production setup (zero AUTH_ROLES configured) keep
    working unchanged, and backfill_users still relies on that same default.
    """
    raw = os.environ.get("AUTH_ROLES", "")
    roles: dict[str, str] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        username, role = entry.split(":", 1)
        username, role = username.strip(), role.strip()
        if username and role:
            roles[username] = role
    return roles


def authenticate(username: str, password: str) -> UserRecord | None:
    """Look up `username` in the `users` table and verify `password` against
    its stored hash -- the live login flow (Fase 2), replacing the old
    AUTH_USERS/AUTH_ROLES env-var check.

    Returns the matching UserRecord on success, None otherwise -- unknown
    username, wrong password, and an account marked inactive all return None
    indistinguishably (never leak which one it was to an unauthenticated
    caller). Opens its own connection (like every other app/web/* call site
    does with `db.get_conn()`) rather than taking one as a parameter --
    this is a web-layer module, not part of app/auth/repository/'s
    conn-passing contract.
    """
    with db.get_conn() as conn:
        user = repository.get_user_by_username(conn, username)
    if user is None or not user.active:
        return None
    if not check_password_hash(user.password_hash, password):
        return None
    return user


def verify_credentials(username: str, password: str) -> bool:
    """True if username exists, is active, and password matches its stored
    hash. Thin bool wrapper over authenticate() for callers that only need a
    yes/no answer."""
    return authenticate(username, password) is not None


def sign_in(username: str, password: str) -> SignedIn | None:
    """Authenticate, compute the effective permissions, issue a refresh token
    and commit. None (and nothing logged as a login) when authenticate fails."""
    user = authenticate(username, password)
    if user is None:
        return None
    with db.get_conn() as conn:
        perms = sorted(
            permissions.effective_permissions(user.roles, repository.get_user_permissions(conn, user.id))
        )
        raw_refresh = refresh_tokens.issue(conn, user.id)
        conn.commit()
    current_app.logger.info("login: user=%s roles=%s", user.username, ",".join(user.roles))
    return SignedIn(user, perms, raw_refresh)


def refresh(raw: str) -> SignedIn | None:
    """Rotate a raw refresh token and reload its user's current roles and
    permissions. None when the token can't be rotated (unknown, expired,
    revoked or reused), or when its user is missing or inactive, in which
    case every token of the user is revoked."""
    with db.get_conn() as conn:
        rotated = refresh_tokens.rotate(conn, raw)
        if rotated is None:
            # Commit anyway: on reuse, rotate revoked the whole family.
            conn.commit()
            return None
        user_id, new_raw = rotated
        user = repository.get_user_by_id(conn, user_id)
        if user is None or not user.active:
            refresh_tokens.revoke_user(conn, user_id)
            conn.commit()
            return None
        perms = sorted(
            permissions.effective_permissions(user.roles, repository.get_user_permissions(conn, user.id))
        )
        conn.commit()
    return SignedIn(user, perms, new_raw)


def sign_out(raw: str) -> None:
    """Revoke that one raw refresh token (unknown or already revoked: no-op)."""
    with db.get_conn() as conn:
        refresh_tokens.revoke(conn, raw)
        conn.commit()


def _set_cookie(response: Response, name: str, value: str, *, path: str) -> None:
    response.set_cookie(
        name, value, max_age=int(REFRESH_TOKEN_LIFETIME.total_seconds()), path=path, httponly=True,
        samesite="Strict", secure=current_app.config["COOKIE_SECURE"],
    )


def _delete_cookie(response: Response, name: str, path: str) -> None:
    response.delete_cookie(
        name, path=path, httponly=True, samesite="Strict", secure=current_app.config["COOKIE_SECURE"],
    )


def set_token_cookies(response: R, signed_in: SignedIn) -> R:
    """Set a fresh access token for the user and the refresh token on `response`."""
    user = signed_in.user
    access = issue_access_token(user.id, user.username, signed_in.permissions, utc_now())
    _set_cookie(response, ACCESS_COOKIE, access, path=_ACCESS_PATH)
    _set_cookie(response, REFRESH_COOKIE, signed_in.raw_refresh, path=_REFRESH_PATH)
    return response


def clear_access_cookie(response: R) -> R:
    _delete_cookie(response, ACCESS_COOKIE, _ACCESS_PATH)
    return response


def clear_token_cookies(response: R) -> R:
    clear_access_cookie(response)
    _delete_cookie(response, REFRESH_COOKIE, _REFRESH_PATH)
    return response
