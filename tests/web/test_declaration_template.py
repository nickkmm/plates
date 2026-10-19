"""Render tests for the public declaration form templates.

The templates are rendered directly with `render_template` inside a test
request context (the context variables mirror `public_declaration._render_form`).
"""

from __future__ import annotations

import json
import re

import pytest
from flask import render_template

from app.core.declarations import MAX_CARDS
from app.core.normalize import DOCUMENT_TYPES, PLATE_TYPES, max_plates_per_card
from app.web.app import app as flask_app

VEHICLE_TYPES = [t for t in PLATE_TYPES if t != "ORNAMENTAL"]
PERSON_LEAVES = ("name", "document_type", "document_number", "address",
                 "neighborhood", "municipality", "phone", "email")

# One person with two cards of different types (indices 0 and 2: sparse on purpose),
# a PÉRDIDA on the second.
FULL_FORM = {
    "acting_as": "POSEEDOR",
    "declarant_name": "MARIA PEREZ",
    "declarant_document_type": "CÉDULA",
    "declarant_document_number": "1234567",
    "declarant_phone": "3109998877",
    "card_0_plate_number": "ABC123",
    "card_0_vehicle_type": "MOTO PARTICULAR",
    "card_0_vehicle_brand": "AKT",
    "card_0_reason": "DETERIORO",
    "card_0_plate_quantity": "1",
    "card_2_plate_number": "XYZ987",
    "card_2_vehicle_type": "CARRO PARTICULAR",
    "card_2_reason": "PÉRDIDA",
    "card_2_plate_quantity": "2",
    "accept_terms": "1",
}


def render(**overrides) -> str:
    form = overrides.get("form") or {}
    indices = sorted({int(m.group(1)) for k in form if (m := re.match(r"card_(\d+)_", k))}) or [0]
    ctx = {
        "form": {},
        "errors": [],
        "token": "tok-abc",
        "turnstile_site_key": "site-key-1",
        "reasons": ("DETERIORO", "PÉRDIDA"),
        "document_types": DOCUMENT_TYPES,
        "vehicle_types": VEHICLE_TYPES,
        "card_indices": indices,
        "max_cards": MAX_CARDS,
        "max_plates_by_type": {t: max_plates_per_card(t) for t in VEHICLE_TYPES},
        "total_plates": 1,
    }
    ctx.update(overrides)
    with flask_app.test_request_context("/declaracion"):
        return render_template("declaration.html", **ctx)


def split(html: str) -> tuple[str, str]:
    """(markup outside the <template>, the <template> contents)."""
    m = re.search(r'<template id="cardTemplate">(.*?)</template>', html, re.S)
    assert m, "no card template"
    return html.replace(m.group(0), ""), m.group(1)


def tag(html: str, name: str) -> str:
    """The first <input|select|textarea ...> opening tag with that name (outside the <template>)."""
    if '<template id="cardTemplate">' in html:
        html = split(html)[0]
    m = re.search(rf'<(?:input|select|textarea)\b[^>]*\bname="{re.escape(name)}"[^>]*>', html)
    assert m, f"no control named {name}"
    return m.group(0)


def card_blocks(html: str) -> list[str]:
    outside = split(html)[0]
    parts = re.split(r'(?=<div class="card-block" data-card=")', outside)
    return [p for p in parts if p.startswith('<div class="card-block"')]


def card_names(i) -> list[str]:
    leaves = [
        "plate_number", "transit_license", "engine_number", "chassis_number", "vehicle_type",
        "registered_in", "vehicle_brand", "reason", "plate_quantity",
    ]
    return [f"card_{i}_{leaf}" for leaf in leaves]


ALL_FIELDS = [
    "acting_as", *[f"declarant_{s}" for s in PERSON_LEAVES], *card_names(0),
    "signature", "accept_terms", "form_token", "website",
]


def test_empty_form_renders_all_fields_standalone():
    html = render()
    for name in ALL_FIELDS:
        tag(html, name)
    assert '<form id="declForm" method="post" action="/declaracion"' in html
    assert 'lang="es"' in html and 'name="viewport"' in html
    # standalone: nothing from _nav.html
    assert "Salir" not in html and "logout" not in html and 'class="tabs"' not in html
    assert "Registros" not in html
    # empty form: nothing checked
    assert not re.search(r"<input[^>]* checked", split(html)[0])
    # the declarant block is always live
    assert "disabled" not in tag(html, "declarant_name")
    assert "required" in tag(html, "declarant_name")


