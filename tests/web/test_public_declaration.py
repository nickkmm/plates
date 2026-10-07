"""Tests for the PUBLIC /declaracion route (no login).

Uses the unauthenticated `client`. Turnstile is bypassed via TURNSTILE_BYPASS
(except where a test is about Turnstile) and uploads go to a tmp dir. DB state
rolls back after each test (tests/conftest.py's transactional clean_db).
"""

from __future__ import annotations

import base64
import io
import time
from datetime import date
from pathlib import Path

import psycopg.errors
import pytest

from app.core import db
from app.web import antispam
from app.web.app import app as flask_app
from app.web.blueprints import public_declaration as pd
from tests.core.db._helpers import _insert_order, make_row

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.4\n" + b"x" * 200


def _data_url(raw: bytes = PNG) -> str:
    return "data:image/png;base64," + base64.b64encode(raw).decode()


def _token(age: float = 10) -> str:
    return antispam.issue_form_token(str(flask_app.secret_key), now=lambda: time.time() - age)


def _bytes_io(content: bytes) -> io.BytesIO:
    return io.BytesIO(content)


def _form(**overrides) -> dict:
    data = {
        "form_token": _token(),
        "reason": "DETERIORO",
        "plate_quantity": "1",
        "acting_as": "PROPIETARIO",
        "owner_name": "Maria Lopez",
        "owner_document_type": "CÉDULA",
        "owner_document_number": "1020304050",
        "owner_address": "CRA 1 # 2-3",
        "owner_neighborhood": "CENTRO",
        "owner_municipality": "PALMIRA",
        "owner_phone": "3001112233",
        "owner_email": "maria@example.com",
        "vehicle_plate_number": "ABC123",
        "vehicle_transit_license": "LIC-0001",
        "vehicle_engine_number": "MOT1",
        "vehicle_chassis_number": "CHA1",
        "vehicle_type": "CARRO PARTICULAR",
        "vehicle_registered_in": "PALMIRA",
        "vehicle_brand": "MAZDA",
        "owner_signature": _data_url(),
        "accept_terms": "1",
        "website": "",
    }
    data.update(overrides)
    return {k: v for k, v in data.items() if v is not None}


def _gestor_form(**overrides) -> dict:
    base = {
        "acting_as": "GESTOR", "owner_relation": "DUEÑO", "gestor_signature": _data_url(),
        "gestor_name": "Juan Gestor", "gestor_document_type": "NIT",
        "gestor_document_number": "900111222", "gestor_address": "CALLE 5",
        "gestor_neighborhood": "CENTRO", "gestor_municipality": "CALI",
        "gestor_phone": "3109998877",
    }
    base.update(overrides)
    return _form(**base)


def _files(directory: Path) -> list[Path]:
    return sorted(directory.iterdir())


def _count(db_conn, table: str) -> int:
    with db_conn.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {table}")
        return int(cur.fetchone()["n"])


