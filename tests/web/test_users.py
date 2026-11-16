"""Integration tests for the admin users screen: GET /usuarios,
GET/POST /usuarios/nuevo, GET/POST /usuarios/<id>, POST /usuarios/<id>/password.

The contract under test:
- Only `users.manage` (seeded for admin only) opens these routes; production
  and point_of_sale get 403 and change nothing.
- Creating a user, changing their roles or active flag and resetting their
  password apply on that user's next request / login.
- Every validation rule of app/core/user_admin.py re-renders the form with
  status 400 and its message, and writes nothing.
- The list never shows a password hash or an internal role name, and the log
  lines never carry a password or a hash.

Needs a real Postgres connection (`db_conn`/`clean_db`).
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

import pytest
from werkzeug.security import generate_password_hash

from app.core import db, user_admin
from app.web.app import app as flask_app
from tests.conftest import PRODUCCION_PASSWORD, PRODUCCION_USERNAME, TEST_PASSWORD, TEST_USERNAME

NEW_PASSWORD = "nuevaclave123"


def _user(conn, username):
    user = db.get_user_by_username(conn, username)
    assert user is not None
    return user


def _snapshot(conn):
    """(username, roles, active) of every user, to assert nothing changed."""
    return [(u.username, u.roles, u.active) for u in db.list_users(conn)]


@pytest.fixture(autouse=True)
def _restore_csrf_setting():
    """_fresh_client turns CSRF off; put the earlier value back afterwards."""
    previous = flask_app.config["WTF_CSRF_ENABLED"]
    yield
    flask_app.config["WTF_CSRF_ENABLED"] = previous


def _fresh_client(username=None, password=None):
    """A browser of its own, optionally logged in. Tests that drive two
    users use only these, never the `client` fixture: that one is held open
    in a `with` block, and its preserved app context (and the user cached on
    flask.g) would leak into the other client's requests."""
    flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    client = flask_app.test_client()
    if username is not None:
        _log_in(client, username, password)
    return client


def _log_in(client, username, password):
    return client.post("/login", data={"username": username, "password": password})


def _new_form(**overrides):
    form = {
        "username": "cajero.nuevo",
        "roles": ["point_of_sale"],
        "password": NEW_PASSWORD,
        "password_confirm": NEW_PASSWORD,
    }
    form.update(overrides)
    return form


class TestCreate:
    def test_admin_creates_a_user_who_can_log_in(self, db_conn, caplog):
        caplog.set_level(logging.INFO)
        authenticated_client = _fresh_client(TEST_USERNAME, TEST_PASSWORD)
        resp = authenticated_client.post("/usuarios/nuevo", data=_new_form())
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/usuarios"

        html = authenticated_client.get("/usuarios").get_data(as_text=True)
        assert "Usuario creado." in html
        assert "cajero.nuevo" in html

        new_user = _user(db_conn, "cajero.nuevo")
        assert new_user.roles == ["point_of_sale"]
        assert new_user.active

        log = "\n".join(r.getMessage() for r in caplog.records)
        assert "user created: by=testuser user=cajero.nuevo" in log
        assert NEW_PASSWORD not in log
        assert new_user.password_hash not in log
        assert "password_hash" not in log

        resp = _log_in(_fresh_client(), "cajero.nuevo", NEW_PASSWORD)
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/ingest"

    def test_username_is_stripped(self, authenticated_client, db_conn):
        resp = authenticated_client.post("/usuarios/nuevo", data=_new_form(username="  cajero.nuevo  "))
        assert resp.status_code == 302
        assert _user(db_conn, "cajero.nuevo")

    def test_new_form_lists_every_role_label(self, authenticated_client):
        html = authenticated_client.get("/usuarios/nuevo").get_data(as_text=True)
        assert "<h1>Nuevo usuario</h1>" in html
        for label in ("Administrador", "Punto de venta", "Producción"):
            assert label in html
        assert 'name="password"' in html and 'name="password_confirm"' in html
        assert "Crear usuario" in html


