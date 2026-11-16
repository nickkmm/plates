"""Validation rules for the admin users screen (/usuarios). No Flask.

Each function returns None when the input is valid, or the first failing
rule's Spanish message. Takes an open `conn` where it needs the database and
never writes, commits or rolls back (same contract as
app/core/db/__init__.py): the web layer does the writes.

**Who may lock whom out**: an admin can't deactivate their own account nor
drop their own admin role, and no change may leave the app without an
active admin. The self rules are checked first, as the more precise message.
The last-admin count runs without row locks; two admins demoting each other
at the same instant is an accepted race.
"""

from __future__ import annotations

import re

import psycopg
from psycopg.rows import DictRow

from app.core import db
from app.core.db.users import UserRecord
from app.core.permissions import ADMIN_ROLE

USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,50}$")
PASSWORD_MIN_LENGTH = 10

MSG_BAD_USERNAME = (
    "El usuario debe tener entre 3 y 50 caracteres: letras, números, punto, guion o guion bajo."
)
# Returned by the web layer when db.create_user raises UniqueViolation.
MSG_DUPLICATE_USERNAME = "Ese usuario ya existe."
MSG_SHORT_PASSWORD = f"La contraseña debe tener al menos {PASSWORD_MIN_LENGTH} caracteres."
MSG_PASSWORD_MISMATCH = "Las contraseñas no coinciden."
MSG_NO_ROLE = "Elige al menos un rol."
MSG_INVALID_ROLE = "Elige un rol válido."
MSG_SELF_DEACTIVATE = "No puedes desactivar tu propio usuario."
MSG_SELF_ADMIN_REMOVAL = "No puedes quitarte el rol de administrador."
MSG_LAST_ADMIN = "Debe quedar al menos un administrador activo."


def validate_password(password: str, password_confirm: str) -> str | None:
    """Length first, then that both entries match."""
    if len(password) < PASSWORD_MIN_LENGTH:
        return MSG_SHORT_PASSWORD
    if password != password_confirm:
        return MSG_PASSWORD_MISMATCH
    return None


def _validate_roles(conn: psycopg.Connection[DictRow], roles: list[str]) -> str | None:
    if not roles:
        return MSG_NO_ROLE
    known = {r.name for r in db.list_roles(conn)}
    if any(role not in known for role in roles):
        return MSG_INVALID_ROLE
    return None


def validate_new_user(
    conn: psycopg.Connection[DictRow],
    *,
    username: str,
    password: str,
    password_confirm: str,
    roles: list[str],
) -> str | None:
    """Username format, roles, then password. `username` is already stripped.
    A duplicate username is left to the UNIQUE constraint."""
    if not USERNAME_RE.fullmatch(username):
        return MSG_BAD_USERNAME
    return _validate_roles(conn, roles) or validate_password(password, password_confirm)


def validate_user_update(
    conn: psycopg.Connection[DictRow],
    *,
    target: UserRecord,
    actor_id: int,
    roles: list[str],
    active: bool,
) -> str | None:
    """Roles, self-deactivation, self admin removal, then the last active
    admin, for `actor_id` setting `target`'s roles and active flag."""
    error = _validate_roles(conn, roles)
    if error:
        return error
    is_self = target.id == actor_id
    was_admin = ADMIN_ROLE in target.roles
    drops_admin = was_admin and ADMIN_ROLE not in roles
    if is_self and not active:
        return MSG_SELF_DEACTIVATE
    if is_self and drops_admin:
        return MSG_SELF_ADMIN_REMOVAL
    if (
        target.active
        and was_admin
        and (drops_admin or not active)
        and db.count_active_users_with_role(conn, ADMIN_ROLE) <= 1
    ):
        return MSG_LAST_ADMIN
    return None
