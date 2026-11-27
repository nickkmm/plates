"""Application-level authentication: DB-backed user store + access decorators.

**Fase 2 change**: the live login flow (authenticate/verify_credentials
below) now queries the `users` table (via app/core/db/users.py) instead of
reading AUTH_USERS/AUTH_ROLES env vars. Nobody's password changes: the Fase 1
backfill (app.schema_migrate.backfill_users) already copied every
AUTH_USERS/AUTH_ROLES entry into `users` with the SAME werkzeug hash.

load_users()/load_roles() below are KEPT, but are now used ONLY by that
one-time, already-applied-in-production backfill -- they are what MOVED
AUTH_USERS/AUTH_ROLES into the `users` table in the first place, so
schema_migrate.py still needs to read them for that historical step. Nothing
in the live request path below calls them anymore.

Access control (#63): every protected route is decorated with
@permission_required("<code>") (or @authenticated for / alone). A user holds
several roles (user_roles); their permissions are the union of those roles'
role_permissions, through app/core/permissions.effective_permissions, and
admin in any of them grants every permission code. The user, their roles and
their permissions are read from the database on EVERY request and cached on
flask.g -- so a deactivated user is logged out, and a role change takes
effect, on their next request.

Generate a password hash for a new user with:

    python -m app.web.hash_password 'the-password'

Sessions are signed with SECRET_KEY (configured in app.py) and store `user`
(username) and (Fase 2) `user_id` (users.id) only -- the last one lets
production_orders.created_by record which user saved an order without
re-querying by username. The roles are never stored in the session; a
cookie issued before that change still carries `role`, which is ignored.
"""

from __future__ import annotations

import os
from functools import wraps
from urllib.parse import urlparse

from flask import abort, g, redirect, request, session, url_for
from werkzeug.security import check_password_hash

from app.auth import permissions, repository
from app.auth.repository import UserRecord
from app.shared import db
from app.web.navigation import NAV_ITEMS

ROLE_ADMIN = "admin"


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
    app/web/auth.py is a web-layer module, not part of app/core/db/'s
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


def is_safe_next(target: str | None) -> bool:
    """True if `target` is a safe local relative path (guards open redirects)."""
    if not target or not target.startswith("/"):
        return False
    # Reject protocol-relative ("//host") and backslash tricks ("/\\host").
    if target.startswith("//") or target.startswith("/\\"):
        return False
    parsed = urlparse(target)
    return not parsed.scheme and not parsed.netloc


def landing_url() -> str:
    """Where a logged-in user lands with no specific ?next= target: the first
    NAV_ITEMS entry their permissions open. With none, `/` -- which answers
    the 403 "no permissions" page itself instead of redirecting again, so
    there is no redirect loop."""
    for endpoint, _label, permission, _active in NAV_ITEMS:
        if has_permission(permission):
            return url_for(endpoint)
    return url_for("index")


def _redirect_to_login():
    """Bounce to /login (preserving the requested path as ?next=), clearing
    whatever's in the session first. Used for "never logged in" and for any
    session that predates a session-shape change (Fase 2: a cookie from
    before user_id existed, which has 'user' but no 'user_id') -- either way
    the person just needs to log in fresh, not see a dead-end error. A cookie
    that still carries the old 'role' key is not stale: it is ignored."""
    session.clear()
    next_url = request.full_path
    if next_url.endswith("?"):
        next_url = next_url[:-1]
    return redirect(url_for("auth_routes.login", next=next_url))


def _session_is_stale() -> bool:
    """True for a missing/incomplete session -- either never logged in, or a
    cookie from before a session-shape change (missing 'user_id' since Fase
    2)."""
    return not session.get("user") or "user_id" not in session


def current_user() -> UserRecord | None:
    """The logged-in user's current `users` row, or None when the session has
    no user_id or no user has that id. The first call in a request loads the
    user, their roles and their effective permissions (the union over all
    their roles) in one connection; both are cached
    on flask.g, so later calls in the same request don't query again."""
    if "_current_user_loaded" not in g:
        user = None
        perms: frozenset[str] = frozenset()
        user_id = session.get("user_id")
        if user_id is not None:
            with db.get_conn() as conn:
                user = repository.get_user_by_id(conn, user_id)
                if user is not None:
                    perms = permissions.effective_permissions(
                        user.roles, repository.get_user_permissions(conn, user.id)
                    )
        g._current_user_loaded = True
        g.current_user = user
        g.current_permissions = perms
    return g.current_user


def has_permission(code: str) -> bool:
    """True only if there is an active current user one of whose roles grants
    `code` (admin in any role grants everything). False (never an error) when
    there is no session."""
    user = current_user()
    if user is None or not user.active:
        return False
    return code in g.current_permissions


def _check_session():
    """The shared checks of @authenticated and @permission_required: a
    response redirecting to /login (session cleared) when the session is
    stale, its user no longer exists or is inactive; otherwise None."""
    if _session_is_stale():
        return _redirect_to_login()
    user = current_user()
    if user is None or not user.active:
        return _redirect_to_login()
    return None


def authenticated(view):
    """Any active logged-in user, whatever their permissions. Only for `/`,
    which itself decides where (or whether) the user may go."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        response = _check_session()
        if response is not None:
            return response
        return view(*args, **kwargs)

    return wrapped


def permission_required(*codes: str):
    """Like @authenticated, then 403 unless the union of the user's roles'
    permissions holds at least one of `codes`. Every protected route uses this with one code; no route
    that writes may accept 'records.view'."""

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            response = _check_session()
            if response is not None:
                return response
            if not any(has_permission(code) for code in codes):
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator
