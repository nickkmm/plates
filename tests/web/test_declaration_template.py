"""Render tests for the public declaration form templates.

The blueprint that serves these doesn't exist yet, so the templates are
rendered directly with `render_template` inside a test request context.
"""

from __future__ import annotations

import re

import pytest
from flask import render_template

from app.core.normalize import DOCUMENT_TYPES, PLATE_TYPES
from app.web.app import app as flask_app

FULL_FORM = {
    "reason": "PÉRDIDA",
    "plate_quantity": "2",
    "acting_as": "GESTOR",
    "owner_name": "MARIA PEREZ",
    "owner_document_type": "CÉDULA",
    "owner_document_number": "1234567",
    "owner_address": "CALLE 1 # 2-3",
    "owner_neighborhood": "CENTRO",
    "owner_municipality": "PALMIRA",
    "owner_phone": "3001112233",
    "owner_email": "maria@example.com",
    "owner_relation": "POSEEDOR",
    "gestor_name": "JUAN GESTOR",
    "gestor_document_type": "NIT",
    "gestor_document_number": "900111222",
    "gestor_phone": "3109998877",
    "vehicle_plate_number": "ABC123",
    "vehicle_type": "MOTO PARTICULAR",
    "vehicle_brand": "AKT",
    "accept_terms": "1",
}


def render(**overrides) -> str:
    ctx = {
        "form": {},
        "errors": [],
        "token": "tok-abc",
        "turnstile_site_key": "site-key-1",
        "reasons": ("DETERIORO", "PÉRDIDA"),
        "document_types": DOCUMENT_TYPES,
        "vehicle_types": PLATE_TYPES,
        "owner_relations": ("DUEÑO", "POSEEDOR"),
    }
    ctx.update(overrides)
    with flask_app.test_request_context("/declaracion"):
        return render_template("declaration.html", **ctx)


def tag(html: str, name: str) -> str:
    """The first <input|select|textarea ...> opening tag with that name."""
    m = re.search(rf'<(?:input|select|textarea)\b[^>]*\bname="{re.escape(name)}"[^>]*>', html)
    assert m, f"no control named {name}"
    return m.group(0)


ALL_FIELDS = [
    "reason", "plate_quantity", "acting_as",
    *[f"owner_{s}" for s in ("name", "document_type", "document_number", "address",
                             "neighborhood", "municipality", "phone", "email", "relation")],
    *[f"gestor_{s}" for s in ("name", "document_type", "document_number", "address",
                              "neighborhood", "municipality", "phone", "email")],
    "vehicle_plate_number", "vehicle_transit_license", "vehicle_engine_number",
    "vehicle_chassis_number", "vehicle_type", "vehicle_registered_in", "vehicle_brand",
    "notarized_pdf", "owner_signature", "gestor_signature", "accept_terms",
    "form_token", "website",
]


def test_empty_form_renders_all_fields_standalone():
    html = render()
    for name in ALL_FIELDS:
        tag(html, name)
    assert '<form id="declForm" method="post" action="/declaracion" enctype="multipart/form-data">' in html
    assert 'lang="es"' in html and 'name="viewport"' in html
    # standalone: nothing from _nav.html
    assert "Salir" not in html and "logout" not in html and 'class="tabs"' not in html
    assert "Registros" not in html
    # empty form: nothing checked, hidden blocks disabled
    assert not re.search(r"<input[^>]* checked", html)
    assert "disabled" in tag(html, "owner_relation")
    assert "disabled" in tag(html, "gestor_name")
    assert "disabled" in tag(html, "notarized_pdf")
    assert 'id="gestorBlock" hidden' in html
    assert 'id="notarizedWrap" hidden' in html


def test_hidden_token_honeypot_and_signature_inputs():
    html = render()
    assert 'name="form_token" value="tok-abc"' in html
    hp = tag(html, "website")
    assert 'tabindex="-1"' in hp and 'autocomplete="off"' in hp
    assert 'class="hp" aria-hidden="true"' in html
    assert "left:-10000px" in html
    for name in ("owner_signature", "gestor_signature"):
        t = tag(html, name)
        assert 'type="hidden"' in t and "disabled" in t  # nothing submitted until drawn
    assert 'touch-action:none' in html
    assert html.count("Borrar firma") >= 2


def test_pdf_accept_and_submit_and_footer():
    html = render()
    assert 'accept="application/pdf"' in tag(html, "notarized_pdf")
    assert "Enviar declaración" in html
    assert "La huella se toma en el mostrador si INGSE la requiere" in html
    assert "name=\"fingerprint\"" not in html and "huella_" not in html


def test_real_document_wording_present():
    html = render()
    for text in (
        "1. Motivo de la solicitud", "2. Calidad en la que actúa", "3. Datos del declarante",
        "4. Datos del vehículo", "5. DECLARACIÓN Y AUTORIZACIÓN", "6. AVISO LEGAL",
        "7. COMPROMISOS Y RESPONSABILIDAD DEL CLIENTE", "8. Firma",
        "Falsedad en documento privado (art. 289 C.P.)", "NIT 900.773.756-0",
        "servicioalcliente@ingse.co", "Calle 42 # 41-47",
        "He leído y entiendo el aviso legal y acepto la declaración",
        "Pérdida o hurto de la(s) placa(s)", "Propietario (tarjeta de propiedad)",
    ):
        assert text in html, text


