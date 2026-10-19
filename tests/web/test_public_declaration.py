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
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import psycopg.errors
import pytest

from app.core import db
from app.core.declarations import MAX_CARDS, DeclarationInput, SubmitResult, approve_declaration
from app.web import antispam
from app.web.app import app as flask_app
from app.web.blueprints import public_declaration as pd
from tests.conftest import TEST_USERNAME

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


def _two_cards_form(**overrides) -> dict:
    """One person declaring two ownership cards of different types (a car and a
    motorcycle, indices 0 and 1) with a single signature."""
    return _form(
        _card(0, plate_number="ABC123", transit_license="L-1"),
        _card(1, plate_number="DEF45G", transit_license="L-2", vehicle_type="MOTO PARTICULAR"),
        **overrides,
    )


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
        return SubmitResult(declaration_id=1, status="pending_review", flags=[])

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

    def test_limit_is_twenty_per_hour_per_ip(self, client, stub, monkeypatch):
        assert pd.MAX_SUBMISSIONS_PER_IP_PER_HOUR == 20
        monkeypatch.setattr(pd.db, "count_recent_by_ip", lambda conn, ip, since: 19)
        assert post(client, _form(), ip="9.9.9.9").status_code == 200  # the 20th is still allowed
        monkeypatch.setattr(pd.db, "count_recent_by_ip", lambda conn, ip, since: 20)
        assert post(client, _form(), ip="9.9.9.9").status_code == 429  # the 21st is not

    def test_success_page_has_the_generic_notarization_line(self, client, stub):
        html = post(client, _form()).get_data(as_text=True)
        assert ("Si declaraste una pérdida, en el mostrador te entregaremos el documento "
                "para que lo autentiques en una notaría.") in html

    def test_loss_declaration_is_accepted_without_a_file(self, client, stub, enabled):
        resp = post(client, _form(_card(0, reason="PÉRDIDA")))
        assert resp.status_code == 200
        assert len(stub.submitted) == 1
        assert [p.suffix for p in _files(enabled)] == [".png"]


