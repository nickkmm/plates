"""Tests for the PUBLIC /declaracion route (no login).

Uses the unauthenticated `client`. Turnstile is bypassed via TURNSTILE_BYPASS
(except where a test is about Turnstile) and uploads go to a tmp dir. DB state
rolls back after each test (tests/conftest.py's transactional clean_db).

Two kinds of test live here:

* Parse-level tests (the `stub` fixture) replace `validate_declaration` and
  `submit_declaration` with recorders, so they only exercise this blueprint:
  field parsing into `DeclarationInput`, the card-count bound, file saving and
  cleanup, the sticky re-render. They do not depend on the core rewrite.
* `TestEndToEnd` submits all the way into the database and therefore needs the
  multi-card `validate_declaration` / `submit_declaration` / schema.
"""

from __future__ import annotations

import base64
import io
import time
from dataclasses import dataclass, field
from pathlib import Path

import psycopg.errors
import pytest

from app.core import db
from app.core.declarations import MAX_CARDS, DeclarationInput, SubmitResult
from app.web import antispam
from app.web.app import app as flask_app
from app.web.blueprints import public_declaration as pd

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.4\n" + b"x" * 200


def _data_url(raw: bytes = PNG) -> str:
    return "data:image/png;base64," + base64.b64encode(raw).decode()


def _token(age: float = 10) -> str:
    return antispam.issue_form_token(str(flask_app.secret_key), now=lambda: time.time() - age)


def _bytes_io(content: bytes) -> io.BytesIO:
    return io.BytesIO(content)


def _card(i: int = 0, **overrides) -> dict:
    """The form fields of ownership-card block `i`."""
    data = {
        "plate_number": "ABC123",
        "transit_license": "LIC-0001",
        "engine_number": "MOT1",
        "chassis_number": "CHA1",
        "vehicle_type": "CARRO PARTICULAR",
        "registered_in": "PALMIRA",
        "vehicle_brand": "MAZDA",
        "reason": "DETERIORO",
        "plate_quantity": "1",
    }
    data.update(overrides)
    return {f"card_{i}_{k}": v for k, v in data.items() if v is not None}


def _owner(i: int, name: str = "Maria Lopez", relation: str = "DUEÑO", **overrides) -> dict:
    """The per-card owner fields (only read when the declarant is a GESTOR)."""
    person = {
        "name": name, "document_type": "CÉDULA", "document_number": "1020304050",
        "address": "CRA 1 # 2-3", "neighborhood": "CENTRO", "municipality": "PALMIRA",
        "phone": "3001112233", "email": "maria@example.com",
    }
    person.update(overrides)
    data = {f"card_{i}_owner_{k}": v for k, v in person.items()}
    data[f"card_{i}_owner_relation"] = relation
    return data


def _form(*cards: dict, **overrides) -> dict:
    """An owner/possessor submission: one declarant and the given card blocks
    (default: one card, index 0)."""
    data = {
        "form_token": _token(),
        "acting_as": "PROPIETARIO",
        "declarant_name": "Maria Lopez",
        "declarant_document_type": "CÉDULA",
        "declarant_document_number": "1020304050",
        "declarant_address": "CRA 1 # 2-3",
        "declarant_neighborhood": "CENTRO",
        "declarant_municipality": "PALMIRA",
        "declarant_phone": "3001112233",
        "declarant_email": "maria@example.com",
        "signature": _data_url(),
        "accept_terms": "1",
        "website": "",
    }
    for card in cards or (_card(0),):
        data.update(card)
    data.update(overrides)
    return {k: v for k, v in data.items() if v is not None}


