"""users reads/writes -- backs app/web/auth.py's DB-backed login flow (Fase
2 moved login off the AUTH_USERS/AUTH_ROLES env vars, see auth.py's module
docstring) and any future admin user-management screen.

See app/shared/db.py for the module-wide connection/transaction
contract that applies to every function here.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

import psycopg
from psycopg.rows import DictRow, class_row


@dataclass
class UserRecord:
    """One `users` row plus the names of the roles it holds (user_roles,
    sorted by name) -- returned by lookups. Consumed by app.web.auth to
    decide whether a login succeeds, what session["user_id"] gets set to,
    and which permissions the user has."""

    id: int
    username: str
    password_hash: str
    roles: list[str]
    active: bool
    created_at: datetime | None = None


_SELECT_COLUMNS = """id, username, password_hash,
    COALESCE(
        (SELECT array_agg(ur.role ORDER BY ur.role) FROM user_roles ur WHERE ur.user_id = users.id),
        '{}'
    ) AS roles,
    active, created_at"""


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


def _insert_roles(
    conn: psycopg.Connection[DictRow], user_id: int, roles: Iterable[str], assigned_by: int | None
) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO user_roles (user_id, role, assigned_by) VALUES (%s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            [(user_id, role, assigned_by) for role in dict.fromkeys(roles)],
        )


def create_user(
    conn: psycopg.Connection[DictRow],
    *,
    username: str,
    password_hash: str,
    roles: Iterable[str],
    active: bool = True,
    assigned_by: int | None = None,
) -> int:
    """Insert one users row plus one user_roles row per distinct role, and
    return the user's id.

    No uniqueness pre-check here -- a duplicate username raises psycopg's
    UniqueViolation from the real UNIQUE constraint on `users.username`,
    same "let the DB constraint be the source of truth" convention as
    resolve_or_create_customer relying on `customers.name`'s constraint.
    Role names aren't validated in Python either: an unknown role raises
    psycopg's ForeignKeyViolation (user_roles_role_fkey).
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO users (username, password_hash, active)
            VALUES (%s, %s, %s)
            RETURNING id
            """,
            (username, password_hash, active),
        )
        row = cur.fetchone()
        assert row is not None, "INSERT ... RETURNING id always yields a row"
        user_id: int = row["id"]
    _insert_roles(conn, user_id, roles, assigned_by)
    return user_id


def list_users(conn: psycopg.Connection[DictRow]) -> list[UserRecord]:
    """Every user, oldest first -- for a future admin user-management screen
    (not consumed by anything in Fase 2 itself)."""
    with conn.cursor(row_factory=class_row(UserRecord)) as cur:
        cur.execute(f"SELECT {_SELECT_COLUMNS} FROM users ORDER BY id")
        return cur.fetchall()


def list_users_with_role(conn: psycopg.Connection[DictRow], role: str) -> list[UserRecord]:
    """Every user holding `role`, active or not, by id."""
    with conn.cursor(row_factory=class_row(UserRecord)) as cur:
        cur.execute(
            f"""
            SELECT {_SELECT_COLUMNS} FROM users
            WHERE EXISTS (SELECT 1 FROM user_roles ur WHERE ur.user_id = users.id AND ur.role = %s)
            ORDER BY id
            """,
            (role,),
        )
        return cur.fetchall()


def update_user(conn: psycopg.Connection[DictRow], user_id: int, *, active: bool) -> bool:
    """Set a user's active flag. False when there is no such user. Roles are
    changed with set_user_roles/assign_role/unassign_role."""
    with conn.cursor() as cur:
        cur.execute("UPDATE users SET active = %s WHERE id = %s", (active, user_id))
        return cur.rowcount > 0


def set_user_roles(
    conn: psycopg.Connection[DictRow], user_id: int, roles: Iterable[str], *, assigned_by: int | None = None
) -> bool:
    """Replace a user's role set: delete the user_roles rows not in `roles`
    and insert the missing ones with `assigned_by`; rows kept keep their
    assigned_at. False, with nothing written, when there is no such user. An
    unknown role raises psycopg's ForeignKeyViolation (user_roles_role_fkey)."""
    names = list(dict.fromkeys(roles))
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM users WHERE id = %s", (user_id,))
        if cur.fetchone() is None:
            return False
        cur.execute(
            "DELETE FROM user_roles WHERE user_id = %s AND NOT (role = ANY(%s))",
            (user_id, names),
        )
    _insert_roles(conn, user_id, names, assigned_by)
    return True


def assign_role(
    conn: psycopg.Connection[DictRow], user_id: int, role: str, *, assigned_by: int | None = None
) -> bool:
    """Give a user one more role. True when a row was added, False when the
    user already had it. An unknown user or role raises psycopg's
    ForeignKeyViolation."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO user_roles (user_id, role, assigned_by) VALUES (%s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (user_id, role, assigned_by),
        )
        return cur.rowcount > 0


def unassign_role(conn: psycopg.Connection[DictRow], user_id: int, role: str) -> bool:
    """Take one role away from a user. True when a row was removed."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM user_roles WHERE user_id = %s AND role = %s", (user_id, role))
        return cur.rowcount > 0


def get_user_permissions(conn: psycopg.Connection[DictRow], user_id: int) -> frozenset[str]:
    """The union of the stored role_permissions codes over all the user's
    roles (empty for an unknown user or one with no roles). Raw rows only:
    permissions.effective_permissions is the caller's job."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT rp.permission
            FROM user_roles ur JOIN role_permissions rp ON rp.role = ur.role
            WHERE ur.user_id = %s
            """,
            (user_id,),
        )
        return frozenset(r["permission"] for r in cur.fetchall())


def set_user_password(conn: psycopg.Connection[DictRow], user_id: int, password_hash: str) -> bool:
    """Replace a user's password hash (werkzeug format, hashed by the caller).
    False when there is no such user."""
    with conn.cursor() as cur:
        cur.execute("UPDATE users SET password_hash = %s WHERE id = %s", (password_hash, user_id))
        return cur.rowcount > 0


def count_active_users_with_role(conn: psycopg.Connection[DictRow], role: str) -> int:
    """How many ACTIVE users hold `role` -- e.g. so a management screen can
    refuse to deactivate or demote the last active admin."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*) AS n
            FROM users u JOIN user_roles ur ON ur.user_id = u.id
            WHERE ur.role = %s AND u.active
            """,
            (role,),
        )
        row = cur.fetchone()
        assert row is not None, "SELECT count(*) always yields a row"
        return int(row["n"])
