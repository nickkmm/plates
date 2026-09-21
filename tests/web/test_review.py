"""Integration tests for app/web/blueprints/review.py -- GET /review (the
flagged-plates queue) and its three actions: POST /review/update,
/review/resolve, /review/delete.

These need both an authenticated client and a real Postgres connection
(`db_conn`/`clean_db`, see tests/conftest.py). `db_conn` and the Flask
route each check out their OWN separate connection from the pool (Fase
1d: db.get_conn() is pooled, but still hands out a distinct
connection/session per checkout -- pooling only reuses the underlying
sockets, it doesn't merge separate `with db.get_conn() as conn:` blocks
into one), so any setup done through `db_conn` must be committed BEFORE
the route is hit, or the route's connection won't see it yet (both run
at the default READ COMMITTED isolation level). Reading state back
through `db_conn` afterward needs no extra commit: a fresh statement on
an already-open connection sees whatever the route's own connection
already committed."""

from __future__ import annotations

from datetime import date
from urllib.parse import urlparse

from psycopg.rows import tuple_row

from app.core import db
from tests.web._helpers import _insert_order, make_row


class TestReviewRoute:
    def test_renders_flagged_rows(self, authenticated_client, db_conn):
        order_id = _insert_order(db_conn, order_number="2100", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_number="REV111"),
                         is_possible_duplicate=True)
        db_conn.commit()

        resp = authenticated_client.get("/review")
        assert resp.status_code == 200
        assert b"REV111" in resp.data

    def test_empty_queue_shows_no_records_message(self, authenticated_client, clean_db):
        resp = authenticated_client.get("/review")
        assert resp.status_code == 200
        assert b"No hay registros marcados" in resp.data



class TestReviewUpdateRoute:
    def test_updates_plate_clears_flag_and_redirects_to_review(self, authenticated_client, db_conn):
        order_id = _insert_order(db_conn, order_number="2200", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_number="UPD111", quantity=1),
                         is_possible_duplicate=True)
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT id FROM plates WHERE plate_number = 'UPD111'")
            plate_id = cur.fetchone()[0]
        db_conn.commit()

        resp = authenticated_client.post("/review/update", data={
            "plate_id": str(plate_id), "plate_type": "MOTO PARTICULAR",
            "plate_number": "UPD222", "quantity": "5",
        })
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/review"

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                "SELECT plate_type, plate_number, quantity, is_voided, is_possible_duplicate "
                "FROM plates WHERE id = %s", (plate_id,),
            )
            assert cur.fetchone() == ("MOTO PARTICULAR", "UPD222", 5, False, False)

    def test_is_voided_checkbox_on_marks_voided(self, authenticated_client, db_conn):
        order_id = _insert_order(db_conn, order_number="2201", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_number="UPD333"),
                         is_possible_duplicate=True)
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT id FROM plates WHERE plate_number = 'UPD333'")
            plate_id = cur.fetchone()[0]
        db_conn.commit()

        authenticated_client.post("/review/update", data={
            "plate_id": str(plate_id), "plate_type": "CARRO PARTICULAR",
            "plate_number": "UPD333", "quantity": "1", "is_voided": "on",
        })

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT is_voided FROM plates WHERE id = %s", (plate_id,))
            assert cur.fetchone() == (True,)

    def test_recalculates_inventory_after_quantity_change(self, authenticated_client, db_conn):
        db.insert_blank_preparation(db_conn, prep_date=date(2026, 1, 1), category="CARRO PARTICULAR",
                                     quantity=100, prepared_by=None)
        order_id = _insert_order(db_conn, order_number="2202", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="UPD444", quantity=5),
                         is_possible_duplicate=True)
        db.recalc_inventory(db_conn)
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT id FROM plates WHERE plate_number = 'UPD444'")
            plate_id = cur.fetchone()[0]
        db_conn.commit()

        authenticated_client.post("/review/update", data={
            "plate_id": str(plate_id), "plate_type": "CARRO PARTICULAR",
            "plate_number": "UPD444", "quantity": "20",
        })

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT consumed FROM inventory_categories WHERE category = 'CARRO PARTICULAR'")
            assert cur.fetchone() == (20,)