def _gestor_form(*cards: dict, **overrides) -> dict:
    """A gestor submission; by default two cards (indices 0 and 1) with different owners."""
    if not cards:
        cards = (
            {**_card(0, plate_number="ABC123", transit_license="L-1"), **_owner(0, "Maria Lopez")},
            {**_card(1, plate_number="DEF456", transit_license="L-2", vehicle_type="MOTO PARTICULAR"),
             **_owner(1, "Pedro Ruiz", "POSEEDOR", document_number="555666777")},
        )
    base = {
        "acting_as": "GESTOR", "declarant_name": "Juan Gestor", "declarant_document_type": "NIT",
        "declarant_document_number": "900111222", "declarant_address": "CALLE 5",
        "declarant_neighborhood": "CENTRO", "declarant_municipality": "CALI",
        "declarant_phone": "3109998877",
    }
    base.update(overrides)
    return _form(*cards, **base)


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
def enabled(monkeypatch, uploads, clean_db) -> Path:
    # clean_db: every test in this module runs inside the rollback fixture, so a
    # submission never persists and the per-IP limit (counted in `declarations`)
    # can't be tripped by rows left over from earlier runs.
    monkeypatch.setenv("TURNSTILE_BYPASS", "1")
    return uploads


@dataclass
class Stub:
    """Recorders standing in for the core (validate/submit) in parse-level tests."""

    validate_errors: list[str] = field(default_factory=list)
    submit_error: Exception | None = None
    validated: list[DeclarationInput] = field(default_factory=list)
    submitted: list[DeclarationInput] = field(default_factory=list)

    @property
    def inp(self) -> DeclarationInput:
        assert len(self.validated) == 1, "validate_declaration was not called exactly once"
        return self.validated[0]


@pytest.fixture
def stub(enabled, monkeypatch) -> Stub:
    st = Stub()

    def fake_validate(inp: DeclarationInput) -> list[str]:
        st.validated.append(inp)
        return list(st.validate_errors)

    def fake_submit(conn, inp: DeclarationInput) -> SubmitResult:
        st.submitted.append(inp)
        if st.submit_error is not None:
            raise st.submit_error
        return SubmitResult(declaration_id=1, status="approved", flags=[])

    monkeypatch.setattr(pd, "validate_declaration", fake_validate)
    monkeypatch.setattr(pd, "submit_declaration", fake_submit)
    return st


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

    def test_post_503_without_keys(self, client, uploads, monkeypatch):
        called = []
        monkeypatch.setattr(pd, "submit_declaration", lambda conn, inp: called.append(inp))
        resp = post(client, _form())
        assert resp.status_code == 503
        assert called == []

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
    assert html.count('class="card-block"') == 1 + 1  # the live block + the <template> clone source
    assert f'data-max-cards="{MAX_CARDS}"' in html and "data-max-by-type=" in html
    _assert_security_headers(resp)


