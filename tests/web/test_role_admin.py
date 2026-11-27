"""Integration tests for the "Roles y permisos" section of the admin users
screen: GET /usuarios/roles, GET/POST /usuarios/roles/nuevo,
GET/POST /usuarios/roles/<name>, POST /usuarios/roles/<name>/delete.

The contract under test:
- Only `users.manage` (seeded for admin only) opens these routes; production
  and point_of_sale get 403 and change nothing.
- A new role can be assigned right away, and a permission change applies on
  each holder's next request.
- Every rule of app/core/service.py re-renders the form with status 400
  and its message, and writes nothing.
- admin is read-only, system roles keep their label and are never deleted,
  and a role some user holds can't be deleted.
- No page shows an internal role name as text.

Needs a real Postgres connection (`db_conn`/`clean_db`).
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

import pytest
from werkzeug.security import generate_password_hash

from app.auth import repository, service
from app.web.app import app as flask_app
from tests.conftest import PRODUCCION_PASSWORD, PRODUCCION_USERNAME, TEST_PASSWORD, TEST_USERNAME

INTERNAL_NAMES = ("custom_", "point_of_sale", "production", "admin")
ADMIN_READ_ONLY = "El rol Administrador siempre tiene todos los permisos y no se puede editar."


def _snapshot(conn):
    """Every role with its label, flag and stored permissions, to assert
    nothing changed."""
    return [(r.name, r.label, r.is_system, r.permissions) for r in repository.list_roles(conn)]


def _role_by_label(conn, label):
    matches = [r for r in repository.list_roles(conn) if r.label == label]
    assert len(matches) == 1, label
    return matches[0]


def _visible_text(html):
    """The page without its tags (and so without attribute values) and
    without the inline <style>."""
    html = re.sub(r"<style.*?</style>", " ", html, flags=re.S)
    return re.sub(r"<[^>]+>", " ", html)


@pytest.fixture(autouse=True)
def _restore_csrf_setting():
    """_fresh_client turns CSRF off; put the earlier value back afterwards."""
    previous = flask_app.config["WTF_CSRF_ENABLED"]
    yield
    flask_app.config["WTF_CSRF_ENABLED"] = previous


def _fresh_client(username=None, password=None):
    """A browser of its own, optionally logged in (see tests/web/test_users.py)."""
    flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    client = flask_app.test_client()
    if username is not None:
        client.post("/login", data={"username": username, "password": password})
    return client


def _create(client, label="Bodega", permissions=("inventory.manage",)):
    return client.post("/usuarios/roles/nuevo", data={"label": label, "permissions": list(permissions)})


class TestCreateAndAssign:
    def test_admin_creates_a_role_and_assigns_it(self, db_conn):
        admin = _fresh_client(TEST_USERNAME, TEST_PASSWORD)
        resp = _create(admin)
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/usuarios/roles"
        html = admin.get("/usuarios/roles").get_data(as_text=True)
        assert "Rol creado." in html
        assert "Bodega" in html
        role = _role_by_label(db_conn, "Bodega")
        assert role.permissions == ["inventory.manage"]
        assert not role.is_system

        target = repository.get_user_by_username(db_conn, PRODUCCION_USERNAME)
        assert target is not None
        resp = admin.post(f"/usuarios/{target.id}", data={"roles": ["production", role.name], "active": "1"})
        assert resp.status_code == 302

        user = _fresh_client(PRODUCCION_USERNAME, PRODUCCION_PASSWORD)
        assert user.get("/inventory").status_code == 200
        assert user.get("/records").status_code == 200
        assert user.get("/precios").status_code == 403

        html = admin.get(f"/usuarios/{target.id}").get_data(as_text=True)
        assert "Bodega" in html
        assert f'value="{role.name}" checked' in html

    def test_new_form(self, authenticated_client, db_conn):
        html = authenticated_client.get("/usuarios/roles/nuevo").get_data(as_text=True)
        assert "<h1>Nuevo rol</h1>" in html
        assert 'name="label"' in html
        assert "Nombre del rol" in html
        assert "Crear rol" in html
        assert 'name="csrf_token"' in html
        assert 'href="/usuarios/roles"' in html
        codes = [p.code for p in repository.list_permissions(db_conn)]
        positions = [html.index(f'name="permissions" value="{code}"') for code in codes]
        assert positions == sorted(positions)
        for permission in repository.list_permissions(db_conn):
            assert permission.label in html

    def test_unknown_codes_are_ignored(self, authenticated_client, db_conn):
        resp = _create(authenticated_client, permissions=["inventory.manage", "superpoder"])
        assert resp.status_code == 302
        assert _role_by_label(db_conn, "Bodega").permissions == ["inventory.manage"]

    def test_sixty_character_label_is_stored_trimmed(self, authenticated_client, db_conn):
        label = "x" * 60
        resp = _create(authenticated_client, label=f"  {label}  ")
        assert resp.status_code == 302
        assert _role_by_label(db_conn, label)


class TestPermissionChanges:
    def test_removing_a_permission_applies_on_the_next_request(self, db_conn):
        admin = _fresh_client(TEST_USERNAME, TEST_PASSWORD)
        production = _fresh_client(PRODUCCION_USERNAME, PRODUCCION_PASSWORD)
        assert production.get("/records").status_code == 200

        role = repository.get_role(db_conn, "production")
        assert role is not None
        kept = [code for code in role.permissions if code != "records.view"]
        resp = admin.post("/usuarios/roles/production", data={"label": "Producción", "permissions": kept})
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/usuarios/roles/production"
        assert "Cambios guardados." in admin.get("/usuarios/roles/production").get_data(as_text=True)
        assert production.get("/records").status_code == 403

    def test_removing_production_manage(self, authenticated_client, db_conn):
        user = repository.get_user_by_username(db_conn, PRODUCCION_USERNAME)
        assert user is not None
        before = repository.get_user_permissions(db_conn, user.id)
        assert "production.manage" in before
        role = repository.get_role(db_conn, "production")
        assert role is not None
        kept = [code for code in role.permissions if code != "production.manage"]
        resp = authenticated_client.post("/usuarios/roles/production", data={"permissions": kept})
        assert resp.status_code == 302
        after = repository.get_user_permissions(db_conn, user.id)
        assert after == before - {"production.manage"}


class TestValidation:
    @pytest.mark.parametrize(
        ("label", "permissions", "message"),
        [
            ("", ["inventory.manage"], "Escribe el nombre del rol."),
            ("   ", ["inventory.manage"], "Escribe el nombre del rol."),
            ("x" * 61, ["inventory.manage"], "Escribe el nombre del rol."),
            ("PRODUCCION", ["inventory.manage"], "Ya existe un rol con ese nombre."),
            ("Bodega", [], "Marca al menos un permiso."),
            ("Bodega", ["superpoder"], "Marca al menos un permiso."),
        ],
        ids=["empty", "whitespace", "61-characters", "duplicate-system", "no-permission", "unknown-only"],
    )
    def test_create_rejected_with_400_and_nothing_written(
        self, authenticated_client, db_conn, label, permissions, message
    ):
        before = _snapshot(db_conn)
        resp = _create(authenticated_client, label=label, permissions=permissions)
        html = resp.get_data(as_text=True)
        assert resp.status_code == 400
        assert f'<div class="error">{message}</div>' in html
        assert _snapshot(db_conn) == before

    def test_duplicate_ignores_case_and_accents(self, authenticated_client, db_conn):
        repository.create_role(db_conn, name="custom_bodega", label="Bódega", permissions=["inventory.manage"])
        before = _snapshot(db_conn)
        resp = _create(authenticated_client, label="bodega")
        assert resp.status_code == 400
        assert "Ya existe un rol con ese nombre." in resp.get_data(as_text=True)
        assert _snapshot(db_conn) == before

    def test_rerender_keeps_the_submitted_values(self, authenticated_client):
        resp = _create(authenticated_client, label="Bodega", permissions=[])
        html = resp.get_data(as_text=True)
        assert 'value="Bodega"' in html
        resp = _create(authenticated_client, label="", permissions=["records.view"])
        html = resp.get_data(as_text=True)
        assert 'value="records.view" checked' in html
        assert 'value="inventory.manage" checked' not in html

    def test_edit_rejected_with_400_and_nothing_written(self, authenticated_client, db_conn):
        repository.create_role(db_conn, name="custom_bodega", label="Bodega", permissions=["inventory.manage"])
        before = _snapshot(db_conn)
        for form, message in (
            ({"label": "", "permissions": ["inventory.manage"]}, service.MSG_BAD_ROLE_LABEL),
            ({"label": "Punto de Venta", "permissions": ["inventory.manage"]},
             service.MSG_DUPLICATE_ROLE_LABEL),
            ({"label": "Bodega"}, service.MSG_NO_PERMISSION),
        ):
            resp = authenticated_client.post("/usuarios/roles/custom_bodega", data=form)
            assert resp.status_code == 400
            assert message in resp.get_data(as_text=True)
            assert _snapshot(db_conn) == before

    def test_custom_role_can_take_its_own_label_with_other_casing(self, authenticated_client, db_conn):
        repository.create_role(db_conn, name="custom_bodega", label="Bodega", permissions=["inventory.manage"])
        resp = authenticated_client.post(
            "/usuarios/roles/custom_bodega", data={"label": "BODEGA", "permissions": ["records.view"]}
        )
        assert resp.status_code == 302
        role = repository.get_role(db_conn, "custom_bodega")
        assert role is not None
        assert role.label == "BODEGA"
        assert role.permissions == ["records.view"]


class TestSystemRoles:
    def test_admin_page_is_read_only(self, authenticated_client, db_conn):
        html = authenticated_client.get("/usuarios/roles/admin").get_data(as_text=True)
        assert ADMIN_READ_ONLY in html
        assert "Guardar cambios" not in html
        assert "Eliminar rol" not in html
        assert 'name="label"' not in html
        for permission in repository.list_permissions(db_conn):
            assert f'name="permissions" value="{permission.code}" checked disabled' in html

    def test_admin_post_is_400_and_writes_nothing(self, authenticated_client, db_conn):
        before = repository.get_role_permissions(db_conn, "admin")
        resp = authenticated_client.post(
            "/usuarios/roles/admin", data={"label": "Jefe", "permissions": ["records.view"]}
        )
        assert resp.status_code == 400
        assert ADMIN_READ_ONLY in resp.get_data(as_text=True)
        assert repository.get_role_permissions(db_conn, "admin") == before
        role = repository.get_role(db_conn, "admin")
        assert role is not None and role.label == "Administrador"

    def test_system_role_label_is_ignored(self, authenticated_client, db_conn):
        resp = authenticated_client.post(
            "/usuarios/roles/production", data={"label": "Fábrica", "permissions": ["records.view"]}
        )
        assert resp.status_code == 302
        role = repository.get_role(db_conn, "production")
        assert role is not None
        assert role.label == "Producción"
        assert role.permissions == ["records.view"]

    def test_system_role_page(self, authenticated_client):
        html = authenticated_client.get("/usuarios/roles/production").get_data(as_text=True)
        assert "<h1>Producción</h1>" in html
        assert 'name="label"' not in html
        assert 'value="Producción" disabled' in html
        assert "Guardar cambios" in html
        assert 'value="records.view" checked' in html
        assert "Eliminar rol" not in html
        assert "¿Eliminar el rol " not in html

    @pytest.mark.parametrize("name", ["production", "admin"])
    def test_system_roles_are_never_deleted(self, authenticated_client, db_conn, name):
        before = _snapshot(db_conn)
        resp = authenticated_client.post(f"/usuarios/roles/{name}/delete")
        assert resp.status_code == 400
        assert "No se puede eliminar un rol del sistema." in resp.get_data(as_text=True)
        assert _snapshot(db_conn) == before


class TestDelete:
    def test_role_held_by_an_inactive_user_is_kept(self, authenticated_client, db_conn):
        repository.create_role(db_conn, name="custom_bodega", label="Bodega", permissions=["inventory.manage"])
        repository.create_user(
            db_conn, username="bodeguero", password_hash=generate_password_hash("x"), roles=["custom_bodega"],
            active=False,
        )
        resp = authenticated_client.post("/usuarios/roles/custom_bodega/delete")
        assert resp.status_code == 400
        assert "No se puede eliminar un rol con usuarios asignados." in resp.get_data(as_text=True)
        assert repository.get_role(db_conn, "custom_bodega") is not None

    def test_unused_custom_role_is_deleted(self, authenticated_client, db_conn):
        repository.create_role(db_conn, name="custom_bodega", label="Bodega", permissions=["inventory.manage"])
        resp = authenticated_client.post("/usuarios/roles/custom_bodega/delete")
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/usuarios/roles"
        assert "Rol eliminado." in authenticated_client.get("/usuarios/roles").get_data(as_text=True)
        assert repository.get_role(db_conn, "custom_bodega") is None

    def test_user_assigned_in_between_maps_to_the_in_use_message(
        self, authenticated_client, db_conn, monkeypatch
    ):
        repository.create_role(db_conn, name="custom_bodega", label="Bodega", permissions=["inventory.manage"])
        real_delete_role = repository.delete_role

        def _assign_then_delete(conn, name):
            repository.create_user(
                conn, username="bodeguero", password_hash=generate_password_hash("x"), roles=[name]
            )
            return real_delete_role(conn, name)

        monkeypatch.setattr(repository, "delete_role", _assign_then_delete)
        resp = authenticated_client.post("/usuarios/roles/custom_bodega/delete")
        assert resp.status_code == 400
        assert "No se puede eliminar un rol con usuarios asignados." in resp.get_data(as_text=True)
        assert repository.get_role(db_conn, "custom_bodega") is not None

    def test_custom_role_page_has_the_delete_button(self, authenticated_client, db_conn):
        repository.create_role(db_conn, name="custom_bodega", label="Bo'dega \"x\"", permissions=["inventory.manage"])
        html = authenticated_client.get("/usuarios/roles/custom_bodega").get_data(as_text=True)
        assert "Eliminar rol" in html
        assert "¿Eliminar el rol " in html
        assert 'action="/usuarios/roles/custom_bodega/delete"' in html
        delete_form = html[html.index('action="/usuarios/roles/custom_bodega/delete"'):]
        assert 'name="csrf_token"' in delete_form[: delete_form.index("</form>")]
        assert "onsubmit='return confirm(\"¿Eliminar el rol \" + \"Bo\\u0027dega \\\"x\\\"\" + \"?\")'" in html


class TestLabelRace:
    def test_unique_violation_on_create_maps_to_the_duplicate_message(
        self, authenticated_client, db_conn, monkeypatch
    ):
        monkeypatch.setattr(service, "validate_role", lambda *args, **kwargs: None)
        before = _snapshot(db_conn)
        resp = _create(authenticated_client, label="Producción")
        assert resp.status_code == 400
        assert "Ya existe un rol con ese nombre." in resp.get_data(as_text=True)
        assert _snapshot(db_conn) == before

    def test_unique_violation_on_update_maps_to_the_duplicate_message(
        self, authenticated_client, db_conn, monkeypatch
    ):
        repository.create_role(db_conn, name="custom_bodega", label="Bodega", permissions=["inventory.manage"])
        monkeypatch.setattr(service, "validate_role", lambda *args, **kwargs: None)
        before = _snapshot(db_conn)
        resp = authenticated_client.post(
            "/usuarios/roles/custom_bodega", data={"label": "Producción", "permissions": ["records.view"]}
        )
        assert resp.status_code == 400
        assert "Ya existe un rol con ese nombre." in resp.get_data(as_text=True)
        assert _snapshot(db_conn) == before


class TestList:
    def test_list_page(self, authenticated_client, db_conn):
        repository.create_role(db_conn, name="custom_bodega", label="Bodega", permissions=["inventory.manage"])
        repository.create_user(
            db_conn, username="bodeguero", password_hash=generate_password_hash("x"), roles=["custom_bodega"],
        )
        resp = authenticated_client.get("/usuarios/roles")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "<h1>Roles y permisos</h1>" in html
        assert "<th>Rol</th><th>Permisos</th><th>Usuarios</th><th>Editar</th>" in html
        text = re.sub(r"\s+", " ", _visible_text(html))
        assert "Administrador (del sistema) Todos 1 Editar" in text
        assert "Punto de venta (del sistema)" in text
        assert "Producción (del sistema) 4 1 Editar" in text
        assert "Bodega 1 1 Editar" in text
        assert "Bodega (del sistema)" not in text
        assert "Nuevo rol" in html and 'href="/usuarios/roles/nuevo"' in html
        for name in ("admin", "point_of_sale", "production", "custom_bodega"):
            assert f'href="/usuarios/roles/{name}"' in html
        order = [html.index(f'href="/usuarios/roles/{r.name}"') for r in repository.list_roles(db_conn)]
        assert order == sorted(order)

    def test_users_page_links_to_roles(self, authenticated_client):
        html = authenticated_client.get("/usuarios").get_data(as_text=True)
        assert re.search(r'<a [^>]*href="/usuarios/roles"[^>]*>Roles y permisos</a>', html)

    def test_unknown_role_is_404(self, authenticated_client, db_conn):
        before = _snapshot(db_conn)
        assert authenticated_client.get("/usuarios/roles/no_existe").status_code == 404
        assert authenticated_client.post(
            "/usuarios/roles/no_existe", data={"label": "X", "permissions": ["records.view"]}
        ).status_code == 404
        assert authenticated_client.post("/usuarios/roles/no_existe/delete").status_code == 404
        assert _snapshot(db_conn) == before


class TestAccess:
    @pytest.mark.parametrize("client_fixture", ["produccion_client", "punto_venta_client"])
    def test_non_admins_get_403_and_change_nothing(self, request, client_fixture, db_conn):
        client = request.getfixturevalue(client_fixture)
        before = _snapshot(db_conn)
        form = {"label": "Bodega", "permissions": ["users.manage"]}
        # GET /usuarios/roles/production/delete is a 405: the route only takes POST.
        assert client.get("/usuarios/roles/nuevo").status_code == 403
        assert client.post("/usuarios/roles/nuevo", data=form).status_code == 403
        assert client.get("/usuarios/roles").status_code == 403
        assert client.get("/usuarios/roles/production").status_code == 403
        assert client.post("/usuarios/roles/production", data=form).status_code == 403
        assert client.post("/usuarios/roles/production/delete").status_code == 403
        assert _snapshot(db_conn) == before


class TestNoInternalNames:
    def test_pages_never_show_internal_role_names(self, db_conn):
        admin = _fresh_client(TEST_USERNAME, TEST_PASSWORD)
        assert _create(admin).status_code == 302
        role = _role_by_label(db_conn, "Bodega")
        target = repository.get_user_by_username(db_conn, PRODUCCION_USERNAME)
        assert target is not None
        admin.post(f"/usuarios/{target.id}", data={"roles": ["production", role.name], "active": "1"})

        for path in ("/usuarios", "/usuarios/roles", "/usuarios/roles/nuevo", f"/usuarios/roles/{role.name}",
                     "/usuarios/roles/point_of_sale", f"/usuarios/{target.id}"):
            resp = admin.get(path)
            assert resp.status_code == 200, path
            text = _visible_text(resp.get_data(as_text=True))
            for name in INTERNAL_NAMES:
                assert name not in text, (path, name)


class TestLogging:
    def test_create_update_delete_are_logged(self, authenticated_client, db_conn, caplog):
        caplog.set_level(logging.INFO)
        assert _create(authenticated_client).status_code == 302
        name = _role_by_label(db_conn, "Bodega").name
        assert re.fullmatch(r"custom_[0-9a-f]{8}", name)
        assert authenticated_client.post(
            f"/usuarios/roles/{name}", data={"label": "Bodega", "permissions": ["records.view"]}
        ).status_code == 302
        assert authenticated_client.post(f"/usuarios/roles/{name}/delete").status_code == 302

        log = "\n".join(r.getMessage() for r in caplog.records)
        assert f"role created: by={TEST_USERNAME} role={name}" in log
        assert f"role updated: by={TEST_USERNAME} role={name}" in log
        assert f"role deleted: by={TEST_USERNAME} role={name}" in log