class TestParsing:
    """The POST fields become a DeclarationInput (core validate/submit stubbed)."""

    def test_owner_declarant_with_one_card(self, client, stub):
        resp = post(client, _form(
            _card(0, plate_quantity="2", vehicle_type="CARRO PARTICULAR"),
            # per-card owner fields (a stale page, or a hand-made post) are never read
            card_0_owner_name="Otro Dueno", card_0_owner_relation="DUEÑO",
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

    def test_possessor_with_two_cards_of_different_types_in_index_order(self, client, stub):
        resp = post(client, _two_cards_form(acting_as="POSEEDOR"))
        assert resp.status_code == 200
        inp = stub.inp
        assert inp.acting_as == "POSEEDOR" and inp.declarant.name == "Maria Lopez"
        assert [c.plate_number for c in inp.cards] == ["ABC123", "DEF45G"]
        first, second = inp.cards
        assert (first.vehicle_type, second.vehicle_type) == ("CARRO PARTICULAR", "MOTO PARTICULAR")
        assert all(c.owner is None and c.owner_relation == "" for c in inp.cards)
        assert stub.submitted == [inp]

    @pytest.mark.parametrize("acting_as", ["GESTOR", "gestor", " Gestor "])
    def test_gestor_posted_by_hand_is_an_ordinary_validation_error(self, client, stub, enabled, acting_as):
        resp = post(client, _two_cards_form(acting_as=acting_as))
        html = resp.get_data(as_text=True)
        assert resp.status_code == 400
        assert "solo para personas que declaran por sí mismas" in html
        assert stub.validated == [] and stub.submitted == []
        assert _files(enabled) == []  # not even the signature was stored
        _assert_security_headers(resp)
        # the form is redrawn with what was typed, a fresh token and no gestor option
        assert 'value="ABC123"' in html and 'value="DEF45G"' in html and "Maria Lopez" in html
        assert not re.search(r'name="acting_as"[^>]* checked', html)
        assert 'value="GESTOR"' not in html and "data:image/png" not in html

    def test_gestor_posted_by_hand_does_not_depend_on_the_rest_of_the_input(self, client, stub):
        resp = post(client, _form(acting_as="GESTOR", declarant_name=""))
        assert resp.status_code == 400
        assert "solo para personas que declaran por sí mismas" in resp.get_data(as_text=True)
        assert stub.validated == [] and stub.submitted == []

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
        extra = {"card_9999999_plate_number": "ZZZ999", "card_x_plate_number": "ZZZ999", "xcard_5_plate_number": "ZZZ999"}
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
        post(client, _two_cards_form())
        assert len(_files(enabled)) == 1  # ONE signature for every card
        name = stub.inp.signature_file
        assert name is not None and (enabled / name).read_bytes() == PNG

    def test_a_posted_pdf_is_never_stored(self, client, stub, enabled):
        # An old page (or a hand-made post) may still send a file: it is ignored.
        post(client, _form(
            _card(0, reason="DETERIORO"),
            _card(1, plate_number="DEF456", reason="PÉRDIDA"),
            notarized_pdf=(_bytes_io(PDF), "n.pdf"),
        ))
        assert [p.suffix for p in _files(enabled)] == [".png"]
        assert not hasattr(stub.inp, "notarized_pdf_file")

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
        resp = post(client, _form(_card(0, reason="PÉRDIDA")))
        html = resp.get_data(as_text=True)
        assert resp.status_code == 400
        assert "Falta el teléfono del declarante." in html
        assert stub.submitted == []
        assert _files(enabled) == []  # the signature saved for the validator was removed

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
        resp = post(client, _form(
            _card(0, plate_number="ABC123"),
            _card(3, plate_number="DEF456", reason="PÉRDIDA", vehicle_type="MOTO PARTICULAR"),
            _card(5, plate_number="GHI789", vehicle_brand="RENAULT"),
            acting_as="POSEEDOR",
        ))
        html = resp.get_data(as_text=True)
        assert resp.status_code == 400
        assert "Falta la licencia de tránsito de la tarjeta 2." in html
        for needle in ('value="ABC123"', 'value="DEF456"', 'value="GHI789"', 'value="RENAULT"',
                       'value="Maria Lopez"'):
            assert needle in html, needle
        assert html.count('class="card-block"') == 3 + 1  # three live blocks + the template
        for i in (0, 3, 5):
            assert f'data-card="{i}"' in html
        assert 'data-card="1"' not in html
        assert 'name="card_3_reason" value="PÉRDIDA" checked' in html
        assert "data:image/png" not in html
        assert "notarizedWrap" not in html and 'type="file"' not in html
        assert '<output id="totalPlates" aria-live="polite">3</output>' in html.replace("\n", "")

    def test_acting_as_is_kept_on_rerender(self, client, stub):
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
            return SubmitResult(declaration_id=1, status="pending_review", flags=[])

        monkeypatch.setattr(pd, "submit_declaration", flaky)
        resp = post(client, _form())
        assert resp.status_code == 200
        assert len(calls) == 2

    def test_persistent_unique_violation_is_a_generic_error(self, client, stub, enabled):
        stub.submit_error = psycopg.errors.UniqueViolation("race")
        resp = post(client, _form())
        assert resp.status_code == 500
        assert _files(enabled) == []

    def test_flagged_and_unflagged_declarations_get_the_identical_page(self, client, stub, monkeypatch):
        """Every declaration is stored pending, with or without flags; the page
        never reveals which (nor any status)."""
        clean = post(client, _form()).get_data(as_text=True)
        monkeypatch.setattr(
            pd, "submit_declaration",
            lambda conn, inp: SubmitResult(declaration_id=2, status="pending_review", flags=["plate_made_recently"]),
        )
        flagged = post(client, _form()).get_data(as_text=True)
        assert flagged == clean
        for word in ("pending", "approved", "aprobad", "conflicto", "alerta", "recientemente", "bandera"):
            assert word not in flagged.lower()


class TestSizeLimits:
    def test_a_large_signature_is_accepted(self, client, stub, enabled):
        big = _data_url(PNG + b"\x01" * (190 * 1024))  # ~253 KB as base64
        assert len(big) > 250_000
        resp = post(client, _two_cards_form(signature=big))
        assert resp.status_code == 200
        assert len(_files(enabled)) == 1

    def test_oversized_body_gets_friendly_413(self, client, stub, enabled):
        resp = post(client, _form(_card(0, reason="PÉRDIDA"), junk="z" * (13 * 1024 * 1024)))
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


def _only_declaration_id(db_conn) -> int:
    with db_conn.cursor() as cur:
        cur.execute("SELECT id FROM declarations")
        (row,) = cur.fetchall()
    return row["id"]


def _approve(db_conn, declaration_id: int) -> None:
    """What the point of sale does from the review screen (through the core)."""
    user = db.get_user_by_username(db_conn, TEST_USERNAME)
    assert user is not None
    approve_declaration(db_conn, declaration_id, user.id)


class TestEndToEnd:
    """Full submissions into the database. These need the rewritten core
    (`validate_declaration` / `submit_declaration`) and schema of the backend
    branch; they cannot pass on the form branch alone."""

    def test_owner_with_one_card_is_stored_pending_and_creates_nothing(self, client, enabled, db_conn):
        resp = post(client, _form(), ip="7.7.7.7")
        assert resp.status_code == 200
        assert "Recibimos tu declaración" in resp.get_data(as_text=True)
        _assert_security_headers(resp)
        with db_conn.cursor() as cur:
            cur.execute("SELECT * FROM declarations")
            row = cur.fetchone()
        assert row["status"] == "pending_review" and row["review_flags"] == []
        assert row["submitter_ip"] == "7.7.7.7"
        # Nothing is created until the point of sale approves the declaration.
        assert db.get_customer_by_name(db_conn, "Maria Lopez") is None
        assert _count(db_conn, "customers") == 0 and _count(db_conn, "ownership_cards") == 0
        assert len(_files(enabled)) == 1
        # ... and approving it afterwards creates the customer and the card.
        _approve(db_conn, row["id"])
        customer = db.get_customer_by_name(db_conn, "Maria Lopez")
        assert customer is not None and customer.type == "CLIENTE"
        assert len(db.list_ownership_cards_for_customer(db_conn, customer.id)) == 1

    def test_person_with_two_cards_of_different_types_gets_one_customer_and_both_cards(
        self, client, enabled, db_conn,
    ):
        resp = post(client, _two_cards_form(acting_as="POSEEDOR"))
        assert resp.status_code == 200
        assert _count(db_conn, "declarations") == 1
        assert db.get_customer_by_name(db_conn, "Maria Lopez") is None   # pending: nothing created
        assert _count(db_conn, "ownership_cards") == 0
        _approve(db_conn, _only_declaration_id(db_conn))
        customer = db.get_customer_by_name(db_conn, "Maria Lopez")
        assert customer is not None and customer.type == "CLIENTE" and customer.document_relation == "POSEEDOR"
        cards = db.list_ownership_cards_for_customer(db_conn, customer.id)
        assert sorted(c.plate_number for c in cards) == ["ABC123", "DEF45G"]
        assert _count(db_conn, "customers") == 1
        assert len(_files(enabled)) == 1  # one signature for the whole declaration

    def test_gestor_posted_by_hand_is_rejected_without_touching_the_db(self, client, enabled, db_conn):
        resp = post(client, _two_cards_form(acting_as="GESTOR", declarant_name="Juan Gestor"))
        assert resp.status_code == 400
        assert "solo para personas que declaran por sí mismas" in resp.get_data(as_text=True)
        assert db.get_customer_by_name(db_conn, "Juan Gestor") is None
        assert _count(db_conn, "declarations") == 0 and _files(enabled) == []

    def test_owner_fields_are_never_read(self, client, enabled, db_conn):
        post(client, _form(_card(0), card_0_owner_name="Otro Dueno", card_0_owner_relation="POSEEDOR"))
        assert db.get_customer_by_name(db_conn, "Otro Dueno") is None
        _approve(db_conn, _only_declaration_id(db_conn))
        assert db.get_customer_by_name(db_conn, "Otro Dueno") is None
        owner = db.get_customer_by_name(db_conn, "Maria Lopez")
        assert owner is not None and owner.document_relation == "DUEÑO"  # PROPIETARIO

    def test_loss_card_is_accepted_without_any_file(self, client, enabled, db_conn):
        resp = post(client, _form(_card(0), _card(1, plate_number="DEF456", reason="PÉRDIDA",
                                                  transit_license="L-2")))
        assert resp.status_code == 200
        assert "Recibimos tu declaración" in resp.get_data(as_text=True)
        assert _count(db_conn, "declarations") == 1
        with db_conn.cursor() as cur:
            cur.execute("SELECT notarized_pdf_path FROM declarations")
            assert cur.fetchone()["notarized_pdf_path"] is None
            cur.execute("SELECT reason FROM declaration_cards ORDER BY id")
            assert [r["reason"] for r in cur.fetchall()] == ["DETERIORO", "PÉRDIDA"]
        assert [p.suffix for p in _files(enabled)] == [".png"]  # only the signature

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
        with db_conn.cursor() as cur:   # the unflagged one is just as pending (empty flags)
            cur.execute("SELECT status, review_flags FROM declarations ORDER BY id LIMIT 1")
            first = cur.fetchone()
        assert (first["status"], first["review_flags"]) == ("pending_review", [])
        assert _count(db_conn, "customers") == 0 and _count(db_conn, "ownership_cards") == 0