class TestAntispam:
    def test_honeypot_pretends_success_and_stores_nothing(self, client, stub, enabled):
        resp = post(client, _form(website="http://spam.example"))
        assert resp.status_code == 200
        assert "Recibimos tu declaración" in resp.get_data(as_text=True)
        assert stub.validated == [] and stub.submitted == []
        assert _files(enabled) == []

    @pytest.mark.parametrize("token", [None, "garbage"])
    def test_invalid_token(self, client, stub, token):
        resp = post(client, _form(form_token=token))
        assert resp.status_code == 400
        assert "expiró o no es válido" in resp.get_data(as_text=True)
        assert stub.submitted == []

    def test_too_fast_token(self, client, stub):
        resp = post(client, _form(form_token=_token(age=0)))
        assert resp.status_code == 400
        assert stub.submitted == []

    def test_expired_token(self, client, stub):
        resp = post(client, _form(form_token=_token(age=3 * 60 * 60)))
        assert resp.status_code == 400
        assert stub.submitted == []

    def test_token_failure_keeps_sticky_values_and_fresh_token(self, client, stub):
        old = _token(age=3 * 60 * 60)
        html = post(client, _form(form_token=old)).get_data(as_text=True)
        assert "CRA 1 # 2-3" in html
        assert old not in html
        assert "data:image/png" not in html  # the signature is never echoed back

    def test_turnstile_failure(self, client, monkeypatch, stub):
        monkeypatch.delenv("TURNSTILE_BYPASS", raising=False)
        monkeypatch.setenv("TURNSTILE_SITE_KEY", "site")
        monkeypatch.setenv("TURNSTILE_SECRET_KEY", "secret")
        monkeypatch.setattr(antispam, "_post", lambda url, data, timeout: {"success": False})
        resp = post(client, _form(**{"cf-turnstile-response": "tok"}))
        assert resp.status_code == 400
        assert "No pudimos verificar" in resp.get_data(as_text=True)
        assert stub.submitted == []

    def test_turnstile_success_is_accepted(self, client, monkeypatch, stub):
        monkeypatch.delenv("TURNSTILE_BYPASS", raising=False)
        monkeypatch.setenv("TURNSTILE_SITE_KEY", "site")
        monkeypatch.setenv("TURNSTILE_SECRET_KEY", "secret")
        monkeypatch.setattr(antispam, "_post", lambda url, data, timeout: {"success": True})
        resp = post(client, _form(**{"cf-turnstile-response": "tok"}))
        assert resp.status_code == 200
        assert len(stub.submitted) == 1

    def test_rate_limit(self, client, stub, monkeypatch):
        monkeypatch.setattr(pd.db, "count_recent_by_ip", lambda conn, ip, since: (
            pd.MAX_SUBMISSIONS_PER_IP_PER_HOUR if ip == "9.9.9.9" else 0
        ))
        resp = post(client, _form(), ip="9.9.9.9")
        assert resp.status_code == 429
        assert "varias declaraciones" in resp.get_data(as_text=True)
        assert stub.submitted == []
        assert post(client, _form(), ip="8.8.8.8").status_code == 200  # other IPs unaffected


