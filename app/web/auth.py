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

Generate a password hash for a new user with:

    python -m app.web.hash_password 'the-password'

Sessions are signed with SECRET_KEY (configured in app.py) and store `user`
(username), `role`, and (Fase 2) `user_id` (users.id) -- the last one lets
production_orders.created_by record which user saved an order without
re-querying by username.
"""

from __future__ import annotations

import os
from functools import wraps
from urllib.parse import urlparse

from flask import abort, redirect, request, session, url_for
from werkzeug.security import check_password_hash

from app.core import db
from app.core.db.users import UserRecord

ROLE_ADMIN = "admin"
ROLE_PRODUCCION = "produccion"
# New in Fase 2 (PTO.V's own role). Spelled lowercase-with-underscore, no
# accent -- see the note on ROLES below about why these exact literals matter.
ROLE_PUNTO_VENTA = "punto_venta"

# Every valid users.role value, validated against this tuple in Python (same
# "no DB CHECK, a tuple in code" pattern as normalize.PLATE_TYPES) rather
# than a CHECK constraint -- see docs/DATABASE.md.
# ⚠️ These are the exact literals already live in production's `users.role`
# column (backfilled from AUTH_ROLES) -- 'produccion' has NO accent, even
# though prose elsewhere ("producción") does. Changing any of these strings
# would silently lock out real users whose row already has the old value.
ROLES = (ROLE_ADMIN, ROLE_PRODUCCION, ROLE_PUNTO_VENTA)

# 'produccion' started as a full-access role and was demoted to read-only
# once real usage clarified it should only ever look things up -- it's the
# only read-only role today (a separate 'consulta' role that used to be
# functionally identical to it was removed as redundant).
FULL_ACCESS_ROLES = {ROLE_ADMIN}

# New in Fase 2: an intermediate access level for 'punto_venta' -- narrower
# than admin (FULL_ACCESS_ROLES) but wider than the read-only catch-all
# @consulta_allowed lets through. @pos_allowed (below) enforces it; which
# routes actually use that decorator is Fase 3's job, not this one's.
POS_ROLES = {ROLE_ADMIN, ROLE_PUNTO_VENTA}


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
        user = db.get_user_by_username(conn, username)
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
    """Where a logged-in user lands with no specific ?next= target.

    POS_ROLES (admin + punto_venta) start at Registrar -- Fase 3 put /ingest
    behind @pos_allowed, not @login_required, so punto_venta can actually use
    it as its daily entry point (that IS its job: capturing sales). Every
    other role (read-only: 'produccion') can't reach /ingest at all, so
    sending it there would just bounce into a 403 -- send it to Registros
    instead, the one place it can actually use.
    """
    if session.get("role") not in POS_ROLES:
        return url_for("records.records")
    return url_for("ingest.ingest")


def _redirect_to_login():
    """Bounce to /login (preserving the requested path as ?next=), clearing
    whatever's in the session first. Used for "never logged in" and for any
    session that predates a session-shape change (e.g. a cookie from before
    roles existed, which has 'user' but no 'role'; or -- Fase 2 -- one from
    before user_id existed, which has 'user'/'role' but no 'user_id') --
    either way the person just needs to log in fresh, not see a dead-end
    error."""
    session.clear()
    next_url = request.full_path
    if next_url.endswith("?"):
        next_url = next_url[:-1]
    return redirect(url_for("auth_routes.login", next=next_url))


def _session_is_stale() -> bool:
    """True for a missing/incomplete session -- either never logged in, or a
    cookie from before a session-shape change (missing 'role' since Fase ~5
    of the read-only-role work, or missing 'user_id' since Fase 2)."""
    return not session.get("user") or "role" not in session or "user_id" not in session


def login_required(view):
    """Redirect to /login when unauthenticated (or on a stale session); 403
    when authenticated with a real role that just isn't full-access. This is
    the DEFAULT for every route -- use @consulta_allowed or @pos_allowed
    instead only for the routes those narrower roles may reach. Never apply
    either of those to anything that writes beyond what they're meant for."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if _session_is_stale():
            return _redirect_to_login()
        if session.get("role") not in FULL_ACCESS_ROLES:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def consulta_allowed(view):
    """Like login_required, but lets EVERY authenticated role through
    (including read-only ones like 'produccion'). Only for routes that purely
    read/look things up -- nothing behind this decorator may write to the
    database."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if _session_is_stale():
            return _redirect_to_login()
        return view(*args, **kwargs)

    return wrapped


def pos_allowed(view):
    """Like login_required, but also lets 'punto_venta' through (POS_ROLES =
    {admin, punto_venta}) -- the Fase 2 access tier for the point-of-sale
    capture flow. 403 for every other authenticated role (produccion).
    Which routes actually use this decorator is Fase 3's job
    (/ingest, /ingest/pdf, /records/<id>/save, /cuadre per the plan) -- this
    phase only adds the decorator itself."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if _session_is_stale():
            return _redirect_to_login()
        if session.get("role") not in POS_ROLES:
            abort(403)
        return view(*args, **kwargs)

    return wrapped