def test_old_single_card_and_double_signature_fields_are_gone():
    html = render()
    for old in ("owner_signature", "gestor_signature", 'name="reason"', 'name="plate_quantity"',
                'name="vehicle_plate_number"', 'name="owner_name"', 'name="gestor_name"',
                'name="owner_relation"', "gestorBlock"):
        assert old not in html, old
    assert html.count('type="hidden" name="signature"') == 1
    assert html.count("<canvas") == 1


def test_one_server_rendered_block_is_first_and_has_no_remove_dependency_on_js_ids():
    html = render()
    assert len(card_blocks(html)) == 1
    assert 'data-card="0"' in html
    assert "Tarjeta de propiedad <span class=\"card-number\">1</span>" in html
    assert "+ Agregar tarjeta de propiedad" in html
    assert ">Quitar<" in html


def test_card_template_is_a_clone_source_with_a_placeholder_index():
    html = render()
    outside, tpl = split(html)
    assert "card___I__" not in outside
    for name in card_names("__I__"):
        assert f'name="{name}"' in tpl, name
    assert tpl.count('class="card-block"') == 1


def test_hidden_token_honeypot_and_signature_inputs():
    html = render()
    assert 'name="form_token" value="tok-abc"' in html
    hp = tag(html, "website")
    assert 'tabindex="-1"' in hp and 'autocomplete="off"' in hp
    assert 'class="hp" aria-hidden="true"' in html
    assert "left:-10000px" in html
    t = tag(html, "signature")
    assert 'type="hidden"' in t and "disabled" in t  # nothing submitted until drawn
    assert "touch-action:none" in html
    assert html.count("Borrar firma") >= 1


def test_there_is_no_file_upload_and_the_form_is_not_multipart():
    html = render()
    assert 'type="file"' not in html
    assert "multipart" not in html
    assert "notarized" not in html.lower() and "autenticado" not in html.lower()
    assert "adjunt" not in html.lower()


def test_loss_note_is_in_every_card_block_and_visible_without_js():
    note = ("Si declaras pérdida, INGSE te entregará este documento en el mostrador "
            "para que lo autentiques en una notaría.")
    html = render(form=FULL_FORM)
    blocks = card_blocks(html)
    assert len(blocks) == 2
    for block in blocks:
        assert note in block
        # plain note: not hidden in the markup (the script hides it for non-PÉRDIDA cards)
        assert re.search(r'<div class="note loss-note">', block)
    assert "loss-note" in split(html)[1]  # the clone template carries it too
    assert "querySelector('.loss-note')" in html and "note.hidden = " in html


def test_submit_and_footer():
    html = render()
    assert "Enviar declaración" in html
    assert "La huella se toma en el mostrador si INGSE la requiere" in html
    assert "name=\"fingerprint\"" not in html and "huella_" not in html


def test_real_document_wording_present():
    html = render()
    for text in (
        "1. Calidad en la que actúa", "2. Datos del declarante", "3. Tarjetas de propiedad",
        "4. DECLARACIÓN Y AUTORIZACIÓN", "5. AVISO LEGAL", "6. COMPROMISOS Y RESPONSABILIDAD DEL CLIENTE",
        "7. Firma", "señalada(s) en la sección 3",
        "Falsedad en documento privado (art. 289 C.P.)", "NIT 900.773.756-0",
        "servicioalcliente@ingse.co", "Calle 42 # 41-47",
        "He leído y entiendo el aviso legal y acepto la declaración",
        "Pérdida o hurto de la(s) placa(s)", "Propietario (tarjeta de propiedad)",
        "Total de placas a fabricar",
    ):
        assert text in html, text


def test_form_is_only_for_individuals_no_gestor_or_per_card_owner_ui():
    html = render()
    # only PROPIETARIO and POSEEDOR are offered
    assert re.findall(r'name="acting_as" value="(\w+)"', html) == ["PROPIETARIO", "POSEEDOR"]
    assert "Este formulario es para personas que declaran por sí mismas" in html
    # no gestor wording, no per-card owner blocks, no "copy previous owner", no gestor JS
    for text in ("gestor", "Gestor", "GESTOR", "owner", "El dueño es", "Copiar los datos", "dueño",
                 "DUEÑO", "copy-owner", "copy-row", "isGestor", "empresa que solicita"):
        assert text not in html, text
    # the five numbered sections are all still there, in order
    positions = [html.index(t) for t in (
        "1. Calidad en la que actúa", "2. Datos del declarante", "3. Tarjetas de propiedad",
        "4. DECLARACIÓN Y AUTORIZACIÓN", "5. AVISO LEGAL", "6. COMPROMISOS", "7. Firma")]
    assert positions == sorted(positions)


def test_select_options_come_from_context():
    html = render()
    for t in DOCUMENT_TYPES:
        assert f'<option value="{t}"' in html
    for t in VEHICLE_TYPES:
        assert f'<option value="{t}"' in html
    assert "ORNAMENTAL" not in html