class TestParsing:
    """The POST fields become a DeclarationInput (core validate/submit stubbed)."""

    def test_owner_declarant_with_one_card(self, client, stub):
        resp = post(client, _form(
            _card(0, plate_quantity="2", vehicle_type="CARRO PARTICULAR"),
            # owner fields posted by a stale page must be ignored for PROPIETARIO
            **_owner(0, "Otro Dueno"),
        ), ip="7.7.7.7")
        assert resp.status_code == 200
        assert "Recibimos tu declaración" in resp.get_data(as_text=True)
        _assert_security_headers(resp)
        inp = stub.inp
        assert inp.acting_as == "PROPIETARIO"
        assert inp.declarant.name == "Maria Lopez" and inp.declarant.document_type == "CÉDULA"
        assert inp.declarant.phone == "3001112233" and inp.declarant.email == "maria@example.com"
        assert inp.submitter_ip == "7.7.7.7" and inp.accepted_terms is True
        assert len(inp.cards) == 1
        card = inp.cards[0]
        assert (card.plate_number, card.transit_license, card.engine_number) == ("ABC123", "LIC-0001", "MOT1")
        assert (card.chassis_number, card.vehicle_type) == ("CHA1", "CARRO PARTICULAR")
        assert (card.registered_in, card.vehicle_brand) == ("PALMIRA", "MAZDA")
        assert card.reason == "DETERIORO" and card.plate_quantity == 2
        assert card.owner is None and card.owner_relation == ""
        assert stub.submitted == [inp]

    def test_gestor_with_several_cards_and_owners_in_index_order(self, client, stub):
        resp = post(client, _gestor_form())
        assert resp.status_code == 200
        inp = stub.inp
        assert inp.acting_as == "GESTOR" and inp.declarant.name == "Juan Gestor"
        assert [c.plate_number for c in inp.cards] == ["ABC123", "DEF456"]
        first, second = inp.cards
        assert first.owner is not None and first.owner.name == "Maria Lopez"
        assert first.owner_relation == "DUEÑO"
        assert second.owner is not None and second.owner.name == "Pedro Ruiz"
        assert second.owner.document_number == "555666777" and second.owner_relation == "POSEEDOR"
        assert second.vehicle_type == "MOTO PARTICULAR"

    def test_sparse_indices_are_read_in_numeric_order(self, client, stub):
        cards = [_card(i, plate_number=f"AAA{i:03d}") for i in (12, 2, 7, 0)]
        post(client, _form(*cards))
        assert [c.plate_number for c in stub.inp.cards] == ["AAA000", "AAA002", "AAA007", "AAA012"]

    def test_per_card_reasons_and_quantities(self, client, stub):
        post(client, _form(
            _card(0, reason="DETERIORO", plate_quantity="2"),
            _card(1, plate_number="DEF456", transit_license="L-2", reason="PÉRDIDA",
                  vehicle_type="MOTO PARTICULAR", plate_quantity="1"),
        ))
        a, b = stub.inp.cards
        assert (a.reason, a.plate_quantity) == ("DETERIORO", 2)
        assert (b.reason, b.plate_quantity) == ("PÉRDIDA", 1)

    @pytest.mark.parametrize("raw", ["abc", "", "1.5", "9" * 5000])
    def test_garbage_quantity_becomes_zero_for_validation(self, client, stub, raw):
        post(client, _form(_card(0, plate_quantity=raw)))
        assert stub.inp.cards[0].plate_quantity == 0

    def test_missing_quantity_becomes_zero(self, client, stub):
        post(client, _form(_card(0, plate_quantity=None)))
        assert stub.inp.cards[0].plate_quantity == 0

    def test_values_are_stripped(self, client, stub):
        post(client, _form(_card(0, plate_number="  ABC123 "), declarant_name="  Maria Lopez  "))
        assert stub.inp.cards[0].plate_number == "ABC123"
        assert stub.inp.declarant.name == "Maria Lopez"

    def test_keys_that_are_not_card_fields_are_ignored(self, client, stub):
        extra = {"card_99999_plate_number": "ZZZ999", "card_x_plate_number": "ZZZ999", "xcard_5_plate_number": "ZZZ999"}
        post(client, _form(**extra))
        assert [c.plate_number for c in stub.inp.cards] == ["ABC123"]

    def test_submitted_data_is_the_raw_form_without_blobs_and_plumbing(self, client, stub):
        post(client, _form(**{"cf-turnstile-response": "tok"}))
        raw = stub.inp.submitted_data
        for key in ("signature", "form_token", "website", "cf-turnstile-response"):
            assert key not in raw
        assert raw["declarant_name"] == "Maria Lopez" and raw["card_0_plate_number"] == "ABC123"

    def test_no_card_blocks_is_rejected_before_validation(self, client, stub):
        data = _form()
        for key in [k for k in data if k.startswith("card_")]:
            del data[key]
        resp = post(client, data)
        assert resp.status_code == 400
        assert "Agrega al menos una tarjeta de propiedad" in resp.get_data(as_text=True)
        assert stub.validated == [] and stub.submitted == []

    def test_exactly_max_cards_is_accepted(self, client, stub):
        post(client, _form(*[_card(i, plate_number=f"AB{i:04d}") for i in range(MAX_CARDS)]))
        assert len(stub.inp.cards) == MAX_CARDS

    def test_more_than_max_cards_is_rejected_before_building_anything(self, client, stub, enabled):
        cards = [_card(i, plate_number=f"AB{i:04d}") for i in range(MAX_CARDS + 1)]
        resp = post(client, _form(*cards))
        html = resp.get_data(as_text=True)
        assert resp.status_code == 400
        assert f"hasta {MAX_CARDS} tarjetas de propiedad" in html
        assert stub.validated == [] and stub.submitted == []
        assert _files(enabled) == []  # not even the signature was stored
        # the re-render never shows more than MAX_CARDS blocks (+ the clone template)
        assert html.count('class="card-block"') == MAX_CARDS + 1

    def test_huge_number_of_card_keys_is_rejected_early(self, client, stub):
        cards = [{f"card_{i}_plate_number": "X"} for i in range(MAX_CARDS + 50)]
        resp = post(client, _form(*cards))
        assert resp.status_code == 400
        assert stub.validated == []


