"""Flask app — plate production: form-based registration, review, inventory.

This module only creates the `app` object, configures it, registers each
domain's blueprint, and holds the 2 routes too small to earn their own
blueprint (/  and /healthz). The actual routes live in app/web/blueprints/,
one module per domain:
  ingest.py        -- GET/POST /ingest, GET/POST /ingest/pdf (the
                      "Registrar" form, by hand or pre-filled from an
                      uploaded pedido PDF)
  records.py       -- GET /records, GET /records/<id>,
                      POST /records/<id>/save, POST /records/search-pdf,
                      POST /records/delete
  review.py        -- GET /review, POST /review/update|resolve|delete
  inventory.py     -- GET /inventory, POST /inventory/alistado
  pricing.py       -- GET /precios, POST /precios/<plate_type> (admin-only
                      tariff editor, Fase 5a)
  cash_summary.py  -- GET /cuadre, GET /cuadre/pdf (daily cash-reconciliation
                      report, Fase 5b)
  customers.py     -- GET /customers, GET/POST /customers/<id> (admin-only
                      customer master-data editor, Fase 6)
  declarations.py  -- GET /declaraciones, GET /declaraciones/<id>[/file/<kind>],
                      POST /declaraciones/<id>/approve|reject (review of the
                      sworn declarations from the public form)
  production.py    -- GET /produccion, GET /produccion/<id> (+ its declaration
                      photos), POST /produccion/<id>/advance|undo (the
                      production board and its status steps)
  users.py         -- GET /usuarios, GET/POST /usuarios/nuevo,
                      GET/POST /usuarios/<id>, POST /usuarios/<id>/password
                      (admin-only login account management)
  auth_routes.py   -- GET/POST /login, POST /logout
  public_declaration.py -- GET/POST /declaracion (PUBLIC sworn-declaration
                      form, no login; 503 until Turnstile is configured)

All routes except /login, /logout, /healthz and /declaracion require an
active logged-in user, checked against the database on every request. Each
one requires one permission code of the user's role -- see
app.web.auth.permission_required and app/core/permissions.py. `/` only
requires a logged-in user (app.web.auth.authenticated) and sends them to the
first nav entry they may open (app/web/navigation.py), or answers a 403 page
when their role has no permissions.

Every POST/PUT/PATCH/DELETE also needs a valid CSRF token (Flask-WTF
CSRFProtect, app/web/csrf.py); a missing or invalid one answers the 400 page
csrf_error.html. /declaracion checks it by hand, after raising its own body
cap. See docs/WEB.md, "CSRF protection".

Every 404 (an unknown URL or an abort(404)) answers the standalone Spanish
page not_found.html, with no nav and no database lookup.
"""

from __future__ import annotations

import logging
import os
from datetime import timedelta

import sentry_sdk
from flask import Flask, Response, make_response, redirect, render_template, request, session, url_for
from flask_wtf.csrf import CSRFError
from sentry_sdk.integrations.flask import FlaskIntegration

from app.core.timezones import format_bogota
from app.web import auth
from app.web.auth import authenticated
from app.web.blueprints.auth_routes import bp as auth_routes_bp
from app.web.blueprints.cash_summary import bp as cash_summary_bp
from app.web.blueprints.customers import bp as customers_bp
from app.web.blueprints.declarations import bp as declarations_bp
from app.web.blueprints.ingest import bp as ingest_bp
from app.web.blueprints.inventory import bp as inventory_bp
from app.web.blueprints.pricing import bp as pricing_bp
from app.web.blueprints.production import PRODUCTION_STATUS_LABELS
from app.web.blueprints.production import bp as production_bp
from app.web.blueprints.public_declaration import bp as public_declaration_bp
from app.web.blueprints.records import bp as records_bp
from app.web.blueprints.review import bp as review_bp
from app.web.blueprints.users import bp as users_bp
from app.web.csrf import csrf
from app.web.helpers import format_cop
from app.web.navigation import NAV_ITEMS

# Sentry: only if SENTRY_DSN is set, so dev/local/tests never send anything.
_SENTRY_DSN = os.environ.get("SENTRY_DSN")
if _SENTRY_DSN:
    sentry_sdk.init(dsn=_SENTRY_DSN, integrations=[FlaskIntegration()])

