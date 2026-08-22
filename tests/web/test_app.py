"""Integration tests for app.web.app via Flask's test_client().

Scope: login_required redirect behavior, the login/logout session lifecycle,
and /healthz. Tests that only need to inspect a redirect's Location header
(not the fully-rendered target page) deliberately avoid the `clean_db`
fixture so they run with zero DB dependency; the couple that assert a real
200 from a DB-backed page (e.g. /ingest, which queries distinct requested_by
/ cities) request `clean_db` explicitly.
"""

from __future__ import annotations

from datetime import date
from urllib.parse import parse_qs, urlparse

from app.core import db
from app.core.models import ExtractedRow


class TestLoginRequired:
    def test_redirects_to_login_with_next(self, client):
        resp = client.get("/ingest")
        assert resp.status_code == 302
        location = urlparse(resp.headers["Location"])
        assert location.path == "/login"
        assert parse_qs(location.query).get("next") == ["/ingest"]

    def test_different_protected_route_also_redirects(self, client):
        resp = client.get("/records")
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/login"

    def test_no_session_root_redirects_to_login(self, client):
        resp = client.get("/")
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/login"

    def test_next_preserves_query_string(self, client):
        """request.full_path always has a trailing '?' when there's no query
        string (stripped by login_required); with a real query string it
        doesn't end in '?', so nothing should be trimmed off it."""
        resp = client.get("/records/5?x=1")
        location = urlparse(resp.headers["Location"])
        assert location.path == "/login"
        assert parse_qs(location.query).get("next") == ["/records/5?x=1"]


class TestLogin:
    def test_valid_credentials_authenticate_without_touching_db(self, client, test_credentials):
        """`/` itself never queries the DB (it just redirects to /ingest) --
        so this proves the session was accepted using only the Location
        header, no DB fixture required."""
        username, password = test_credentials
        resp = client.post("/login", data={"username": username, "password": password})
        assert resp.status_code == 302

        resp2 = client.get("/")
        assert urlparse(resp2.headers["Location"]).path == "/ingest"

    def test_valid_credentials_grant_access_to_a_protected_page(self, client, test_credentials, clean_db):
        username, password = test_credentials
        client.post("/login", data={"username": username, "password": password})
        resp = client.get("/ingest")
        assert resp.status_code == 200

    def test_valid_credentials_redirect_to_safe_next(self, client, test_credentials):
        username, password = test_credentials
        resp = client.post(
            "/login",
            data={"username": username, "password": password, "next": "/inventory"},
        )
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/inventory"

    def test_unsafe_next_is_ignored_on_login(self, client, test_credentials):
        username, password = test_credentials
        resp = client.post(
            "/login",
            data={"username": username, "password": password, "next": "http://evil.com"},
        )
        assert resp.status_code == 302
        location = urlparse(resp.headers["Location"])
        assert location.netloc == ""
        assert location.path == "/ingest"

    def test_wrong_password_does_not_authenticate(self, client, test_credentials):
        username, _ = test_credentials
        resp = client.post("/login", data={"username": username, "password": "wrong-password"})
        assert resp.status_code == 401

        resp2 = client.get("/")
        assert urlparse(resp2.headers["Location"]).path == "/login"

    def test_unknown_username_does_not_authenticate(self, client):
        resp = client.post("/login", data={"username": "ghost", "password": "whatever"})
        assert resp.status_code == 401

    def test_login_stores_admin_role_in_session_for_user_with_no_auth_roles_entry(
        self, client, test_credentials
    ):
        """test_credentials' username has no AUTH_ROLES entry (see
        tests/conftest.py) -- role_for() must default it to admin, and
        login_post must persist that into the session."""
        username, password = test_credentials
        client.post("/login", data={"username": username, "password": password})
        with client.session_transaction() as sess:
            assert sess["role"] == "admin"

    def test_login_stores_configured_role_in_session(self, client, consulta_credentials):
        username, password = consulta_credentials
        client.post("/login", data={"username": username, "password": password})
        with client.session_transaction() as sess:
            assert sess["role"] == "consulta"

    def test_consulta_role_lands_on_records_not_ingest(self, client, consulta_credentials):
        """Regression guard: login (and '/') used to always redirect to
        /ingest regardless of role -- for 'consulta' that route 403s, so the
        person landed straight on an error page right after logging in. The
        default landing page must be role-aware."""
        username, password = consulta_credentials
        resp = client.post("/login", data={"username": username, "password": password})
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/records"

    def test_root_redirects_consulta_role_to_records(self, client, consulta_credentials):
        username, password = consulta_credentials
        client.post("/login", data={"username": username, "password": password})
        resp = client.get("/")
        assert urlparse(resp.headers["Location"]).path == "/records"


class TestLogout:
    def test_logout_clears_session(self, client, test_credentials):
        username, password = test_credentials
        client.post("/login", data={"username": username, "password": password})
        resp = client.get("/")
        assert urlparse(resp.headers["Location"]).path == "/ingest"  # logged in

        client.post("/logout")
        resp2 = client.get("/")
        assert urlparse(resp2.headers["Location"]).path == "/login"  # logged out


class TestHealthz:
    def test_returns_200_without_session(self, client):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.get_json() == {"status": "ok"}


class TestLoginAlreadyAuthenticated:
    def test_get_login_while_logged_in_redirects_to_ingest(self, authenticated_client):
        resp = authenticated_client.get("/login")
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/ingest"


class TestLoginPageRendersWhenNotAuthenticated:
    def test_plain_get_renders_the_login_form(self, client):
        resp = client.get("/login")
        assert resp.status_code == 200
        assert b"<form" in resp.data

    def test_unsafe_next_query_param_is_dropped_from_the_rendered_form(self, client):
        """is_safe_next() is unit-tested directly in test_auth.py; this wires
        the same guard through the actual GET /login route -- an open-redirect
        payload in ?next= must not end up echoed into the page."""
        resp = client.get("/login?next=http://evil.com")
        assert resp.status_code == 200
        assert b"evil.com" not in resp.data