class TestFiles:
    def test_signature_is_stored_once_and_named_on_the_input(self, client, stub, enabled):
        post(client, _gestor_form())
        assert len(_files(enabled)) == 1  # ONE signature for every card
        name = stub.inp.signature_file
        assert name is not None and (enabled / name).read_bytes() == PNG
        assert stub.inp.notarized_pdf_file is None

    def test_pdf_is_stored_when_any_card_is_a_loss(self, client, stub, enabled):
        post(client, _form(
            _card(0, reason="DETERIORO"),
            _card(1, plate_number="DEF456", reason="PÉRDIDA"),
            notarized_pdf=(_bytes_io(PDF), "n.pdf"),
        ))
        assert sorted(p.suffix for p in _files(enabled)) == [".pdf", ".png"]
        assert stub.inp.notarized_pdf_file is not None

    def test_pdf_is_discarded_when_no_card_is_a_loss(self, client, stub, enabled):
        post(client, _form(notarized_pdf=(_bytes_io(PDF), "n.pdf")))
        assert [p.suffix for p in _files(enabled)] == [".png"]
        assert stub.inp.notarized_pdf_file is None

    def test_invalid_pdf_is_reported_and_every_file_deleted(self, client, stub, enabled):
        resp = post(client, _form(_card(0, reason="PÉRDIDA"), notarized_pdf=(_bytes_io(b"nope"), "x.pdf")))
        assert resp.status_code == 400
        assert "PDF" in resp.get_data(as_text=True)
        assert stub.submitted == []
        assert _files(enabled) == []

    def test_bad_signature_is_rejected_and_cleaned(self, client, stub, enabled):
        resp = post(client, _form(signature="data:image/png;base64,@@@"))
        assert resp.status_code == 400
        assert "Firma del declarante" in resp.get_data(as_text=True)
        assert stub.submitted == []
        assert _files(enabled) == []

    def test_missing_signature_leaves_the_file_name_unset_for_the_validator(self, client, stub):
        resp = post(client, _form(signature=None))
        assert stub.inp.signature_file is None
        assert resp.status_code == 200  # the stub validator reports nothing

    def test_validation_errors_keep_sticky_values_and_delete_files(self, client, stub, enabled):
        stub.validate_errors = ["Falta el teléfono del declarante."]
        resp = post(client, _form(
            _card(0, reason="PÉRDIDA"), notarized_pdf=(_bytes_io(PDF), "n.pdf"),
        ))
        html = resp.get_data(as_text=True)
        assert resp.status_code == 400
        assert "Falta el teléfono del declarante." in html
        assert stub.submitted == []
        assert _files(enabled) == []  # signature and PDF saved for the validator were removed

    def test_unexpected_failure_cleans_files_and_hides_details(self, client, stub, enabled):
        stub.submit_error = RuntimeError("secret internal detail")
        resp = post(client, _form())
        html = resp.get_data(as_text=True)
        assert resp.status_code == 500
        assert "problema temporal" in html
        assert "secret internal detail" not in html
        assert _files(enabled) == []


class TestStickyRerender:
    def test_every_card_block_is_redrawn_with_what_was_typed(self, client, stub):
        stub.validate_errors = ["Falta la licencia de tránsito de la tarjeta 2."]
        resp = post(client, _gestor_form(
            {**_card(0, plate_number="ABC123"), **_owner(0, "Maria Lopez")},
            {**_card(3, plate_number="DEF456", reason="PÉRDIDA", vehicle_type="MOTO PARTICULAR"),
             **_owner(3, "Pedro Ruiz", "POSEEDOR")},
            {**_card(5, plate_number="GHI789", vehicle_brand="RENAULT"), **_owner(5, "Ana Gomez")},
        ))
        html = resp.get_data(as_text=True)
        assert resp.status_code == 400
        assert "Falta la licencia de tránsito de la tarjeta 2." in html
        for needle in ('value="ABC123"', 'value="DEF456"', 'value="GHI789"', 'value="RENAULT"',
                       'value="Maria Lopez"', 'value="Pedro Ruiz"', 'value="Ana Gomez"',
                       'value="Juan Gestor"'):
            assert needle in html, needle
        assert html.count('class="card-block"') == 3 + 1  # three live blocks + the template
        for i in (0, 3, 5):
            assert f'data-card="{i}"' in html
        assert 'data-card="1"' not in html
        assert 'name="card_3_reason" value="PÉRDIDA" checked' in html
        assert "data:image/png" not in html
        assert 'id="notarizedWrap" hidden' not in html  # one card is a PÉRDIDA
        assert '<output id="totalPlates" aria-live="polite">3</output>' in html.replace("\n", "")

    def test_owner_submission_rerenders_without_owner_values(self, client, stub):
        stub.validate_errors = ["x"]
        resp = post(client, _form(_card(0)))
        html = resp.get_data(as_text=True)
        assert resp.status_code == 400
        assert 'name="acting_as" value="PROPIETARIO" required checked' in html


