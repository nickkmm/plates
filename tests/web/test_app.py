"""Integration tests for app.web.app via Flask's test_client().

Scope: login_required redirect behavior, the login/logout session lifecycle,
and /healthz. Tests that only need to inspect a redirect's Location header
(not the fully-rendered target page) deliberately avoid the `clean_db`
fixture so they run with zero DB dependency; the couple that assert a real
200 from a DB-backed page (e.g. /ingest, which queries distinct requested_by
/ cities) request `clean_db` explicitly.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

from psycopg.rows import tuple_row

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
    def test_valid_credentials_authenticate_and_reach_ingest(self, client, test_credentials):
        """Fase 2: logging in now DOES touch the DB (it queries `users`), via
        the `test_credentials` fixture's own `clean_db` dependency -- this
        used to be DB-free (AUTH_USERS was an env var). `/` itself still
        never queries the DB on its own (it just redirects to /ingest), so
        this proves the session was accepted using only the Location
        header."""
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

    def test_successful_login_is_logged(self, client, test_credentials, caplog):
        """A successful login must produce an INFO-level activity log line
        (see app.web.app's app.logger.setLevel(logging.INFO) -- without it
        this is silently dropped by Flask's default WARNING-level handler),
        the only way to see "who logged in when" outside the DB."""
        username, password = test_credentials
        with caplog.at_level(logging.INFO):
            client.post("/login", data={"username": username, "password": password})
        assert any(
            "login" in r.message and username in r.message and "admin" in r.message
            for r in caplog.records
        )

    def test_failed_login_is_not_logged_as_a_login(self, client, clean_db, caplog):
        with caplog.at_level(logging.INFO):
            client.post("/login", data={"username": "ghost", "password": "whatever"})
        assert not any(r.message.startswith("login:") for r in caplog.records)

    def test_unknown_username_does_not_authenticate(self, client, clean_db):
        """Fase 2: authenticate() queries `users` for this username even
        though it doesn't exist -- needs a real (reachable) DB, hence
        `clean_db` (this test has no credentials fixture to inherit it
        from)."""
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

    def test_login_stores_configured_role_in_session(self, client, produccion_credentials):
        username, password = produccion_credentials
        client.post("/login", data={"username": username, "password": password})
        with client.session_transaction() as sess:
            assert sess["role"] == "production"

    def test_production_role_lands_on_records_not_ingest(self, client, produccion_credentials):
        """Regression guard: login (and '/') used to always redirect to
        /ingest regardless of role -- for 'production' that route 403s, so the
        person landed straight on an error page right after logging in. The
        default landing page must be role-aware."""
        username, password = produccion_credentials
        resp = client.post("/login", data={"username": username, "password": password})
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/records"

    def test_root_redirects_production_role_to_records(self, client, produccion_credentials):
        username, password = produccion_credentials
        client.post("/login", data={"username": username, "password": password})
        resp = client.get("/")
        assert urlparse(resp.headers["Location"]).path == "/records"


class TestStaleSessionPredatingRoles:
    """Regression guard for a real production incident (2026-08-22): a session
    cookie created before roles existed has 'user' but no 'role' key. Both
    decorators used to treat that as "logged in, role is None, None isn't a
    full-access role" -> a dead-end 403 with no way to recover short of
    manually clearing cookies. It must instead be treated like "not logged
    in" -- bounce to /login so a fresh login (which does set 'role') fixes
    it themselves."""

    def test_full_access_route_redirects_to_login_instead_of_403(self, client):
        with client.session_transaction() as sess:
            sess["user"] = "someone"  # no "role" key -- simulates a pre-roles cookie
        resp = client.get("/ingest")
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/login"

    def test_consulta_allowed_route_also_redirects_to_login(self, client):
        with client.session_transaction() as sess:
            sess["user"] = "someone"
        resp = client.get("/records")
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/login"

    def test_the_stale_session_is_cleared(self, client):
        with client.session_transaction() as sess:
            sess["user"] = "someone"
        client.get("/ingest")
        with client.session_transaction() as sess:
            assert "user" not in sess

    def test_a_fresh_login_recovers_full_access(self, client, test_credentials):
        """The whole point: logging out/in again (not clearing cookies by
        hand) is enough to fix it, since a real login always sets 'role'."""
        with client.session_transaction() as sess:
            sess["user"] = "someone"
        client.get("/ingest")  # bounced, session cleared

        username, password = test_credentials
        client.post("/login", data={"username": username, "password": password})
        resp = client.get("/ingest")
        assert resp.status_code == 200


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
# the Flask route each check out their OWN separate connection from the pool
# (Fase 1d: db.get_conn() is pooled, but still hands out a distinct
# connection/session per checkout -- pooling only reuses the underlying
# sockets, it doesn't merge separate `with db.get_conn() as conn:` blocks
# into one), so any setup done through `db_conn` must be committed BEFORE the
# route is hit, or the route's connection won't see it yet (both run at the
# default READ COMMITTED isolation level). Reading state back through
# `db_conn` afterward needs no extra commit: a fresh statement on an
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


def _insert_order(conn, *, order_number: str, order_date, requested_by: str) -> int:
    """Back-compat shim for this file's many "just need an order header to
    exist" setup calls -- see the identical helper (and its full rationale)
    in tests/core/test_db.py."""
    return db.insert_order(
        conn,
        row=ExtractedRow(order_number=order_number, order_date=order_date, requested_by=requested_by,
                          plate_type="MOTO PARTICULAR"),
        customer_id=None,
        created_by=None,
    )


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


class TestInventoryRoute:
    def test_renders_material_cards_and_ornamental_counter(self, authenticated_client, db_conn):
        db.insert_blank_preparation(db_conn, prep_date=date(2026, 1, 1), category="CARRO PARTICULAR",
                                     quantity=50, prepared_by=None)
        order_id = _insert_order(db_conn, order_number="2500", order_date=date(2026, 1, 1),
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

        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT category, quantity, prepared_by FROM blank_preparations")
            assert cur.fetchall() == [("MOTO PARTICULAR", 250, "GILMER")]
            cur.execute("SELECT prepared FROM inventory_categories WHERE category = 'MOTO PARTICULAR'")
            assert cur.fetchone() == (250,)


class TestRecordsRoute:
    def test_renders_recent_orders(self, authenticated_client, db_conn):
        _insert_order(db_conn, order_number="2600", order_date=date(2026, 1, 1),
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
        order_id = _insert_order(db_conn, order_number="2601", order_date=date(2020, 1, 1),
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
        assert b"resultado(s) para" in resp.data

    def test_search_with_no_match_shows_a_clear_message(self, authenticated_client, clean_db):
        resp = authenticated_client.get("/records?q=ZZZ999")
        assert resp.status_code == 200
        assert "No se encontró ninguna placa".encode() in resp.data

    def test_search_by_order_number_finds_the_order(self, authenticated_client, db_conn):
        """A query that matches an order_number (not any plate_number) must
        still surface that order, via the new order_matches section."""
        order_id = _insert_order(db_conn, order_number="OLD-2603", order_date=date(2019, 1, 1),
                                    requested_by="OLD CLIENT 2")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="NOMATCH1"),
                         is_possible_duplicate=False)
        db_conn.commit()

        resp = authenticated_client.get("/records?q=OLD-2603")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "OLD-2603" in html
        assert "OLD CLIENT 2" in html
        assert "Coincidencias por número de orden" in html
        # the query doesn't match any plate_number, so no plate-results section
        assert "resultado(s) para" not in html

    def test_order_number_search_does_not_affect_plate_search_results(
        self, authenticated_client, db_conn
    ):
        """Regression guard: adding order-number search must not change the
        existing plate-search behavior when a query only matches a plate."""
        order_id = _insert_order(db_conn, order_number="2604", order_date=date(2020, 1, 1),
                                    requested_by="OLD CLIENT 3")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="PLT999"),
                         is_possible_duplicate=False)
        db_conn.commit()

        resp = authenticated_client.get("/records?q=PLT999")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "PLT999" in html
        assert "resultado(s) para" in html
        # PLT999 doesn't match the order_number "2604", so no order section
        assert "Coincidencias por número de orden" not in html

    def test_query_matching_both_a_plate_and_an_order_number_shows_both_sections(
        self, authenticated_client, db_conn
    ):
        order_id = _insert_order(db_conn, order_number="2605", order_date=date(2021, 1, 1),
                                    requested_by="BOTH CLIENT")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="ABC2605"),
                         is_possible_duplicate=False)
        db_conn.commit()

        resp = authenticated_client.get("/records?q=2605")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Coincidencias por número de orden" in html
        assert "resultado(s) para" in html

    def test_blank_query_falls_back_to_recent_orders(self, authenticated_client, db_conn):
        _insert_order(db_conn, order_number="2602", order_date=date(2026, 1, 1),
                         requested_by="PEDRO")
        db_conn.commit()

        resp = authenticated_client.get("/records?q=")
        assert resp.status_code == 200
        assert b"2602" in resp.data
        assert b"resultado(s) para" not in resp.data

    def test_plate_search_result_ver_link_carries_the_query_along(
        self, authenticated_client, db_conn
    ):
        """So record_detail's 'Volver a Registros' can send the person back to
        this same search instead of always landing on the unfiltered list."""
        order_id = _insert_order(db_conn, order_number="2606", order_date=date(2021, 1, 1),
                                    requested_by="LINK CLIENT")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="LNK606"),
                         is_possible_duplicate=False)
        db_conn.commit()

        resp = authenticated_client.get("/records?q=LNK606")
        assert f"/records/{order_id}?q=LNK606" in resp.data.decode()

    def test_order_search_result_ver_link_carries_the_query_along(
        self, authenticated_client, db_conn
    ):
        order_id = _insert_order(db_conn, order_number="OLD-2607", order_date=date(2019, 1, 1),
                                    requested_by="OLD CLIENT 4")
        db_conn.commit()

        resp = authenticated_client.get("/records?q=OLD-2607")
        assert f"/records/{order_id}?q=OLD-2607" in resp.data.decode()

    def test_recent_orders_ver_link_has_no_query(self, authenticated_client, db_conn):
        """Regression guard: the default (unfiltered) list has no search to
        return to, so its 'Ver' link must stay a plain record_detail URL."""
        order_id = _insert_order(db_conn, order_number="2608", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db_conn.commit()

        resp = authenticated_client.get("/records")
        assert f'href="/records/{order_id}"' in resp.data.decode()


class TestRecordDetailRoute:
    def test_existing_order_renders_its_plates(self, authenticated_client, db_conn):
        order_id = _insert_order(db_conn, order_number="2700", order_date=date(2026, 1, 1),
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

    def test_back_link_preserves_the_originating_search_query(self, authenticated_client, db_conn):
        """Reached via a Registros search's 'Ver' link (?q=... passed through)
        -- 'Volver a Registros' must return to that same search, not the bare
        unfiltered list."""
        order_id = _insert_order(db_conn, order_number="2701", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db_conn.commit()

        resp = authenticated_client.get(f"/records/{order_id}?q=PEDRO")
        assert "/records?q=PEDRO" in resp.data.decode()

    def test_back_link_has_no_query_when_reached_without_one(self, authenticated_client, db_conn):
        order_id = _insert_order(db_conn, order_number="2702", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db_conn.commit()

        resp = authenticated_client.get(f"/records/{order_id}")
        assert 'href="/records"' in resp.data.decode()

    def test_plates_are_rendered_as_editable_inline_forms(self, authenticated_client, db_conn):
        """Smoke test for the inline-edit table: each plate row must expose a
        hidden plate_id and an editable plate_number input, so the page really
        offers editing rather than just read-only display."""
        order_id = _insert_order(db_conn, order_number="2701", order_date=date(2026, 1, 1),
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


class TestRecordSaveRoute:
    def test_get_and_post_require_login(self, client):
        resp = client.post("/records/1/save", data={})
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/login"

        resp = client.get("/records/1/save")
        assert resp.status_code != 200

    def test_updates_the_order_header(self, authenticated_client, db_conn):
        order_id = _insert_order(db_conn, order_number="3000", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="SAV100", quantity=1),
                         is_possible_duplicate=False)
        with db_conn.cursor(row_factory=tuple_row) as cur:
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

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                "SELECT order_number, order_date, requested_by FROM production_orders WHERE id = %s",
                (order_id,),
            )
            assert cur.fetchone() == ("3000-B", date(2026, 2, 2), "MARIA")

    def test_successful_save_is_logged(self, authenticated_client, db_conn, caplog):
        order_id = _insert_order(db_conn, order_number="3002", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="SAV300", quantity=1),
                         is_possible_duplicate=False)
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT id FROM plates WHERE plate_number = 'SAV300'")
            plate_id = cur.fetchone()[0]
        db_conn.commit()

        with caplog.at_level(logging.INFO):
            authenticated_client.post(f"/records/{order_id}/save", data={
                "order_number": "3002", "order_date": "2026-01-01", "requested_by": "PEDRO",
                "plate_id": [str(plate_id)], "plate_type": ["CARRO PARTICULAR"],
                "plate_number": ["SAV300"], "plate_to": [""], "city": [""],
                "quantity": ["1"], "is_plate_annulled": [""],
            })
        assert any(
            r.message == f"record saved: user=testuser order_id={order_id} order_number=3002"
            for r in caplog.records
        )

    def test_edits_an_existing_plate_in_place(self, authenticated_client, db_conn):
        order_id = _insert_order(db_conn, order_number="3001", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="SAV200", quantity=1),
                         is_possible_duplicate=True)
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT id FROM plates WHERE plate_number = 'SAV200'")
            plate_id = cur.fetchone()[0]
        db_conn.commit()

        authenticated_client.post(f"/records/{order_id}/save", data={
            "order_number": "3001", "order_date": "2026-01-01", "requested_by": "PEDRO",
            "plate_id": [str(plate_id)], "plate_type": ["MOTO PARTICULAR"],
            "plate_number": ["abc12d"], "plate_to": [""], "city": ["CALI"],
            "quantity": ["7"], "is_plate_annulled": [""],
        })

        with db_conn.cursor(row_factory=tuple_row) as cur:
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
        order_id = _insert_order(db_conn, order_number="3002", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="SAV300", quantity=1),
                         is_possible_duplicate=False)
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="SAV301", quantity=1),
                         is_possible_duplicate=False)
        with db_conn.cursor(row_factory=tuple_row) as cur:
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

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT plate_number, is_voided FROM plates WHERE order_id = %s ORDER BY id",
                        (order_id,))
            assert cur.fetchall() == [("SAV300", True), ("SAV301", True)]

    def test_order_detail_page_reflects_the_current_voided_state(self, authenticated_client, db_conn):
        """The header checkbox must come back checked on reload, computed
        from the order's actual plates -- not just remembered from the form."""
        order_id = _insert_order(db_conn, order_number="3002b", order_date=date(2026, 1, 1),
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
        order_id = _insert_order(db_conn, order_number="3003", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="SAV400", quantity=1),
                         is_possible_duplicate=False)
        with db_conn.cursor(row_factory=tuple_row) as cur:
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

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM plates WHERE order_id = %s", (order_id,))
            assert cur.fetchone()[0] == 2
            cur.execute("SELECT plate_type, quantity, order_id FROM plates WHERE plate_number = 'ADD50D'")
            assert cur.fetchone() == ("MOTO PARTICULAR", 3, order_id)

    def test_removing_a_row_from_the_submit_deletes_it(self, authenticated_client, db_conn):
        """The form has no explicit delete button/route -- removing a row
        client-side (JS) before submitting is what deletes it: its plate_id is
        simply absent from the next save."""
        order_id = _insert_order(db_conn, order_number="3004", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="SAV500", quantity=1),
                         is_possible_duplicate=False)
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="SAV600", quantity=1),
                         is_possible_duplicate=False)
        with db_conn.cursor(row_factory=tuple_row) as cur:
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

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT plate_number FROM plates WHERE order_id = %s", (order_id,))
            assert [r[0] for r in cur.fetchall()] == ["SAV500"]

    def test_removing_every_row_deletes_all_plates_but_keeps_the_order(self, authenticated_client, db_conn):
        order_id = _insert_order(db_conn, order_number="3005", order_date=date(2026, 1, 1),
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

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM plates WHERE order_id = %s", (order_id,))
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT count(*) FROM production_orders WHERE id = %s", (order_id,))
            assert cur.fetchone()[0] == 1  # the order itself survives, just empty

    def test_new_row_is_flagged_a_possible_duplicate_against_a_live_plate(self, authenticated_client, db_conn):
        _insert_order(db_conn, order_number="3006", order_date=date(2026, 1, 1), requested_by="PEDRO")
        db.insert_plate(
            db_conn,
            order_id=_insert_order(db_conn, order_number="3007", order_date=date(2026, 1, 1),
                                      requested_by="PEDRO"),
            row=make_row(plate_type="CARRO PARTICULAR", plate_number="SAV800", quantity=1),
            is_possible_duplicate=False,
        )
        other_order_id = _insert_order(db_conn, order_number="3008", order_date=date(2026, 1, 1),
                                          requested_by="MARIA")
        db_conn.commit()

        authenticated_client.post(f"/records/{other_order_id}/save", data={
            "order_number": "3008", "order_date": "2026-01-01", "requested_by": "MARIA",
            "plate_id": [""], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["sav800"], "plate_to": [""], "city": [""],
            "quantity": ["1"], "is_plate_annulled": [""],
        })

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                "SELECT is_possible_duplicate FROM plates WHERE order_id = %s AND plate_number = 'SAV800'",
                (other_order_id,),
            )
            assert cur.fetchone() == (True,)

    def test_recalculates_inventory_after_saving(self, authenticated_client, db_conn):
        db.insert_blank_preparation(db_conn, prep_date=date(2026, 1, 1), category="CARRO PARTICULAR",
                                     quantity=100, prepared_by=None)
        order_id = _insert_order(db_conn, order_number="3009", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db_conn.commit()

        authenticated_client.post(f"/records/{order_id}/save", data={
            "order_number": "3009", "order_date": "2026-01-01", "requested_by": "PEDRO",
            "plate_id": [""], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["sav900"], "plate_to": [""], "city": [""],
            "quantity": ["15"], "is_plate_annulled": [""],
        })

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT consumed FROM inventory_categories WHERE category = 'CARRO PARTICULAR'")
            assert cur.fetchone() == (15,)

    def test_invalid_plate_number_rerenders_with_error_and_saves_nothing(self, authenticated_client, db_conn):
        order_id = _insert_order(db_conn, order_number="3010", order_date=date(2026, 1, 1),
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

        with db_conn.cursor(row_factory=tuple_row) as cur:
            # the whole save is one transaction: the header edit didn't stick
            # either, and the original plate is untouched.
            cur.execute("SELECT order_number FROM production_orders WHERE id = %s", (order_id,))
            assert cur.fetchone() == ("3010",)
            cur.execute("SELECT count(*) FROM plates WHERE order_id = %s", (order_id,))
            assert cur.fetchone()[0] == 1

    def test_future_date_is_rejected(self, authenticated_client, db_conn):
        order_id = _insert_order(db_conn, order_number="3011", order_date=date(2026, 1, 1),
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


class TestRecordSavePosFields:
    """Fase 3 parity: record_save re-resolves customer_id from a possibly-
    edited requested_by (per the plan's B5, only WHICH customer it points to
    -- never that customer's own stored attributes), and writes the 10
    order-level POS fields + per-line reason/vehicle_brand."""

    def test_renaming_requested_by_resolves_and_points_to_a_new_customer(
        self, authenticated_client, db_conn
    ):
        order_id = _insert_order(db_conn, order_number="3100", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db_conn.commit()

        authenticated_client.post(f"/records/{order_id}/save", data={
            "order_number": "3100", "order_date": "2026-01-01", "requested_by": "MARIA NUEVA",
            "plate_id": [], "plate_type": [], "plate_number": [], "plate_to": [],
            "city": [], "quantity": [], "is_plate_annulled": [],
        })

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT customer_id, requested_by FROM production_orders WHERE id = %s",
                        (order_id,))
            customer_id, requested_by = cur.fetchone()
            assert requested_by == "MARIA NUEVA"
            cur.execute("SELECT name FROM customers WHERE id = %s", (customer_id,))
            assert cur.fetchone() == ("MARIA NUEVA",)

    def test_never_rewrites_an_existing_customers_own_attributes(self, authenticated_client, db_conn):
        """B5: record_save may only re-point customer_id, never edit the
        customer's own stored data -- there is no form field for it at all."""
        with db.get_conn() as conn:
            db.resolve_or_create_customer(conn, "PEDRO", type="EMPRESA", phone="555")
            conn.commit()
        order_id = _insert_order(db_conn, order_number="3101", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db_conn.commit()

        authenticated_client.post(f"/records/{order_id}/save", data={
            "order_number": "3101", "order_date": "2026-01-02", "requested_by": "PEDRO",
            "plate_id": [], "plate_type": [], "plate_number": [], "plate_to": [],
            "city": [], "quantity": [], "is_plate_annulled": [],
        })

        with db.get_conn() as conn:
            customer = db.get_customer_by_name(conn, "PEDRO")
        assert customer is not None
        assert customer.type == "EMPRESA"
        assert customer.phone == "555"

    def test_saves_order_level_pos_fields(self, authenticated_client, db_conn):
        order_id = _insert_order(db_conn, order_number="3102", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db_conn.commit()

        authenticated_client.post(f"/records/{order_id}/save", data={
            "order_number": "3102", "order_date": "2026-01-01", "requested_by": "PEDRO",
            "receipt_type": "RECIBO", "receipt_number": "R-9", "payment_method": "CRÉDITO",
            "tracking_number": "G-9", "client_order_number": "P-9",
            "delivery_date": "2026-01-15", "payment_date": "2026-01-16",
            "dispatched_by": "CARLOS", "received_by": "SOFIA", "notes": "nota",
            "plate_id": [], "plate_type": [], "plate_number": [], "plate_to": [],
            "city": [], "quantity": [], "is_plate_annulled": [],
        })

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                "SELECT receipt_type, receipt_number, payment_method, tracking_number, "
                "client_order_number, dispatched_by, received_by, notes "
                "FROM production_orders WHERE id = %s",
                (order_id,),
            )
            assert cur.fetchone() == (
                "RECIBO", "R-9", "CRÉDITO", "G-9", "P-9", "CARLOS", "SOFIA", "nota",
            )

    def test_edits_reason_and_vehicle_brand_on_an_existing_plate(self, authenticated_client, db_conn):
        order_id = _insert_order(db_conn, order_number="3103", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="RVB100"),
                         is_possible_duplicate=False)
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT id FROM plates WHERE plate_number = 'RVB100'")
            plate_id = cur.fetchone()[0]
        db_conn.commit()

        authenticated_client.post(f"/records/{order_id}/save", data={
            "order_number": "3103", "order_date": "2026-01-01", "requested_by": "PEDRO",
            "plate_id": [str(plate_id)], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["RVB100"], "plate_to": [""], "city": [""],
            "quantity": ["1"], "is_plate_annulled": [""],
            "reason": ["PÉRDIDA"], "vehicle_brand": ["FORD"], "unit_price": [""],
        })

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT reason, vehicle_brand FROM plates WHERE id = %s", (plate_id,))
            assert cur.fetchone() == ("PÉRDIDA", "FORD")

    def test_existing_plates_unit_price_cannot_be_overwritten_via_the_form(
        self, authenticated_client, db_conn
    ):
        """unit_price for an already-saved plate is never a caller-supplied
        value -- update_plate_full has no such parameter, so even a POST that
        carries a tampered unit_price for an existing row is ignored; only
        the re-snapshot rule (plate_type changed or not) governs it."""
        order_id = _insert_order(db_conn, order_number="3104", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="NOP100",
                                      unit_price=Decimal("150000")),
                         is_possible_duplicate=False)
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT id FROM plates WHERE plate_number = 'NOP100'")
            plate_id = cur.fetchone()[0]
        db_conn.commit()

        authenticated_client.post(f"/records/{order_id}/save", data={
            "order_number": "3104", "order_date": "2026-01-01", "requested_by": "PEDRO",
            "plate_id": [str(plate_id)], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["NOP100"], "plate_to": [""], "city": [""],
            "quantity": ["1"], "is_plate_annulled": [""],
            "unit_price": ["999999"],  # tampered -- must be ignored
        })

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT unit_price FROM plates WHERE id = %s", (plate_id,))
            assert cur.fetchone() == (Decimal("150000"),)  # untouched, type didn't change

    def test_new_plate_row_falls_back_to_the_current_tariff_when_left_blank(
        self, authenticated_client, db_conn
    ):
        order_id = _insert_order(db_conn, order_number="3105", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.set_price(db_conn, "MOTO PARTICULAR", Decimal("90000"), "admin")
        db_conn.commit()

        authenticated_client.post(f"/records/{order_id}/save", data={
            "order_number": "3105", "order_date": "2026-01-01", "requested_by": "PEDRO",
            "plate_id": [""], "plate_type": ["MOTO PARTICULAR"],
            "plate_number": ["NEW90D"], "plate_to": [""], "city": [""],
            "quantity": ["1"], "is_plate_annulled": [""], "unit_price": [""],
        })

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT unit_price FROM plates WHERE plate_number = 'NEW90D'")
            assert cur.fetchone() == (Decimal("90000"),)


class TestRecordsDeleteRoute:
    def test_deletes_order_and_its_plates_and_redirects(self, authenticated_client, db_conn):
        order_id = _insert_order(db_conn, order_number="2800", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="RD111", quantity=8),
                         is_possible_duplicate=False)
        db_conn.commit()

        resp = authenticated_client.post("/records/delete", data={"order_id": str(order_id)})
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/records"

        with db_conn.cursor(row_factory=tuple_row) as cur:
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
        keep_order = _insert_order(db_conn, order_number="2801", order_date=date(2026, 1, 1),
                                      requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=keep_order,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="RD200", quantity=3),
                         is_possible_duplicate=False)
        delete_order = _insert_order(db_conn, order_number="2802", order_date=date(2026, 1, 1),
                                        requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=delete_order,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="RD201", quantity=8),
                         is_possible_duplicate=False)
        db.recalc_inventory(db_conn)
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT consumed FROM inventory_categories WHERE category = 'CARRO PARTICULAR'")
            assert cur.fetchone() == (11,)  # sanity check before deletion
        db_conn.commit()

        authenticated_client.post("/records/delete", data={"order_id": str(delete_order)})

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT consumed FROM inventory_categories WHERE category = 'CARRO PARTICULAR'")
            assert cur.fetchone() == (3,)  # correctly dropped to just the surviving order