# ----------------------------------------------------------------------------
# Everything below needs both an authenticated client AND a real Postgres
# connection (`db_conn`/`clean_db` -- see tests/conftest.py). `db_conn` and
# the Flask route each open their OWN separate connection (db.get_conn() has
# no injection point), so any setup done through `db_conn` must be committed
# BEFORE the route is hit, or the route's connection won't see it yet (both
# run at the default READ COMMITTED isolation level). Reading state back
# through `db_conn` afterward needs no extra commit: a fresh statement on an
# already-open connection sees whatever the route's own connection already
# committed.
# ----------------------------------------------------------------------------


def make_row(**overrides: object) -> ExtractedRow:
    defaults: dict[str, object] = {
        "order_number": "1",
        "order_date": date(2026, 1, 1),
        "requested_by": "PEDRO",
        "plate_type": "CARRO PARTICULAR",
    }
    defaults.update(overrides)
    return ExtractedRow(**defaults)


class TestReviewRoute:
    def test_renders_flagged_rows(self, authenticated_client, db_conn):
        order_id = db.insert_order(db_conn, order_number="2100", order_date=date(2026, 1, 1),
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
        assert "No hay registros marcados".encode() in resp.data


class TestReviewUpdateRoute:
    def test_updates_plate_clears_flag_and_redirects_to_review(self, authenticated_client, db_conn):
        order_id = db.insert_order(db_conn, order_number="2200", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_number="UPD111", quantity=1),
                         is_possible_duplicate=True)
        with db_conn.cursor() as cur:
            cur.execute("SELECT id FROM plates WHERE plate_number = 'UPD111'")
            plate_id = cur.fetchone()[0]
        db_conn.commit()

        resp = authenticated_client.post("/review/update", data={
            "plate_id": str(plate_id), "plate_type": "MOTO PARTICULAR",
            "plate_number": "UPD222", "quantity": "5",
        })
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/review"

        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT plate_type, plate_number, quantity, is_voided, is_possible_duplicate "
                "FROM plates WHERE id = %s", (plate_id,),
            )
            assert cur.fetchone() == ("MOTO PARTICULAR", "UPD222", 5, False, False)

    def test_is_voided_checkbox_on_marks_voided(self, authenticated_client, db_conn):
        order_id = db.insert_order(db_conn, order_number="2201", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_number="UPD333"),
                         is_possible_duplicate=True)
        with db_conn.cursor() as cur:
            cur.execute("SELECT id FROM plates WHERE plate_number = 'UPD333'")
            plate_id = cur.fetchone()[0]
        db_conn.commit()

        authenticated_client.post("/review/update", data={
            "plate_id": str(plate_id), "plate_type": "CARRO PARTICULAR",
            "plate_number": "UPD333", "quantity": "1", "is_voided": "on",
        })

        with db_conn.cursor() as cur:
            cur.execute("SELECT is_voided FROM plates WHERE id = %s", (plate_id,))
            assert cur.fetchone() == (True,)

    def test_recalculates_inventory_after_quantity_change(self, authenticated_client, db_conn):
        db.insert_blank_preparation(db_conn, prep_date=date(2026, 1, 1), category="CARRO PARTICULAR",
                                     quantity=100, prepared_by=None)
        order_id = db.insert_order(db_conn, order_number="2202", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="UPD444", quantity=5),
                         is_possible_duplicate=True)
        db.recalc_inventory(db_conn)
        with db_conn.cursor() as cur:
            cur.execute("SELECT id FROM plates WHERE plate_number = 'UPD444'")
            plate_id = cur.fetchone()[0]
        db_conn.commit()

        authenticated_client.post("/review/update", data={
            "plate_id": str(plate_id), "plate_type": "CARRO PARTICULAR",
            "plate_number": "UPD444", "quantity": "20",
        })

        with db_conn.cursor() as cur:
            cur.execute("SELECT consumed FROM inventory_categories WHERE category = 'CARRO PARTICULAR'")
            assert cur.fetchone() == (20,)


class TestReviewResolveRoute:
    def test_clears_flag_without_other_changes_and_redirects(self, authenticated_client, db_conn):
        order_id = db.insert_order(db_conn, order_number="2300", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_number="RES111", quantity=3),
                         is_possible_duplicate=True)
        with db_conn.cursor() as cur:
            cur.execute("SELECT id FROM plates WHERE plate_number = 'RES111'")
            plate_id = cur.fetchone()[0]
        db_conn.commit()

        resp = authenticated_client.post("/review/resolve", data={"plate_id": str(plate_id)})
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/review"

        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT plate_number, quantity, is_possible_duplicate FROM plates WHERE id = %s",
                (plate_id,),
            )
            assert cur.fetchone() == ("RES111", 3, False)


class TestReviewDeleteRoute:
    def test_deletes_plate_and_redirects(self, authenticated_client, db_conn):
        order_id = db.insert_order(db_conn, order_number="2400", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="DEL111", quantity=7),
                         is_possible_duplicate=True)
        with db_conn.cursor() as cur:
            cur.execute("SELECT id FROM plates WHERE plate_number = 'DEL111'")
            plate_id = cur.fetchone()[0]
        db_conn.commit()

        resp = authenticated_client.post("/review/delete", data={"plate_id": str(plate_id)})
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/review"

        with db_conn.cursor() as cur:
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
        order_id = db.insert_order(db_conn, order_number="2402", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="DEL222", quantity=7),
                         is_possible_duplicate=True)
        db.recalc_inventory(db_conn)
        with db_conn.cursor() as cur:
            cur.execute("SELECT consumed FROM inventory_categories WHERE category = 'CARRO PARTICULAR'")
            assert cur.fetchone() == (7,)  # sanity check before deletion
            cur.execute("SELECT id FROM plates WHERE plate_number = 'DEL222'")
            plate_id = cur.fetchone()[0]
        db_conn.commit()

        authenticated_client.post("/review/delete", data={"plate_id": str(plate_id)})

        with db_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM plates")
            assert cur.fetchone()[0] == 0  # the plate really is gone
            cur.execute("SELECT consumed FROM inventory_categories WHERE category = 'CARRO PARTICULAR'")
            assert cur.fetchone() == (0,)  # consumed correctly reset

    def test_deleting_one_of_a_flagged_pair_clears_the_surviving_sibling(self, authenticated_client, db_conn):
        """Exercises db.delete_plate's sibling-clearing behavior through the
        actual route, not just the db.py function directly."""
        order_id = db.insert_order(db_conn, order_number="2401", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_number="SIB999"),
                         is_possible_duplicate=True)
        db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_number="SIB999"),
                         is_possible_duplicate=True)
        with db_conn.cursor() as cur:
            cur.execute("SELECT id FROM plates WHERE plate_number = 'SIB999' ORDER BY id")
            first_id, second_id = [r[0] for r in cur.fetchall()]
        db_conn.commit()

        authenticated_client.post("/review/delete", data={"plate_id": str(first_id)})

        with db_conn.cursor() as cur:
            cur.execute("SELECT is_possible_duplicate FROM plates WHERE id = %s", (second_id,))
            assert cur.fetchone() == (False,)