class TestSubmitRetry:
    def test_unique_violation_is_retried_once(self, client, stub, monkeypatch):
        calls: list[int] = []

        def flaky(conn, inp):
            calls.append(1)
            if len(calls) == 1:
                raise psycopg.errors.UniqueViolation("race")
            return SubmitResult(declaration_id=1, status="approved", flags=[])

        monkeypatch.setattr(pd, "submit_declaration", flaky)
        resp = post(client, _form())
        assert resp.status_code == 200
        assert len(calls) == 2

    def test_persistent_unique_violation_is_a_generic_error(self, client, stub, enabled):
        stub.submit_error = psycopg.errors.UniqueViolation("race")
        resp = post(client, _form())
        assert resp.status_code == 500
        assert _files(enabled) == []

    def test_pending_and_approved_get_the_identical_page(self, client, stub, monkeypatch):
        approved = post(client, _form()).get_data(as_text=True)
        monkeypatch.setattr(
            pd, "submit_declaration",
            lambda conn, inp: SubmitResult(declaration_id=2, status="pending_review", flags=["plate_made_recently"]),
        )
        pending = post(client, _form()).get_data(as_text=True)
        assert pending == approved
        for word in ("pending", "conflicto", "alerta", "recientemente", "bandera"):
            assert word not in pending.lower()


class TestSizeLimits:
    def test_a_large_signature_and_a_pdf_are_accepted(self, client, stub, enabled):
        big = _data_url(PNG + b"\x01" * (190 * 1024))  # ~253 KB as base64
        assert len(big) > 250_000
        pdf = PDF + b"y" * (1024 * 1024)
        resp = post(client, _gestor_form(signature=big, notarized_pdf=(_bytes_io(pdf), "n.pdf")))
        assert resp.status_code == 200  # (cards default to DETERIORO: pdf discarded)
        assert len(_files(enabled)) == 1

    def test_a_pdf_for_a_loss_card_is_kept_next_to_the_signature(self, client, stub, enabled):
        pdf = PDF + b"y" * (1024 * 1024)
        resp = post(client, _form(_card(0, reason="PÉRDIDA"), notarized_pdf=(_bytes_io(pdf), "n.pdf")))
        assert resp.status_code == 200
        assert len(_files(enabled)) == 2

    def test_oversized_body_gets_friendly_413(self, client, stub, enabled):
        huge = _bytes_io(b"%PDF-" + b"z" * (13 * 1024 * 1024))
        resp = post(client, _form(_card(0, reason="PÉRDIDA"), notarized_pdf=(huge, "n.pdf")))
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


