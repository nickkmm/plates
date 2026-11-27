"""CSRF protection (Flask-WTF CSRFProtect, app/web/csrf.py), tested ON.

Every other test module runs with CSRF off (the `client` fixture in
tests/web/conftest.py); the `csrf_on` fixture here turns it back on and
restores the earlier value on teardown. Tokens are read from a rendered page,
like a browser would.

The contract under test:
- A POST without a valid token answers the Spanish 400 page and the route
  never runs: no row and no session changes.
- The same POST with the token from a rendered page behaves as it does today.
- /ingest/pdf accepts the token in the X-CSRFToken header (its XHR sends it).
- /declaracion checks the token by hand, after raising its own 40 MB cap.
- /logout is POST-only.
- Every posting form in app/web/templates/ carries the token.
"""

from __future__ import annotations

import io
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import pytest
from psycopg.rows import tuple_row

from app.auth import repository
from app.core import db
from app.web.app import app as flask_app
from tests.conftest import TEST_PASSWORD, TEST_USERNAME
from tests.pdf_fixtures import build_pedido_pdf
from tests.web._helpers import _insert_order, make_row
from tests.web.test_declarations import _pending_recent, _status
from tests.web.test_public_declaration import (  # noqa: F401 -- fixtures
    _files,
    _form,
    enabled,
    post,
    stub,
    uploads,
)

CSRF_MESSAGE = "La sesión expiró, recarga la página e intenta de nuevo."
TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "app" / "web" / "templates"
_TOKEN_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


@pytest.fixture
def csrf_on(client):
    previous = flask_app.config["WTF_CSRF_ENABLED"]
    flask_app.config["WTF_CSRF_ENABLED"] = True
    try:
        yield client
    finally:
        flask_app.config["WTF_CSRF_ENABLED"] = previous


def _token(client, path: str) -> str:
    html = client.get(path).get_data(as_text=True)
    match = _TOKEN_RE.search(html)
    assert match is not None, f"no csrf_token on {path}"
    return match.group(1)


def _log_in(client) -> None:
    token = _token(client, "/login")
    resp = client.post("/login", data={"username": TEST_USERNAME, "password": TEST_PASSWORD, "csrf_token": token})
    assert resp.status_code == 302


@pytest.fixture
def staff(csrf_on, test_credentials):
    """The CSRF-on client, logged in as the admin test user (with a token)."""
    _log_in(csrf_on)
    return csrf_on


def _assert_csrf_rejected(resp) -> None:
    assert resp.status_code == 400
    html = resp.get_data(as_text=True)
    assert CSRF_MESSAGE in html
    assert "Salir" not in html
    assert resp.headers["Cache-Control"] == "no-store"


def _logged_in(client) -> bool:
    return urlparse(client.get("/").headers["Location"]).path != "/login"


class TestLogin:
    def test_without_token_is_rejected_and_logs_nobody_in(self, csrf_on, test_credentials):
        resp = csrf_on.post("/login", data={"username": TEST_USERNAME, "password": TEST_PASSWORD})
        _assert_csrf_rejected(resp)
        assert 'href="/"' in resp.get_data(as_text=True)
        with csrf_on.session_transaction() as sess:
            assert "user" not in sess
        resp = csrf_on.get("/records")
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/login"

    def test_with_token_logs_in(self, csrf_on, test_credentials):
        token = _token(csrf_on, "/login")
        resp = csrf_on.post(
            "/login", data={"username": TEST_USERNAME, "password": TEST_PASSWORD, "csrf_token": token},
        )
        assert resp.status_code == 302
        with csrf_on.session_transaction() as sess:
            assert sess["user"] == TEST_USERNAME

    def test_invalid_token_is_rejected(self, csrf_on, test_credentials):
        _token(csrf_on, "/login")
        resp = csrf_on.post(
            "/login", data={"username": TEST_USERNAME, "password": TEST_PASSWORD, "csrf_token": "forged"},
        )
        _assert_csrf_rejected(resp)


class TestLogout:
    def test_get_is_405_and_keeps_the_session(self, staff):
        assert staff.get("/logout").status_code == 405
        assert _logged_in(staff)

    def test_post_without_token_is_rejected_and_keeps_the_session(self, staff):
        _assert_csrf_rejected(staff.post("/logout"))
        assert _logged_in(staff)

    def test_post_with_token_logs_out(self, staff):
        resp = staff.post("/logout", data={"csrf_token": _token(staff, "/records")})
        assert resp.status_code == 302
        assert not _logged_in(staff)


