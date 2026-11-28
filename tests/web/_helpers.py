"""Shared setup helpers for tests/web/test_*.py route-integration tests --
moved out of the old tests/web/test_app.py (Fase 1d step 10 test-file
split) so test_login.py, test_review.py, test_inventory.py,
test_records.py, test_ingest.py and test_roles.py can all import them
without duplicating. Plain functions, not fixtures -- import them
explicitly, e.g. `from tests.web._helpers import make_row, _insert_order`."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta

from app.orders import repository as orders_repository
from app.shared.models import ExtractedRow
from app.shared.timezones import utc_now
from app.shared.tokens import ACCESS_COOKIE, decode_access_token, issue_access_token


def log_in(client, username: str, password: str):
    """POST /login, which leaves both token cookies on the client."""
    return client.post("/login", data={"username": username, "password": password})


def set_access_token(client, user_id: int, username: str, perms: Iterable[str], now: datetime | None = None) -> str:
    """Put an access token issued at `now` (default: now) on the client, the
    way login does. A `now` more than 5 minutes ago gives an expired token."""
    token = issue_access_token(user_id, username, perms, now or utc_now())
    client.set_cookie(ACCESS_COOKIE, token, path="/")
    return token


def expire_access_token(client) -> None:
    """Replace the client's access token with the same claims issued 6
    minutes ago, as if the user had waited past its 5-minute lifetime: the
    next page goes through /api/auth/refresh/continue."""
    cookie = client.get_cookie(ACCESS_COOKIE)
    assert cookie is not None
    claims = decode_access_token(cookie.value, utc_now())
    set_access_token(client, claims.user_id, claims.username, claims.perms, now=utc_now() - timedelta(minutes=6))


def make_row(**overrides: object) -> ExtractedRow:
    defaults: dict[str, object] = {
        "order_number": "1",
        "order_date": date(2026, 1, 1),
        "requested_by": "PEDRO",
        "plate_type": "CARRO PARTICULAR",
    }
    defaults.update(overrides)
    return ExtractedRow(**defaults)


def _insert_order(conn, *, order_number: str, order_date, requested_by: str) -> int:
    """Back-compat shim for this file's many "just need an order header to
    exist" setup calls -- see the identical helper (and its full rationale)
    in tests/core/db/_helpers.py."""
    return orders_repository.insert_order(
        conn,
        row=ExtractedRow(order_number=order_number, order_date=order_date, requested_by=requested_by,
                          plate_type="MOTO PARTICULAR"),
        customer_id=None,
        created_by=None,
    )
