"""roles / role_permissions / permissions reads/writes. get_role_permissions
reads one role's stored codes (app/web/auth.py reads a user's, the union over
all their roles, through users.get_user_permissions); the writes and
list_permissions are the data layer for a future admin role-management
screen.

Permission codes are validated against app/core/permissions.py's
PERMISSION_CODES in Python before anything is written, which gives a clean
ValueError; role_permissions_permission_fkey (a FK to permissions(code)) also
enforces them in the DB. These functions don't protect system roles; that
guard belongs to the screen that manages them.

See app/core/db/__init__.py for the module-wide connection/transaction
contract that applies to every function here.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import psycopg
from psycopg.rows import DictRow, class_row

from app.core.permissions import PERMISSION_CODES


@dataclass
class RoleRecord:
    """One `roles` row plus its stored permission codes (sorted
    alphabetically) and how many users -- active or not -- hold the role
    (user_roles rows)."""

    name: str
    label: str
    is_system: bool
    permissions: list[str]
    user_count: int


@dataclass
class PermissionRecord:
    """One `permissions` row: a catalog code and its Spanish label."""

    code: str
    label: str


_SELECT_ROLES = """
    SELECT r.name, r.label, r.is_system,
           COALESCE(
               (SELECT array_agg(rp.permission ORDER BY rp.permission)
                FROM role_permissions rp WHERE rp.role = r.name),
               '{}'
           ) AS permissions,
           (SELECT count(*) FROM user_roles ur WHERE ur.role = r.name)::int AS user_count
    FROM roles r
"""


def _validated(permissions: Iterable[str]) -> list[str]:
    """The codes as a list, or ValueError if any isn't in the catalog."""
    codes = list(permissions)
    unknown = sorted(set(codes) - PERMISSION_CODES)
    if unknown:
        raise ValueError(f"Permisos no válidos: {', '.join(unknown)}.")
    return codes


def _insert_permissions(conn: psycopg.Connection[DictRow], name: str, codes: list[str]) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO role_permissions (role, permission) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            [(name, code) for code in codes],
        )


def list_roles(conn: psycopg.Connection[DictRow]) -> list[RoleRecord]:
    """Every role: system roles first, then by label."""
    with conn.cursor(row_factory=class_row(RoleRecord)) as cur:
        cur.execute(f"{_SELECT_ROLES} ORDER BY r.is_system DESC, r.label")
        return cur.fetchall()


def list_permissions(conn: psycopg.Connection[DictRow]) -> list[PermissionRecord]:
    """Every permission in the catalog, in display order (sort_order)."""
    with conn.cursor(row_factory=class_row(PermissionRecord)) as cur:
        cur.execute("SELECT code, label FROM permissions ORDER BY sort_order")
        return cur.fetchall()


def get_role(conn: psycopg.Connection[DictRow], name: str) -> RoleRecord | None:
    """One role by name, or None for no such role."""
    with conn.cursor(row_factory=class_row(RoleRecord)) as cur:
        cur.execute(f"{_SELECT_ROLES} WHERE r.name = %s", (name,))
        return cur.fetchone()


def get_role_permissions(conn: psycopg.Connection[DictRow], name: str) -> frozenset[str]:
    """The stored permission codes of a role (empty for an unknown role).
    Raw rows only: permissions.effective_permissions is the caller's job."""
    with conn.cursor() as cur:
        cur.execute("SELECT permission FROM role_permissions WHERE role = %s", (name,))
        return frozenset(r["permission"] for r in cur.fetchall())


def create_role(
    conn: psycopg.Connection[DictRow], *, name: str, label: str, permissions: Iterable[str]
) -> None:
    """Insert a non-system role and its permission rows.

    ValueError before writing anything if a code isn't in the catalog. No
    uniqueness pre-check: a duplicate name or label raises psycopg's
    UniqueViolation from the real constraints.
    """
    codes = _validated(permissions)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO roles (name, label, is_system) VALUES (%s, %s, false)",
            (name, label),
        )
    _insert_permissions(conn, name, codes)


def update_role(
    conn: psycopg.Connection[DictRow], name: str, *, label: str, permissions: Iterable[str]
) -> bool:
    """Set the label and replace (not merge) the permission set. ValueError
    before writing anything on an unknown code; False, with nothing written,
    when the role doesn't exist."""
    codes = _validated(permissions)
    with conn.cursor() as cur:
        cur.execute("UPDATE roles SET label = %s WHERE name = %s", (label, name))
        if cur.rowcount == 0:
            return False
        cur.execute("DELETE FROM role_permissions WHERE role = %s", (name,))
    _insert_permissions(conn, name, codes)
    return True


def delete_role(conn: psycopg.Connection[DictRow], name: str) -> bool:
    """Delete a role; its permission rows go with it (ON DELETE CASCADE).
    False when there was no such role. No pre-check: while any user still holds
    the role, user_roles_role_fkey raises psycopg's ForeignKeyViolation."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM roles WHERE name = %s", (name,))
        return cur.rowcount > 0
