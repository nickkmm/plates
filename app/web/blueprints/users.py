"""/usuarios -- admin screen to list login accounts, create one, change its
roles and active flag, and reset its password.

The rules (username format, password length, valid roles, no locking
yourself out, at least one active admin) live in app/core/user_admin.py;
this module only reads the form, writes through app.core.db and commits.
There is no delete: deactivating a user is how access is removed, and
app.web.auth re-reads roles and the active flag on every request, so a
change applies on the user's next request.

@permission_required("users.manage"), seeded for admin only.
"""

from __future__ import annotations

import psycopg
from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from psycopg.rows import DictRow
from werkzeug.security import generate_password_hash

from app.core import db, user_admin
from app.core.db import UserRecord
from app.web.auth import permission_required

bp = Blueprint("users", __name__)


def _role_labels(conn: psycopg.Connection[DictRow]) -> dict[str, str]:
    return {r.name: r.label for r in db.list_roles(conn)}


def _get_user_or_404(conn: psycopg.Connection[DictRow], user_id: int) -> UserRecord:
    user = db.get_user_by_id(conn, user_id)
    if user is None:
        abort(404)
    return user


def _render_edit(user: UserRecord, *, roles: list[str], active: bool, error: str | None = None):
    with db.get_conn() as conn:
        all_roles = db.list_roles(conn)
    page = render_template(
        "user_form.html", user=user, all_roles=all_roles, selected_roles=roles, active=active,
        error=error,
    )
    return (page, 400) if error else page


@bp.get("/usuarios")
@permission_required("users.manage")
def users():
    with db.get_conn() as conn:
        rows = db.list_users(conn)
        labels = _role_labels(conn)
    role_text = {
        u.id: ", ".join(sorted(labels[r] for r in u.roles)) or "Sin roles" for u in rows
    }
    return render_template("users.html", rows=rows, role_text=role_text)


@bp.route("/usuarios/nuevo", methods=["GET", "POST"])
@permission_required("users.manage")
def user_new():
    with db.get_conn() as conn:
        all_roles = db.list_roles(conn)
    if request.method == "GET":
        return render_template("user_form.html", user=None, all_roles=all_roles, selected_roles=[])

    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    password_confirm = request.form.get("password_confirm") or ""
    roles = request.form.getlist("roles")

    def _rerender(error: str):
        return render_template(
            "user_form.html", user=None, all_roles=all_roles, selected_roles=roles,
            username=username, error=error,
        ), 400

    try:
        with db.get_conn() as conn:
            error = user_admin.validate_new_user(
                conn, username=username, password=password, password_confirm=password_confirm,
                roles=roles,
            )
            if error:
                return _rerender(error)
            db.create_user(
                conn, username=username, password_hash=generate_password_hash(password),
                roles=roles, assigned_by=session["user_id"],
            )
            conn.commit()
    except psycopg.errors.UniqueViolation:
        return _rerender(user_admin.MSG_DUPLICATE_USERNAME)

    current_app.logger.info(
        "user created: by=%s user=%s roles=%s", session["user"], username, ",".join(sorted(set(roles)))
    )
    flash("Usuario creado.")
    return redirect(url_for("users.users"))


@bp.route("/usuarios/<int:user_id>", methods=["GET", "POST"])
@permission_required("users.manage")
def user_edit(user_id: int):
    with db.get_conn() as conn:
        user = _get_user_or_404(conn, user_id)
    if request.method == "GET":
        return _render_edit(user, roles=user.roles, active=user.active)

    roles = request.form.getlist("roles")
    active = request.form.get("active") is not None
    with db.get_conn() as conn:
        error = user_admin.validate_user_update(
            conn, target=user, actor_id=session["user_id"], roles=roles, active=active,
        )
        if error is None:
            db.set_user_roles(conn, user_id, roles, assigned_by=session["user_id"])
            db.update_user(conn, user_id, active=active)
            conn.commit()
    if error:
        return _render_edit(user, roles=roles, active=active, error=error)

    current_app.logger.info(
        "user updated: by=%s user=%s roles=%s", session["user"], user.username, ",".join(sorted(set(roles)))
    )
    flash("Cambios guardados.")
    return redirect(url_for("users.user_edit", user_id=user_id))


@bp.post("/usuarios/<int:user_id>/password")
@permission_required("users.manage")
def user_password(user_id: int):
    with db.get_conn() as conn:
        user = _get_user_or_404(conn, user_id)
    password = request.form.get("password") or ""
    error = user_admin.validate_password(password, request.form.get("password_confirm") or "")
    if error:
        return _render_edit(user, roles=user.roles, active=user.active, error=error)

    with db.get_conn() as conn:
        db.set_user_password(conn, user_id, generate_password_hash(password))
        conn.commit()
    current_app.logger.info(
        "user password reset: by=%s user=%s roles=%s", session["user"], user.username, ",".join(user.roles)
    )
    flash("Contraseña actualizada.")
    return redirect(url_for("users.user_edit", user_id=user_id))
