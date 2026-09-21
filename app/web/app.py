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
  auth_routes.py   -- GET/POST /login, GET/POST /logout

All routes except /login, /logout and /healthz require an authenticated
session. Most also require a full-access role (admin/producción); see
app.web.auth.login_required. A small allowlist of read-only lookup routes
(/records, /records/<id>) instead accepts any authenticated role, including
the read-only 'consulta' role -- see app.web.auth.consulta_allowed.
"""

from __future__ import annotations

import os
from datetime import timedelta

import sentry_sdk
from flask import Flask, redirect
from sentry_sdk.integrations.flask import FlaskIntegration

from app.web import auth
from app.web.auth import consulta_allowed
from app.web.blueprints.auth_routes import bp as auth_routes_bp
from app.web.blueprints.ingest import bp as ingest_bp
from app.web.blueprints.inventory import bp as inventory_bp
from app.web.blueprints.records import bp as records_bp
from app.web.blueprints.review import bp as review_bp
from app.web.helpers import format_cop

# Sentry: only if SENTRY_DSN is set, so dev/local/tests never send anything.
_SENTRY_DSN = os.environ.get("SENTRY_DSN")
if _SENTRY_DSN:
    sentry_sdk.init(dsn=_SENTRY_DSN, integrations=[FlaskIntegration()])

app = Flask(__name__)

# Session signing key — required so cookies can't be forged. Generate one with:
#   python -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError(
        "SECRET_KEY env var is required. Generate one with: "
        'python -c "import secrets; print(secrets.token_hex(32))"'
    )
app.secret_key = SECRET_KEY

app.register_blueprint(auth_routes_bp)
app.register_blueprint(ingest_bp)
app.register_blueprint(inventory_bp)
app.register_blueprint(records_bp)
app.register_blueprint(review_bp)

# Mark the session cookie Secure only behind HTTPS (set COOKIE_SECURE=1 in
# production). Off by default so local plain-HTTP dev still works.
_COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "").strip().lower() in ("1", "true", "yes")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=_COOKIE_SECURE,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
)

@app.context_processor
def _inject_full_access_roles() -> dict:
    """Expose auth.FULL_ACCESS_ROLES/POS_ROLES to templates, so they gate
    edit/delete UI on role membership (not a hardcoded '!= consulta') --
    keeps templates in sync automatically if a role's access level ever
    changes again. pos_roles (Fase 3) is its own key, not a rename of
    full_access_roles: admin-only actions (Revisión/Inventario links, the
    Registros 'Eliminar' button) must stay gated on full_access_roles, while
    /ingest and /records/<id>/save's edit form are now POS_ROLES (admin +
    punto_venta) -- see _nav.html and record_detail.html."""
    return {"full_access_roles": auth.FULL_ACCESS_ROLES, "pos_roles": auth.POS_ROLES}


app.jinja_env.filters["cop"] = format_cop


@app.get("/")
@consulta_allowed
def index():
    return redirect(auth.landing_url())


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
