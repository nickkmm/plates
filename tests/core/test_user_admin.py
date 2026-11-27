"""app/core/service.py: each validation rule of the /usuarios screen and
its exact Spanish message, against the seeded test users (`db_conn`):
testuser (admin), produccionuser (production), puntoventauser
(point_of_sale), and the seeded roles (Administrador, Punto de venta,
Producción) for the role rules."""

from __future__ import annotations

import re

from werkzeug.security import generate_password_hash

from app.auth import repository, service
from app.auth.repository import RoleRecord, UserRecord

GOOD_PASSWORD = "correcta123"


def _user(conn, username: str) -> UserRecord:
    user = repository.get_user_by_username(conn, username)
    assert user is not None
    return user


def _new(conn, **overrides):
    values = {
        "username": "nuevo.user",
        "password": GOOD_PASSWORD,
        "password_confirm": GOOD_PASSWORD,
        "roles": ["point_of_sale"],
    }
    values.update(overrides)
    return service.validate_new_user(conn, **values)


class TestValidatePassword:
    def test_valid(self):
        assert service.validate_password(GOOD_PASSWORD, GOOD_PASSWORD) is None

    def test_nine_characters_is_too_short(self):
        assert service.validate_password("123456789", "123456789") == (
            "La contraseña debe tener al menos 10 caracteres."
        )

    def test_mismatch(self):
        assert service.validate_password(GOOD_PASSWORD, GOOD_PASSWORD + "x") == (
            "Las contraseñas no coinciden."
        )

    def test_length_is_checked_before_match(self):
        assert service.validate_password("corta", "otra") == service.MSG_SHORT_PASSWORD


class TestValidateNewUser:
    def test_valid(self, db_conn):
        assert _new(db_conn) is None

    def test_bad_usernames(self, db_conn):
        message = (
            "El usuario debe tener entre 3 y 50 caracteres: letras, números, punto, guion o guion bajo."
        )
        for username in ("ab", "a b c", "x" * 51, "ñandú", ""):
            assert _new(db_conn, username=username) == message, username

    def test_no_role(self, db_conn):
        assert _new(db_conn, roles=[]) == "Elige al menos un rol."

    def test_forged_role(self, db_conn):
        assert _new(db_conn, roles=["point_of_sale", "superuser"]) == "Elige un rol válido."

    def test_short_password(self, db_conn):
        assert _new(db_conn, password="123456789", password_confirm="123456789") == (
            service.MSG_SHORT_PASSWORD
        )

    def test_mismatched_passwords(self, db_conn):
        assert _new(db_conn, password_confirm="otra-cosa-123") == service.MSG_PASSWORD_MISMATCH

    def test_order_username_then_roles_then_password(self, db_conn):
        assert _new(db_conn, username="ab", roles=[], password="x") == service.MSG_BAD_USERNAME
        assert _new(db_conn, roles=[], password="x") == service.MSG_NO_ROLE

    def test_duplicate_message_constant(self):
        assert service.MSG_DUPLICATE_USERNAME == "Ese usuario ya existe."


class TestValidateUserUpdate:
    def test_valid_role_change(self, db_conn):
        target = _user(db_conn, "produccionuser")
        admin = _user(db_conn, "testuser")
        assert service.validate_user_update(
            db_conn, target=target, actor_id=admin.id, roles=["point_of_sale"], active=True
        ) is None

    def test_no_role(self, db_conn):
        target = _user(db_conn, "produccionuser")
        admin = _user(db_conn, "testuser")
        assert service.validate_user_update(
            db_conn, target=target, actor_id=admin.id, roles=[], active=True
        ) == service.MSG_NO_ROLE

    def test_forged_role(self, db_conn):
        target = _user(db_conn, "produccionuser")
        admin = _user(db_conn, "testuser")
        assert service.validate_user_update(
            db_conn, target=target, actor_id=admin.id, roles=["superuser"], active=True
        ) == service.MSG_INVALID_ROLE

    def test_self_deactivation(self, db_conn):
        admin = _user(db_conn, "testuser")
        assert service.validate_user_update(
            db_conn, target=admin, actor_id=admin.id, roles=["admin"], active=False
        ) == "No puedes desactivar tu propio usuario."

    def test_self_admin_removal(self, db_conn):
        admin = _user(db_conn, "testuser")
        assert service.validate_user_update(
            db_conn, target=admin, actor_id=admin.id, roles=["production"], active=True
        ) == "No puedes quitarte el rol de administrador."

    def test_last_admin_deactivation_and_demotion(self, db_conn):
        admin = _user(db_conn, "testuser")
        actor = _user(db_conn, "produccionuser")
        message = "Debe quedar al menos un administrador activo."
        assert service.validate_user_update(
            db_conn, target=admin, actor_id=actor.id, roles=["admin"], active=False
        ) == message
        assert service.validate_user_update(
            db_conn, target=admin, actor_id=actor.id, roles=["production"], active=True
        ) == message

    def test_last_admin_rule_does_not_fire_with_another_active_admin(self, db_conn):
        repository.create_user(db_conn, username="otroadmin", password_hash=generate_password_hash("x"), roles=["admin"])
        admin = _user(db_conn, "testuser")
        actor = _user(db_conn, "produccionuser")
        assert service.validate_user_update(
            db_conn, target=admin, actor_id=actor.id, roles=["admin"], active=False
        ) is None
        assert service.validate_user_update(
            db_conn, target=admin, actor_id=actor.id, roles=["production"], active=True
        ) is None

    def test_inactive_admin_does_not_count_as_the_other_admin(self, db_conn):
        repository.create_user(
            db_conn, username="adminviejo", password_hash=generate_password_hash("x"), roles=["admin"],
            active=False,
        )
        admin = _user(db_conn, "testuser")
        actor = _user(db_conn, "produccionuser")
        assert service.validate_user_update(
            db_conn, target=admin, actor_id=actor.id, roles=["admin"], active=False
        ) == service.MSG_LAST_ADMIN

    def test_reactivating_an_inactive_admin_is_allowed(self, db_conn):
        user_id = repository.create_user(
            db_conn, username="adminviejo", password_hash=generate_password_hash("x"), roles=["admin"],
            active=False,
        )
        target = repository.get_user_by_id(db_conn, user_id)
        assert target is not None
        admin = _user(db_conn, "testuser")
        assert service.validate_user_update(
            db_conn, target=target, actor_id=admin.id, roles=["production"], active=False
        ) is None