class TestIngestPostRoute:
    def test_valid_single_plate_submission_inserts_and_shows_summary(self, authenticated_client, clean_db):
        resp = authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "3000", "order_date": "2026-01-01",
            "requested_by": "PEDRO",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["AAA111"], "plate_to": [""], "quantity": ["1"],
            "city": ["BOGOTA"],
        })
        assert resp.status_code == 200
        assert b"Registrado" in resp.data

        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT order_number, requested_by FROM production_orders")
            assert cur.fetchall() == [("3000", "PEDRO")]
            cur.execute("SELECT plate_number, plate_type, quantity, city FROM plates")
            assert cur.fetchall() == [("AAA111", "CARRO PARTICULAR", 1, "BOGOTA")]

    def test_successful_submission_is_logged(self, authenticated_client, clean_db, caplog):
        with caplog.at_level(logging.INFO):
            authenticated_client.post("/ingest", data={
                "payment_method": "OTROS",
                "order_number": "3001", "order_date": "2026-01-01",
                "requested_by": "PEDRO",
                "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
                "plate_number": ["AAA112"], "plate_to": [""], "quantity": ["1"],
                "city": ["BOGOTA"],
            })
        assert any(
            r.message == "order created: user=testuser order_number=3001 plates=1"
            for r in caplog.records
        )

    def test_two_lines_in_one_order_keep_their_own_cities(self, authenticated_client, clean_db):
        """The regression test for the original bug: city is per plate, so two
        lines of one order must NOT collapse onto the first line's city."""
        resp = authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
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

        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM production_orders")
            assert cur.fetchone() == (1,)  # still ONE order
            cur.execute("SELECT plate_number, city FROM plates ORDER BY id")
            assert cur.fetchall() == [("AAA111", "CALI"), ("BBB222", "PASTO")]

    def test_blank_city_on_a_moto_line_is_saved_as_colombia(self, authenticated_client, clean_db):
        """MOTO PARTICULAR is nationwide, so a blank Ciudad means COLOMBIA."""
        authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "3011", "order_date": "2026-01-01",
            "requested_by": "PEDRO",
            "line_type": ["placa", "placa"],
            "plate_type": ["MOTO PARTICULAR", "CARRO PARTICULAR"],
            "plate_number": ["ABC12D", "BBB222"], "plate_to": ["", ""],
            "quantity": ["1", "1"],
            "city": ["", ""],
        })
        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT plate_number, city FROM plates ORDER BY id")
            # the carro line stays blank -- only nationwide types get the default
            assert cur.fetchall() == [("ABC12D", "COLOMBIA"), ("BBB222", None)]

    def test_lines_survive_a_post_that_omits_the_city_field_entirely(
        self, authenticated_client, clean_db
    ):
        """City is optional, so a payload without it must still save the lines
        (the per-line zips would otherwise silently truncate to nothing)."""
        authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "3012", "order_date": "2026-01-01",
            "requested_by": "PEDRO",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["AAA111"], "plate_to": [""], "quantity": ["1"],
        })
        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT plate_number, city FROM plates")
            assert cur.fetchall() == [("AAA111", None)]

    def test_rango_line_type_inserts_desde_hasta(self, authenticated_client, clean_db):
        resp = authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "3001", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["rango"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["AAA100"], "plate_to": ["AAA105"], "quantity": ["6"],
        })
        assert resp.status_code == 200
        assert b"Registrado" in resp.data

        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT plate_number, plate_to FROM plates")
            assert cur.fetchall() == [("AAA100", "AAA105")]

    def test_no_plate_lines_shows_error_and_inserts_nothing(self, authenticated_client, clean_db):
        resp = authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "3002", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": [], "plate_type": [], "plate_number": [], "plate_to": [], "quantity": [],
        })
        assert resp.status_code == 200
        assert "Agregá al menos una placa".encode() in resp.data

        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM production_orders")
            assert cur.fetchone()[0] == 0

    def test_requested_by_with_digits_is_rejected(self, authenticated_client, clean_db):
        resp = authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "3003", "order_date": "2026-01-01",
            "requested_by": "PEDRO123", "city": "",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": [""], "plate_to": [""], "quantity": ["1"],
        })
        assert resp.status_code == 200
        assert b"Error" in resp.data

        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM production_orders")
            assert cur.fetchone()[0] == 0

    def test_future_order_date_is_rejected(self, authenticated_client, clean_db):
        resp = authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "3004", "order_date": "2999-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": [""], "plate_to": [""], "quantity": ["1"],
        })
        assert resp.status_code == 200
        assert b"futura" in resp.data

        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM production_orders")
            assert cur.fetchone()[0] == 0

    def test_rango_missing_hasta_is_rejected(self, authenticated_client, clean_db):
        resp = authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "3005", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["rango"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["AAA100"], "plate_to": [""], "quantity": ["1"],
        })
        assert resp.status_code == 200
        assert b"desde" in resp.data and b"hasta" in resp.data

        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM production_orders")
            assert cur.fetchone()[0] == 0

    def test_reposicion_line_type_flags_batch_replacement_with_no_plate_number(
        self, authenticated_client, clean_db
    ):
        resp = authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "3006", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["reposicion"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": [""], "plate_to": [""], "quantity": ["50"],
        })
        assert resp.status_code == 200
        assert b"Registrado" in resp.data

        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                "SELECT plate_number, plate_to, is_batch_replacement, is_voided FROM plates"
            )
            assert cur.fetchall() == [(None, None, True, False)]

    def test_anulada_orden_line_type_flags_voided_with_no_plate_number(
        self, authenticated_client, clean_db
    ):
        resp = authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "3007", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["anulada_orden"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": [""], "plate_to": [""], "quantity": ["1"],
        })
        assert resp.status_code == 200
        assert b"Registrado" in resp.data

        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                "SELECT plate_number, plate_to, is_batch_replacement, is_voided FROM plates"
            )
            assert cur.fetchall() == [(None, None, False, True)]

    def test_muestra_line_type_defaults_plate_number_when_blank(self, authenticated_client, clean_db):
        resp = authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "3008", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["muestra"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": [""], "plate_to": [""], "quantity": ["1"],
        })
        assert resp.status_code == 200
        assert b"Registrado" in resp.data

        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT plate_number, plate_to FROM plates")
            assert cur.fetchall() == [("MUESTRA", None)]

    def test_placa_anulada_line_type_flags_plate_annulled_and_validates_format(
        self, authenticated_client, clean_db
    ):
        resp = authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "3009", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["placa_anulada"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["AAA200"], "plate_to": [""], "quantity": ["1"],
        })
        assert resp.status_code == 200
        assert b"Registrado" in resp.data

        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT plate_number, plate_to, is_plate_annulled FROM plates")
            assert cur.fetchall() == [("AAA200", None, True)]

    def test_placa_anulada_with_blank_plate_number_skips_validation(self, authenticated_client, clean_db):
        """placa_anulada records a lost blank as waste -- the specific plate
        identifier is sometimes simply unknown, so (unlike 'rango') a blank
        plate_number here is valid and skips validate_plate() entirely."""
        resp = authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "3012", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["placa_anulada"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": [""], "plate_to": [""], "quantity": ["1"],
        })
        assert resp.status_code == 200
        assert b"Registrado" in resp.data

        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT plate_number, is_plate_annulled FROM plates")
            assert cur.fetchall() == [(None, True)]

    def test_placa_line_type_with_blank_plate_number_skips_validation(self, authenticated_client, clean_db):
        """The 'placa' (single) branch only calls validate_plate() when a
        plate_number was actually given -- an intentionally blank plate is
        valid (e.g. a line whose plate is genuinely unknown yet)."""
        resp = authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "3010", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": [""], "plate_to": [""], "quantity": ["1"],
        })
        assert resp.status_code == 200
        assert b"Registrado" in resp.data

        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT plate_number FROM plates")
            assert cur.fetchall() == [(None,)]

    def test_line_with_blank_plate_type_is_skipped_entirely(self, authenticated_client, clean_db):
        """A row with no plate_type selected (e.g. a leftover empty line in
        the form) is dropped before validation/insert -- only the other,
        properly-filled line in the same submission gets inserted."""
        resp = authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "3011", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["placa", "placa"], "plate_type": ["", "CARRO PARTICULAR"],
            "plate_number": ["", "AAA300"], "plate_to": ["", ""], "quantity": ["1", "1"],
        })
        assert resp.status_code == 200
        assert b"Registrado" in resp.data

        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT plate_number FROM plates")
            assert cur.fetchall() == [("AAA300",)]  # only the filled-in line landed