def test_max_plates_by_type_is_passed_to_the_page_as_json():
    html = render()
    m = re.search(r"data-max-by-type='([^']*)'", html)
    assert m
    data = json.loads(m.group(1))
    assert data["CARRO PARTICULAR"] == 2 and data["CARRO PÚBLICO"] == 2
    assert data["MOTO PARTICULAR"] == 1 and data["REMOLQUE"] == 1
    assert f'data-max-cards="{MAX_CARDS}"' in html


def test_populated_form_is_sticky_with_sparse_indices():
    html = render(form=FULL_FORM)
    assert 'value="MARIA PEREZ"' in tag(html, "declarant_name")
    assert 'value="3109998877"' in tag(html, "declarant_phone")
    assert re.search(r'name="acting_as" value="POSEEDOR" checked', html)
    assert not re.search(r'name="acting_as" value="PROPIETARIO"[^>]*checked', html)
    assert "checked" in tag(html, "accept_terms")
    blocks = card_blocks(html)
    assert [re.match(r'<div class="card-block" data-card="(\d+)"', b).group(1) for b in blocks] == ["0", "2"]
    first, second = blocks
    assert 'value="ABC123"' in tag(first, "card_0_plate_number")
    assert 'value="AKT"' in tag(first, "card_0_vehicle_brand")
    assert 'value="XYZ987"' in tag(second, "card_2_plate_number")
    assert '<option value="MOTO PARTICULAR" selected>' in first
    assert '<option value="CARRO PARTICULAR" selected>' in second
    assert '<option value="CÉDULA" selected>' in html
    # reasons are per card
    assert re.search(r'name="card_0_reason" value="DETERIORO" required checked', first)
    assert re.search(r'name="card_2_reason" value="PÉRDIDA" checked', second)
    assert not re.search(r'name="card_2_reason" value="DETERIORO"[^>]*checked', second)


def test_plate_quantity_select_follows_the_card_type():
    html = render(form=FULL_FORM)
    first, second = card_blocks(html)
    # the motorcycle only offers 1; the car offers 1 and 2 with the stored 2 selected
    assert re.findall(r'<option value="(\d)"', first.split("card_0_plate_quantity")[1].split("</select>")[0]) == ["1"]
    car = second.split("card_2_plate_quantity")[1].split("</select>")[0]
    assert re.findall(r'<option value="(\d)"', car) == ["1", "2"]
    assert '<option value="2" selected>' in car


def test_car_defaults_to_two_plates_and_a_new_block_to_one():
    car = render(form={"card_0_vehicle_type": "CARRO PARTICULAR"})
    assert '<option value="2" selected>' in car
    blank = render()
    assert '<option value="1" selected>' in blank


def test_total_plates_is_an_output_not_an_input():
    html = render(total_plates=3)
    assert re.search(r'<output id="totalPlates"[^>]*>3</output>', html)
    assert 'name="total' not in html and 'name="plate_quantity"' not in html


@pytest.mark.parametrize("acting", ["PROPIETARIO", "POSEEDOR"])
def test_owner_or_possessor_is_checked(acting):
    html = render(form={"acting_as": acting, "card_0_reason": "DETERIORO"})
    assert re.search(rf'name="acting_as" value="{acting}"( required)? checked', html)


def test_a_posted_gestor_is_not_pre_selected_on_the_re_rendered_form():
    html = render(form={"acting_as": "GESTOR", "card_0_reason": "DETERIORO"})
    assert not re.search(r'name="acting_as"[^>]* checked', html)
    assert "gestor" not in html.lower()


def test_form_values_are_escaped():
    html = render(form={"declarant_name": '"><script>alert(1)</script>',
                        "card_0_plate_number": '"><img src=x onerror=1>'})
    assert "<script>alert(1)</script>" not in html
    assert "<img src=x" not in html


def test_errors_banner_and_resign_note():
    html = render(errors=["La placa no es válida.", "Falta la firma."])
    assert "La placa no es válida." in html and "Falta la firma." in html
    assert 'class="error" role="alert"><ul>' in html
    assert html.count("Vuelve a firmar.") == 1
    assert "La placa no es válida." not in render()
    assert "Vuelve a firmar" not in render()


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
    # Bare context (what an old caller would pass): the page must still render.
    with flask_app.test_request_context("/declaracion"):
        html = render_template(name, form={}, errors=[], token="t", turnstile_site_key="",
                               reasons=(), document_types=(), vehicle_types=())
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


def test_the_name_input_refuses_digits_in_the_browser():
    html = render()
    assert 'name="declarant_name"' in html and 'pattern="[^0-9]*"' in html
