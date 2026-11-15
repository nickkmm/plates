"""Route access decided per request from `users` and `role_permissions`
(app.web.auth.permission_required): a deactivated user or a role change takes
effect on the very next request, a custom role sees only its own screens, a
role with no permissions gets the 403 page on `/`, and admin keeps every
permission whatever role_permissions stores for it.

Needs a real Postgres connection (`db_conn`/`clean_db`): setup done through
`db_conn` is committed so the app's own connection sees it.
"""

from __future__ import annotations

from urllib.parse import urlparse

from werkzeug.security import generate_password_hash

from app.core import db

NO_PERMISSIONS_TEXT = "Tu usuario no tiene permisos asignados. Pide a un administrador que te asigne un rol."


def _create_and_log_in(client, conn, *, role, username="customuser", password="custompass123"):
    db.create_user(conn, username=username, password_hash=generate_password_hash(password), role=role)
    conn.commit()
    client.post("/login", data={"username": username, "password": password})


class TestDeactivation:
    def test_deactivated_user_is_logged_out_on_the_next_request(self, punto_venta_client, db_conn):
        assert punto_venta_client.get("/ingest").status_code == 200

        user = db.get_user_by_username(db_conn, "puntoventauser")
        assert user is not None
        db.update_user(db_conn, user.id, role=user.role, active=False)
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
        db.update_user(db_conn, user.id, role="point_of_sale", active=True)
        db_conn.commit()

        assert produccion_client.get("/ingest").status_code == 200


class TestCustomRole:
    def test_inventory_only_role_sees_only_inventory(self, client, db_conn):
        db.create_role(db_conn, name="inventory_clerk", label="Inventario solo",
                       permissions=["inventory.manage"])
        _create_and_log_in(client, db_conn, role="inventory_clerk")

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
        _create_and_log_in(client, db_conn, role="empty_role")

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