class TestReviewResolveRoute:
    def test_clears_flag_without_other_changes_and_redirects(self, authenticated_client, db_conn):
        order_id = _insert_order(db_conn, order_number="2300", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_number="RES111", quantity=3),
                         is_possible_duplicate=True)
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT id FROM plates WHERE plate_number = 'RES111'")
            plate_id = cur.fetchone()[0]
        db_conn.commit()

        resp = authenticated_client.post("/review/resolve", data={"plate_id": str(plate_id)})
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/review"

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                "SELECT plate_number, quantity, is_possible_duplicate FROM plates WHERE id = %s",
                (plate_id,),
            )
            assert cur.fetchone() == ("RES111", 3, False)



class TestReviewDeleteRoute:
    def test_deletes_plate_and_redirects(self, authenticated_client, db_conn):
        order_id = _insert_order(db_conn, order_number="2400", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="DEL111", quantity=7),
                         is_possible_duplicate=True)
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT id FROM plates WHERE plate_number = 'DEL111'")
            plate_id = cur.fetchone()[0]
        db_conn.commit()

        resp = authenticated_client.post("/review/delete", data={"plate_id": str(plate_id)})
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/review"

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM plates WHERE id = %s", (plate_id,))
            assert cur.fetchone()[0] == 0

    def test_recalc_resets_consumed_when_a_category_loses_its_last_plate(
        self, authenticated_client, db_conn
    ):
        """db.recalc_inventory's consumed-sum iterates the fixed set of known
        materials (INVENTORY_CATEGORIES), not just whatever GROUP BY plate_type
        happens to return -- so a category that drops to zero live plates gets
        consumed explicitly reset to 0, instead of being skipped and left
        stale. A category with other still-live plates continues to
        recalculate correctly (unaffected by this fix)."""
        db.insert_blank_preparation(db_conn, prep_date=date(2026, 1, 1), category="CARRO PARTICULAR",
                                     quantity=100, prepared_by=None)
        order_id = _insert_order(db_conn, order_number="2402", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="DEL222", quantity=7),
                         is_possible_duplicate=True)
        db.recalc_inventory(db_conn)
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT consumed FROM inventory_categories WHERE category = 'CARRO PARTICULAR'")
            assert cur.fetchone() == (7,)  # sanity check before deletion
            cur.execute("SELECT id FROM plates WHERE plate_number = 'DEL222'")
            plate_id = cur.fetchone()[0]
        db_conn.commit()

        authenticated_client.post("/review/delete", data={"plate_id": str(plate_id)})

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM plates")
            assert cur.fetchone()[0] == 0  # the plate really is gone
            cur.execute("SELECT consumed FROM inventory_categories WHERE category = 'CARRO PARTICULAR'")
            assert cur.fetchone() == (0,)  # consumed correctly reset

    def test_deleting_one_of_a_flagged_pair_clears_the_surviving_sibling(self, authenticated_client, db_conn):
        """Exercises db.delete_plate's sibling-clearing behavior through the
        actual route, not just the db.py function directly."""
        order_id = _insert_order(db_conn, order_number="2401", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_number="SIB999"),
                         is_possible_duplicate=True)
        db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_number="SIB999"),
                         is_possible_duplicate=True)
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT id FROM plates WHERE plate_number = 'SIB999' ORDER BY id")
            first_id, second_id = [r[0] for r in cur.fetchall()]
        db_conn.commit()

        authenticated_client.post("/review/delete", data={"plate_id": str(first_id)})

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT is_possible_duplicate FROM plates WHERE id = %s", (second_id,))
            assert cur.fetchone() == (False,)

