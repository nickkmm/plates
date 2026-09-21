"""users reads/writes -- backs app/web/auth.py's DB-backed login flow (Fase
2 moved login off the AUTH_USERS/AUTH_ROLES env vars, see auth.py's module
docstring) and any future admin user-management screen.

See app/core/db/__init__.py for the module-wide connection/transaction
contract that applies to every function here.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg
from psycopg.rows import DictRow, class_row


@dataclass
class UserRecord:
    """One `users` row -- returned by lookups. Consumed by app.web.auth to
    decide whether a login succeeds and what session["role"]/
    session["user_id"] get set to."""

    id: int
    username: str
    password_hash: str
    role: str
    active: bool


_SELECT_COLUMNS = "id, username, password_hash, role, active"


def get_user_by_username(conn: psycopg.Connection[DictRow], username: str) -> UserRecord | None:
    """Look up one user by username (case-sensitive, matching the UNIQUE
    constraint). Returns None for no such user -- an inactive user IS
    returned (active=False); it's app.web.auth.authenticate's job to decide
    an inactive account can't log in, not this lookup's."""
    with conn.cursor(row_factory=class_row(UserRecord)) as cur:
        cur.execute(f"SELECT {_SELECT_COLUMNS} FROM users WHERE username = %s", (username,))
        return cur.fetchone()


def get_user_by_id(conn: psycopg.Connection[DictRow], user_id: int) -> UserRecord | None:
    """Look up one user by id (session["user_id"] / production_orders.created_by)."""
    with conn.cursor(row_factory=class_row(UserRecord)) as cur:
        cur.execute(f"SELECT {_SELECT_COLUMNS} FROM users WHERE id = %s", (user_id,))
        return cur.fetchone()


def create_user(
    conn: psycopg.Connection[DictRow], *, username: str, password_hash: str, role: str, active: bool = True
) -> int:
    """Insert one users row and return its id.

    No uniqueness pre-check here -- a duplicate username raises psycopg's
    UniqueViolation from the real UNIQUE constraint on `users.username`,
    same "let the DB constraint be the source of truth" convention as
    resolve_or_create_customer relying on `customers.name`'s constraint.
    `role` is NOT validated against auth.ROLES here -- per the plan, that
    validation lives in Python at the web layer (auth.py), the same
    "no CHECK in the DB, a tuple in Python" pattern already used for
    plate_type_prices/normalize.PLATE_TYPES.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO users (username, password_hash, role, active)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (username, password_hash, role, active),
        )
        row = cur.fetchone()
        assert row is not None, "INSERT ... RETURNING id always yields a row"
        return row["id"]


def list_users(conn: psycopg.Connection[DictRow]) -> list[UserRecord]:
    """Every user, oldest first -- for a future admin user-management screen
    (not consumed by anything in Fase 2 itself)."""
    with conn.cursor(row_factory=class_row(UserRecord)) as cur:
        cur.execute(f"SELECT {_SELECT_COLUMNS} FROM users ORDER BY id")
        return cur.fetchall()