class TestCreateValidation:
    @pytest.mark.parametrize(
        ("overrides", "message"),
        [
            ({"username": "ab"}, user_admin.MSG_BAD_USERNAME),
            ({"username": "a b c"}, user_admin.MSG_BAD_USERNAME),
            ({"username": "testuser"}, "Ese usuario ya existe."),
            ({"password": "123456789", "password_confirm": "123456789"},
             "La contraseña debe tener al menos 10 caracteres."),
            ({"password_confirm": "otraclave123"}, "Las contraseñas no coinciden."),
            ({"roles": []}, "Elige al menos un rol."),
            ({"roles": ["superuser"]}, "Elige un rol válido."),
        ],
        ids=["short-username", "spaces", "duplicate", "short-password", "mismatch", "no-role",
             "forged-role"],
    )
    def test_rejected_with_400_and_nothing_written(self, authenticated_client, db_conn, overrides, message):
        before = _snapshot(db_conn)
        form = _new_form(**overrides)
        resp = authenticated_client.post("/usuarios/nuevo", data=form)
        html = resp.get_data(as_text=True)
        assert resp.status_code == 400
        assert message in html
        assert _snapshot(db_conn) == before
        assert f'value="{form["username"]}"' in html
        assert form["password"] not in html
        assert form["password_confirm"] not in html

    def test_checked_roles_are_kept(self, authenticated_client):
        resp = authenticated_client.post(
            "/usuarios/nuevo", data=_new_form(roles=["production"], password_confirm="otraclave123")
        )
        html = resp.get_data(as_text=True)
        assert 'value="production" checked' in html
        assert 'value="point_of_sale" checked' not in html


class TestEdit:
    def test_edit_page_shows_current_roles_and_active(self, authenticated_client, db_conn):
        target = _user(db_conn, "produccionuser")
        html = authenticated_client.get(f"/usuarios/{target.id}").get_data(as_text=True)
        assert "<h1>Usuario produccionuser</h1>" in html
        assert 'value="production" checked' in html
        assert 'value="admin" checked' not in html
        assert 'name="active" value="1" checked' in html
        assert 'name="username"' not in html
        assert "Guardar cambios" in html and "Cambiar contraseña" in html

    def test_role_change_and_deactivation_apply_on_the_next_request(self, db_conn):
        target = _user(db_conn, PRODUCCION_USERNAME)
        authenticated_client = _fresh_client(TEST_USERNAME, TEST_PASSWORD)
        production = _fresh_client(PRODUCCION_USERNAME, PRODUCCION_PASSWORD)
        assert production.get("/ingest").status_code == 403

        resp = authenticated_client.post(
            f"/usuarios/{target.id}", data={"roles": ["point_of_sale"], "active": "1"}
        )
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == f"/usuarios/{target.id}"
        assert "Cambios guardados." in authenticated_client.get(f"/usuarios/{target.id}").get_data(
            as_text=True
        )
        assert production.get("/ingest").status_code == 200

        resp = authenticated_client.post(f"/usuarios/{target.id}", data={"roles": ["point_of_sale"]})
        assert resp.status_code == 302
        assert not _user(db_conn, PRODUCCION_USERNAME).active
        resp = production.get("/ingest")
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/login"

    def test_two_roles_give_the_union(self, db_conn):
        target = _user(db_conn, PRODUCCION_USERNAME)
        authenticated_client = _fresh_client(TEST_USERNAME, TEST_PASSWORD)
        production = _fresh_client(PRODUCCION_USERNAME, PRODUCCION_PASSWORD)
        authenticated_client.post(
            f"/usuarios/{target.id}", data={"roles": ["production", "point_of_sale"], "active": "1"}
        )
        assert _user(db_conn, PRODUCCION_USERNAME).roles == ["point_of_sale", "production"]
        assert production.get("/ingest").status_code == 200
        assert production.get("/records").status_code == 200

    def test_update_is_logged(self, authenticated_client, db_conn, caplog):
        caplog.set_level(logging.INFO)
        target = _user(db_conn, PRODUCCION_USERNAME)
        authenticated_client.post(f"/usuarios/{target.id}", data={"roles": ["production"], "active": "1"})
        log = "\n".join(r.getMessage() for r in caplog.records)
        assert "user updated: by=testuser user=produccionuser roles=production" in log