def _role(name="custom_00000000", *, is_system=False, user_count=0) -> RoleRecord:
    return RoleRecord(name=name, label="Bodega", is_system=is_system, permissions=[], user_count=user_count)


class TestValidateRole:
    def test_valid(self, db_conn):
        assert service.validate_role(db_conn, label="Bodega", permissions=["inventory.manage"]) is None

    def test_empty_label(self, db_conn):
        assert service.validate_role(db_conn, label="", permissions=["inventory.manage"]) == (
            "Escribe el nombre del rol."
        )

    def test_sixty_characters_is_the_limit(self, db_conn):
        assert service.validate_role(db_conn, label="x" * 60, permissions=["inventory.manage"]) is None
        assert service.validate_role(db_conn, label="x" * 61, permissions=["inventory.manage"]) == (
            service.MSG_BAD_ROLE_LABEL
        )

    def test_duplicate_ignores_case_and_accents(self, db_conn):
        message = "Ya existe un rol con ese nombre."
        for label in ("PRODUCCION", "producción", "punto DE VENTA", "Administrador"):
            assert service.validate_role(db_conn, label=label, permissions=["records.view"]) == message, label
        repository.create_role(db_conn, name="custom_bodega", label="Bódega", permissions=["inventory.manage"])
        assert service.validate_role(db_conn, label="bodega", permissions=["records.view"]) == message

    def test_the_edited_role_is_left_out(self, db_conn):
        repository.create_role(db_conn, name="custom_bodega", label="Bodega", permissions=["inventory.manage"])
        assert service.validate_role(
            db_conn, label="BODEGA", permissions=["inventory.manage"], role_name="custom_bodega"
        ) is None
        assert service.validate_role(
            db_conn, label="Produccion", permissions=["inventory.manage"], role_name="custom_bodega"
        ) == service.MSG_DUPLICATE_ROLE_LABEL

    def test_no_permission(self, db_conn):
        assert service.validate_role(db_conn, label="Bodega", permissions=[]) == "Marca al menos un permiso."

    def test_label_is_checked_before_permissions(self, db_conn):
        assert service.validate_role(db_conn, label="", permissions=[]) == service.MSG_BAD_ROLE_LABEL
        assert service.validate_role(db_conn, label="Producción", permissions=[]) == (
            service.MSG_DUPLICATE_ROLE_LABEL
        )


class TestCleanPermissions:
    def test_unknown_codes_and_duplicates_are_dropped(self, db_conn):
        assert service.clean_permissions(
            db_conn, ["inventory.manage", "superpoder", "inventory.manage"]
        ) == ["inventory.manage"]

    def test_only_unknown_codes_leave_nothing(self, db_conn):
        permissions = service.clean_permissions(db_conn, ["superpoder"])
        assert permissions == []
        assert service.validate_role(db_conn, label="Bodega", permissions=permissions) == (
            service.MSG_NO_PERMISSION
        )

    def test_catalog_order(self, db_conn):
        assert service.clean_permissions(db_conn, ["users.manage", "orders.capture"]) == [
            "orders.capture", "users.manage",
        ]


class TestValidateRoleDelete:
    def test_unused_custom_role(self):
        assert service.validate_role_delete(_role()) is None

    def test_system_role(self):
        assert service.validate_role_delete(_role("production", is_system=True)) == (
            "No se puede eliminar un rol del sistema."
        )

    def test_role_with_users(self):
        assert service.validate_role_delete(_role(user_count=1)) == (
            "No se puede eliminar un rol con usuarios asignados."
        )


def test_new_role_name():
    names = {service.new_role_name() for _ in range(20)}
    assert len(names) == 20
    assert all(re.fullmatch(r"custom_[0-9a-f]{8}", name) for name in names)
