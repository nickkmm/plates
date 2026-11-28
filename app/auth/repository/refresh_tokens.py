"""refresh_tokens reads/writes -- backs app/auth/refresh_tokens.py (issue,
rotate and revoke the opaque refresh tokens of the JWT authentication).

Only `token_hash` (the SHA-256 hex of a token) is ever written or compared
here; hashing the token is the service's job. Revoking sets `revoked_at` to
the current naive-UTC time and leaves already revoked rows untouched.

See app/shared/db.py for the module-wide connection/transaction
contract that applies to every function here.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

import psycopg
from psycopg.rows import DictRow, class_row

from app.shared.timezones import utc_now


@dataclass
class RefreshTokenRecord:
    """One `refresh_tokens` row (naive-UTC timestamps)."""

    id: int
    user_id: int
    token_hash: str
    family_id: uuid.UUID
    expires_at: datetime
    revoked_at: datetime | None
    created_at: datetime


def insert_refresh_token(
    conn: psycopg.Connection[DictRow],
    *,
    user_id: int,
    token_hash: str,
    family_id: uuid.UUID,
    expires_at: datetime,
) -> int:
    """Insert one token row and return its id."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO refresh_tokens (user_id, token_hash, family_id, expires_at)
            VALUES (%s, %s, %s, %s) RETURNING id
            """,
            (user_id, token_hash, family_id, expires_at),
        )
        row = cur.fetchone()
        assert row is not None
        return int(row["id"])


def get_refresh_token_for_update(
    conn: psycopg.Connection[DictRow], token_hash: str
) -> RefreshTokenRecord | None:
    """The row with that hash, locked FOR UPDATE until the transaction ends,
    so two concurrent rotations of the same token serialize. None if unknown."""
    with conn.cursor(row_factory=class_row(RefreshTokenRecord)) as cur:
        cur.execute(
            """
            SELECT id, user_id, token_hash, family_id, expires_at, revoked_at, created_at
            FROM refresh_tokens WHERE token_hash = %s FOR UPDATE
            """,
            (token_hash,),
        )
        return cur.fetchone()


def revoke_refresh_token(conn: psycopg.Connection[DictRow], token_id: int) -> bool:
    """Revoke one row. False if it doesn't exist or was already revoked."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE refresh_tokens SET revoked_at = %s WHERE id = %s AND revoked_at IS NULL",
            (utc_now(), token_id),
        )
        return cur.rowcount > 0


def revoke_refresh_token_family(conn: psycopg.Connection[DictRow], family_id: uuid.UUID) -> int:
    """Revoke every unrevoked row of a family; returns how many."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE refresh_tokens SET revoked_at = %s WHERE family_id = %s AND revoked_at IS NULL",
            (utc_now(), family_id),
        )
        return cur.rowcount


def revoke_user_refresh_tokens(conn: psycopg.Connection[DictRow], user_id: int) -> int:
    """Revoke every unrevoked row of a user, across families; returns how many."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE refresh_tokens SET revoked_at = %s WHERE user_id = %s AND revoked_at IS NULL",
            (utc_now(), user_id),
        )
        return cur.rowcount
