"""Route access decided per request from `users`, `user_roles` and
`role_permissions` (app.web.auth.permission_required): a deactivated user or
a role change takes effect on the very next request, a custom role sees only
its own screens, a user with several roles gets the union of their
permissions, a user with no permissions gets the 403 page on `/`, and admin
(in any of the user's roles) keeps every permission whatever role_permissions
stores for it.

Needs a real Postgres connection (`db_conn`/`clean_db`): setup done through
`db_conn` is committed so the app's own connection sees it.
"""

from __future__ import annotations

from urllib.parse import urlparse

from werkzeug.security import generate_password_hash

from app.core import db
from app.core.permissions import ADMIN_ROLE, PERMISSION_CODES, effective_permissions
from tests.conftest import _TEST_USERS

NO_PERMISSIONS_TEXT = "Tu usuario no tiene permisos asignados. Pide a un administrador que te asigne un rol."


def _create_and_log_in(client, conn, *, roles, username="customuser", password="custompass123"):
    user_id = db.create_user(conn, username=username, password_hash=generate_password_hash(password), roles=roles)
    conn.commit()
    client.post("/login", data={"username": username, "password": password})
    return user_id


class TestDeactivation:
    def test_deactivated_user_is_logged_out_on_the_next_request(self, punto_venta_client, db_conn):
        assert punto_venta_client.get("/ingest").status_code == 200

        user = db.get_user_by_username(db_conn, "puntoventauser")
        assert user is not None
        db.update_user(db_conn, user.id, active=False)
        db_conn.commit()

        resp = punto_venta_client.get("/ingest")
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/login"
        with punto_venta_client.session_transaction() as sess:
            assert "user" not in sess
            assert "user_id" not in sess


class TestRoleChange:
    def test_role_change_takes_effect_on_the_next_request(self, produccion_client, db_conn):
        assert produccion_client.get("/ingest").status_code == 403

        user = db.get_user_by_username(db_conn, "produccionuser")
        assert user is not None
        db.set_user_roles(db_conn, user.id, ["point_of_sale"])
        db_conn.commit()

        assert produccion_client.get("/ingest").status_code == 200


class TestCustomRole:
    def test_inventory_only_role_sees_only_inventory(self, client, db_conn):
        db.create_role(db_conn, name="inventory_clerk", label="Inventario solo",
                       permissions=["inventory.manage"])
        _create_and_log_in(client, db_conn, roles=["inventory_clerk"])

        resp = client.get("/inventory")
        assert resp.status_code == 200
        assert client.get("/records").status_code == 403

        html = resp.get_data(as_text=True)
        assert 'href="/inventory"' in html
        assert "Inventario" in html
        for href in ("/ingest", "/cuadre", "/declaraciones", "/review", "/precios", "/customers", "/records"):
            assert f'href="{href}"' not in html

        resp = client.get("/")
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/inventory"


class TestNoPermissions:
    def test_role_with_no_permissions_gets_the_403_page_on_index(self, client, db_conn):
        db.create_role(db_conn, name="empty_role", label="Sin permisos", permissions=[])
        _create_and_log_in(client, db_conn, roles=["empty_role"])

        resp = client.get("/")
        assert resp.status_code == 403
        html = resp.get_data(as_text=True)
        assert NO_PERMISSIONS_TEXT in html
        assert "Salir" in html


class TestAdminAlwaysHasEveryPermission:
    def test_admin_without_role_permissions_rows_still_reaches_every_screen(
        self, authenticated_client, db_conn
    ):
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM role_permissions WHERE role = 'admin'")
        db_conn.commit()

        for path in ("/ingest", "/cuadre", "/declaraciones", "/review", "/inventory", "/precios",
                     "/customers", "/records"):
            assert authenticated_client.get(path).status_code == 200, path


class TestSeveralRoles:
    @staticmethod
    def _production_plus_stock(client, conn):
        db.create_role(conn, name="stock", label="Bodega", permissions=["inventory.manage"])
        return _create_and_log_in(client, conn, roles=["production", "stock"])

    def test_permissions_are_the_union_of_the_roles(self, client, db_conn):
        self._production_plus_stock(client, db_conn)

        resp = client.get("/inventory")
        assert resp.status_code == 200
        assert client.get("/records").status_code == 200
        assert client.get("/precios").status_code == 403

        html = resp.get_data(as_text=True)
        assert "Inventario" in html
        assert "Registros" in html

    def test_assigning_and_unassigning_admin_applies_on_the_next_request(self, client, db_conn):
        user_id = self._production_plus_stock(client, db_conn)
        assert client.get("/precios").status_code == 403

        assert db.assign_role(db_conn, user_id, ADMIN_ROLE) is True
        db_conn.commit()
        assert client.get("/precios").status_code == 200

        assert db.unassign_role(db_conn, user_id, ADMIN_ROLE) is True
        db_conn.commit()
        assert client.get("/precios").status_code == 403

    def test_user_whose_roles_are_all_removed_gets_the_403_page(self, client, db_conn):
        user_id = _create_and_log_in(client, db_conn, roles=["production"])
        assert client.get("/records").status_code == 200

        assert db.set_user_roles(db_conn, user_id, []) is True
        db_conn.commit()

        resp = client.get("/")
        assert resp.status_code == 403
        assert NO_PERMISSIONS_TEXT in resp.get_data(as_text=True)

    def test_old_cookie_with_role_still_works_and_the_role_in_it_is_ignored(self, client, db_conn):
        """A cookie issued before the session stopped storing the role still
        carries `role`; it keeps working, and access follows the database."""
        user = db.get_user_by_username(db_conn, "produccionuser")
        assert user is not None
        with client.session_transaction() as sess:
            sess["user"] = user.username
            sess["role"] = ADMIN_ROLE
            sess["user_id"] = user.id

        assert client.get("/records").status_code == 200
        assert client.get("/precios").status_code == 403


class TestAccessUnchanged:
    def test_every_seeded_user_keeps_the_access_of_their_single_role(self, db_conn):
        """The old rule (one role: admin gets every code, otherwise that
        role's stored codes in the catalog) and the new one (the union over
        user_roles) give each seeded user the same permissions."""
        for username, _password_hash, role in _TEST_USERS:
            user = db.get_user_by_username(db_conn, username)
            assert user is not None
            if role == ADMIN_ROLE:
                before = PERMISSION_CODES
            else:
                before = db.get_role_permissions(db_conn, role) & PERMISSION_CODES
            after = effective_permissions(user.roles, db.get_user_permissions(db_conn, user.id))
            assert after == before, username