class TestIngestDuplicateWarning:
    def test_plate_already_in_db_blocks_save_and_shows_warning_with_link(
        self, authenticated_client, clean_db
    ):
        authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "4000", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["DUP001"], "plate_to": [""], "quantity": ["1"],
        })
        resp = authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "4001", "order_date": "2026-01-02",
            "requested_by": "MARIA", "city": "",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["DUP001"], "plate_to": [""], "quantity": ["1"],
        })
        assert resp.status_code == 200
        assert b"ya existen" in resp.data
        assert b"4000" in resp.data  # points to the order where it already exists
        assert b'value="MARIA"' in resp.data  # typed header data preserved

        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM production_orders WHERE order_number = '4001'")
            assert cur.fetchone()[0] == 0  # second order was NOT saved

    def test_confirm_duplicate_forces_the_save_through(self, authenticated_client, clean_db):
        authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "4002", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["DUP002"], "plate_to": [""], "quantity": ["1"],
        })
        resp = authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "4003", "order_date": "2026-01-02",
            "requested_by": "MARIA", "city": "",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["DUP002"], "plate_to": [""], "quantity": ["1"],
            "confirm_duplicate": "1",
        })
        assert resp.status_code == 200
        assert b"Registrado" in resp.data

        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                "SELECT is_possible_duplicate FROM plates p "
                "JOIN production_orders o ON o.id = p.order_id WHERE o.order_number = '4003'"
            )
            assert cur.fetchall() == [(True,)]  # saved, and still flagged for the review queue

    def test_plate_repeated_within_the_same_submission_blocks_save(
        self, authenticated_client, clean_db
    ):
        resp = authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "4004", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["placa", "placa"], "plate_type": ["CARRO PARTICULAR", "CARRO PARTICULAR"],
            "plate_number": ["DUP003", "DUP003"], "plate_to": ["", ""], "quantity": ["1", "1"],
        })
        assert resp.status_code == 200
        assert b"ya existen" in resp.data
        assert "mismo envío".encode() in resp.data  # no prior order to link to, just a same-batch repeat

        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM production_orders WHERE order_number = '4004'")
            assert cur.fetchone()[0] == 0