@pytest.fixture
def uploads(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def enabled(monkeypatch, uploads) -> Path:
    monkeypatch.setenv("TURNSTILE_BYPASS", "1")
    return uploads


def post(client, data: dict, *, ip: str | None = None):
    headers = {"X-Real-IP": ip} if ip else {}
    return client.post("/declaracion", data=data, headers=headers, content_type="multipart/form-data")


def _assert_security_headers(resp) -> None:
    assert resp.headers["Cache-Control"] == "no-store"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["Referrer-Policy"] == "same-origin"
    assert resp.headers["X-Frame-Options"] == "DENY"


class TestAvailabilityGate:
    @pytest.fixture(autouse=True)
    def _no_keys(self, monkeypatch):
        for name in ("TURNSTILE_BYPASS", "TURNSTILE_SITE_KEY", "TURNSTILE_SECRET_KEY"):
            monkeypatch.delenv(name, raising=False)

    def test_get_503_without_keys(self, client):
        resp = client.get("/declaracion")
        assert resp.status_code == 503
        assert "no está disponible por ahora" in resp.get_data(as_text=True)

    def test_post_503_without_keys(self, client, db_conn, uploads):
        resp = post(client, _form())
        assert resp.status_code == 503
        assert _count(db_conn, "declarations") == 0

    def test_only_one_key_is_not_enough(self, client, monkeypatch):
        monkeypatch.setenv("TURNSTILE_SITE_KEY", "site")
        assert client.get("/declaracion").status_code == 503

    def test_both_keys_enable(self, client, monkeypatch):
        monkeypatch.setenv("TURNSTILE_SITE_KEY", "site")
        monkeypatch.setenv("TURNSTILE_SECRET_KEY", "secret")
        resp = client.get("/declaracion")
        assert resp.status_code == 200
        assert 'data-sitekey="site"' in resp.get_data(as_text=True)

    def test_bypass_enables(self, client, monkeypatch):
        monkeypatch.setenv("TURNSTILE_BYPASS", "1")
        assert client.get("/declaracion").status_code == 200


def test_get_renders_form_with_token_and_headers(client, enabled):
    resp = client.get("/declaracion")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'name="form_token"' in html and 'name="website"' in html
    _assert_security_headers(resp)


class TestAntispam:
    def test_honeypot_pretends_success_and_stores_nothing(self, client, enabled, db_conn):
        resp = post(client, _form(website="http://spam.example"))
        assert resp.status_code == 200
        assert "Recibimos tu declaración" in resp.get_data(as_text=True)
        assert _count(db_conn, "declarations") == 0
        assert _files(enabled) == []

    @pytest.mark.parametrize("token", [None, "garbage"])
    def test_invalid_token(self, client, enabled, db_conn, token):
        resp = post(client, _form(form_token=token))
        assert resp.status_code == 400
        assert "expiró o no es válido" in resp.get_data(as_text=True)
        assert _count(db_conn, "declarations") == 0

    def test_too_fast_token(self, client, enabled, db_conn):
        resp = post(client, _form(form_token=_token(age=0)))
        assert resp.status_code == 400
        assert _count(db_conn, "declarations") == 0

    def test_expired_token(self, client, enabled, db_conn):
        resp = post(client, _form(form_token=_token(age=3 * 60 * 60)))
        assert resp.status_code == 400
        assert _count(db_conn, "declarations") == 0

    def test_token_failure_keeps_sticky_values_and_fresh_token(self, client, enabled):
        old = _token(age=3 * 60 * 60)
        html = post(client, _form(form_token=old)).get_data(as_text=True)
        assert "CRA 1 # 2-3" in html
        assert old not in html
        assert "data:image/png" not in html  # signatures are never echoed back

    def test_turnstile_failure(self, client, monkeypatch, uploads, db_conn):
        monkeypatch.delenv("TURNSTILE_BYPASS", raising=False)
        monkeypatch.setenv("TURNSTILE_SITE_KEY", "site")
        monkeypatch.setenv("TURNSTILE_SECRET_KEY", "secret")
        monkeypatch.setattr(antispam, "_post", lambda url, data, timeout: {"success": False})
        resp = post(client, _form(**{"cf-turnstile-response": "tok"}))
        assert resp.status_code == 400
        assert "No pudimos verificar" in resp.get_data(as_text=True)
        assert _count(db_conn, "declarations") == 0

    def test_turnstile_success_is_accepted(self, client, monkeypatch, uploads, db_conn):
        monkeypatch.delenv("TURNSTILE_BYPASS", raising=False)
        monkeypatch.setenv("TURNSTILE_SITE_KEY", "site")
        monkeypatch.setenv("TURNSTILE_SECRET_KEY", "secret")
        monkeypatch.setattr(antispam, "_post", lambda url, data, timeout: {"success": True})
        resp = post(client, _form(**{"cf-turnstile-response": "tok"}))
        assert resp.status_code == 200
        assert _count(db_conn, "declarations") == 1

    def test_rate_limit(self, client, enabled, db_conn):
        for _ in range(pd.MAX_SUBMISSIONS_PER_IP_PER_HOUR):
            db.insert_declaration(
                db_conn, reason="DETERIORO", plate_quantity=1, acting_as="PROPIETARIO",
                status="pending_review", submitted_data={}, submitter_ip="9.9.9.9",
            )
        db_conn.commit()
        resp = post(client, _form(), ip="9.9.9.9")
        assert resp.status_code == 429
        assert "varias declaraciones" in resp.get_data(as_text=True)
        assert _count(db_conn, "declarations") == pd.MAX_SUBMISSIONS_PER_IP_PER_HOUR
        assert post(client, _form(), ip="8.8.8.8").status_code == 200  # other IPs unaffected


class TestValidation:
    def test_errors_keep_sticky_values_and_delete_files(self, client, enabled, db_conn):
        resp = post(client, _form(owner_phone="", vehicle_transit_license=""))
        html = resp.get_data(as_text=True)
        assert resp.status_code == 400
        assert "Falta el teléfono" in html
        assert "Falta la licencia de tránsito" in html
        assert "CRA 1 # 2-3" in html
        assert _files(enabled) == []
        assert _count(db_conn, "declarations") == 0

    def test_loss_without_pdf_rejected(self, client, enabled):
        resp = post(client, _form(reason="PÉRDIDA"))
        assert resp.status_code == 400
        assert "PDF de la declaración notariada" in resp.get_data(as_text=True)
        assert _files(enabled) == []

    def test_loss_with_invalid_pdf_rejected_and_cleaned(self, client, enabled):
        resp = post(client, _form(reason="PÉRDIDA", notarized_pdf=(_bytes_io(b"nope"), "x.pdf")))
        assert resp.status_code == 400
        assert "PDF" in resp.get_data(as_text=True)
        assert _files(enabled) == []

    def test_loss_with_pdf_accepted(self, client, enabled):
        resp = post(client, _form(reason="PÉRDIDA", notarized_pdf=(_bytes_io(PDF), "n.pdf")))
        assert resp.status_code == 200
        assert sorted(p.suffix for p in _files(enabled)) == [".pdf", ".png"]

    def test_deterioro_ignores_pdf(self, client, enabled, db_conn):
        resp = post(client, _form(notarized_pdf=(_bytes_io(PDF), "n.pdf")))
        assert resp.status_code == 200
        assert [p.suffix for p in _files(enabled)] == [".png"]
        with db_conn.cursor() as cur:
            cur.execute("SELECT notarized_pdf_path FROM declarations")
            assert cur.fetchone()["notarized_pdf_path"] is None

    def test_bad_signature_rejected(self, client, enabled):
        resp = post(client, _form(owner_signature="data:image/png;base64,@@@"))
        assert resp.status_code == 400
        assert _files(enabled) == []

    def test_missing_signature_rejected(self, client, enabled):
        resp = post(client, _form(owner_signature=None))
        assert resp.status_code == 400
        assert "Falta la firma" in resp.get_data(as_text=True)

    def test_gestor_signature_required_for_gestor(self, client, enabled):
        resp = post(client, _gestor_form(gestor_signature=None))
        assert resp.status_code == 400
        assert "Falta la firma del gestor" in resp.get_data(as_text=True)
        assert _files(enabled) == []  # the owner signature saved first was deleted

    def test_garbage_quantity_is_a_validation_error(self, client, enabled):
        resp = post(client, _form(plate_quantity="abc"))
        assert resp.status_code == 400
        assert "cantidad de placas" in resp.get_data(as_text=True)


class TestSubmission:
    def test_cliente_only_auto_approves(self, client, enabled, db_conn):
        resp = post(client, _form(), ip="7.7.7.7")
        html = resp.get_data(as_text=True)
        assert resp.status_code == 200
        assert "Recibimos tu declaración" in html
        _assert_security_headers(resp)
        customer = db.get_customer_by_name(db_conn, "Maria Lopez")
        assert customer is not None and customer.type == "CLIENTE"
        assert len(db.list_ownership_cards_for_customer(db_conn, customer.id)) == 1
        with db_conn.cursor() as cur:
            cur.execute("SELECT * FROM declarations")
            row = cur.fetchone()
        assert row["status"] == "approved"
        assert row["submitter_ip"] == "7.7.7.7"
        assert row["gestor_id"] is None
        raw = row["submitted_data"]["raw"]
        assert "owner_signature" not in raw and "form_token" not in raw and "website" not in raw
        assert raw["owner_name"] == "Maria Lopez"
        assert _files(enabled) == [enabled / row["owner_signature_path"]]
        assert (enabled / row["owner_signature_path"]).read_bytes() == PNG

    def test_gestor_submission_creates_gestor_client_and_card(self, client, enabled, db_conn):
        resp = post(client, _gestor_form())
        assert resp.status_code == 200
        gestor = db.get_customer_by_name(db_conn, "Juan Gestor")
        owner = db.get_customer_by_name(db_conn, "Maria Lopez")
        assert gestor is not None and gestor.type == "GESTOR"
        assert owner is not None and owner.type == "CLIENTE"
        assert owner.document_relation == "DUEÑO"
        assert len(db.list_ownership_cards_for_customer(db_conn, owner.id)) == 1
        assert len(_files(enabled)) == 2

    def test_gestor_block_ignored_unless_gestor(self, client, enabled, db_conn):
        post(client, _form(owner_relation="POSEEDOR", gestor_name="Otro Gestor"))
        owner = db.get_customer_by_name(db_conn, "Maria Lopez")
        assert owner is not None and owner.document_relation == "DUEÑO"  # from PROPIETARIO
        assert db.get_customer_by_name(db_conn, "Otro Gestor") is None

    def test_conflict_is_held_with_the_same_generic_page(self, client, enabled, db_conn):
        clean_resp = post(client, _form(
            vehicle_plate_number="ZZZ999", vehicle_transit_license="L-OK", owner_name="Pedro Ok",
        ))
        order_id = _insert_order(db_conn, order_number="R1", order_date=date.today(), requested_by="X")
        db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_number="ABC123"),
                        is_possible_duplicate=False)
        db_conn.commit()
        cards_before = _count(db_conn, "ownership_cards")
        customers_before = _count(db_conn, "customers")
        resp = post(client, _form())
        assert resp.status_code == 200
        assert resp.get_data(as_text=True) == clean_resp.get_data(as_text=True)
        body = resp.get_data(as_text=True).lower()
        for word in ("pending", "conflicto", "alerta", "recientemente", "bandera"):
            assert word not in body
        assert _count(db_conn, "ownership_cards") == cards_before
        assert _count(db_conn, "customers") == customers_before
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT status, customer_id, review_flags FROM declarations "
                "WHERE submitted_data->'vehicle'->>'plate_number' = 'ABC123'"
            )
            row = cur.fetchone()
        assert row["status"] == "pending_review" and row["customer_id"] is None
        assert "plate_made_recently" in row["review_flags"]

    def test_unique_violation_is_retried_once(self, client, enabled, db_conn, monkeypatch):
        real = pd.submit_declaration
        calls: list[int] = []

        def flaky(conn, inp):
            calls.append(1)
            if len(calls) == 1:
                raise psycopg.errors.UniqueViolation("race")
            return real(conn, inp)

        monkeypatch.setattr(pd, "submit_declaration", flaky)
        resp = post(client, _form())
        assert resp.status_code == 200
        assert len(calls) == 2
        assert _count(db_conn, "declarations") == 1

    def test_unexpected_failure_cleans_files_and_hides_details(self, client, enabled, monkeypatch):
        def boom(conn, inp):
            raise RuntimeError("secret internal detail")

        monkeypatch.setattr(pd, "submit_declaration", boom)
        resp = post(client, _form())
        html = resp.get_data(as_text=True)
        assert resp.status_code == 500
        assert "problema temporal" in html
        assert "secret internal detail" not in html
        assert _files(enabled) == []

    def test_persistent_unique_violation_is_a_generic_error(self, client, enabled, monkeypatch):
        def always(conn, inp):
            raise psycopg.errors.UniqueViolation("race")

        monkeypatch.setattr(pd, "submit_declaration", always)
        resp = post(client, _form())
        assert resp.status_code == 500
        assert _files(enabled) == []


