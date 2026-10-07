"""/declaracion -- the PUBLIC (no login) sworn-declaration form.

This is the app's first public write endpoint, so there is deliberately NO
login decorator and everything else here is defence in depth:

* Availability gate: the form only works when Turnstile is configured
  (`TURNSTILE_SITE_KEY` + `TURNSTILE_SECRET_KEY`) or `TURNSTILE_BYPASS` is on
  (dev/tests). Otherwise GET and POST answer 503, so production exposes
  nothing until the owner sets the Cloudflare keys.
* POST pipeline, cheapest check first: honeypot, signed form token (minimum
  and maximum age), Turnstile, per-IP rate limit (counted in Postgres), then
  parsing, file saving, validation and finally `submit_declaration`.
* Files are written before validation (the validator needs their names); any
  failure deletes every file saved during the request.
* Privacy: the response is the same generic page for an approved and for a
  `pending_review` declaration. Status, flags and any detail about a previous
  owner never reach the anonymous submitter (they go to the log without PII).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import psycopg.errors
from flask import Blueprint, Response, current_app, make_response, render_template, request
from werkzeug.exceptions import RequestEntityTooLarge

from app.core import db, normalize
from app.core.declarations import (
    ACTING_AS_GESTOR,
    LOSS_REASON,
    DeclarationInput,
    PersonInput,
    SubmitResult,
    VehicleInput,
    submit_declaration,
    validate_declaration,
)
from app.web import antispam
from app.web.uploads import UploadError, resolve_upload_path, save_pdf, save_signature_png

bp = Blueprint("public_declaration", __name__)

MAX_SUBMISSIONS_PER_IP_PER_HOUR = 5

# Form keys never copied into `submitted_data`: the signatures are huge base64
# blobs (stored as files) and the rest is anti-spam plumbing.
_EXCLUDED_RAW_KEYS = frozenset(
    {"owner_signature", "gestor_signature", "form_token", "website", "cf-turnstile-response"}
)
_PERSON_FIELDS = (
    "name", "document_type", "document_number", "address", "neighborhood",
    "municipality", "phone", "email",
)
# VehicleInput field -> form field name.
_VEHICLE_FORM_KEYS = {
    "plate_number": "vehicle_plate_number",
    "transit_license": "vehicle_transit_license",
    "engine_number": "vehicle_engine_number",
    "chassis_number": "vehicle_chassis_number",
    "vehicle_type": "vehicle_type",
    "registered_in": "vehicle_registered_in",
    "vehicle_brand": "vehicle_brand",
}

_ERR_TOKEN = "El formulario expiró o no es válido, inténtalo de nuevo."
_ERR_TURNSTILE = "No pudimos verificar que eres una persona. Inténtalo de nuevo."
_ERR_GENERIC = "No pudimos guardar tu declaración por un problema temporal. Inténtalo de nuevo en unos minutos."
_ERR_TOO_LARGE = "Los archivos enviados son demasiado grandes. Reduce el tamaño del PDF e inténtalo de nuevo."
_ERR_RATE_LIMIT = (
    "Recibimos varias declaraciones desde tu conexión. "
    "Inténtalo de nuevo más tarde o acércate al mostrador de INGSE."
)


def _form_enabled() -> bool:
    if os.environ.get("TURNSTILE_BYPASS", "").strip().lower() in ("1", "true", "yes"):
        return True
    return bool(
        os.environ.get("TURNSTILE_SITE_KEY", "").strip()
        and os.environ.get("TURNSTILE_SECRET_KEY", "").strip()
    )


def _page(template: str, status: int = 200, **context: Any) -> Response:
    response = make_response(render_template(template, **context), status)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "DENY"
    return response


def _render_form(
    status: int = 200, *, form: dict[str, str] | None = None, errors: list[str] | None = None,
) -> Response:
    return _page(
        "declaration.html", status,
        form=form or {}, errors=errors or [],
        token=antispam.issue_form_token(str(current_app.secret_key)),
        turnstile_site_key=os.environ.get("TURNSTILE_SITE_KEY", "").strip(),
        reasons=normalize.DECLARATION_REASONS,
        document_types=normalize.DOCUMENT_TYPES,
        vehicle_types=[t for t in normalize.PLATE_TYPES if t != "ORNAMENTAL"],
        owner_relations=("DUEÑO", "POSEEDOR"),
    )


def _sticky_form() -> dict[str, str]:
    return {k: v for k, v in request.form.items() if k not in _EXCLUDED_RAW_KEYS}


def _parse_quantity(raw: str) -> int:
    try:
        return int(raw.strip())
    except ValueError:
        return 0  # fails validation ("al menos 1")


def _person(form: dict[str, str], prefix: str) -> PersonInput:
    return PersonInput(**{f: form.get(f"{prefix}_{f}", "").strip() for f in _PERSON_FIELDS})


def _build_input(form: dict[str, str]) -> DeclarationInput:
    acting_as = form.get("acting_as", "").strip()
    is_gestor = acting_as == ACTING_AS_GESTOR
    return DeclarationInput(
        reason=form.get("reason", "").strip(),
        plate_quantity=_parse_quantity(form.get("plate_quantity", "")),
        acting_as=acting_as,
        owner=_person(form, "owner"),
        owner_relation=form.get("owner_relation", "").strip() if is_gestor else "",
        gestor=_person(form, "gestor") if is_gestor else None,
        vehicle=VehicleInput(
            **{field: form.get(key, "").strip() for field, key in _VEHICLE_FORM_KEYS.items()},
        ),
        submitter_ip=antispam.client_ip(request),
        user_agent=(request.headers.get("User-Agent") or "")[:500] or None,
        accepted_terms=bool(form.get("accept_terms", "").strip()),
    )


def _delete_files(names: list[str]) -> None:
    for name in names:
        try:
            resolve_upload_path(name).unlink(missing_ok=True)
        except (UploadError, OSError):
            current_app.logger.warning("could not delete orphan upload %s", name)


def _save_files(inp: DeclarationInput, saved: list[str]) -> list[str]:
    """Store signatures / PDF and set their names on `inp`; every stored name
    is appended to `saved` so the caller can clean up. A missing file is left
    as None (the validator reports it); only a present-but-bad one errors here."""
    errors: list[str] = []

    def attempt(save: Callable[[Any], str], value: Any, label: str) -> str | None:
        try:
            name = save(value)
        except UploadError as exc:
            errors.append(f"{label}: {exc}")
            return None
        saved.append(name)
        return name

    owner_sig = request.form.get("owner_signature", "").strip()
    if owner_sig:
        inp.owner_signature_file = attempt(save_signature_png, owner_sig, "Firma del propietario/poseedor")
    if inp.acting_as == ACTING_AS_GESTOR:
        gestor_sig = request.form.get("gestor_signature", "").strip()
        if gestor_sig:
            inp.gestor_signature_file = attempt(save_signature_png, gestor_sig, "Firma del gestor")
    # The PDF only matters for PÉRDIDA; one sent for DETERIORO is discarded.
    if normalize.normalize_choice(inp.reason, normalize.DECLARATION_REASONS) == LOSS_REASON:
        pdf = request.files.get("notarized_pdf")
        if pdf is not None and pdf.filename:
            inp.notarized_pdf_file = attempt(save_pdf, pdf, "PDF autenticado")
    return errors


def _submit(inp: DeclarationInput) -> SubmitResult:
    """Run submit_declaration; on a UniqueViolation (two people declaring the
    same plate at once) roll back and retry once -- the second pass sees the
    other row and stores a `pending_review` conflict instead."""
    with db.get_conn() as conn:
        try:
            result = submit_declaration(conn, inp)
        except psycopg.errors.UniqueViolation:
            conn.rollback()
            result = submit_declaration(conn, inp)
        conn.commit()
        return result


@bp.errorhandler(RequestEntityTooLarge)
def _too_large(_exc: RequestEntityTooLarge) -> Response:
    return _render_form(413, errors=[_ERR_TOO_LARGE])


@bp.get("/declaracion")
def declaration_form() -> Response:
    if not _form_enabled():
        return _page("declaration_unavailable.html", 503)
    return _render_form()


@bp.post("/declaracion")
def declaration_submit() -> Response:
    if not _form_enabled():
        return _page("declaration_unavailable.html", 503)
    ip = antispam.client_ip(request)
    form = request.form

    # a. honeypot: pretend success, store nothing.
    if antispam.honeypot_triggered(form):
        current_app.logger.info("declaration honeypot triggered ip=%s", ip)
        return _page("declaration_received.html")

    sticky = _sticky_form()

    # b. signed form token (minimum and maximum age).
    check = antispam.verify_form_token(form.get("form_token"), str(current_app.secret_key))
    if check is not antispam.TokenCheck.OK:
        current_app.logger.info("declaration token rejected (%s) ip=%s", check.value, ip)
        return _render_form(400, form=sticky, errors=[_ERR_TOKEN])

    # c. Turnstile.
    if not antispam.verify_turnstile(form.get("cf-turnstile-response"), ip):
        current_app.logger.info("declaration turnstile failed ip=%s", ip)
        return _render_form(400, form=sticky, errors=[_ERR_TURNSTILE])

    # d. per-IP rate limit, counted in Postgres (2 gunicorn workers).
    with db.get_conn() as conn:
        recent = db.count_recent_by_ip(conn, ip, since=datetime.now() - timedelta(hours=1))
    if antispam.is_rate_limited(recent, MAX_SUBMISSIONS_PER_IP_PER_HOUR):
        current_app.logger.info("declaration rate limited ip=%s", ip)
        return _render_form(429, form=sticky, errors=[_ERR_RATE_LIMIT])

    # e-f. build the input and store the files.
    inp = _build_input(dict(form.items()))
    inp.submitted_data = sticky
    saved: list[str] = []
    errors = _save_files(inp, saved)

    # g. validate; on any error leave no files behind.
    errors += validate_declaration(inp)
    if errors:
        _delete_files(saved)
        return _render_form(400, form=sticky, errors=errors)

    # h. persist (one retry on a plate/licence UniqueViolation race).
    try:
        result = _submit(inp)
    except Exception:
        _delete_files(saved)
        current_app.logger.exception("declaration submit failed ip=%s", ip)
        return _render_form(500, form=sticky, errors=[_ERR_GENERIC])

    # i. same page for approved and pending_review; log without PII.
    current_app.logger.info(
        "declaration stored id=%s status=%s flags=%s ip=%s",
        result.declaration_id, result.status, ",".join(result.flags) or "-", ip,
    )
    return _page("declaration_received.html")