class TestIngestOrderDuplicateWarning:
    """The order-number collision check (db.find_orders_by_number) runs
    BEFORE the plate-number dup check in ingest_post -- a reposición
    placeholder order has plate_number=NULL, so it would otherwise sail
    straight through the plate check and silently create a second
    production_orders row under the same order_number (see
    app.core.ingestion.replace_order's docstring for the real WCY449/pedido
    1030 case)."""

    def test_colliding_order_number_blocks_save_and_shows_warning(
        self, authenticated_client, clean_db
    ):
        authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "5000", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["reposicion"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": [""], "plate_to": [""], "quantity": ["50"],
        })
        resp = authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "5000", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["WCY449"], "plate_to": [""], "quantity": ["1"],
        })
        assert resp.status_code == 200
        assert b"ya existe" in resp.data
        assert b'value="PEDRO"' in resp.data  # typed header data preserved

        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM production_orders WHERE order_number = '5000'")
            assert cur.fetchone()[0] == 1  # still just the original placeholder order
            cur.execute(
                "SELECT count(*) FROM plates p JOIN production_orders o ON o.id = p.order_id "
                "WHERE o.order_number = '5000'"
            )
            assert cur.fetchone()[0] == 1  # the new plate line was NOT saved

    def test_confirm_order_duplicate_saves_as_a_genuinely_new_second_order(
        self, authenticated_client, clean_db
    ):
        """Today's (bad, but explicitly chosen) behavior, fully preserved:
        confirming creates a SECOND production_orders row sharing the number."""
        authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "5001", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["reposicion"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": [""], "plate_to": [""], "quantity": ["50"],
        })
        resp = authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "5001", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["WCY450"], "plate_to": [""], "quantity": ["1"],
            "confirm_order_duplicate": "1",
        })
        assert resp.status_code == 200
        assert b"Registrado" in resp.data

        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM production_orders WHERE order_number = '5001'")
            assert cur.fetchone()[0] == 2  # two orders now share this number
            cur.execute(
                "SELECT count(*) FROM plates p JOIN production_orders o ON o.id = p.order_id "
                "WHERE o.order_number = '5001'"
            )
            assert cur.fetchone()[0] == 2  # both the old reposición row and the new plate exist

    def test_replace_order_fuses_a_single_existing_order_into_one(
        self, authenticated_client, clean_db
    ):
        authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "5002", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["reposicion"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": [""], "plate_to": [""], "quantity": ["50"],
        })
        resp = authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "5002", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["WCY451"], "plate_to": [""], "quantity": ["1"],
            "replace_order": "1",
        })
        assert resp.status_code == 200
        assert b"Registrado" in resp.data

        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM production_orders WHERE order_number = '5002'")
            assert cur.fetchone()[0] == 1  # exactly one order, not two
            cur.execute(
                "SELECT p.plate_number FROM plates p JOIN production_orders o ON o.id = p.order_id "
                "WHERE o.order_number = '5002'"
            )
            assert cur.fetchall() == [("WCY451",)]  # only the newly submitted plate remains

    def test_replace_order_fuses_two_pre_existing_colliding_orders_into_one(
        self, authenticated_client, db_conn
    ):
        """Proves the fuse behavior when the collision is already 2 orders
        deep (order_number has no unique constraint, so this really happens
        -- see WCY449/pedido 1030)."""
        first_id = _insert_order(db_conn, order_number="5003", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=first_id,
                         row=make_row(order_number="5003", plate_type="CARRO PARTICULAR",
                                      is_batch_replacement=True, quantity=30),
                         is_possible_duplicate=False)
        second_id = _insert_order(db_conn, order_number="5003", order_date=date(2026, 1, 2),
                                     requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=second_id,
                         row=make_row(order_number="5003", plate_type="CARRO PARTICULAR",
                                      plate_number="OLD999"),
                         is_possible_duplicate=False)
        db_conn.commit()

        resp = authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "5003", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["placa", "placa"],
            "plate_type": ["CARRO PARTICULAR", "CARRO PARTICULAR"],
            "plate_number": ["NEW100", "NEW101"], "plate_to": ["", ""], "quantity": ["1", "1"],
            "replace_order": "1",
        })
        assert resp.status_code == 200
        assert b"Registrado" in resp.data

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM production_orders WHERE order_number = '5003'")
            assert cur.fetchone()[0] == 1  # both pre-existing orders fused into one
            cur.execute(
                "SELECT p.plate_number FROM plates p JOIN production_orders o ON o.id = p.order_id "
                "WHERE o.order_number = '5003' ORDER BY p.plate_number"
            )
            assert cur.fetchall() == [("NEW100",), ("NEW101",)]
            cur.execute("SELECT count(*) FROM plates WHERE plate_number = 'OLD999'")
            assert cur.fetchone()[0] == 0  # the old plate from the second fused order is gone too

    def test_no_collision_path_is_completely_unaffected(self, authenticated_client, clean_db):
        """Regression coverage: an ordinary, no-collision /ingest submission
        must save immediately, with no order-duplicate warning at all."""
        resp = authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "5004", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["FRE599"], "plate_to": [""], "quantity": ["1"],
        })
        assert resp.status_code == 200
        assert b"Registrado" in resp.data
        assert b"ya existe" not in resp.data

        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM production_orders WHERE order_number = '5004'")
            assert cur.fetchone()[0] == 1
            cur.execute("SELECT plate_number FROM plates WHERE order_id = ("
                        "SELECT id FROM production_orders WHERE order_number = '5004')")
            assert cur.fetchall() == [("FRE599",)]


