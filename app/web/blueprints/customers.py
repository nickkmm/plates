"""/customers -- admin-only screen to view and fix a customer's own stored
attributes (Fase 6).

Everywhere else in the app (ingestion.py, record_save, /ingest's own
resolve_or_create_customer call) only ever resolves/creates a customer by
name, or fills in a still-NULL attribute (db.resolve_or_create_customer's
COALESCE, see its docstring) -- this is the one place a person can
knowingly overwrite what's already stored, e.g. fixing a typo or filling
in one of the 191 customers the pre-POS historical backfill left with
every attribute NULL (see docs/DATABASE.md).

@login_required (admin-only), same access level as /precios -- master
data, not order/payment capture, so 'point_of_sale' doesn't get it.
"""

from __future__ import annotations

import psycopg
from flask import Blueprint, redirect, render_template, request, url_for

from app.core import db
from app.core.normalize import (
    CUSTOMER_TYPES,
    DOCUMENT_RELATIONS,
    DOCUMENT_TYPES,
    normalize_requested_by,
)
from app.web.auth import login_required
from app.web.helpers import customer_fields_from_form

bp = Blueprint("customers", __name__)


@bp.get("/customers")
@login_required
def customers():
    query = (request.args.get("q") or "").strip()
    with db.get_conn() as conn:
        rows = db.list_customers(conn)
    if query:
        needle = query.upper()
        rows = [c for c in rows if needle in c.name.upper()]
    return render_template("customers.html", rows=rows, query=query)


@bp.get("/customers/<int:customer_id>")
@login_required
def customer_detail(customer_id: int):
    with db.get_conn() as conn:
        customer = db.get_customer(conn, customer_id)
        cards = db.list_ownership_cards_for_customer(conn, customer_id) if customer else []
    if customer is None:
        return redirect(url_for("customers.customers"))
    return render_template(
        "customer_detail.html", customer=customer, cards=cards,
        customer_types=CUSTOMER_TYPES, document_types=DOCUMENT_TYPES,
        document_relations=DOCUMENT_RELATIONS,
    )


@bp.post("/customers/<int:customer_id>")
@login_required
def customer_save(customer_id: int):
    def _rerender(error: str):
        with db.get_conn() as conn:
            customer = db.get_customer(conn, customer_id)
            cards = db.list_ownership_cards_for_customer(conn, customer_id)
        return render_template(
            "customer_detail.html", customer=customer, cards=cards, error=error,
            customer_types=CUSTOMER_TYPES, document_types=DOCUMENT_TYPES,
            document_relations=DOCUMENT_RELATIONS,
        ), 400

    name = (request.form.get("name") or "").strip()
    if not name:
        return _rerender("El nombre no puede quedar vacío.")
    canonical = normalize_requested_by(name)

    try:
        with db.get_conn() as conn:
            existing = db.get_customer(conn, customer_id)
            if existing is None:
                return redirect(url_for("customers.customers"))
            db.update_customer(
                conn, customer_id, name=canonical, **customer_fields_from_form(request.form),
            )
            conn.commit()
    except psycopg.errors.UniqueViolation:
        return _rerender(f"Ya existe otro cliente con el nombre '{canonical}'.")

    return redirect(url_for("customers.customer_detail", customer_id=customer_id))