class TestCustomers:
    def test_save(self, staff, db_conn):
        customer_id = db.resolve_or_create_customer(db_conn, "PTO.V", type="EMPRESA")
        db_conn.commit()
        url = f"/customers/{customer_id}"
        data = {"name": "PTO.V", "customer_type": "CLIENTE", "customer_municipality": "CALI"}

        _assert_csrf_rejected(staff.post(url, data=data))
        customer = db.get_customer(db_conn, customer_id)
        assert customer is not None and customer.type == "EMPRESA"

        resp = staff.post(url, data={**data, "csrf_token": _token(staff, url)})
        assert resp.status_code == 302
        customer = db.get_customer(db_conn, customer_id)
        assert customer is not None and customer.type == "CLIENTE"


class TestDeclarations:
    def test_reject(self, staff, db_conn):
        declaration_id = _pending_recent(db_conn)
        url = f"/declaraciones/{declaration_id}/reject"

        _assert_csrf_rejected(staff.post(url, data={"note": "datos falsos"}))
        assert _status(db_conn, declaration_id) == "pending_review"

        token = _token(staff, f"/declaraciones/{declaration_id}")
        resp = staff.post(url, data={"note": "datos falsos", "csrf_token": token})
        assert resp.status_code == 302
        assert _status(db_conn, declaration_id) == "rejected"


def _order_numbers() -> list[str]:
    with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
        cur.execute("SELECT order_number FROM production_orders")
        return [row[0] for row in cur.fetchall()]


class TestIngest:
    _DATA = {
        "payment_method": "OTROS",
        "order_number": "3000", "order_date": "2026-01-01",
        "requested_by": "PEDRO",
        "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
        "plate_number": ["AAA111"], "plate_to": [""], "quantity": ["1"],
        "city": ["BOGOTA"],
    }

    def test_ingest(self, staff):
        _assert_csrf_rejected(staff.post("/ingest", data=self._DATA))
        assert _order_numbers() == []

        resp = staff.post("/ingest", data={**self._DATA, "csrf_token": _token(staff, "/ingest")})
        assert resp.status_code == 200
        assert b"Registrado" in resp.data
        assert _order_numbers() == ["3000"]

    def _upload(self, client, **kwargs):
        return client.post(
            "/ingest/pdf",
            data={"file": (io.BytesIO(build_pedido_pdf()), "pedido.pdf"), **kwargs.pop("data", {})},
            content_type="multipart/form-data",
            **kwargs,
        )

    def test_pdf_without_token_is_rejected(self, staff):
        _assert_csrf_rejected(self._upload(staff))

    def test_pdf_with_the_token_only_in_the_header_is_accepted(self, staff):
        token = _token(staff, "/ingest/pdf")
        resp = self._upload(staff, headers={"X-CSRFToken": token})
        assert resp.status_code == 200
        assert CSRF_MESSAGE not in resp.get_data(as_text=True)

    def test_pdf_with_the_form_field_is_accepted(self, staff):
        token = _token(staff, "/ingest/pdf")
        assert self._upload(staff, data={"csrf_token": token}).status_code == 200


class TestInventory:
    def test_alistado(self, staff):
        data = {"category": "MOTO PARTICULAR", "quantity": "250", "prep_date": "2026-01-01", "prepared_by": "GILMER"}

        def preparations():
            with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
                cur.execute("SELECT category, quantity FROM blank_preparations")
                return cur.fetchall()

        _assert_csrf_rejected(staff.post("/inventory/alistado", data=data))
        assert preparations() == []

        resp = staff.post("/inventory/alistado", data={**data, "csrf_token": _token(staff, "/inventory")})
        assert resp.status_code == 302
        assert preparations() == [("MOTO PARTICULAR", 250)]


class TestPricing:
    def test_update(self, staff, db_conn):
        url = "/precios/CARRO PARTICULAR"

        def price():
            return {r.plate_type: r.unit_price for r in db.price_list(db_conn)}.get("CARRO PARTICULAR")

        _assert_csrf_rejected(staff.post(url, data={"unit_price": "95000"}))
        assert price() is None

        resp = staff.post(url, data={"unit_price": "95000", "csrf_token": _token(staff, "/precios")})
        assert resp.status_code == 302
        assert price() == 95000