class TestSizeLimits:
    def test_two_large_signatures_and_a_pdf_are_accepted(self, client, enabled):
        big = _data_url(PNG + b"\x01" * (190 * 1024))  # ~253 KB as base64
        assert len(big) > 250_000
        pdf = PDF + b"y" * (1024 * 1024)
        resp = post(client, _gestor_form(
            reason="PÉRDIDA", owner_signature=big, gestor_signature=big,
            notarized_pdf=(_bytes_io(pdf), "n.pdf"),
        ))
        assert resp.status_code == 200
        assert len(_files(enabled)) == 3

    def test_oversized_body_gets_friendly_413(self, client, enabled):
        huge = _bytes_io(b"%PDF-" + b"z" * (13 * 1024 * 1024))
        resp = post(client, _form(reason="PÉRDIDA", notarized_pdf=(huge, "n.pdf")))
        assert resp.status_code == 413
        assert "demasiado grandes" in resp.get_data(as_text=True)
        assert _files(enabled) == []

    def test_other_routes_keep_accepting_multi_megabyte_uploads(self, authenticated_client):
        # /ingest/pdf takes ~4 MB pedido PDFs; the global cap must not reject them.
        blob = b"%PDF-1.4\n" + b"q" * (4 * 1024 * 1024)
        resp = authenticated_client.post(
            "/ingest/pdf", data={"pdf": (_bytes_io(blob), "p.pdf")}, content_type="multipart/form-data",
        )
        assert resp.status_code != 413