class TestProduccionRole:
    """The read-only 'production' role (see app.web.auth.consulta_allowed) --
    started as a full-access role and was demoted to read-only once real
    usage showed it should only ever look things up (see
    auth.FULL_ACCESS_ROLES) -- may only reach the small consult-only
    allowlist -- /records and /records/<id> -- and must be default-denied
    (403) everywhere else that writes or otherwise isn't a pure lookup.
    These tests never touch the DB for the 403 assertions: the role check in
    login_required/consulta_allowed runs before any view body, so an
    empty/garbage POST body is enough to prove the request never reached the
    handler.
    """

    def test_records_returns_200(self, produccion_client, db_conn):
        _insert_order(db_conn, order_number="5100", order_date=date(2026, 1, 1),
                         requested_by="PEDRO")
        db_conn.commit()

        resp = produccion_client.get("/records")
        assert resp.status_code == 200
        assert b"5100" in resp.data

    def test_recent_orders_list_hides_the_delete_button(self, produccion_client, db_conn):
        """/records/delete already 403s for 'production' (see below), but the
        recent-orders list used to show the 'Eliminar' button to every role
        regardless -- same class of bug as the plate-edit form: it looked
        available even though clicking it would just 403."""
        _insert_order(db_conn, order_number="5101", order_date=date(2026, 1, 1),
                         requested_by="PEDRO")
        db_conn.commit()

        resp = produccion_client.get("/records")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "5101" in html
        assert "Eliminar" not in html
        assert 'action="/records/delete"' not in html

    def test_record_detail_shows_a_read_only_table_not_the_edit_form(self, produccion_client, db_conn):
        """Regression guard: /records/<id>/save already 403s for 'production'
        (see below), but the page itself used to render the same editable
        inputs and 'Guardar' button to every role regardless -- so a
        read-only user saw something that looked editable but wasn't, which
        is exactly the confusing behavior reported. No form, no inputs, no
        save button; the plate's data must still be visible as plain text."""
        order_id = _insert_order(db_conn, order_number="5102", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="PRD111", quantity=2),
                         is_possible_duplicate=False)
        db_conn.commit()

        resp = produccion_client.get(f"/records/{order_id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "PRD111" in html
        assert 'name="plate_number"' not in html
        assert 'name="plate_id"' not in html
        assert "Guardar" not in html
        assert f'action="/records/{order_id}/save"' not in html
        assert "Agregar" not in html
        assert "<form" not in html.split("</nav>")[1]  # no form anywhere past the nav bar

    def test_ingest_get_is_forbidden(self, produccion_client):
        resp = produccion_client.get("/ingest")
        assert resp.status_code == 403

    def test_ingest_post_is_forbidden(self, produccion_client):
        resp = produccion_client.post("/ingest", data={
            "payment_method": "OTROS",})
        assert resp.status_code == 403

    def test_ingest_pdf_is_forbidden(self, produccion_client):
        resp = produccion_client.get("/ingest/pdf")
        assert resp.status_code == 403

    def test_review_is_forbidden(self, produccion_client):
        resp = produccion_client.get("/review")
        assert resp.status_code == 403

    def test_review_update_is_forbidden(self, produccion_client):
        resp = produccion_client.post("/review/update", data={})
        assert resp.status_code == 403

    def test_inventory_is_forbidden(self, produccion_client):
        resp = produccion_client.get("/inventory")
        assert resp.status_code == 403

    def test_inventory_alistado_is_forbidden(self, produccion_client):
        resp = produccion_client.post("/inventory/alistado", data={})
        assert resp.status_code == 403

    def test_records_delete_is_forbidden(self, produccion_client):
        resp = produccion_client.post("/records/delete", data={})
        assert resp.status_code == 403

    def test_record_save_is_forbidden(self, produccion_client):
        """Reconciliation guard: /records/<id>/save (the consolidated
        edit-order-in-one-shot route) is a write route -- it must stay on the
        default-deny @login_required, not the consult-only allowlist."""
        resp = produccion_client.post("/records/1/save", data={})
        assert resp.status_code == 403

    def test_nav_hides_full_access_only_links(self, produccion_client):
        resp = produccion_client.get("/records")
        html = resp.data.decode()
        assert 'href="/ingest"' not in html
        assert 'href="/review"' not in html
        assert 'href="/inventory"' not in html

    def test_admin_client_is_unaffected_full_access_regression_guard(
        self, authenticated_client, clean_db
    ):
        """The existing admin-role fixture must still get through everywhere
        -- the role gate must not accidentally narrow full access."""
        assert authenticated_client.get("/ingest").status_code == 200
        assert authenticated_client.get("/review").status_code == 200
        assert authenticated_client.get("/inventory").status_code == 200
        assert authenticated_client.get("/records").status_code == 200


class TestPuntoVentaRole:
    """'point_of_sale' (Fase 2's POS_ROLES) is the Fase 3 payoff: narrower than
    admin, but wide enough to actually run the POS capture flow -- /ingest,
    /ingest/pdf, and /records/<id>/save now sit behind @pos_allowed instead
    of admin-only @login_required. Everything admin-only (review, inventario,
    records delete) must still 403 it, same as production."""

    def test_lands_on_ingest_not_records(self, client, punto_venta_credentials):
        username, password = punto_venta_credentials
        resp = client.post("/login", data={"username": username, "password": password})
        assert resp.status_code == 302
        resp2 = client.get("/")
        assert urlparse(resp2.headers["Location"]).path == "/ingest"

    def test_nav_shows_registrar_but_not_admin_only_links(self, punto_venta_client, clean_db):
        resp = punto_venta_client.get("/ingest")
        html = resp.data.decode()
        assert 'href="/ingest"' in html
        assert 'href="/review"' not in html
        assert 'href="/inventory"' not in html

    def test_ingest_get_returns_200(self, punto_venta_client, clean_db):
        assert punto_venta_client.get("/ingest").status_code == 200

    def test_ingest_pdf_get_returns_200(self, punto_venta_client, clean_db):
        assert punto_venta_client.get("/ingest/pdf").status_code == 200

    def test_can_save_an_order_through_ingest(self, punto_venta_client, clean_db):
        resp = punto_venta_client.post("/ingest", data={
            "payment_method": "EFECTIVO",
            "order_number": "7000", "order_date": "2026-01-01", "requested_by": "PEDRO",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["PTV100"], "plate_to": [""], "quantity": ["1"], "city": [""],
        })
        assert resp.status_code == 200
        assert b"Registrado" in resp.data

    def test_can_reach_and_save_record_detail(self, punto_venta_client, db_conn):
        order_id = _insert_order(db_conn, order_number="7001", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db_conn.commit()
        resp = punto_venta_client.get(f"/records/{order_id}")
        assert resp.status_code == 200
        assert 'name="order_number"' in resp.data.decode()  # sees the real edit form, not read-only

        resp = punto_venta_client.post(f"/records/{order_id}/save", data={
            "order_number": "7001", "order_date": "2026-01-01", "requested_by": "MARIA",
            "plate_id": [], "plate_type": [], "plate_number": [], "plate_to": [],
            "city": [], "quantity": [], "is_plate_annulled": [],
        })
        assert resp.status_code == 302

    def test_review_is_forbidden(self, punto_venta_client):
        assert punto_venta_client.get("/review").status_code == 403

    def test_inventory_is_forbidden(self, punto_venta_client):
        assert punto_venta_client.get("/inventory").status_code == 403

    def test_inventory_alistado_is_forbidden(self, punto_venta_client):
        assert punto_venta_client.post("/inventory/alistado", data={}).status_code == 403

    def test_records_delete_is_forbidden(self, punto_venta_client):
        assert punto_venta_client.post("/records/delete", data={}).status_code == 403

    def test_records_read_access_still_works(self, punto_venta_client, db_conn):
        _insert_order(db_conn, order_number="7002", order_date=date(2026, 1, 1), requested_by="PEDRO")
        db_conn.commit()
        resp = punto_venta_client.get("/records")
        assert resp.status_code == 200
        assert b"7002" in resp.data


class TestIngestPaymentMethodRequired:
    def test_blank_payment_method_is_rejected_and_saves_nothing(self, authenticated_client, clean_db):
        resp = authenticated_client.post("/ingest", data={
            "order_number": "6500", "order_date": "2026-01-01", "requested_by": "PEDRO",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["PAY100"], "plate_to": [""], "quantity": ["1"], "city": [""],
        })
        assert resp.status_code == 200
        assert b"forma de pago" in resp.data.lower()

        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM production_orders WHERE order_number = '6500'")
            assert cur.fetchone()[0] == 0

    def test_otros_is_accepted_for_genuinely_unknown_cases(self, authenticated_client, clean_db):
        resp = authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "6501", "order_date": "2026-01-01", "requested_by": "PEDRO",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["PAY101"], "plate_to": [""], "quantity": ["1"], "city": [""],
        })
        assert resp.status_code == 200
        assert b"Registrado" in resp.data


