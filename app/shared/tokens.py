"""JWT access tokens: issue and verify (docs/MIGRATION_PLAN.md, "Decisions").

An access token is a short-lived HS256 JWT signed with the dedicated
`JWT_SECRET` (never `SECRET_KEY`), with the claims `sub` (the user id as a
string), `username`, `perms` (sorted permission codes), `iat` and `exp`
(integer Unix seconds). The refresh tokens that renew it are opaque and live
in the database (app/auth/refresh_tokens.py); both lifetimes are defined here
only.

Pure: no web framework, no database. `now` is always passed in, naive UTC
like app/shared/timezones.py::utc_now() (an aware value is converted to UTC),
and expiry is checked against it, not the wall clock. Every PyJWT exception is
translated into TokenError, so callers never see a `jwt.*` exception. The
secret is read from the environment on every call; app/web/app.py refuses to
start without it.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

ACCESS_TOKEN_LIFETIME = timedelta(minutes=5)
REFRESH_TOKEN_LIFETIME = timedelta(days=30)

# The cookies that carry them (set by app/auth/login.py, read by app/shared/auth.py).
ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"

_ALGORITHM = "HS256"
_REQUIRED_CLAIMS = ("sub", "username", "perms", "iat", "exp")


class TokenError(Exception):
    """An access token that can't be accepted."""


class ExpiredTokenError(TokenError):
    """The token is genuine but `now` is at or past its `exp`."""


class InvalidTokenError(TokenError):
    """Bad signature, malformed token, wrong algorithm, or a missing or ill-typed claim."""


@dataclass(frozen=True)
class AccessClaims:
    """The verified claims of an access token; datetimes are naive UTC."""

    user_id: int
    username: str
    perms: tuple[str, ...]
    issued_at: datetime
    expires_at: datetime


def _secret() -> str:
    secret = os.environ.get("JWT_SECRET")
    if not secret:
        raise RuntimeError("JWT_SECRET env var is required to issue or verify access tokens.")
    return secret


def _timestamp(value: datetime) -> int:
    """Integer Unix seconds of a naive-UTC (or aware) datetime."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(value.timestamp())


def _from_timestamp(value: int) -> datetime:
    return datetime.fromtimestamp(value, UTC).replace(tzinfo=None)


def issue_access_token(user_id: int, username: str, perms: Iterable[str], now: datetime) -> str:
    """A signed access token for that user, valid for ACCESS_TOKEN_LIFETIME from `now`."""
    issued_at = _timestamp(now)
    payload = {
        "sub": str(user_id),
        "username": username,
        "perms": sorted(set(perms)),
        "iat": issued_at,
        "exp": issued_at + int(ACCESS_TOKEN_LIFETIME.total_seconds()),
    }
    return jwt.encode(payload, _secret(), algorithm=_ALGORITHM)


def decode_access_token(token: str, now: datetime) -> AccessClaims:
    """The claims of a genuine, unexpired token. ExpiredTokenError when `now`
    is at or past `exp`; InvalidTokenError for anything else wrong with it."""
    secret = _secret()
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[_ALGORITHM],
            options={"require": list(_REQUIRED_CLAIMS), "verify_exp": False, "verify_iat": False},
        )
    except jwt.PyJWTError as exc:
        raise InvalidTokenError(str(exc)) from exc

    sub = payload["sub"]
    username = payload["username"]
    perms = payload["perms"]
    iat = payload["iat"]
    exp = payload["exp"]
    if not isinstance(sub, str) or not sub.isdigit():
        raise InvalidTokenError("sub must be a user id string")
    if not isinstance(username, str):
        raise InvalidTokenError("username must be a string")
    if not isinstance(perms, list) or not all(isinstance(p, str) for p in perms):
        raise InvalidTokenError("perms must be a list of strings")
    for value in (iat, exp):
        if not isinstance(value, int) or isinstance(value, bool):
            raise InvalidTokenError("iat and exp must be integer Unix seconds")

    if _timestamp(now) >= exp:
        raise ExpiredTokenError("access token expired")
    return AccessClaims(
        user_id=int(sub),
        username=username,
        perms=tuple(perms),
        issued_at=_from_timestamp(iat),
        expires_at=_from_timestamp(exp),
    )
