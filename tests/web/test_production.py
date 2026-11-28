"""Integration tests for the /produccion board, its order detail, the
advance / undo actions and the production-status badge on the declaration and
order screens.

Contract under test:
- `production.view` (admin, production, point_of_sale) opens the board, the
  detail and the declaration photos linked to the order; nothing under
  /produccion serves the signature or the notarized PDF.
- `production.manage` (production, admin) makes every step; the point of
  sale (`production.deliver` only) may only mark a ready order delivered and
  undo that delivery -- anything else is 403 and changes nothing.
- a stale `expected_status` re-renders the detail with the Spanish message
  (400) and changes nothing.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

import pytest

from app.declarations import repository as declarations_repository
from app.orders import repository as orders_repository
from app.production import repository as production_repository
from tests.core.test_declarations import _count
from tests.web._helpers import make_row
from tests.web.test_declarations import JPEG, _approved, _attend, _with_photos

BOARD_URL = "/produccion"
ACTION_LABELS = ("Empezar realización", "Marcar lista para entregar", "Marcar entregada",
                 "Deshacer último cambio")


@pytest.fixture
def uploads(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path))
    return tmp_path


def _detail(order_id: int) -> str:
    return f"{BOARD_URL}/{order_id}"


def _order(db_conn, order_number: str = "8001", plate_number: str = "ABC123") -> int:
    """An order with one priced and paid plate (the detail must hide both)."""
    row = make_row(
        order_number=order_number, order_date=date(2026, 9, 3), requested_by="TRANSITOS SAS",
        plate_number=plate_number, unit_price=Decimal("1954000"), payment_method="EFECTIVO",
        receipt_type="FACTURA", receipt_number="R-555",
    )
    order_id = orders_repository.insert_order(db_conn, row=row, customer_id=None, created_by=None)
    orders_repository.insert_plate(db_conn, order_id=order_id, row=row, is_possible_duplicate=False)
    return order_id


def _set_status(db_conn, order_id: int, status: str) -> None:
    current = production_repository.get_production_status(db_conn, order_id)
    assert current is not None
    with db_conn.cursor() as cur:
        cur.execute("UPDATE production_orders SET production_status = %s WHERE id = %s", (status, order_id))


def _counts(html: str) -> dict[str, int]:
    return {
        label: int(n)
        for label, n in re.findall(r">(Por realizar|En realización|Listas para entregar) \((\d+)\)<", html)
    }


def _post(client, order_id: int, action: str, expected_status: str):
    return client.post(f"{_detail(order_id)}/{action}", data={"expected_status": expected_status})


class TestProductionFlow:
    def test_production_moves_an_order_through_every_status_and_undoes_the_last(
        self, produccion_client, db_conn,
    ):
        order_id = _order(db_conn)
        resp = produccion_client.get(BOARD_URL)
        html = resp.data.decode()
        assert resp.status_code == 200
        assert "8001" in html and "TRANSITOS SAS" in html and "prod-pending" in html
        assert "Por realizar" in html
        assert _counts(html) == {"Por realizar": 1, "En realización": 0, "Listas para entregar": 0}

        expected_counts = {
            "pending": {"Por realizar": 0, "En realización": 1, "Listas para entregar": 0},
            "in_progress": {"Por realizar": 0, "En realización": 0, "Listas para entregar": 1},
            "ready": {"Por realizar": 0, "En realización": 0, "Listas para entregar": 0},
        }
        for status, counts in expected_counts.items():
            resp = _post(produccion_client, order_id, "advance", status)
            assert resp.status_code == 302
            assert resp.headers["Location"].endswith(_detail(order_id))
            assert _counts(produccion_client.get(BOARD_URL).data.decode()) == counts
        assert production_repository.get_production_status(db_conn, order_id) == "delivered"

        resp = _post(produccion_client, order_id, "undo", "delivered")
        assert resp.status_code == 302
        assert production_repository.get_production_status(db_conn, order_id) == "ready"

    def test_each_step_is_in_the_history_and_logged(self, produccion_client, db_conn, caplog):
        order_id = _order(db_conn)
        assert "Sin cambios todavía." in produccion_client.get(_detail(order_id)).data.decode()
        with caplog.at_level("INFO"):
            _post(produccion_client, order_id, "advance", "pending")
        assert f"order_id={order_id} pending -> in_progress" in caplog.text
        html = produccion_client.get(_detail(order_id)).data.decode()
        assert "Por realizar → En realización" in html and "produccionuser" in html

    def test_the_detail_offers_the_next_step_for_production(self, produccion_client, db_conn):
        order_id = _order(db_conn)
        html = produccion_client.get(_detail(order_id)).data.decode()
        assert "Empezar realización" in html and "Deshacer último cambio" not in html
        assert 'name="expected_status" value="pending"' in html
        _set_status(db_conn, order_id, "delivered")
        html = produccion_client.get(_detail(order_id)).data.decode()
        assert "Orden entregada." in html and "Marcar entregada" not in html
        assert "Deshacer último cambio" in html and "¿Deshacer el último cambio de estado?" in html

    def test_tabs_filter_by_status_and_keep_the_search(self, produccion_client, db_conn):
        pending = _order(db_conn, "8001", "AAA111")
        ready = _order(db_conn, "8002", "BBB222")
        _set_status(db_conn, ready, "ready")
        html = produccion_client.get(f"{BOARD_URL}?status=ready").data.decode()
        assert f"/produccion/{ready}" in html and f"/produccion/{pending}" not in html
        html = produccion_client.get(f"{BOARD_URL}?status=bogus").data.decode()
        assert f"/produccion/{pending}" in html and f"/produccion/{ready}" not in html
        html = produccion_client.get(f"{BOARD_URL}?status=all&q=BBB222").data.decode()
        assert f"/produccion/{ready}" in html and f"/produccion/{pending}" not in html
        assert "status=pending&amp;q=BBB222" in html
        assert "No hay órdenes en este estado." in produccion_client.get(
            f"{BOARD_URL}?status=delivered").data.decode()

    def test_unknown_order_is_404(self, produccion_client, db_conn):
        assert produccion_client.get(_detail(999999)).status_code == 404
        assert _post(produccion_client, 999999, "advance", "pending").status_code == 404
        assert _post(produccion_client, 999999, "undo", "ready").status_code == 404


class TestPointOfSale:
    @pytest.mark.parametrize("status", ["pending", "in_progress"])
    def test_sees_no_action_and_gets_403_before_ready(self, punto_venta_client, db_conn, status):
        order_id = _order(db_conn)
        _set_status(db_conn, order_id, status)
        assert punto_venta_client.get(BOARD_URL).status_code == 200
        resp = punto_venta_client.get(_detail(order_id))
        assert resp.status_code == 200
        html = resp.data.decode()
        for label in ACTION_LABELS:
            assert label not in html
        events = _count(db_conn, "production_order_events")
        assert _post(punto_venta_client, order_id, "advance", status).status_code == 403
        assert _post(punto_venta_client, order_id, "undo", status).status_code == 403
        assert production_repository.get_production_status(db_conn, order_id) == status
        assert _count(db_conn, "production_order_events") == events

    def test_marks_a_ready_order_delivered_and_undoes_only_that(self, punto_venta_client, db_conn):
        order_id = _order(db_conn)
        _set_status(db_conn, order_id, "ready")
        html = punto_venta_client.get(_detail(order_id)).data.decode()
        assert "Marcar entregada" in html and "Deshacer último cambio" not in html
        assert _post(punto_venta_client, order_id, "advance", "ready").status_code == 302
        assert production_repository.get_production_status(db_conn, order_id) == "delivered"

        html = punto_venta_client.get(_detail(order_id)).data.decode()
        assert "Deshacer último cambio" in html
        assert _post(punto_venta_client, order_id, "undo", "delivered").status_code == 302
        assert production_repository.get_production_status(db_conn, order_id) == "ready"
        assert _post(punto_venta_client, order_id, "undo", "ready").status_code == 403
        assert production_repository.get_production_status(db_conn, order_id) == "ready"


class TestStaleStatus:
    def test_stale_expected_status_is_400_and_changes_nothing(self, produccion_client, db_conn):
        order_id = _order(db_conn)
        events = _count(db_conn, "production_order_events")
        resp = _post(produccion_client, order_id, "advance", "in_progress")
        assert resp.status_code == 400
        assert "El estado de la orden cambió mientras tanto" in resp.data.decode()
        assert production_repository.get_production_status(db_conn, order_id) == "pending"
        assert _count(db_conn, "production_order_events") == events

    def test_undo_of_a_pending_order_is_400(self, produccion_client, db_conn):
        order_id = _order(db_conn)
        resp = _post(produccion_client, order_id, "undo", "pending")
        assert resp.status_code == 400
        assert "La orden todavía no ha empezado." in resp.data.decode()


class TestDetailAndPhotos:
    def test_detail_shows_order_plates_and_declaration_without_prices_or_private_files(
        self, produccion_client, db_conn, uploads,
    ):
        declaration_id, _card_id, _names = _with_photos(db_conn, uploads)
        order_id = _order(db_conn)
        declarations_repository.set_declaration_production_order(db_conn, declaration_id, order_id)
        resp = produccion_client.get(_detail(order_id))
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Orden 8001" in html and "prod-pending" in html
        assert "Datos de la orden" in html and "TRANSITOS SAS" in html
        assert "CARRO PARTICULAR" in html and "ABC123" in html          # plate type, number and material
        assert f"Declaración #{declaration_id}" in html
        assert "MARIA LOPEZ" in html and "1020304050" in html           # declarant name and document
        assert "LIC-001" in html and "CHA1" in html                     # card transit licence and chassis
        assert f"/produccion/{order_id}/declarations/{declaration_id}/file/id_front" in html
        assert "/photo/used_plates" in html
        assert "/file/signature" not in html and "notarized_pdf" not in html
        assert "$1.954.000" not in html and "$" not in html
        assert "EFECTIVO" not in html and "R-555" not in html and "FACTURA" not in html

    def test_photo_routes_serve_only_a_declaration_linked_to_the_order(
        self, produccion_client, db_conn, uploads,
    ):
        declaration_id, card_id, _ = _with_photos(db_conn, uploads)
        other_id, other_card_id, _ = _with_photos(db_conn, uploads)
        order_id = _order(db_conn, "8001")
        other_order_id = _order(db_conn, "8002", "XYZ987")
        declarations_repository.set_declaration_production_order(db_conn, declaration_id, order_id)
        declarations_repository.set_declaration_production_order(db_conn, other_id, other_order_id)
        base = f"{_detail(order_id)}/declarations/{declaration_id}"

        for url in (f"{base}/file/id_front", f"{base}/cards/{card_id}/photo/front"):
            resp = produccion_client.get(url)
            assert (resp.status_code, resp.mimetype, resp.data) == (200, "image/jpeg", JPEG)
            assert resp.headers["Cache-Control"] == "no-store"
            assert resp.headers["X-Content-Type-Options"] == "nosniff"

        other = f"{_detail(order_id)}/declarations/{other_id}"
        assert produccion_client.get(f"{other}/file/id_front").status_code == 404
        assert produccion_client.get(f"{other}/cards/{other_card_id}/photo/front").status_code == 404
        assert produccion_client.get(f"{base}/cards/{other_card_id}/photo/front").status_code == 404
        assert produccion_client.get(f"{base}/cards/{card_id}/photo/side").status_code == 404
        for kind in ("signature", "notarized_pdf", "bogus"):
            assert produccion_client.get(f"{base}/file/{kind}").status_code == 404

    def test_a_missing_photo_file_is_404(self, produccion_client, db_conn, uploads):
        declaration_id, _, names = _with_photos(db_conn, uploads)
        order_id = _order(db_conn)
        declarations_repository.set_declaration_production_order(db_conn, declaration_id, order_id)
        (uploads / names["id_front"]).unlink()
        url = f"{_detail(order_id)}/declarations/{declaration_id}/file/id_front"
        assert produccion_client.get(url).status_code == 404


class TestBadges:
    def _attended_in_progress(self, db_conn) -> tuple[int, int]:
        declaration_id = _approved(db_conn)
        order_id = _attend(db_conn, declaration_id)
        assert production_repository.set_production_status(
            db_conn, order_id, from_status="pending", to_status="in_progress", user_id=None,
        )
        return declaration_id, order_id

    def test_declarations_list_and_detail_show_the_production_status(self, authenticated_client, db_conn):
        declaration_id, _ = self._attended_in_progress(db_conn)
        html = authenticated_client.get("/declaraciones?stage=attended").data.decode()
        assert "En realización" in html and "prod-in_progress" in html
        html = authenticated_client.get(f"/declaraciones/{declaration_id}").data.decode()
        assert "Estado en producción:" in html and "En realización" in html and "prod-in_progress" in html

    def test_declarations_list_reads_the_statuses_in_one_query(self, authenticated_client, db_conn, monkeypatch):
        self._attended_in_progress(db_conn)
        second = _approved(db_conn, name="BETO ATENDIDO", plate="BBB222")
        _attend(db_conn, second, "7002")
        calls = []
        original = production_repository.production_statuses_for_orders

        def spy(conn, order_ids):
            calls.append(set(order_ids))
            return original(conn, order_ids)

        monkeypatch.setattr(production_repository, "production_statuses_for_orders", spy)
        html = authenticated_client.get("/declaraciones?stage=attended").data.decode()
        assert len(calls) == 1 and len(calls[0]) == 2
        assert "prod-in_progress" in html and "prod-pending" in html

    def test_record_detail_shows_the_status_and_links_to_the_board(self, authenticated_client, db_conn):
        _, order_id = self._attended_in_progress(db_conn)
        html = authenticated_client.get(f"/records/{order_id}").data.decode()
        assert "Estado en producción:" in html and "En realización" in html
        assert f'href="/produccion/{order_id}"' in html and "Ver en Producción" in html

    def test_record_detail_read_only_view_shows_the_status(self, produccion_client, db_conn):
        _, order_id = self._attended_in_progress(db_conn)
        html = produccion_client.get(f"/records/{order_id}").data.decode()
        assert "Estado en producción:" in html and "prod-in_progress" in html
        assert f'href="/produccion/{order_id}"' in html