class TestEndToEnd:
    """Full submissions into the database. These need the rewritten core
    (`validate_declaration` / `submit_declaration`) and schema of the backend
    branch; they cannot pass on the form branch alone."""

    def test_owner_with_one_card_auto_approves(self, client, enabled, db_conn):
        resp = post(client, _form(), ip="7.7.7.7")
        assert resp.status_code == 200
        assert "Recibimos tu declaración" in resp.get_data(as_text=True)
        _assert_security_headers(resp)
        customer = db.get_customer_by_name(db_conn, "Maria Lopez")
        assert customer is not None and customer.type == "CLIENTE"
        assert len(db.list_ownership_cards_for_customer(db_conn, customer.id)) == 1
        with db_conn.cursor() as cur:
            cur.execute("SELECT * FROM declarations")
            row = cur.fetchone()
        assert row["status"] == "approved" and row["submitter_ip"] == "7.7.7.7"
        assert len(_files(enabled)) == 1

    def test_gestor_with_two_cards_creates_both_owners_and_both_cards(self, client, enabled, db_conn):
        resp = post(client, _gestor_form())
        assert resp.status_code == 200
        gestor = db.get_customer_by_name(db_conn, "Juan Gestor")
        maria = db.get_customer_by_name(db_conn, "Maria Lopez")
        pedro = db.get_customer_by_name(db_conn, "Pedro Ruiz")
        assert gestor is not None and gestor.type == "GESTOR"
        assert maria is not None and maria.type == "CLIENTE" and maria.document_relation == "DUEÑO"
        assert pedro is not None and pedro.type == "CLIENTE" and pedro.document_relation == "POSEEDOR"
        assert len(db.list_ownership_cards_for_customer(db_conn, maria.id)) == 1
        assert len(db.list_ownership_cards_for_customer(db_conn, pedro.id)) == 1
        assert len(_files(enabled)) == 1  # one signature for the whole declaration

    def test_owner_fields_are_ignored_unless_gestor(self, client, enabled, db_conn):
        post(client, _form(_card(0), **_owner(0, "Otro Dueno")))
        assert db.get_customer_by_name(db_conn, "Otro Dueno") is None
        owner = db.get_customer_by_name(db_conn, "Maria Lopez")
        assert owner is not None and owner.document_relation == "DUEÑO"  # PROPIETARIO

    def test_loss_card_without_pdf_is_rejected(self, client, enabled, db_conn):
        resp = post(client, _form(_card(0), _card(1, plate_number="DEF456", reason="PÉRDIDA",
                                                  transit_license="L-2")))
        assert resp.status_code == 400
        assert "PDF" in resp.get_data(as_text=True)
        assert _files(enabled) == []
        assert _count(db_conn, "declarations") == 0

    def test_missing_signature_is_rejected(self, client, enabled, db_conn):
        resp = post(client, _form(signature=None))
        assert resp.status_code == 400
        assert "firma" in resp.get_data(as_text=True).lower()
        assert _count(db_conn, "declarations") == 0

    def test_more_than_max_cards_is_rejected_without_touching_the_db(self, client, enabled, db_conn):
        resp = post(client, _form(*[_card(i, plate_number=f"AB{i:04d}") for i in range(MAX_CARDS + 1)]))
        assert resp.status_code == 400
        assert _count(db_conn, "declarations") == 0 and _files(enabled) == []

    def test_validation_errors_keep_sticky_values_and_delete_files(self, client, enabled, db_conn):
        resp = post(client, _form(_card(0, transit_license=""), declarant_phone=""))
        html = resp.get_data(as_text=True)
        assert resp.status_code == 400
        assert "CRA 1 # 2-3" in html
        assert _files(enabled) == []
        assert _count(db_conn, "declarations") == 0

    def test_a_plate_made_recently_is_held_with_the_same_generic_page(self, client, enabled, db_conn):
        from datetime import date

        from tests.core.db._helpers import _insert_order, make_row

        clean_resp = post(client, _form(_card(0, plate_number="ZZZ999", transit_license="L-OK"),
                                        declarant_name="Pedro Ok"))
        order_id = _insert_order(db_conn, order_number="R1", order_date=date.today(), requested_by="X")
        db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_number="ABC123"),
                        is_possible_duplicate=False)
        db_conn.commit()
        resp = post(client, _form())
        assert resp.status_code == 200
        assert resp.get_data(as_text=True) == clean_resp.get_data(as_text=True)
        with db_conn.cursor() as cur:
            cur.execute("SELECT status, review_flags FROM declarations ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
        assert row["status"] == "pending_review"
        assert "plate_made_recently" in row["review_flags"]
