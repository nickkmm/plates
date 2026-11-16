"""The permission catalog: every action a user's roles can be granted.

Pure Python (no Flask, no database). Codes are English identifiers stored in
`role_permissions.permission`; labels are the Spanish text staff will see.
The catalog is mirrored in the `permissions` table (same codes, labels and
order), which role_permissions.permission references by FK; a test keeps the
table and this tuple identical, so adding a code takes both a new migration
and an entry here. Every protected route checks one of these codes on each
request (app/web/auth.py's permission_required), through
effective_permissions below.
"""

from __future__ import annotations

from collections.abc import Iterable

PERMISSIONS: tuple[tuple[str, str], ...] = (
    ("orders.capture", "Registrar y editar órdenes"),
    ("cash_summary.view", "Ver el cuadre de caja"),
    ("declarations.review", "Revisar declaraciones"),
    ("production.view", "Ver el tablero de producción"),
    ("production.manage", "Cambiar el estado de producción"),
    ("production.deliver", "Marcar órdenes como entregadas"),
    ("records.view", "Consultar registros"),
    ("records.delete", "Eliminar órdenes"),
    ("duplicates.review", "Revisar posibles duplicados"),
    ("inventory.manage", "Gestionar inventario"),
    ("pricing.manage", "Editar precios"),
    ("customers.manage", "Gestionar clientes"),
    ("users.manage", "Gestionar usuarios y roles"),
)

PERMISSION_CODES: frozenset[str] = frozenset(code for code, _ in PERMISSIONS)

# The admin role always holds every permission, whatever role_permissions
# stores for it -- so no admin can ever be locked out of a new permission.
ADMIN_ROLE = "admin"


def effective_permissions(roles: Iterable[str], stored: Iterable[str]) -> frozenset[str]:
    """The permissions a user with `roles` really has: every code when
    ADMIN_ROLE is one of them, otherwise the `stored` codes that are still in
    the catalog. `stored` is the union of the role_permissions codes of all
    the user's roles."""
    if ADMIN_ROLE in set(roles):
        return PERMISSION_CODES
    return frozenset(stored) & PERMISSION_CODES