class TestInventoryRoute:
    def test_renders_material_cards_and_ornamental_counter(self, authenticated_client, db_conn):
        db.insert_blank_preparation(db_conn, prep_date=date(2026, 1, 1), category="CARRO PARTICULAR",
                                     quantity=50, prepared_by=None)
        order_id = db.insert_order(db_conn, order_number="2500", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_type="ORNAMENTAL", quantity=3),
                         is_possible_duplicate=False)
        db.recalc_inventory(db_conn)
        db_conn.commit()

        resp = authenticated_client.get("/inventory")
        assert resp.status_code == 200
        assert b"CARRO PARTICULAR" in resp.data
        assert b"ORNAMENTAL" in resp.data

    def test_empty_db_still_renders_200(self, authenticated_client, clean_db):
        resp = authenticated_client.get("/inventory")
        assert resp.status_code == 200
        assert b"ORNAMENTAL" in resp.data


class TestInventoryAlistadoRoute:
    def test_inserts_blank_preparation_recalculates_and_redirects(self, authenticated_client, clean_db):
        resp = authenticated_client.post("/inventory/alistado", data={
            "category": "MOTO PARTICULAR", "quantity": "250",
            "prep_date": "2026-01-01", "prepared_by": "GILMER",
        })
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/inventory"

        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT category, quantity, prepared_by FROM blank_preparations")
            assert cur.fetchall() == [("MOTO PARTICULAR", 250, "GILMER")]
            cur.execute("SELECT prepared FROM inventory_categories WHERE category = 'MOTO PARTICULAR'")
            assert cur.fetchone() == (250,)


class TestRecordsRoute:
    def test_renders_recent_orders(self, authenticated_client, db_conn):
        db.insert_order(db_conn, order_number="2600", order_date=date(2026, 1, 1),
                         requested_by="PEDRO")
        db_conn.commit()

        resp = authenticated_client.get("/records")
        assert resp.status_code == 200
        assert b"2600" in resp.data
        assert b"PEDRO" in resp.data

    def test_empty_db_shows_no_orders_message(self, authenticated_client, clean_db):
        resp = authenticated_client.get("/records")
        assert resp.status_code == 200
        assert "No hay órdenes registradas todavía".encode() in resp.data

    def test_search_query_finds_a_plate_outside_the_recent_orders_page(
        self, authenticated_client, db_conn
    ):
        """The plate lives in an order old enough that recent_orders() alone
        wouldn't necessarily show it -- the search path is a separate query,
        not a client-side filter of the 50 recent rows."""
        order_id = db.insert_order(db_conn, order_number="2601", order_date=date(2020, 1, 1),
                                    requested_by="OLD CLIENT")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="SRC123"),
                         is_possible_duplicate=False)
        db_conn.commit()

        resp = authenticated_client.get("/records?q=SRC123")
        assert resp.status_code == 200
        assert b"SRC123" in resp.data
        assert b"OLD CLIENT" in resp.data
        # the plain recent-orders table must not render alongside search results
        assert "resultado(s) para".encode() in resp.data

    def test_search_with_no_match_shows_a_clear_message(self, authenticated_client, clean_db):
        resp = authenticated_client.get("/records?q=ZZZ999")
        assert resp.status_code == 200
        assert "No se encontró ninguna placa".encode() in resp.data

    def test_blank_query_falls_back_to_recent_orders(self, authenticated_client, db_conn):
        db.insert_order(db_conn, order_number="2602", order_date=date(2026, 1, 1),
                         requested_by="PEDRO")
        db_conn.commit()

        resp = authenticated_client.get("/records?q=")
        assert resp.status_code == 200
        assert b"2602" in resp.data
        assert "resultado(s) para".encode() not in resp.data