class TestIngestCustomerCapture:
    def test_new_customer_attributes_are_persisted(self, authenticated_client, clean_db):
        authenticated_client.post("/ingest", data={
            "payment_method": "EFECTIVO",
            "order_number": "6600", "order_date": "2026-01-01", "requested_by": "CLIENTE UNO",
            "customer_type": "PERSONA NATURAL", "customer_document_type": "CÉDULA",
            "customer_document_number": "999", "customer_document_relation": "DUEÑO",
            "customer_phone": "3001112222", "customer_email": "uno@x.com",
            "customer_transit_license": "LICX",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["CUS100"], "plate_to": [""], "quantity": ["1"], "city": [""],
        })
        with db.get_conn() as conn:
            customer = db.get_customer_by_name(conn, "CLIENTE UNO")
        assert customer is not None
        assert customer.type == "PERSONA NATURAL"
        assert customer.document_type == "CÉDULA"
        assert customer.document_number == "999"
        assert customer.document_relation == "DUEÑO"
        assert customer.phone == "3001112222"
        assert customer.email == "uno@x.com"
        assert customer.transit_license == "LICX"

    def test_document_relation_is_forced_null_unless_cedula(self, authenticated_client, clean_db):
        """Server-side enforcement: even if a raw POST carries a
        document_relation, it must never be stored unless document_type is
        genuinely CÉDULA -- the HTML disables the field for anything else,
        but the server never trusts that alone."""
        authenticated_client.post("/ingest", data={
            "payment_method": "EFECTIVO",
            "order_number": "6601", "order_date": "2026-01-01", "requested_by": "CLIENTE DOS",
            "customer_document_type": "NIT", "customer_document_relation": "DUEÑO",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["CUS101"], "plate_to": [""], "quantity": ["1"], "city": [""],
        })
        with db.get_conn() as conn:
            customer = db.get_customer_by_name(conn, "CLIENTE DOS")
        assert customer is not None
        assert customer.document_type == "NIT"
        assert customer.document_relation is None

    def test_transit_license_is_forced_null_unless_persona_natural(self, authenticated_client, clean_db):
        authenticated_client.post("/ingest", data={
            "payment_method": "EFECTIVO",
            "order_number": "6602", "order_date": "2026-01-01", "requested_by": "CLIENTE TRES",
            "customer_type": "EMPRESA", "customer_transit_license": "LICY",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["CUS102"], "plate_to": [""], "quantity": ["1"], "city": [""],
        })
        with db.get_conn() as conn:
            customer = db.get_customer_by_name(conn, "CLIENTE TRES")
        assert customer is not None
        assert customer.type == "EMPRESA"
        assert customer.transit_license is None

    def test_existing_customer_matched_by_requested_by_is_not_overwritten(
        self, authenticated_client, clean_db
    ):
        with db.get_conn() as conn:
            db.resolve_or_create_customer(conn, "CLIENTE CUATRO", type="EMPRESA", phone="111")
            conn.commit()
        authenticated_client.post("/ingest", data={
            "payment_method": "EFECTIVO",
            "order_number": "6603", "order_date": "2026-01-01", "requested_by": "CLIENTE CUATRO",
            "customer_type": "PERSONA NATURAL", "customer_phone": "999",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["CUS103"], "plate_to": [""], "quantity": ["1"], "city": [""],
        })
        with db.get_conn() as conn:
            customer = db.get_customer_by_name(conn, "CLIENTE CUATRO")
        assert customer is not None
        assert customer.type == "EMPRESA"
        assert customer.phone == "111"


