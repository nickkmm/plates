"""Validation rules for the admin users screen (/usuarios) and its roles
section (/usuarios/roles). No Flask.

Each function returns None when the input is valid, or the first failing
rule's Spanish message. Takes an open `conn` where it needs the database and
never writes, commits or rolls back (same contract as
app/shared/db.py): the web layer does the writes.

**Who may lock whom out**: an admin can't deactivate their own account nor
drop their own admin role, and no change may leave the app without an
active admin. The self rules are checked first, as the more precise message.
The last-admin count runs without row locks; two admins demoting each other
at the same instant is an accepted race.

**Roles**: a role label is compared with every other role's label ignoring
case and accents, so "Bodega" and "bódega" can't both exist (the DB's UNIQUE
on roles.label only catches exact duplicates). Only custom roles with no
users can be deleted; the screen never edits admin (see app/auth/admin_routes.py).
"""

from __future__ import annotations

import re
import secrets
import unicodedata

import psycopg
from psycopg.rows import DictRow

from app.auth import repository
from app.auth.permissions import ADMIN_ROLE
from app.auth.repository import RoleRecord, UserRecord

USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,50}$")
PASSWORD_MIN_LENGTH = 10
ROLE_LABEL_MAX_LENGTH = 60

MSG_BAD_USERNAME = (
    "El usuario debe tener entre 3 y 50 caracteres: letras, números, punto, guion o guion bajo."
)
# Returned by the web layer when repository.create_user raises UniqueViolation.
MSG_DUPLICATE_USERNAME = "Ese usuario ya existe."
MSG_SHORT_PASSWORD = f"La contraseña debe tener al menos {PASSWORD_MIN_LENGTH} caracteres."
MSG_PASSWORD_MISMATCH = "Las contraseñas no coinciden."
MSG_NO_ROLE = "Elige al menos un rol."
MSG_INVALID_ROLE = "Elige un rol válido."
MSG_SELF_DEACTIVATE = "No puedes desactivar tu propio usuario."
MSG_SELF_ADMIN_REMOVAL = "No puedes quitarte el rol de administrador."
MSG_LAST_ADMIN = "Debe quedar al menos un administrador activo."
MSG_BAD_ROLE_LABEL = "Escribe el nombre del rol."
# Also returned by the web layer when create_role/update_role raise UniqueViolation.
MSG_DUPLICATE_ROLE_LABEL = "Ya existe un rol con ese nombre."
MSG_NO_PERMISSION = "Marca al menos un permiso."
MSG_DELETE_SYSTEM_ROLE = "No se puede eliminar un rol del sistema."
# Also returned by the web layer when delete_role raises ForeignKeyViolation.
MSG_DELETE_ROLE_IN_USE = "No se puede eliminar un rol con usuarios asignados."


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
    known = {r.name for r in repository.list_roles(conn)}
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
        and repository.count_active_users_with_role(conn, ADMIN_ROLE) <= 1
    ):
        return MSG_LAST_ADMIN
    return None


def new_role_name() -> str:
    """The internal name of a new custom role. Never typed nor shown."""
    return "custom_" + secrets.token_hex(4)


def _fold_label(label: str) -> str:
    """Case-folded and accent-stripped (same approach as
    normalize._strip_accents), for comparing role labels only."""
    nfkd = unicodedata.normalize("NFKD", label)
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch)).casefold()


def clean_permissions(conn: psycopg.Connection[DictRow], permissions: list[str]) -> list[str]:
    """The submitted codes that are in the catalog, without duplicates, in
    catalog order. Unknown codes are dropped silently."""
    wanted = set(permissions)
    return [p.code for p in repository.list_permissions(conn) if p.code in wanted]


def validate_role(
    conn: psycopg.Connection[DictRow],
    *,
    label: str,
    permissions: list[str],
    role_name: str | None = None,
) -> str | None:
    """Label, its uniqueness among the other roles, then at least one
    permission. `label` is already stripped and `permissions` already went
    through clean_permissions; `role_name` is the role being edited (None
    when creating), left out of the uniqueness check."""
    if not label or len(label) > ROLE_LABEL_MAX_LENGTH:
        return MSG_BAD_ROLE_LABEL
    folded = _fold_label(label)
    if any(r.name != role_name and _fold_label(r.label) == folded for r in repository.list_roles(conn)):
        return MSG_DUPLICATE_ROLE_LABEL
    if not permissions:
        return MSG_NO_PERMISSION
    return None


def validate_role_delete(role: RoleRecord) -> str | None:
    """System roles are never deleted, nor a role some user (active or not)
    still holds."""
    if role.is_system:
        return MSG_DELETE_SYSTEM_ROLE
    if role.user_count > 0:
        return MSG_DELETE_ROLE_IN_USE
    return None