class TestRecordDetailRoute:
    def test_existing_order_renders_its_plates(self, authenticated_client, db_conn):
        order_id = db.insert_order(db_conn, order_number="2700", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="DET111", quantity=2),
                         is_possible_duplicate=False)
        db_conn.commit()

        resp = authenticated_client.get(f"/records/{order_id}")
        assert resp.status_code == 200
        assert b"DET111" in resp.data
        assert b"2700" in resp.data

    def test_nonexistent_order_redirects_to_records(self, authenticated_client, clean_db):
        resp = authenticated_client.get("/records/999999")
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/records"

    def test_plates_are_rendered_as_editable_inline_forms(self, authenticated_client, db_conn):
        """Smoke test for the inline-edit table: each plate row must expose a
        hidden plate_id and an editable plate_number input, so the page really
        offers editing rather than just read-only display."""
        order_id = db.insert_order(db_conn, order_number="2701", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="EDT111", quantity=2),
                         is_possible_duplicate=False)
        db_conn.commit()

        resp = authenticated_client.get(f"/records/{order_id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'name="plate_number"' in html
        assert 'name="plate_id"' in html
        assert f'action="/records/{order_id}/save"' in html
        assert 'name="order_number"' in html  # header is editable too
        assert "+ Agregar placa" in html
        assert "Guardar cambios" in html

    def test_consulta_role_sees_a_read_only_table_not_the_edit_form(self, consulta_client, db_conn):
        """Regression guard: /records/<id>/save already 403s for 'consulta'
        (see TestConsultaRole), but the page itself used to render the same
        editable inputs and 'Guardar' button to every role regardless -- so a
        consulta user saw something that looked editable but wasn't, which is
        exactly the confusing behavior reported. No form, no inputs, no save
        button; the plate's data must still be visible as plain text."""
        order_id = db.insert_order(db_conn, order_number="2702", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="ROT111", quantity=2),
                         is_possible_duplicate=False)
        db_conn.commit()

        resp = consulta_client.get(f"/records/{order_id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "ROT111" in html
        assert 'name="plate_number"' not in html
        assert 'name="plate_id"' not in html
        assert "Guardar" not in html
        assert f'action="/records/{order_id}/save"' not in html
        assert "Agregar" not in html
        assert "<form" not in html.split("</nav>")[1]  # no form anywhere past the nav bar


class TestRecordSaveRoute:
    def test_get_and_post_require_login(self, client):
        resp = client.post("/records/1/save", data={})
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/login"

        resp = client.get("/records/1/save")
        assert resp.status_code != 200

    def test_updates_the_order_header(self, authenticated_client, db_conn):
        order_id = db.insert_order(db_conn, order_number="3000", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="SAV100", quantity=1),
                         is_possible_duplicate=False)
        with db_conn.cursor() as cur:
            cur.execute("SELECT id FROM plates WHERE plate_number = 'SAV100'")
            plate_id = cur.fetchone()[0]
        db_conn.commit()

        resp = authenticated_client.post(f"/records/{order_id}/save", data={
            "order_number": "3000-B", "order_date": "2026-02-02", "requested_by": "MARIA",
            "plate_id": [str(plate_id)], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["SAV100"], "plate_to": [""], "city": [""],
            "quantity": ["1"], "is_plate_annulled": [""],
        })
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == f"/records/{order_id}"

        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT order_number, order_date, requested_by FROM production_orders WHERE id = %s",
                (order_id,),
            )
            assert cur.fetchone() == ("3000-B", date(2026, 2, 2), "MARIA")

    def test_edits_an_existing_plate_in_place(self, authenticated_client, db_conn):
        order_id = db.insert_order(db_conn, order_number="3001", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="SAV200", quantity=1),
                         is_possible_duplicate=True)
        with db_conn.cursor() as cur:
            cur.execute("SELECT id FROM plates WHERE plate_number = 'SAV200'")
            plate_id = cur.fetchone()[0]
        db_conn.commit()

        authenticated_client.post(f"/records/{order_id}/save", data={
            "order_number": "3001", "order_date": "2026-01-01", "requested_by": "PEDRO",
            "plate_id": [str(plate_id)], "plate_type": ["MOTO PARTICULAR"],
            "plate_number": ["abc12d"], "plate_to": [""], "city": ["CALI"],
            "quantity": ["7"], "is_plate_annulled": [""],
        })

        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT plate_type, plate_number, city, quantity, is_possible_duplicate "
                "FROM plates WHERE id = %s", (plate_id,),
            )
            assert cur.fetchone() == ("MOTO PARTICULAR", "ABC12D", "CALI", 7, False)
            # still just the one plate under this order -- an update, not a duplicate insert
            cur.execute("SELECT count(*) FROM plates WHERE order_id = %s", (order_id,))
            assert cur.fetchone()[0] == 1

    def test_order_voided_checkbox_applies_to_every_plate_in_the_order(self, authenticated_client, db_conn):
        """order_voided is ONE header-level checkbox, not a per-plate field --
        the 'anulada' word on the schema's plates.is_voided column really
        means 'the whole order was voided', so checking it must flip every
        plate row in this order, including one that keeps a real plate_number
        (the plate was made, then the order got voided after the fact)."""
        order_id = db.insert_order(db_conn, order_number="3002", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="SAV300", quantity=1),
                         is_possible_duplicate=False)
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="SAV301", quantity=1),
                         is_possible_duplicate=False)
        with db_conn.cursor() as cur:
            cur.execute("SELECT id FROM plates WHERE plate_number = 'SAV300'")
            id_300 = cur.fetchone()[0]
            cur.execute("SELECT id FROM plates WHERE plate_number = 'SAV301'")
            id_301 = cur.fetchone()[0]
        db_conn.commit()

        authenticated_client.post(f"/records/{order_id}/save", data={
            "order_number": "3002", "order_date": "2026-01-01", "requested_by": "PEDRO",
            "order_voided": "on",
            "plate_id": [str(id_300), str(id_301)],
            "plate_type": ["CARRO PARTICULAR", "CARRO PARTICULAR"],
            "plate_number": ["SAV300", "SAV301"], "plate_to": ["", ""], "city": ["", ""],
            "quantity": ["1", "1"], "is_plate_annulled": ["", ""],
        })

        with db_conn.cursor() as cur:
            cur.execute("SELECT plate_number, is_voided FROM plates WHERE order_id = %s ORDER BY id",
                        (order_id,))
            assert cur.fetchall() == [("SAV300", True), ("SAV301", True)]

    def test_order_detail_page_reflects_the_current_voided_state(self, authenticated_client, db_conn):
        """The header checkbox must come back checked on reload, computed
        from the order's actual plates -- not just remembered from the form."""
        order_id = db.insert_order(db_conn, order_number="3002b", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="SAV302",
                                      quantity=1, is_voided=True),
                         is_possible_duplicate=False)
        db_conn.commit()

        resp = authenticated_client.get(f"/records/{order_id}")
        html = resp.data.decode()
        assert '<input type="checkbox" name="order_voided" checked>' in html

    def test_adds_a_new_plate_row_in_the_same_submit(self, authenticated_client, db_conn):
        order_id = db.insert_order(db_conn, order_number="3003", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="SAV400", quantity=1),
                         is_possible_duplicate=False)
        with db_conn.cursor() as cur:
            cur.execute("SELECT id FROM plates WHERE plate_number = 'SAV400'")
            existing_id = cur.fetchone()[0]
        db_conn.commit()

        resp = authenticated_client.post(f"/records/{order_id}/save", data={
            "order_number": "3003", "order_date": "2026-01-01", "requested_by": "PEDRO",
            "plate_id": [str(existing_id), ""],
            "plate_type": ["CARRO PARTICULAR", "MOTO PARTICULAR"],
            "plate_number": ["SAV400", "add50d"], "plate_to": ["", ""],
            "city": ["", ""], "quantity": ["1", "3"], "is_plate_annulled": ["", ""],
        })
        assert resp.status_code == 302

        with db_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM plates WHERE order_id = %s", (order_id,))
            assert cur.fetchone()[0] == 2
            cur.execute("SELECT plate_type, quantity, order_id FROM plates WHERE plate_number = 'ADD50D'")
            assert cur.fetchone() == ("MOTO PARTICULAR", 3, order_id)

    def test_removing_a_row_from_the_submit_deletes_it(self, authenticated_client, db_conn):
        """The form has no explicit delete button/route -- removing a row
        client-side (JS) before submitting is what deletes it: its plate_id is
        simply absent from the next save."""
        order_id = db.insert_order(db_conn, order_number="3004", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="SAV500", quantity=1),
                         is_possible_duplicate=False)
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="SAV600", quantity=1),
                         is_possible_duplicate=False)
        with db_conn.cursor() as cur:
            cur.execute("SELECT id FROM plates WHERE plate_number = 'SAV500'")
            keep_id = cur.fetchone()[0]
        db_conn.commit()

        # Only SAV500 is submitted -- SAV600's row was removed in the browser.
        authenticated_client.post(f"/records/{order_id}/save", data={
            "order_number": "3004", "order_date": "2026-01-01", "requested_by": "PEDRO",
            "plate_id": [str(keep_id)], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["SAV500"], "plate_to": [""], "city": [""],
            "quantity": ["1"], "is_plate_annulled": [""],
        })

        with db_conn.cursor() as cur:
            cur.execute("SELECT plate_number FROM plates WHERE order_id = %s", (order_id,))
            assert [r[0] for r in cur.fetchall()] == ["SAV500"]

    def test_removing_every_row_deletes_all_plates_but_keeps_the_order(self, authenticated_client, db_conn):
        order_id = db.insert_order(db_conn, order_number="3005", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="SAV700", quantity=1),
                         is_possible_duplicate=False)
        db_conn.commit()

        resp = authenticated_client.post(f"/records/{order_id}/save", data={
            "order_number": "3005", "order_date": "2026-01-01", "requested_by": "PEDRO",
            "plate_id": [], "plate_type": [], "plate_number": [], "plate_to": [],
            "city": [], "quantity": [], "is_plate_annulled": [],
        })
        assert resp.status_code == 302

        with db_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM plates WHERE order_id = %s", (order_id,))
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT count(*) FROM production_orders WHERE id = %s", (order_id,))
            assert cur.fetchone()[0] == 1  # the order itself survives, just empty

    def test_new_row_is_flagged_a_possible_duplicate_against_a_live_plate(self, authenticated_client, db_conn):
        db.insert_order(db_conn, order_number="3006", order_date=date(2026, 1, 1), requested_by="PEDRO")
        db.insert_plate(
            db_conn,
            order_id=db.insert_order(db_conn, order_number="3007", order_date=date(2026, 1, 1),
                                      requested_by="PEDRO"),
            row=make_row(plate_type="CARRO PARTICULAR", plate_number="SAV800", quantity=1),
            is_possible_duplicate=False,
        )
        other_order_id = db.insert_order(db_conn, order_number="3008", order_date=date(2026, 1, 1),
                                          requested_by="MARIA")
        db_conn.commit()

        authenticated_client.post(f"/records/{other_order_id}/save", data={
            "order_number": "3008", "order_date": "2026-01-01", "requested_by": "MARIA",
            "plate_id": [""], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["sav800"], "plate_to": [""], "city": [""],
            "quantity": ["1"], "is_plate_annulled": [""],
        })

        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT is_possible_duplicate FROM plates WHERE order_id = %s AND plate_number = 'SAV800'",
                (other_order_id,),
            )
            assert cur.fetchone() == (True,)

    def test_recalculates_inventory_after_saving(self, authenticated_client, db_conn):
        db.insert_blank_preparation(db_conn, prep_date=date(2026, 1, 1), category="CARRO PARTICULAR",
                                     quantity=100, prepared_by=None)
        order_id = db.insert_order(db_conn, order_number="3009", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db_conn.commit()

        authenticated_client.post(f"/records/{order_id}/save", data={
            "order_number": "3009", "order_date": "2026-01-01", "requested_by": "PEDRO",
            "plate_id": [""], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["sav900"], "plate_to": [""], "city": [""],
            "quantity": ["15"], "is_plate_annulled": [""],
        })

        with db_conn.cursor() as cur:
            cur.execute("SELECT consumed FROM inventory_categories WHERE category = 'CARRO PARTICULAR'")
            assert cur.fetchone() == (15,)

    def test_invalid_plate_number_rerenders_with_error_and_saves_nothing(self, authenticated_client, db_conn):
        order_id = db.insert_order(db_conn, order_number="3010", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="SAV111", quantity=1),
                         is_possible_duplicate=False)
        db_conn.commit()

        resp = authenticated_client.post(f"/records/{order_id}/save", data={
            "order_number": "SHOULD-NOT-STICK", "order_date": "2026-01-01", "requested_by": "PEDRO",
            "plate_id": [""], "plate_type": ["CARRO PARTICULAR"],
            # CARRO PARTICULAR requires AAA000 -- all-digits doesn't match.
            "plate_number": ["123456"], "plate_to": [""], "city": [""],
            "quantity": ["1"], "is_plate_annulled": [""],
        })
        assert resp.status_code == 400
        assert b"Error" in resp.data

        with db_conn.cursor() as cur:
            # the whole save is one transaction: the header edit didn't stick
            # either, and the original plate is untouched.
            cur.execute("SELECT order_number FROM production_orders WHERE id = %s", (order_id,))
            assert cur.fetchone() == ("3010",)
            cur.execute("SELECT count(*) FROM plates WHERE order_id = %s", (order_id,))
            assert cur.fetchone()[0] == 1

    def test_future_date_is_rejected(self, authenticated_client, db_conn):
        order_id = db.insert_order(db_conn, order_number="3011", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db_conn.commit()

        resp = authenticated_client.post(f"/records/{order_id}/save", data={
            "order_number": "3011", "order_date": "2999-01-01", "requested_by": "PEDRO",
            "plate_id": [], "plate_type": [], "plate_number": [], "plate_to": [],
            "city": [], "quantity": [], "is_plate_annulled": [],
        })
        assert resp.status_code == 400
        assert "futura" in resp.get_data(as_text=True)

    def test_nonexistent_order_redirects_to_records(self, authenticated_client, clean_db):
        resp = authenticated_client.post("/records/999999/save", data={
            "order_number": "X", "order_date": "2026-01-01", "requested_by": "PEDRO",
            "plate_id": [], "plate_type": [], "plate_number": [], "plate_to": [],
            "city": [], "quantity": [], "is_plate_annulled": [],
        })
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/records"



class TestRecordsDeleteRoute:
    def test_deletes_order_and_its_plates_and_redirects(self, authenticated_client, db_conn):
        order_id = db.insert_order(db_conn, order_number="2800", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="RD111", quantity=8),
                         is_possible_duplicate=False)
        db_conn.commit()

        resp = authenticated_client.post("/records/delete", data={"order_id": str(order_id)})
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/records"

        with db_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM production_orders WHERE id = %s", (order_id,))
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT count(*) FROM plates WHERE order_id = %s", (order_id,))
            assert cur.fetchone()[0] == 0

    def test_recalc_still_reflects_a_second_order_that_still_has_plates(self, authenticated_client, db_conn):
        """Complements the review/delete quirk test above: verify recalc DOES
        correctly reflect consumed when the category still has other live
        plates left after the delete (i.e. the stale-value quirk is specific
        to a category dropping to zero plates, not recalc being broken
        outright)."""
        db.insert_blank_preparation(db_conn, prep_date=date(2026, 1, 1), category="CARRO PARTICULAR",
                                     quantity=100, prepared_by=None)
        keep_order = db.insert_order(db_conn, order_number="2801", order_date=date(2026, 1, 1),
                                      requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=keep_order,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="RD200", quantity=3),
                         is_possible_duplicate=False)
        delete_order = db.insert_order(db_conn, order_number="2802", order_date=date(2026, 1, 1),
                                        requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=delete_order,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="RD201", quantity=8),
                         is_possible_duplicate=False)
        db.recalc_inventory(db_conn)
        with db_conn.cursor() as cur:
            cur.execute("SELECT consumed FROM inventory_categories WHERE category = 'CARRO PARTICULAR'")
            assert cur.fetchone() == (11,)  # sanity check before deletion
        db_conn.commit()

        authenticated_client.post("/records/delete", data={"order_id": str(delete_order)})

        with db_conn.cursor() as cur:
            cur.execute("SELECT consumed FROM inventory_categories WHERE category = 'CARRO PARTICULAR'")
            assert cur.fetchone() == (3,)  # correctly dropped to just the surviving order