class TestIngestPlateLineFieldsPersist:
    def test_reason_vehicle_brand_unit_price_are_saved(self, authenticated_client, clean_db):
        authenticated_client.post("/ingest", data={
            "payment_method": "EFECTIVO",
            "order_number": "6700", "order_date": "2026-01-01", "requested_by": "PEDRO",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["LIN100"], "plate_to": [""], "quantity": ["1"], "city": [""],
            "reason": ["DETERIORO"], "vehicle_brand": ["TOYOTA"], "unit_price": ["180000"],
        })
        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                "SELECT reason, vehicle_brand, unit_price FROM plates WHERE plate_number = 'LIN100'"
            )
            assert cur.fetchone() == ("DETERIORO", "TOYOTA", Decimal("180000"))


class TestIngestStickyRoundTrip:
    """Item 5 of the Fase 3 plan: every new POS field (order-level, cliente,
    and per-line) must survive a duplicate-order bounce via the shared
    _sticky_header_from_form/_sticky_lines_from_form helpers."""

    def test_every_new_field_survives_a_duplicate_order_bounce(self, authenticated_client, clean_db):
        payload = {
            "order_number": "6800", "order_date": "2026-01-01",
            "requested_by": "CLIENTE ABC",
            "receipt_type": "FACTURA", "receipt_number": "FAC-1",
            "customer_type": "PERSONA NATURAL", "customer_document_type": "CÉDULA",
            "customer_document_number": "111", "customer_document_relation": "DUEÑO",
            "customer_phone": "3000000000", "customer_email": "abc@x.com",
            "customer_transit_license": "LIC-9",
            "payment_method": "TRANSFERENCIA",
            "tracking_number": "GUIA-1", "client_order_number": "PED-1",
            "delivery_date": "2026-01-10", "payment_date": "2026-01-02",
            "dispatched_by": "ANA", "received_by": "LUIS", "notes": "obs",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["AAA900"], "plate_to": [""], "quantity": ["3"],
            "city": ["CALI"], "reason": ["DETERIORO"], "vehicle_brand": ["MAZDA"],
            "unit_price": ["150000"],
        }
        first = authenticated_client.post("/ingest", data=payload)
        assert b"Registrado" in first.data

        resp = authenticated_client.post("/ingest", data=payload)  # same order_number -> bounce
        assert resp.status_code == 200
        assert b"ya existe" in resp.data
        html = resp.data.decode()

        for expected in [
            'value="6800"', 'value="CLIENTE ABC"', 'value="FAC-1"',
            'value="111"', 'value="3000000000"', 'value="abc@x.com"',
            'value="LIC-9"', 'value="GUIA-1"', 'value="PED-1"',
            'value="2026-01-10"', 'value="2026-01-02"', 'value="ANA"', 'value="LUIS"',
            'value="obs"', 'value="AAA900"', 'value="CALI"', 'value="MAZDA"', 'value="150000"',
        ]:
            assert expected in html, f"missing round-tripped field: {expected}"

        assert '<option value="FACTURA" selected>' in html
        assert '<option value="TRANSFERENCIA" selected>' in html
        assert '<option value="PERSONA NATURAL" selected>' in html
        assert '<option value="CÉDULA" selected>' in html
        assert '<option value="DUEÑO" selected>' in html
        assert '<option value="DETERIORO" selected>' in html

        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM production_orders WHERE order_number = '6800'")
            assert cur.fetchone()[0] == 1  # second post was NOT saved
