"""Opaque refresh tokens: issue, rotate with reuse detection, revoke.

A refresh token is `secrets.token_urlsafe(32)`, valid for
app/shared/tokens.py::REFRESH_TOKEN_LIFETIME. Only its SHA-256 hex is stored
(refresh_tokens.token_hash); the raw value is returned to the caller once and
never written anywhere. Every token belongs to a family (family_id): `issue`
starts a new one and each `rotate` revokes the presented token and issues
its successor in the same family. Presenting a revoked token again is reuse
(a stolen token, or a client replaying an old one), so the whole family is
revoked and the caller gets None.

`rotate` reads the row with SELECT ... FOR UPDATE, so two concurrent
`rotate` calls with the same token serialize: the second one waits for the
first transaction, then sees a revoked token and triggers reuse detection.

Every function takes an open `conn` and never commits or rolls back (see
app/shared/db.py). The JSON auth API (app/auth/api.py) issues, rotates and
revokes them; the /usuarios screen (app/auth/admin_routes.py) revokes a user's.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid

import psycopg
from psycopg.rows import DictRow

from app.auth import repository
from app.shared.timezones import utc_now
from app.shared.tokens import REFRESH_TOKEN_LIFETIME


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _insert(conn: psycopg.Connection[DictRow], user_id: int, family_id: uuid.UUID) -> str:
    raw = secrets.token_urlsafe(32)
    repository.insert_refresh_token(
        conn,
        user_id=user_id,
        token_hash=_hash(raw),
        family_id=family_id,
        expires_at=utc_now() + REFRESH_TOKEN_LIFETIME,
    )
    return raw


def issue(conn: psycopg.Connection[DictRow], user_id: int) -> str:
    """A new refresh token for the user, in a new family. Returns the raw value."""
    return _insert(conn, user_id, uuid.uuid4())


def rotate(conn: psycopg.Connection[DictRow], raw: str) -> tuple[int, str] | None:
    """Trade a valid token for its successor: `(user_id, new_raw)`.

    None for an unknown or expired token (an expired one doesn't revoke its
    family: expiry is normal). None for a revoked token too, after revoking
    every token in its family (reuse detection).
    """
    row = repository.get_refresh_token_for_update(conn, _hash(raw))
    if row is None:
        return None
    if row.revoked_at is not None:
        repository.revoke_refresh_token_family(conn, row.family_id)
        return None
    if row.expires_at <= utc_now():
        return None
    repository.revoke_refresh_token(conn, row.id)
    return row.user_id, _insert(conn, row.user_id, row.family_id)


def revoke(conn: psycopg.Connection[DictRow], raw: str) -> None:
    """Revoke that one token. Unknown or already revoked: no-op."""
    row = repository.get_refresh_token_for_update(conn, _hash(raw))
    if row is not None:
        repository.revoke_refresh_token(conn, row.id)


def revoke_user(conn: psycopg.Connection[DictRow], user_id: int) -> None:
    """Revoke every token of the user, in every family."""
    repository.revoke_user_refresh_tokens(conn, user_id)
