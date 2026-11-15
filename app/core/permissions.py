"""The permission catalog: every action a role can be granted.

Pure Python (no Flask, no database). Codes are English identifiers stored in
`role_permissions.permission`; labels are the Spanish text staff will see.
Codes are validated here in Python, not with a CHECK in the database (same
pattern as normalize.PLATE_TYPES and `plate_types`). The routes don't consume
this yet: access is still decided by app/web/auth.py's hard-coded role sets.
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


def effective_permissions(role: str, stored: Iterable[str]) -> frozenset[str]:
    """The permissions `role` really has: every code for ADMIN_ROLE, otherwise
    the `stored` codes (role_permissions rows) that are still in the catalog."""
    if role == ADMIN_ROLE:
        return PERMISSION_CODES
    return frozenset(stored) & PERMISSION_CODES