app = Flask(__name__)

# Flask's own default log handler (added lazily the first time app.logger is
# used) only emits WARNING and above -- INFO-level activity logging (login,
# order creation, record save; see the blueprints that call
# current_app.logger.info) would otherwise be silently dropped. This is the
# ONLY thing that makes those visible in `docker logs`/Dozzle; gunicorn's own
# error log (stderr, level INFO by default) is what actually carries them
# through to the container's stdout/stderr, since Flask logs to
# request.environ["wsgi.errors"] inside a request.
app.logger.setLevel(logging.INFO)

# Session signing key — required so cookies can't be forged. Generate one with:
#   python -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError(
        "SECRET_KEY env var is required. Generate one with: "
        'python -c "import secrets; print(secrets.token_hex(32))"'
    )
app.secret_key = SECRET_KEY
csrf.init_app(app)

app.register_blueprint(auth_routes_bp)
app.register_blueprint(cash_summary_bp)
app.register_blueprint(customers_bp)
app.register_blueprint(declarations_bp)
app.register_blueprint(ingest_bp)
app.register_blueprint(inventory_bp)
app.register_blueprint(pricing_bp)
app.register_blueprint(production_bp)
app.register_blueprint(public_declaration_bp)
app.register_blueprint(records_bp)
app.register_blueprint(review_bp)
app.register_blueprint(users_bp)

# Mark the session cookie Secure only behind HTTPS (set COOKIE_SECURE=1 in
# production). Off by default so local plain-HTTP dev still works.
_COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "").strip().lower() in ("1", "true", "yes")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=_COOKIE_SECURE,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    # Public /declaracion: PDF (<=10 MB) + two PNG signatures as data URLs
    # (~270 KB each). Werkzeug 3.1 caps non-file form fields at 500 KB by
    # default, which would reject a gestor submission.
    MAX_CONTENT_LENGTH=12 * 1024 * 1024,
    MAX_FORM_MEMORY_SIZE=2 * 1024 * 1024,
    # A CSRF token lives as long as the session (12 h for staff, the browser
    # session for anonymous visitors), so a slow /declaracion is never rejected.
    WTF_CSRF_TIME_LIMIT=None,
)


@app.errorhandler(CSRFError)
def _csrf_error(_exc: CSRFError) -> Response:
    """Missing or invalid CSRF token: a standalone Spanish page with a link
    back to the public form or to `/`. Rendered straight from the Jinja
    environment, skipping the context processors, so it never looks up the
    user or their permissions in the database."""
    back_url = "/declaracion" if request.path == "/declaracion" else url_for("index")
    body = app.jinja_env.get_template("csrf_error.html").render(back_url=back_url)
    response = make_response(body, 400)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.errorhandler(404)
def _not_found(_exc: Exception) -> Response:
    """Unknown URL or abort(404): a standalone Spanish page with a link back
    to the public form (a visitor without a session under /declaracion) or to
    `/`. Like _csrf_error it skips the context processors and never shows
    the request or the abort() message."""
    on_public_form = request.path == "/declaracion" or request.path.startswith("/declaracion/")
    back_url = "/declaracion" if on_public_form and "user_id" not in session else url_for("index")
    body = app.jinja_env.get_template("not_found.html").render(back_url=back_url)
    response = make_response(body, 404)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.context_processor
def _inject_permissions() -> dict:
    """Expose `can(code)` (auth.has_permission) and `nav_items` (the
    NAV_ITEMS entries the user may open) to templates, so they gate links and
    edit/delete UI on the same permission the route checks. On pages with no
    session (login, the public /declaracion) `can` is False and `nav_items`
    is empty, without touching the database."""
    nav_items = [item for item in NAV_ITEMS if auth.has_permission(item[2])]
    return {"can": auth.has_permission, "nav_items": nav_items}


app.jinja_env.filters["cop"] = format_cop
app.jinja_env.filters["bogota"] = format_bogota
# The production-status labels, for _production_badge.html on every screen.
app.jinja_env.globals["production_status_labels"] = PRODUCTION_STATUS_LABELS


@app.get("/")
@authenticated
def index():
    landing = auth.landing_url()
    if landing == url_for("index"):
        return render_template("no_permissions.html"), 403
    return redirect(landing)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