class TestRecords:
    def test_delete(self, staff, db_conn):
        order_id = _insert_order(db_conn, order_number="2800", order_date=date(2026, 1, 1), requested_by="PEDRO")
        db_conn.commit()

        _assert_csrf_rejected(staff.post("/records/delete", data={"order_id": str(order_id)}))
        assert _order_numbers() == ["2800"]

        token = _token(staff, "/records")
        resp = staff.post("/records/delete", data={"order_id": str(order_id), "csrf_token": token})
        assert resp.status_code == 302
        assert _order_numbers() == []


class TestReview:
    def test_resolve(self, staff, db_conn):
        order_id = _insert_order(db_conn, order_number="2300", order_date=date(2026, 1, 1), requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_number="RES111", quantity=3),
                         is_possible_duplicate=True)
        db_conn.commit()

        def flagged():
            with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
                cur.execute("SELECT id, is_possible_duplicate FROM plates WHERE plate_number = 'RES111'")
                return cur.fetchone()

        plate_id, _ = flagged()
        _assert_csrf_rejected(staff.post("/review/resolve", data={"plate_id": str(plate_id)}))
        assert flagged() == (plate_id, True)

        token = _token(staff, "/review")
        resp = staff.post("/review/resolve", data={"plate_id": str(plate_id), "csrf_token": token})
        assert resp.status_code == 302
        assert flagged() == (plate_id, False)


class TestUsers:
    def test_create(self, staff, db_conn):
        data = {
            "username": "cajero.nuevo", "roles": ["point_of_sale"],
            "password": "nuevaclave123", "password_confirm": "nuevaclave123",
        }

        _assert_csrf_rejected(staff.post("/usuarios/nuevo", data=data))
        assert repository.get_user_by_username(db_conn, "cajero.nuevo") is None

        resp = staff.post("/usuarios/nuevo", data={**data, "csrf_token": _token(staff, "/usuarios/nuevo")})
        assert resp.status_code == 302
        assert repository.get_user_by_username(db_conn, "cajero.nuevo") is not None


def _big_file(megabytes: int):
    return (io.BytesIO(b"z" * (megabytes * 1024 * 1024)), "relleno.bin")


class TestPublicDeclaration:
    def test_without_token_is_rejected_and_stores_nothing(self, csrf_on, stub, enabled):
        csrf_on.get("/declaracion")
        resp = post(csrf_on, _form())
        _assert_csrf_rejected(resp)
        assert 'href="/declaracion"' in resp.get_data(as_text=True)
        assert stub.validated == [] and stub.submitted == []
        assert _files(enabled) == []

    def test_with_token_is_accepted(self, csrf_on, stub, enabled):
        token = _token(csrf_on, "/declaracion")
        resp = post(csrf_on, _form(csrf_token=token))
        assert resp.status_code == 200
        assert "Recibimos tu declaración" in resp.get_data(as_text=True)
        assert len(stub.submitted) == 1
        assert "csrf_token" not in stub.inp.submitted_data

    def test_a_13_mb_body_with_token_is_not_413(self, csrf_on, stub, enabled):
        token = _token(csrf_on, "/declaracion")
        resp = post(csrf_on, _form(csrf_token=token, junk=_big_file(13)))
        assert resp.status_code == 200
        assert len(stub.submitted) == 1

    def test_an_over_40_mb_body_still_gets_the_friendly_413(self, csrf_on, stub, enabled):
        token = _token(csrf_on, "/declaracion")
        resp = post(csrf_on, _form(csrf_token=token, junk=_big_file(41)))
        assert resp.status_code == 413
        assert "demasiado grandes" in resp.get_data(as_text=True)
        assert stub.submitted == []
        assert _files(enabled) == []


def test_every_posting_form_carries_the_token():
    templates = sorted(TEMPLATES_DIR.glob("*.html"))
    assert templates
    checked = posting = 0
    for path in templates:
        html = path.read_text(encoding="utf-8")
        posting += html.lower().count('method="post"')
        for match in re.finditer(r"<form\b[^>]*>", html):
            if 'method="post"' not in match.group(0).lower():
                continue
            body = html[match.end():html.index("</form>", match.end())]
            assert "csrf_token()" in body, f"{path.name}: a posting form without csrf_token()"
            checked += 1
    # every method="post" belongs to a <form> tag the loop above checked
    assert checked == posting > 0
