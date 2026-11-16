"""app/core/user_admin.py: each validation rule of the /usuarios screen and
its exact Spanish message, against the seeded test users (`db_conn`):
testuser (admin), produccionuser (production), puntoventauser
(point_of_sale)."""

from __future__ import annotations

from werkzeug.security import generate_password_hash

from app.core import db, user_admin
from app.core.db import UserRecord

GOOD_PASSWORD = "correcta123"


def _user(conn, username: str) -> UserRecord:
    user = db.get_user_by_username(conn, username)
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
    return user_admin.validate_new_user(conn, **values)


class TestValidatePassword:
    def test_valid(self):
        assert user_admin.validate_password(GOOD_PASSWORD, GOOD_PASSWORD) is None

    def test_nine_characters_is_too_short(self):
        assert user_admin.validate_password("123456789", "123456789") == (
            "La contraseña debe tener al menos 10 caracteres."
        )

    def test_mismatch(self):
        assert user_admin.validate_password(GOOD_PASSWORD, GOOD_PASSWORD + "x") == (
            "Las contraseñas no coinciden."
        )

    def test_length_is_checked_before_match(self):
        assert user_admin.validate_password("corta", "otra") == user_admin.MSG_SHORT_PASSWORD


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
            user_admin.MSG_SHORT_PASSWORD
        )

    def test_mismatched_passwords(self, db_conn):
        assert _new(db_conn, password_confirm="otra-cosa-123") == user_admin.MSG_PASSWORD_MISMATCH

    def test_order_username_then_roles_then_password(self, db_conn):
        assert _new(db_conn, username="ab", roles=[], password="x") == user_admin.MSG_BAD_USERNAME
        assert _new(db_conn, roles=[], password="x") == user_admin.MSG_NO_ROLE

    def test_duplicate_message_constant(self):
        assert user_admin.MSG_DUPLICATE_USERNAME == "Ese usuario ya existe."


class TestValidateUserUpdate:
    def test_valid_role_change(self, db_conn):
        target = _user(db_conn, "produccionuser")
        admin = _user(db_conn, "testuser")
        assert user_admin.validate_user_update(
            db_conn, target=target, actor_id=admin.id, roles=["point_of_sale"], active=True
        ) is None

    def test_no_role(self, db_conn):
        target = _user(db_conn, "produccionuser")
        admin = _user(db_conn, "testuser")
        assert user_admin.validate_user_update(
            db_conn, target=target, actor_id=admin.id, roles=[], active=True
        ) == user_admin.MSG_NO_ROLE

    def test_forged_role(self, db_conn):
        target = _user(db_conn, "produccionuser")
        admin = _user(db_conn, "testuser")
        assert user_admin.validate_user_update(
            db_conn, target=target, actor_id=admin.id, roles=["superuser"], active=True
        ) == user_admin.MSG_INVALID_ROLE

    def test_self_deactivation(self, db_conn):
        admin = _user(db_conn, "testuser")
        assert user_admin.validate_user_update(
            db_conn, target=admin, actor_id=admin.id, roles=["admin"], active=False
        ) == "No puedes desactivar tu propio usuario."

    def test_self_admin_removal(self, db_conn):
        admin = _user(db_conn, "testuser")
        assert user_admin.validate_user_update(
            db_conn, target=admin, actor_id=admin.id, roles=["production"], active=True
        ) == "No puedes quitarte el rol de administrador."

    def test_last_admin_deactivation_and_demotion(self, db_conn):
        admin = _user(db_conn, "testuser")
        actor = _user(db_conn, "produccionuser")
        message = "Debe quedar al menos un administrador activo."
        assert user_admin.validate_user_update(
            db_conn, target=admin, actor_id=actor.id, roles=["admin"], active=False
        ) == message
        assert user_admin.validate_user_update(
            db_conn, target=admin, actor_id=actor.id, roles=["production"], active=True
        ) == message

    def test_last_admin_rule_does_not_fire_with_another_active_admin(self, db_conn):
        db.create_user(db_conn, username="otroadmin", password_hash=generate_password_hash("x"), roles=["admin"])
        admin = _user(db_conn, "testuser")
        actor = _user(db_conn, "produccionuser")
        assert user_admin.validate_user_update(
            db_conn, target=admin, actor_id=actor.id, roles=["admin"], active=False
        ) is None
        assert user_admin.validate_user_update(
            db_conn, target=admin, actor_id=actor.id, roles=["production"], active=True
        ) is None

    def test_inactive_admin_does_not_count_as_the_other_admin(self, db_conn):
        db.create_user(
            db_conn, username="adminviejo", password_hash=generate_password_hash("x"), roles=["admin"],
            active=False,
        )
        admin = _user(db_conn, "testuser")
        actor = _user(db_conn, "produccionuser")
        assert user_admin.validate_user_update(
            db_conn, target=admin, actor_id=actor.id, roles=["admin"], active=False
        ) == user_admin.MSG_LAST_ADMIN

    def test_reactivating_an_inactive_admin_is_allowed(self, db_conn):
        user_id = db.create_user(
            db_conn, username="adminviejo", password_hash=generate_password_hash("x"), roles=["admin"],
            active=False,
        )
        target = db.get_user_by_id(db_conn, user_id)
        assert target is not None
        admin = _user(db_conn, "testuser")
        assert user_admin.validate_user_update(
            db_conn, target=target, actor_id=admin.id, roles=["production"], active=False
        ) is None
