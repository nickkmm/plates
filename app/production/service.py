"""Production-status business rules: moving an order one step through
pending -> in_progress -> ready -> delivered, or one step back. No Flask.

The data layer is app/production/repository.py. Takes an open `conn` and never
commits/rolls back (same contract as app/shared/db.py).

**Optimistic concurrency**: the caller passes the status the person SAW
(`expected_status`, e.g. from the board they clicked on). The change only
happens while the order is still in that status -- checked first, then
enforced again by repository.set_production_status's conditional UPDATE -- so two
people clicking at once never move an order two steps: the second gets the
"cambió mientras tanto" ValueError and nothing changes.

**No roles here**: who may make which transition (production staff move every
step; the point of sale may also mark ready -> delivered) is decided by the
web layer from permissions. These functions take no role.
"""

from __future__ import annotations

import psycopg
from psycopg.rows import DictRow

from app.production import repository
from app.production.repository import PRODUCTION_STATUSES, STATUS_DELIVERED, STATUS_PENDING

MSG_ORDER_NOT_FOUND = "La orden no existe."
MSG_ALREADY_DELIVERED = "La orden ya fue entregada."
MSG_NOT_STARTED = "La orden todavía no ha empezado."
MSG_STATUS_CHANGED = (
    "El estado de la orden cambió mientras tanto. Revisa el estado actual e inténtalo de nuevo."
)


def next_status(status: str) -> str | None:
    """The status after `status`; None after delivered or for an unknown one."""
    if status not in PRODUCTION_STATUSES:
        return None
    index = PRODUCTION_STATUSES.index(status) + 1
    return PRODUCTION_STATUSES[index] if index < len(PRODUCTION_STATUSES) else None


def previous_status(status: str) -> str | None:
    """The status before `status`; None before pending or for an unknown one."""
    if status not in PRODUCTION_STATUSES:
        return None
    index = PRODUCTION_STATUSES.index(status) - 1
    return PRODUCTION_STATUSES[index] if index >= 0 else None


def _move_order(
    conn: psycopg.Connection[DictRow], order_id: int, user_id: int | None, *,
    current: str, expected_status: str, target: str | None,
) -> str:
    """Shared tail of advance_order/revert_order: the stale-status checks and
    the conditional update. `target` is the status to move to (None when
    `expected_status` has no neighbour in that direction)."""
    if current != expected_status or target is None:
        raise ValueError(MSG_STATUS_CHANGED)
    if not repository.set_production_status(
        conn, order_id, from_status=expected_status, to_status=target, user_id=user_id,
    ):
        raise ValueError(MSG_STATUS_CHANGED)
    return target


def advance_order(
    conn: psycopg.Connection[DictRow], order_id: int, user_id: int | None, *, expected_status: str,
) -> str:
    """Move the order one step forward and return its new status.

    Raises ValueError (Spanish message for the staff) when the order does
    not exist, when `expected_status` is delivered, or when the order is no
    longer in `expected_status` (someone else moved it) -- in every case
    nothing changes.
    """
    current = repository.get_production_status(conn, order_id)
    if current is None:
        raise ValueError(MSG_ORDER_NOT_FOUND)
    if expected_status == STATUS_DELIVERED:
        raise ValueError(MSG_ALREADY_DELIVERED)
    return _move_order(
        conn, order_id, user_id, current=current, expected_status=expected_status,
        target=next_status(expected_status),
    )


def revert_order(
    conn: psycopg.Connection[DictRow], order_id: int, user_id: int | None, *, expected_status: str,
) -> str:
    """Move the order one step back (undo) and return its new status.

    Same checks as advance_order, except that pending (nothing to undo)
    raises "todavía no ha empezado"; reverting delivered -> ready is allowed,
    to undo a delivery marked by mistake.
    """
    current = repository.get_production_status(conn, order_id)
    if current is None:
        raise ValueError(MSG_ORDER_NOT_FOUND)
    if expected_status == STATUS_PENDING:
        raise ValueError(MSG_NOT_STARTED)
    return _move_order(
        conn, order_id, user_id, current=current, expected_status=expected_status,
        target=previous_status(expected_status),
    )