def test_select_options_come_from_context():
    html = render()
    for t in DOCUMENT_TYPES:
        assert f'<option value="{t}"' in html
    for t in PLATE_TYPES:
        assert f'<option value="{t}"' in html
    assert '<option value="DUEÑO"' in html and '<option value="POSEEDOR"' in html


def test_populated_form_is_sticky():
    html = render(form=FULL_FORM)
    assert 'value="MARIA PEREZ"' in tag(html, "owner_name")
    assert 'value="ABC123"' in tag(html, "vehicle_plate_number")
    assert 'value="2"' in tag(html, "plate_quantity")
    assert 'value="3001112233"' in tag(html, "owner_phone")
    assert 'value="JUAN GESTOR"' in tag(html, "gestor_name")
    assert 'value="AKT"' in tag(html, "vehicle_brand")
    assert "checked" in tag(html, "accept_terms")
    # radios: only the chosen ones are checked
    assert re.search(r'name="reason" value="PÉRDIDA" checked', html)
    assert not re.search(r'name="reason" value="DETERIORO"[^>]*checked', html)
    assert re.search(r'name="acting_as" value="GESTOR" checked', html)
    assert not re.search(r'name="acting_as" value="PROPIETARIO"[^>]*checked', html)
    # selects
    assert '<option value="CÉDULA" selected>' in html
    assert '<option value="NIT" selected>' in html
    assert '<option value="MOTO PARTICULAR" selected>' in html
    assert '<option value="POSEEDOR" selected>' in html
    # GESTOR + PÉRDIDA: blocks visible and enabled, pdf required
    assert 'id="gestorBlock" hidden' not in html
    assert 'id="ownerRelationWrap" hidden' not in html
    assert 'id="notarizedWrap" hidden' not in html
    assert "disabled" not in tag(html, "owner_relation")
    assert "disabled" not in tag(html, "gestor_name")
    nt = tag(html, "notarized_pdf")
    assert "disabled" not in nt and "required" in nt


def test_propietario_hides_gestor_and_relation():
    html = render(form={"acting_as": "PROPIETARIO", "reason": "DETERIORO"})
    assert re.search(r'name="acting_as" value="PROPIETARIO" required checked', html)
    assert "disabled" in tag(html, "owner_relation")
    assert "disabled" in tag(html, "gestor_document_number")
    assert "disabled" in tag(html, "notarized_pdf")


def test_form_values_are_escaped():
    html = render(form={"owner_name": '"><script>alert(1)</script>'})
    assert "<script>alert(1)</script>" not in html


def test_errors_banner_and_reattach_note():
    html = render(errors=["La placa no es válida.", "Falta la firma."])
    assert "La placa no es válida." in html and "Falta la firma." in html
    assert 'class="error" role="alert"><ul>' in html
    assert "Vuelve a adjuntar el PDF y a firmar" in html
    assert "La placa no es válida." not in render()
    assert "Vuelve a adjuntar" not in render()


def test_reattach_note_next_to_pdf_only_for_perdida():
    perdida = render(errors=["x"], form={"reason": "PÉRDIDA"})
    deterioro = render(errors=["x"], form={"reason": "DETERIORO"})
    assert perdida.count("Vuelve a adjuntar el PDF y a firmar") == 2  # next to the PDF + signatures
    assert deterioro.count("Vuelve a adjuntar el PDF y a firmar") == 1


def test_turnstile_with_and_without_site_key():
    with_key = render(turnstile_site_key="site-key-1")
    assert 'class="cf-turnstile" data-sitekey="site-key-1"' in with_key
    assert 'src="https://challenges.cloudflare.com/turnstile/v0/api.js"' in with_key
    without = render(turnstile_site_key="")
    assert "cf-turnstile" not in without and "challenges.cloudflare.com" not in without


def test_no_external_resources_besides_turnstile():
    html = render()
    assert re.findall(r'(?:src|href)="https?://[^"]+"', html) == [
        'src="https://challenges.cloudflare.com/turnstile/v0/api.js"'
    ]


@pytest.mark.parametrize("name", ["declaration.html", "declaration_received.html"])
def test_templates_are_standalone(name):
    with flask_app.test_request_context("/declaracion"):
        html = render_template(name, form={}, errors=[], token="t", turnstile_site_key="",
                               reasons=(), document_types=(), vehicle_types=(), owner_relations=())
    assert 'class="tabs"' not in html and "Salir" not in html
    assert html.lstrip().startswith("<!doctype html>")


def test_received_page_is_generic():
    with flask_app.test_request_context("/declaracion/recibida"):
        html = render_template("declaration_received.html")
    assert "Recibimos tu declaración" in html
    assert "Un funcionario de INGSE la revisará y te contactará si hace falta algo" in html
    low = html.lower()
    for word in ("aprobad", "conflicto", "rechazad", "duplicad", "revisión manual"):
        assert word not in low, word
