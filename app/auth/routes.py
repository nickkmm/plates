"""/login and /logout -- the Jinja login page (open) and logout (open, POST only).

They sign in and out through app/auth/login.py, the same code as the JSON
auth API (app/auth/api.py), and set the same two token cookies. The access
check every OTHER blueprint uses (permission_required/authenticated) lives in
app/shared/auth.py. The Flask session only holds the CSRF token.
"""

from __future__ import annotations

from flask import Blueprint, redirect, render_template, request, session, url_for

from app.auth import login as auth_login
from app.shared.auth import current_user, is_safe_next
from app.web.navigation import landing_url

bp = Blueprint("auth_routes", __name__)


@bp.get("/login")
def login():
    if current_user() is not None:
        return redirect(landing_url())
    next_url = request.args.get("next", "")
    return render_template("login.html", next=next_url if is_safe_next(next_url) else "")


@bp.post("/login")
def login_post():
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    next_url = request.form.get("next") or ""
    signed_in = auth_login.sign_in(username, password)
    if signed_in is not None:
        # The request itself carries no token yet: land by the new permissions.
        target = next_url if is_safe_next(next_url) else landing_url(signed_in.permissions)
        return auth_login.set_token_cookies(redirect(target), signed_in)
    return (
        render_template(
            "login.html",
            error="Usuario o contraseña incorrectos.",
            next=next_url if is_safe_next(next_url) else "",
        ),
        401,
    )


@bp.post("/logout")
def logout():
    """Clears the access cookie and the session, then ends at
    /api/auth/logout/continue, which receives the refresh cookie and revokes it."""
    session.clear()
    return auth_login.clear_access_cookie(redirect(url_for("auth_api.logout_continue")))