class TestEditValidation:
    def _assert_rejected(self, client, conn, user_id, form, message):
        before = _snapshot(conn)
        resp = client.post(f"/usuarios/{user_id}", data=form)
        assert resp.status_code == 400
        assert message in resp.get_data(as_text=True)
        assert _snapshot(conn) == before
        return resp.get_data(as_text=True)

    def test_no_role(self, authenticated_client, db_conn):
        target = _user(db_conn, PRODUCCION_USERNAME)
        html = self._assert_rejected(
            authenticated_client, db_conn, target.id, {"active": "1"}, "Elige al menos un rol."
        )
        assert 'name="active" value="1" checked' in html

    def test_forged_role(self, authenticated_client, db_conn):
        target = _user(db_conn, PRODUCCION_USERNAME)
        self._assert_rejected(
            authenticated_client, db_conn, target.id, {"roles": ["superuser"], "active": "1"},
            "Elige un rol válido.",
        )

    def test_admin_cannot_deactivate_themselves(self, authenticated_client, db_conn):
        admin = _user(db_conn, "testuser")
        html = self._assert_rejected(
            authenticated_client, db_conn, admin.id, {"roles": ["admin"]},
            "No puedes desactivar tu propio usuario.",
        )
        assert 'name="active" value="1" checked' not in html

    def test_admin_cannot_remove_their_own_admin_role(self, authenticated_client, db_conn):
        admin = _user(db_conn, "testuser")
        html = self._assert_rejected(
            authenticated_client, db_conn, admin.id, {"roles": ["production"], "active": "1"},
            "No puedes quitarte el rol de administrador.",
        )
        assert 'value="production" checked' in html
        assert 'value="admin" checked' not in html

    def test_last_active_admin(self, client, db_conn):
        db.create_role(db_conn, name="user_manager", label="Gestor de usuarios", permissions=["users.manage"])
        db.create_user(
            db_conn, username="gestor.usuarios", password_hash=generate_password_hash("gestorclave123"),
            roles=["user_manager"],
        )
        db_conn.commit()
        _log_in(client, "gestor.usuarios", "gestorclave123")
        admin = _user(db_conn, "testuser")
        message = "Debe quedar al menos un administrador activo."

        self._assert_rejected(client, db_conn, admin.id, {"roles": ["admin"]}, message)
        self._assert_rejected(client, db_conn, admin.id, {"roles": ["production"], "active": "1"}, message)

        db.create_user(
            db_conn, username="otro.admin", password_hash=generate_password_hash("otroclave123"),
            roles=["admin"],
        )
        db_conn.commit()
        resp = client.post(f"/usuarios/{admin.id}", data={"roles": ["admin"]})
        assert resp.status_code == 302
        assert not _user(db_conn, "testuser").active


class TestPassword:
    def test_reset_password(self, db_conn, caplog):
        caplog.set_level(logging.INFO)
        authenticated_client = _fresh_client(TEST_USERNAME, TEST_PASSWORD)
        target = _user(db_conn, PRODUCCION_USERNAME)
        resp = authenticated_client.post(
            f"/usuarios/{target.id}/password",
            data={"password": NEW_PASSWORD, "password_confirm": NEW_PASSWORD},
        )
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == f"/usuarios/{target.id}"
        html = authenticated_client.get(f"/usuarios/{target.id}").get_data(as_text=True)
        assert "Contraseña actualizada." in html

        log = "\n".join(r.getMessage() for r in caplog.records)
        assert "user password reset: by=testuser user=produccionuser roles=production" in log
        assert NEW_PASSWORD not in log
        assert _user(db_conn, PRODUCCION_USERNAME).password_hash not in log

        assert _log_in(_fresh_client(), PRODUCCION_USERNAME, PRODUCCION_PASSWORD).status_code == 401
        resp = _log_in(_fresh_client(), PRODUCCION_USERNAME, NEW_PASSWORD)
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/records"

    @pytest.mark.parametrize(
        ("password", "confirm", "message"),
        [
            ("123456789", "123456789", "La contraseña debe tener al menos 10 caracteres."),
            (NEW_PASSWORD, "otraclave123", "Las contraseñas no coinciden."),
        ],
        ids=["short", "mismatch"],
    )
    def test_rejected(self, authenticated_client, db_conn, password, confirm, message):
        target = _user(db_conn, PRODUCCION_USERNAME)
        old_hash = target.password_hash
        resp = authenticated_client.post(
            f"/usuarios/{target.id}/password", data={"password": password, "password_confirm": confirm}
        )
        html = resp.get_data(as_text=True)
        assert resp.status_code == 400
        assert message in html
        assert password not in html and confirm not in html
        assert 'value="production" checked' in html
        assert _user(db_conn, PRODUCCION_USERNAME).password_hash == old_hash


