"""Application-level authentication: user store + login-required decorator.

Users come from the AUTH_USERS env var as comma-separated `username:hash`
pairs, where `hash` is a werkzeug password hash (pbkdf2). Example:

    AUTH_USERS=alice:pbkdf2:sha256:600000$abc...$def...,bob:pbkdf2:sha256:...

Generate a hash for a user with:

    python -m app.web.hash_password 'the-password'

Sessions are signed with SECRET_KEY (configured in app.py).

Roles come from a SEPARATE env var, AUTH_ROLES (see load_roles() for why it
isn't folded into AUTH_USERS). Any username with no AUTH_ROLES entry defaults
to ROLE_ADMIN, so the existing single-role production setup (zero AUTH_ROLES
configured) keeps working unchanged.
"""

from __future__ import annotations

import os
from functools import wraps
from urllib.parse import urlparse

from flask import abort, redirect, request, session, url_for
from werkzeug.security import check_password_hash

ROLE_ADMIN = "admin"
ROLE_PRODUCCION = "produccion"
ROLE_CONSULTA = "consulta"
# 'produccion' started as a full-access role and was demoted to read-only
# (same as 'consulta') once real usage clarified it should only ever look
# things up. Kept as its own role (rather than just aliased to 'consulta')
# in case it needs to diverge again later.
FULL_ACCESS_ROLES = {ROLE_ADMIN}


def load_users() -> dict[str, str]:
    """Parse AUTH_USERS into {username: password_hash}.

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

    Separate env var from AUTH_USERS on purpose: AUTH_USERS values are werkzeug
    hashes that themselves contain ':' (e.g. 'pbkdf2:sha256:600000$salt$hash'),
    so a third ':role' suffix on that line would be ambiguous to parse. Any
    username with no entry here defaults to ROLE_ADMIN -- this keeps the
    existing single-role production setup working unchanged with zero AUTH_ROLES
    configured.
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


def role_for(username: str) -> str:
    return load_roles().get(username, ROLE_ADMIN)


def verify_credentials(username: str, password: str) -> bool:
    """True if username exists and the password matches its stored hash."""
    pw_hash = load_users().get(username)
    if not pw_hash:
        return False
    return check_password_hash(pw_hash, password)


def is_safe_next(target: str | None) -> bool:
    """True if `target` is a safe local relative path (guards open redirects)."""
    if not target or not target.startswith("/"):
        return False
    # Reject protocol-relative ("//host") and backslash tricks ("/\\host").
    if target.startswith("//") or target.startswith("/\\"):
        return False
    parsed = urlparse(target)
    return not parsed.scheme and not parsed.netloc


def _redirect_to_login():
    """Bounce to /login (preserving the requested path as ?next=), clearing
    whatever's in the session first. Used for both "never logged in" and "the
    session predates a session-shape change" (e.g. a cookie from before roles
    existed, which has 'user' but no 'role') -- either way the person just
    needs to log in fresh, not see a dead-end error."""
    session.clear()
    next_url = request.full_path
    if next_url.endswith("?"):
        next_url = next_url[:-1]
    return redirect(url_for("login", next=next_url))


def login_required(view):
    """Redirect to /login when unauthenticated (or on a stale pre-roles
    session); 403 when authenticated with a real role that just isn't
    full-access. This is the DEFAULT for every route -- use @consulta_allowed
    instead only for the handful of read-only lookup routes the 'consulta'
    role (PTO.V) may reach. Never apply @consulta_allowed to anything that
    writes."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user") or "role" not in session:
            return _redirect_to_login()
        if session.get("role") not in FULL_ACCESS_ROLES:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def consulta_allowed(view):
    """Like login_required, but lets EVERY authenticated role through (including
    'consulta'). Only for routes that purely read/look things up -- nothing
    behind this decorator may write to the database."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user") or "role" not in session:
            return _redirect_to_login()
        return view(*args, **kwargs)

    return wrapped
