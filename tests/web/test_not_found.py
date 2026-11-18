"""The app-wide 404 page (not_found.html, app.web.app._not_found).

Every unknown URL and every abort(404) answers the standalone Spanish page
with status 404 and Cache-Control: no-store. Its "Volver al inicio" link goes
to /declaracion for a visitor without a session under /declaracion, and to /
otherwise. It never echoes the request or the abort() message.
"""

from __future__ import annotations

NOT_FOUND_TITLE = "Página no encontrada"


def _assert_not_found(resp, back_url: str) -> str:
    assert resp.status_code == 404
    html = resp.get_data(as_text=True)
    assert NOT_FOUND_TITLE in html
    assert "Not Found" not in html
    assert f'<a class="back" href="{back_url}">Volver al inicio</a>' in html
    return html


def test_unknown_url_signed_out_links_home(client):
    _assert_not_found(client.get("/no-existe"), "/")


def test_unknown_url_under_the_public_form_links_back_to_it(client):
    _assert_not_found(client.get("/declaracion/no-existe"), "/declaracion")


def test_unknown_url_signed_in_links_home(authenticated_client):
    _assert_not_found(authenticated_client.get("/no-existe"), "/")


def test_signed_in_under_declaracion_links_home(authenticated_client):
    _assert_not_found(authenticated_client.get("/declaracion/no-existe"), "/")


def test_missing_declaration_shows_the_page_without_the_abort_message(authenticated_client):
    for path in ("/declaraciones/999999/pdf", "/declaraciones/999999/file/signature"):
        html = _assert_not_found(authenticated_client.get(path), "/")
        assert "La declaración no existe." not in html


def test_unknown_url_is_not_cached(client):
    assert client.get("/no-existe").headers["Cache-Control"] == "no-store"


def test_the_page_does_not_echo_the_request(client):
    html = _assert_not_found(client.get("/no-existe-<script>?q=<script>"), "/")
    assert "<script>" not in html