class TestNotFound:
    def test_unknown_user_is_404(self, authenticated_client, clean_db):
        assert authenticated_client.get("/usuarios/999999").status_code == 404
        assert authenticated_client.post(
            "/usuarios/999999", data={"roles": ["admin"], "active": "1"}
        ).status_code == 404
        assert authenticated_client.post(
            "/usuarios/999999/password", data={"password": NEW_PASSWORD, "password_confirm": NEW_PASSWORD}
        ).status_code == 404


class TestAccess:
    @pytest.mark.parametrize("client_fixture", ["produccion_client", "punto_venta_client"])
    def test_non_admins_get_403_and_change_nothing(self, request, client_fixture, db_conn):
        client = request.getfixturevalue(client_fixture)
        target = _user(db_conn, "testuser")
        before = _snapshot(db_conn)
        hashes = [u.password_hash for u in db.list_users(db_conn)]
        assert client.get("/usuarios").status_code == 403
        assert client.get("/usuarios/nuevo").status_code == 403
        assert client.post("/usuarios/nuevo", data=_new_form()).status_code == 403
        assert client.get(f"/usuarios/{target.id}").status_code == 403
        assert client.post(f"/usuarios/{target.id}", data={"roles": ["production"]}).status_code == 403
        assert client.post(
            f"/usuarios/{target.id}/password", data={"password": NEW_PASSWORD, "password_confirm": NEW_PASSWORD}
        ).status_code == 403
        assert _snapshot(db_conn) == before
        assert [u.password_hash for u in db.list_users(db_conn)] == hashes


class TestNav:
    def test_admin_sees_usuarios_after_clientes(self, authenticated_client):
        html = authenticated_client.get("/records").get_data(as_text=True)
        assert 'href="/usuarios"' in html
        assert html.index('href="/customers"') < html.index('href="/usuarios"')
        assert ">Usuarios</a>" in html

    @pytest.mark.parametrize("client_fixture", ["produccion_client", "punto_venta_client"])
    def test_non_admins_do_not_see_usuarios(self, request, client_fixture):
        client = request.getfixturevalue(client_fixture)
        resp = client.get("/records")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'href="/usuarios"' not in html
        assert "Usuarios" not in html


class TestList:
    def test_shows_labels_never_hashes_or_role_names(self, authenticated_client, db_conn):
        db.create_user(db_conn, username="sin.roles", password_hash=generate_password_hash("x"), roles=[])
        db.create_user(
            db_conn, username="inactivo", password_hash=generate_password_hash("x"), roles=["production"],
            active=False,
        )
        resp = authenticated_client.get("/usuarios")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        for user in db.list_users(db_conn):
            assert user.username in html
            assert user.password_hash not in html
        assert "point_of_sale" not in html
        assert "production" not in html
        for label in ("Administrador", "Punto de venta", "Producción", "Sin roles"):
            assert label in html
        assert "Activo" in html and "Inactivo" in html
        assert "Nuevo usuario" in html and 'href="/usuarios/nuevo"' in html
        for header in ("<th>Usuario</th>", "<th>Roles</th>", "<th>Estado</th>", "<th>Creado</th>"):
            assert header in html
