"""/login and /logout -- session login page (open) and logout (open).

Named auth_routes.py, not auth.py: app/web/auth.py already exists and holds
the actual auth logic (user store, role lookup, the login_required/
consulta_allowed decorators) used by every OTHER blueprint too -- these are
just the two routes that call into it.
"""

from __future__ import annotations

from flask import Blueprint, redirect, render_template, request, session, url_for

from app.web import auth

bp = Blueprint("auth_routes", __name__)


@bp.get("/login")
def login():
    if session.get("user"):
        return redirect(auth.landing_url())
    next_url = request.args.get("next", "")
    return render_template("login.html", next=next_url if auth.is_safe_next(next_url) else "")


@bp.post("/login")
def login_post():
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    next_url = request.form.get("next") or ""
    user = auth.authenticate(username, password)
    if user is not None:
        session.clear()
        session["user"] = user.username
        session["role"] = user.role
        session["user_id"] = user.id
        session.permanent = True
        return redirect(next_url if auth.is_safe_next(next_url) else auth.landing_url())
    return (
        render_template(
            "login.html",
            error="Usuario o contraseña incorrectos.",
            next=next_url if auth.is_safe_next(next_url) else "",
        ),
        401,
    )


@bp.route("/logout", methods=["GET", "POST"])
def logout():
    session.clear()
    return redirect(url_for("auth_routes.login"))
