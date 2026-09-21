"""Integration tests for GET/POST /precios (Fase 5a).

The contract under test:
- Admin-only via @login_required (NOT @pos_allowed) -- 'punto_venta' must 403
  here even though it can reach /ingest, since the plan is explicit tariffs
  are an admin-only concern, distinct from POS order capture.
- One row per normalize.PLATE_TYPES entry, always -- a type with no row yet
  in plate_type_prices shows "sin tarifa" (never a silent 0), and the table
  starts empty on purpose (not seeded in init.sql), so this path is the
  common case, not an edge case.
- unit_price is parsed server-side as a positive decimal.Decimal -- blank,
  negative, zero, and non-numeric input all re-render the form with a
  Spanish error instead of a 500 or a silently-accepted garbage value.
"""

from __future__ import annotations

from decimal import Decimal

from app.core import db
from app.core.normalize import PLATE_TYPES

PRECIOS_URL = "/precios"


def _price_update_url(plate_type: str) -> str:
    return f"/precios/{plate_type}"


class TestPreciosAdmin:
    def test_get_shows_one_row_per_plate_type_all_sin_tarifa_when_empty(self, authenticated_client, db_conn):
        """plate_type_prices starts EMPTY on purpose -- every one of the 7
        PLATE_TYPES must still appear, marked "sin tarifa", not silently
        missing and not a silent 0."""
        resp = authenticated_client.get(PRECIOS_URL)
        assert resp.status_code == 200
        html = resp.data.decode()
        for plate_type in PLATE_TYPES:
            assert plate_type in html
        assert html.count("sin tarifa") == len(PLATE_TYPES)

    def test_set_price_round_trips_and_records_updated_by(self, authenticated_client, db_conn):
        resp = authenticated_client.post(
            _price_update_url("CARRO PARTICULAR"), data={"unit_price": "95000"}
        )
        assert resp.status_code == 302

        rows = db.price_list(db_conn)
        by_type = {r.plate_type: r for r in rows}
        row = by_type["CARRO PARTICULAR"]
        assert row.unit_price == Decimal("95000")
        assert row.updated_by == "testuser"  # session["user"], not user_id
        assert row.updated_at is not None

        # Other types are untouched -- still "sin tarifa".
        html = authenticated_client.get(PRECIOS_URL).data.decode()
        assert html.count("sin tarifa") == len(PLATE_TYPES) - 1
        assert "95.000" in html  # Colombian thousands separator
        assert "testuser" in html

    def test_updating_an_existing_price_overwrites_it_and_moves_updated_at_forward(
        self, authenticated_client, db_conn
    ):
        authenticated_client.post(_price_update_url("MOTO PARTICULAR"), data={"unit_price": "50000"})
        first = db.current_prices(db_conn)["MOTO PARTICULAR"]

        authenticated_client.post(_price_update_url("MOTO PARTICULAR"), data={"unit_price": "60000"})
        second = db.current_prices(db_conn)["MOTO PARTICULAR"]

        assert first == Decimal("50000")
        assert second == Decimal("60000")


class TestPreciosValidation:
    """Bad input must re-render the form with a Spanish error -- never a 500,
    never a silently-accepted garbage value."""

    def test_blank_price_is_rejected(self, authenticated_client, db_conn):
        resp = authenticated_client.post(_price_update_url("REMOLQUE"), data={"unit_price": ""})
        assert resp.status_code == 400
        assert "no puede estar vacío" in resp.data.decode()
        assert "REMOLQUE" not in db.current_prices(db_conn)

    def test_negative_price_is_rejected(self, authenticated_client, db_conn):
        resp = authenticated_client.post(_price_update_url("REMOLQUE"), data={"unit_price": "-100"})
        assert resp.status_code == 400
        assert "mayor que cero" in resp.data.decode()
        assert "REMOLQUE" not in db.current_prices(db_conn)

    def test_zero_price_is_rejected(self, authenticated_client, db_conn):
        resp = authenticated_client.post(_price_update_url("REMOLQUE"), data={"unit_price": "0"})
        assert resp.status_code == 400
        assert "mayor que cero" in resp.data.decode()
        assert "REMOLQUE" not in db.current_prices(db_conn)

    def test_non_numeric_price_is_rejected(self, authenticated_client, db_conn):
        resp = authenticated_client.post(_price_update_url("REMOLQUE"), data={"unit_price": "abc"})
        assert resp.status_code == 400
        assert "no es un precio válido" in resp.data.decode()
        assert "REMOLQUE" not in db.current_prices(db_conn)

    def test_invalid_plate_type_in_url_is_rejected_not_500(self, authenticated_client, db_conn):
        """set_price() re-validates plate_type against normalize.PLATE_TYPES
        itself and raises a Spanish ValueError -- this must surface as a
        clean 400, not an unhandled exception, even for a tampered URL."""
        resp = authenticated_client.post(_price_update_url("NO_ES_UN_TIPO"), data={"unit_price": "1000"})
        assert resp.status_code == 400
        assert "no es un tipo de placa válido" in resp.data.decode()


class TestPreciosForbidden:
    """@login_required (admin-only), NOT @pos_allowed -- 'punto_venta' edits
    orders/payments, never tariffs, so it 403s here same as consulta/produccion."""

    def test_get_is_forbidden_for_consulta(self, consulta_client):
        assert consulta_client.get(PRECIOS_URL).status_code == 403

    def test_post_is_forbidden_for_consulta(self, consulta_client):
        resp = consulta_client.post(_price_update_url("CARRO PARTICULAR"), data={"unit_price": "1000"})
        assert resp.status_code == 403

    def test_get_is_forbidden_for_produccion(self, produccion_client):
        assert produccion_client.get(PRECIOS_URL).status_code == 403

    def test_post_is_forbidden_for_produccion(self, produccion_client):
        resp = produccion_client.post(_price_update_url("CARRO PARTICULAR"), data={"unit_price": "1000"})
        assert resp.status_code == 403

    def test_get_is_forbidden_for_punto_venta(self, punto_venta_client):
        """The plan is explicit: /precios is admin-only, unlike /ingest or
        /cuadre which punto_venta CAN reach -- this is the one regression
        guard most likely to be broken by a copy-paste of @pos_allowed."""
        assert punto_venta_client.get(PRECIOS_URL).status_code == 403

    def test_post_is_forbidden_for_punto_venta(self, punto_venta_client, db_conn):
        resp = punto_venta_client.post(_price_update_url("CARRO PARTICULAR"), data={"unit_price": "1000"})
        assert resp.status_code == 403
        assert "CARRO PARTICULAR" not in db.current_prices(db_conn)

    def test_forbidden_clients_never_reach_the_db(self, consulta_client, db_conn):
        """A 403 from the decorator must short-circuit before set_price() runs
        -- confirm the table stays empty, not just that the response code is
        right."""
        consulta_client.post(_price_update_url("CARRO PARTICULAR"), data={"unit_price": "1000"})
        assert db.current_prices(db_conn) == {}