class TestIngestPostRoute:
    def test_valid_single_plate_submission_inserts_and_shows_summary(self, authenticated_client, clean_db):
        resp = authenticated_client.post("/ingest", data={
            "order_number": "3000", "order_date": "2026-01-01",
            "requested_by": "PEDRO",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["AAA111"], "plate_to": [""], "quantity": ["1"],
            "city": ["BOGOTA"],
        })
        assert resp.status_code == 200
        assert "Registrado".encode() in resp.data

        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT order_number, requested_by FROM production_orders")
            assert cur.fetchall() == [("3000", "PEDRO")]
            cur.execute("SELECT plate_number, plate_type, quantity, city FROM plates")
            assert cur.fetchall() == [("AAA111", "CARRO PARTICULAR", 1, "BOGOTA")]

    def test_two_lines_in_one_order_keep_their_own_cities(self, authenticated_client, clean_db):
        """The regression test for the original bug: city is per plate, so two
        lines of one order must NOT collapse onto the first line's city."""
        resp = authenticated_client.post("/ingest", data={
            "order_number": "3010", "order_date": "2026-01-01",
            "requested_by": "PEDRO",
            "line_type": ["placa", "placa"],
            "plate_type": ["CARRO PARTICULAR", "CARRO PARTICULAR"],
            "plate_number": ["AAA111", "BBB222"], "plate_to": ["", ""],
            "quantity": ["1", "1"],
            "city": ["CALI", "PASTO"],
        })
        assert resp.status_code == 200
        assert b"Registrado" in resp.data

        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM production_orders")
            assert cur.fetchone() == (1,)  # still ONE order
            cur.execute("SELECT plate_number, city FROM plates ORDER BY id")
            assert cur.fetchall() == [("AAA111", "CALI"), ("BBB222", "PASTO")]

    def test_blank_city_on_a_moto_line_is_saved_as_colombia(self, authenticated_client, clean_db):
        """MOTO PARTICULAR is nationwide, so a blank Ciudad means COLOMBIA."""
        authenticated_client.post("/ingest", data={
            "order_number": "3011", "order_date": "2026-01-01",
            "requested_by": "PEDRO",
            "line_type": ["placa", "placa"],
            "plate_type": ["MOTO PARTICULAR", "CARRO PARTICULAR"],
            "plate_number": ["ABC12D", "BBB222"], "plate_to": ["", ""],
            "quantity": ["1", "1"],
            "city": ["", ""],
        })
        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT plate_number, city FROM plates ORDER BY id")
            # the carro line stays blank -- only nationwide types get the default
            assert cur.fetchall() == [("ABC12D", "COLOMBIA"), ("BBB222", None)]

    def test_lines_survive_a_post_that_omits_the_city_field_entirely(
        self, authenticated_client, clean_db
    ):
        """City is optional, so a payload without it must still save the lines
        (the per-line zips would otherwise silently truncate to nothing)."""
        authenticated_client.post("/ingest", data={
            "order_number": "3012", "order_date": "2026-01-01",
            "requested_by": "PEDRO",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["AAA111"], "plate_to": [""], "quantity": ["1"],
        })
        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT plate_number, city FROM plates")
            assert cur.fetchall() == [("AAA111", None)]

    def test_rango_line_type_inserts_desde_hasta(self, authenticated_client, clean_db):
        resp = authenticated_client.post("/ingest", data={
            "order_number": "3001", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["rango"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["AAA100"], "plate_to": ["AAA105"], "quantity": ["6"],
        })
        assert resp.status_code == 200
        assert "Registrado".encode() in resp.data

        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT plate_number, plate_to FROM plates")
            assert cur.fetchall() == [("AAA100", "AAA105")]

    def test_no_plate_lines_shows_error_and_inserts_nothing(self, authenticated_client, clean_db):
        resp = authenticated_client.post("/ingest", data={
            "order_number": "3002", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": [], "plate_type": [], "plate_number": [], "plate_to": [], "quantity": [],
        })
        assert resp.status_code == 200
        assert "Agregá al menos una placa".encode() in resp.data

        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM production_orders")
            assert cur.fetchone()[0] == 0

    def test_requested_by_with_digits_is_rejected(self, authenticated_client, clean_db):
        resp = authenticated_client.post("/ingest", data={
            "order_number": "3003", "order_date": "2026-01-01",
            "requested_by": "PEDRO123", "city": "",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": [""], "plate_to": [""], "quantity": ["1"],
        })
        assert resp.status_code == 200
        assert b"Error" in resp.data

        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM production_orders")
            assert cur.fetchone()[0] == 0

    def test_future_order_date_is_rejected(self, authenticated_client, clean_db):
        resp = authenticated_client.post("/ingest", data={
            "order_number": "3004", "order_date": "2999-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": [""], "plate_to": [""], "quantity": ["1"],
        })
        assert resp.status_code == 200
        assert "futura".encode() in resp.data

        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM production_orders")
            assert cur.fetchone()[0] == 0

    def test_rango_missing_hasta_is_rejected(self, authenticated_client, clean_db):
        resp = authenticated_client.post("/ingest", data={
            "order_number": "3005", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["rango"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["AAA100"], "plate_to": [""], "quantity": ["1"],
        })
        assert resp.status_code == 200
        assert "desde".encode() in resp.data and "hasta".encode() in resp.data

        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM production_orders")
            assert cur.fetchone()[0] == 0

    def test_reposicion_line_type_flags_batch_replacement_with_no_plate_number(
        self, authenticated_client, clean_db
    ):
        resp = authenticated_client.post("/ingest", data={
            "order_number": "3006", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["reposicion"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": [""], "plate_to": [""], "quantity": ["50"],
        })
        assert resp.status_code == 200
        assert "Registrado".encode() in resp.data

        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT plate_number, plate_to, is_batch_replacement, is_voided FROM plates"
            )
            assert cur.fetchall() == [(None, None, True, False)]

    def test_anulada_orden_line_type_flags_voided_with_no_plate_number(
        self, authenticated_client, clean_db
    ):
        resp = authenticated_client.post("/ingest", data={
            "order_number": "3007", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["anulada_orden"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": [""], "plate_to": [""], "quantity": ["1"],
        })
        assert resp.status_code == 200
        assert "Registrado".encode() in resp.data

        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT plate_number, plate_to, is_batch_replacement, is_voided FROM plates"
            )
            assert cur.fetchall() == [(None, None, False, True)]

    def test_muestra_line_type_defaults_plate_number_when_blank(self, authenticated_client, clean_db):
        resp = authenticated_client.post("/ingest", data={
            "order_number": "3008", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["muestra"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": [""], "plate_to": [""], "quantity": ["1"],
        })
        assert resp.status_code == 200
        assert "Registrado".encode() in resp.data

        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT plate_number, plate_to FROM plates")
            assert cur.fetchall() == [("MUESTRA", None)]

    def test_placa_anulada_line_type_flags_plate_annulled_and_validates_format(
        self, authenticated_client, clean_db
    ):
        resp = authenticated_client.post("/ingest", data={
            "order_number": "3009", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["placa_anulada"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["AAA200"], "plate_to": [""], "quantity": ["1"],
        })
        assert resp.status_code == 200
        assert "Registrado".encode() in resp.data

        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT plate_number, plate_to, is_plate_annulled FROM plates")
            assert cur.fetchall() == [("AAA200", None, True)]

    def test_placa_anulada_with_blank_plate_number_skips_validation(self, authenticated_client, clean_db):
        """placa_anulada records a lost blank as waste -- the specific plate
        identifier is sometimes simply unknown, so (unlike 'rango') a blank
        plate_number here is valid and skips validate_plate() entirely."""
        resp = authenticated_client.post("/ingest", data={
            "order_number": "3012", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["placa_anulada"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": [""], "plate_to": [""], "quantity": ["1"],
        })
        assert resp.status_code == 200
        assert "Registrado".encode() in resp.data

        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT plate_number, is_plate_annulled FROM plates")
            assert cur.fetchall() == [(None, True)]

    def test_placa_line_type_with_blank_plate_number_skips_validation(self, authenticated_client, clean_db):
        """The 'placa' (single) branch only calls validate_plate() when a
        plate_number was actually given -- an intentionally blank plate is
        valid (e.g. a line whose plate is genuinely unknown yet)."""
        resp = authenticated_client.post("/ingest", data={
            "order_number": "3010", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": [""], "plate_to": [""], "quantity": ["1"],
        })
        assert resp.status_code == 200
        assert "Registrado".encode() in resp.data

        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT plate_number FROM plates")
            assert cur.fetchall() == [(None,)]

    def test_line_with_blank_plate_type_is_skipped_entirely(self, authenticated_client, clean_db):
        """A row with no plate_type selected (e.g. a leftover empty line in
        the form) is dropped before validation/insert -- only the other,
        properly-filled line in the same submission gets inserted."""
        resp = authenticated_client.post("/ingest", data={
            "order_number": "3011", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["placa", "placa"], "plate_type": ["", "CARRO PARTICULAR"],
            "plate_number": ["", "AAA300"], "plate_to": ["", ""], "quantity": ["1", "1"],
        })
        assert resp.status_code == 200
        assert "Registrado".encode() in resp.data

        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT plate_number FROM plates")
            assert cur.fetchall() == [("AAA300",)]  # only the filled-in line landed


class TestIngestDuplicateWarning:
    def test_plate_already_in_db_blocks_save_and_shows_warning_with_link(
        self, authenticated_client, clean_db
    ):
        authenticated_client.post("/ingest", data={
            "order_number": "4000", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["DUP001"], "plate_to": [""], "quantity": ["1"],
        })
        resp = authenticated_client.post("/ingest", data={
            "order_number": "4001", "order_date": "2026-01-02",
            "requested_by": "MARIA", "city": "",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["DUP001"], "plate_to": [""], "quantity": ["1"],
        })
        assert resp.status_code == 200
        assert "ya existen".encode() in resp.data
        assert b"4000" in resp.data  # points to the order where it already exists
        assert b'value="MARIA"' in resp.data  # typed header data preserved

        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM production_orders WHERE order_number = '4001'")
            assert cur.fetchone()[0] == 0  # second order was NOT saved

    def test_confirm_duplicate_forces_the_save_through(self, authenticated_client, clean_db):
        authenticated_client.post("/ingest", data={
            "order_number": "4002", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["DUP002"], "plate_to": [""], "quantity": ["1"],
        })
        resp = authenticated_client.post("/ingest", data={
            "order_number": "4003", "order_date": "2026-01-02",
            "requested_by": "MARIA", "city": "",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["DUP002"], "plate_to": [""], "quantity": ["1"],
            "confirm_duplicate": "1",
        })
        assert resp.status_code == 200
        assert "Registrado".encode() in resp.data

        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT is_possible_duplicate FROM plates p "
                "JOIN production_orders o ON o.id = p.order_id WHERE o.order_number = '4003'"
            )
            assert cur.fetchall() == [(True,)]  # saved, and still flagged for the review queue

    def test_plate_repeated_within_the_same_submission_blocks_save(
        self, authenticated_client, clean_db
    ):
        resp = authenticated_client.post("/ingest", data={
            "order_number": "4004", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["placa", "placa"], "plate_type": ["CARRO PARTICULAR", "CARRO PARTICULAR"],
            "plate_number": ["DUP003", "DUP003"], "plate_to": ["", ""], "quantity": ["1", "1"],
        })
        assert resp.status_code == 200
        assert "ya existen".encode() in resp.data
        assert "mismo envío".encode() in resp.data  # no prior order to link to, just a same-batch repeat

        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM production_orders WHERE order_number = '4004'")
            assert cur.fetchone()[0] == 0


class TestConsultaRole:
    """The read-only 'consulta' role (see app.web.auth.consulta_allowed) may
    only reach the small consult-only allowlist -- /records and
    /records/<id> -- and must be default-denied (403) everywhere else that
    writes or otherwise isn't a pure lookup. These tests never touch the DB
    for the 403 assertions: the role check in login_required/consulta_allowed
    runs before any view body, so an empty/garbage POST body is enough to
    prove the request never reached the handler.
    """

    def test_records_returns_200(self, consulta_client, db_conn):
        db.insert_order(db_conn, order_number="5000", order_date=date(2026, 1, 1),
                         requested_by="PEDRO")
        db_conn.commit()

        resp = consulta_client.get("/records")
        assert resp.status_code == 200
        assert b"5000" in resp.data

    def test_record_detail_returns_200(self, consulta_client, db_conn):
        order_id = db.insert_order(db_conn, order_number="5001", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="CNS111"),
                         is_possible_duplicate=False)
        db_conn.commit()

        resp = consulta_client.get(f"/records/{order_id}")
        assert resp.status_code == 200
        assert b"CNS111" in resp.data

    def test_recent_orders_list_hides_the_delete_button(self, consulta_client, db_conn):
        """/records/delete already 403s for 'consulta' (see below), but the
        recent-orders list used to show the 'Eliminar' button to every role
        regardless -- same class of bug as the plate-edit form: it looked
        available even though clicking it would just 403."""
        db.insert_order(db_conn, order_number="5002", order_date=date(2026, 1, 1),
                         requested_by="PEDRO")
        db_conn.commit()

        resp = consulta_client.get("/records")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "5002" in html
        assert "Eliminar" not in html
        assert 'action="/records/delete"' not in html

    def test_ingest_get_is_forbidden(self, consulta_client):
        resp = consulta_client.get("/ingest")
        assert resp.status_code == 403

    def test_ingest_post_is_forbidden(self, consulta_client):
        resp = consulta_client.post("/ingest", data={})
        assert resp.status_code == 403

    def test_ingest_pdf_is_forbidden(self, consulta_client):
        resp = consulta_client.get("/ingest/pdf")
        assert resp.status_code == 403

    def test_review_is_forbidden(self, consulta_client):
        resp = consulta_client.get("/review")
        assert resp.status_code == 403

    def test_review_update_is_forbidden(self, consulta_client):
        resp = consulta_client.post("/review/update", data={})
        assert resp.status_code == 403

    def test_inventory_is_forbidden(self, consulta_client):
        resp = consulta_client.get("/inventory")
        assert resp.status_code == 403

    def test_inventory_alistado_is_forbidden(self, consulta_client):
        resp = consulta_client.post("/inventory/alistado", data={})
        assert resp.status_code == 403

    def test_records_delete_is_forbidden(self, consulta_client):
        resp = consulta_client.post("/records/delete", data={})
        assert resp.status_code == 403

    def test_record_save_is_forbidden(self, consulta_client):
        """Reconciliation guard: /records/<id>/save (the consolidated
        edit-order-in-one-shot route) is a write route -- it must stay on the
        default-deny @login_required, not the consult-only allowlist."""
        resp = consulta_client.post("/records/1/save", data={})
        assert resp.status_code == 403

    def test_admin_client_is_unaffected_full_access_regression_guard(
        self, authenticated_client, clean_db
    ):
        """The existing admin-role fixture must still get through everywhere
        -- the role gate must not accidentally narrow full access."""
        assert authenticated_client.get("/ingest").status_code == 200
        assert authenticated_client.get("/review").status_code == 200
        assert authenticated_client.get("/inventory").status_code == 200
        assert authenticated_client.get("/records").status_code == 200
