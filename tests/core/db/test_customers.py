"""Integration tests for app/core/db/customers.py --
resolve_or_create_customer, list_customers, get_customer_by_name,
get_customer. Needs a real Postgres connection (see
tests/conftest.py's `db_conn`/`clean_db`)."""

from __future__ import annotations

import psycopg
import pytest
from psycopg.rows import tuple_row

from app.core import db


class TestResolveOrCreateCustomer:
    def test_creates_a_new_customer_and_returns_its_id(self, db_conn):
        customer_id = db.resolve_or_create_customer(db_conn, "PTO.V")
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT name FROM customers WHERE id = %s", (customer_id,))
            assert cur.fetchone() == ("PTO.V",)

    def test_new_customer_has_all_attributes_null(self, db_conn):
        customer_id = db.resolve_or_create_customer(db_conn, "NUEVO CLIENTE")
        customer = db.get_customer(db_conn, customer_id)
        assert customer is not None
        assert customer.type is None
        assert customer.document_type is None
        assert customer.document_number is None
        assert customer.document_relation is None
        assert customer.phone is None
        assert customer.email is None
        assert customer.transit_license is None

    def test_second_call_with_same_name_returns_same_id(self, db_conn):
        first = db.resolve_or_create_customer(db_conn, "MUVA")
        second = db.resolve_or_create_customer(db_conn, "MUVA")
        assert first == second
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM customers")
            assert cur.fetchone()[0] == 1

    def test_spelling_variant_resolves_to_the_same_customer(self, db_conn):
        """PTOV / PTO DE VENTA / PTO.V are curated as the SAME entity in
        normalize._REQUESTED_BY_MAP_RAW -- resolve_or_create_customer must
        canonicalize BEFORE the lookup, not just before the insert, or a
        second spelling would create a near-duplicate customer row."""
        first = db.resolve_or_create_customer(db_conn, "PTOV")
        second = db.resolve_or_create_customer(db_conn, "PTO DE VENTA")
        assert first == second
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT name FROM customers")
            assert cur.fetchall() == [("PTO.V",)]

    def test_distinct_entities_are_not_merged(self, db_conn):
        """MUVA GRANADA and MUVA SERGIO are deliberately kept separate by
        normalize_requested_by (see its module docstring) -- must resolve to
        two different customers."""
        granada = db.resolve_or_create_customer(db_conn, "MUVA GRANADA")
        sergio = db.resolve_or_create_customer(db_conn, "MUVA SERGIO")
        assert granada != sergio

    def test_new_customer_takes_the_given_attributes(self, db_conn):
        """Fase 3: the /ingest 'cliente' fieldset writes attributes for a
        genuinely brand-new customer through these keyword args."""
        customer_id = db.resolve_or_create_customer(
            db_conn, "CLIENTE NUEVO", type="PERSONA NATURAL", document_type="CÉDULA",
            document_number="123456", document_relation="DUEÑO", phone="3001112233",
            email="a@b.com", transit_license="LIC1",
        )
        customer = db.get_customer(db_conn, customer_id)
        assert customer is not None
        assert customer.type == "PERSONA NATURAL"
        assert customer.document_type == "CÉDULA"
        assert customer.document_number == "123456"
        assert customer.document_relation == "DUEÑO"
        assert customer.phone == "3001112233"
        assert customer.email == "a@b.com"
        assert customer.transit_license == "LIC1"

    def test_existing_customer_attributes_are_never_overwritten(self, db_conn):
        """An attribute the customer ALREADY has a value for must never be
        replaced by a different one from a later call -- only a still-NULL
        attribute may be filled in (see
        test_existing_customer_null_attributes_get_backfilled below)."""
        first_id = db.resolve_or_create_customer(
            db_conn, "PTO.V", type="EMPRESA", phone="3009998877",
        )
        second_id = db.resolve_or_create_customer(
            db_conn, "PTO.V", type="PERSONA NATURAL", phone="3000000000",
        )
        assert first_id == second_id
        customer = db.get_customer(db_conn, first_id)
        assert customer is not None
        assert customer.type == "EMPRESA"
        assert customer.phone == "3009998877"

    def test_existing_customer_null_attributes_get_backfilled(self, db_conn):
        """The other half of the contract: a still-NULL attribute on an
        EXISTING customer (e.g. one of the 191 rows the pre-POS historical
        backfill created with every attribute NULL) gets filled in the next
        time a caller passes a real value for it -- without touching any
        attribute the customer already has a value for."""
        first_id = db.resolve_or_create_customer(db_conn, "PTO.V", type="EMPRESA")
        second_id = db.resolve_or_create_customer(
            db_conn, "PTO.V", type="PERSONA NATURAL", phone="3001234567",
        )
        assert first_id == second_id
        customer = db.get_customer(db_conn, first_id)
        assert customer is not None
        assert customer.type == "EMPRESA"  # already set -- untouched
        assert customer.phone == "3001234567"  # was NULL -- now filled in

    def test_a_null_argument_never_blanks_out_an_existing_value(self, db_conn):
        """A caller that doesn't have a given field to offer passes None for
        it (e.g. ingestion.py's/record_save's name-only calls) -- that must
        never be mistaken for "clear this attribute"."""
        first_id = db.resolve_or_create_customer(db_conn, "PTO.V", phone="3009998877")
        second_id = db.resolve_or_create_customer(db_conn, "PTO.V")
        assert first_id == second_id
        customer = db.get_customer(db_conn, first_id)
        assert customer is not None
        assert customer.phone == "3009998877"



class TestListCustomers:
    def test_returns_every_customer_alphabetically(self, db_conn):
        db.resolve_or_create_customer(db_conn, "ZETA")
        db.resolve_or_create_customer(db_conn, "ALFA")
        names = [c.name for c in db.list_customers(db_conn)]
        assert names == ["ALFA", "ZETA"]

    def test_empty_when_no_customers(self, db_conn):
        assert db.list_customers(db_conn) == []



class TestGetCustomerByName:
    def test_finds_an_existing_customer_by_canonical_name(self, db_conn):
        customer_id = db.resolve_or_create_customer(db_conn, "PTO.V")
        found = db.get_customer_by_name(db_conn, "PTO.V")
        assert found is not None
        assert found.id == customer_id

    def test_matches_via_canonicalization_not_just_exact_string(self, db_conn):
        customer_id = db.resolve_or_create_customer(db_conn, "PTOV")  # stored as "PTO.V"
        found = db.get_customer_by_name(db_conn, "pto de venta")  # same canonical form
        assert found is not None
        assert found.id == customer_id

    def test_unknown_name_returns_none(self, db_conn):
        assert db.get_customer_by_name(db_conn, "NO EXISTE") is None



class TestGetCustomer:
    def test_returns_none_for_nonexistent_id(self, db_conn):
        assert db.get_customer(db_conn, 999999) is None


class TestUpdateCustomer:
    """Unlike resolve_or_create_customer (COALESCE, never overwrites an
    existing value), update_customer is a real, deliberate overwrite -- the
    /customers admin screen's whole reason to exist. See its docstring."""

    def test_overwrites_every_attribute(self, db_conn):
        customer_id = db.resolve_or_create_customer(
            db_conn, "PTO.V", type="EMPRESA", phone="3001112233",
        )
        db.update_customer(
            db_conn, customer_id, name="PTO.V", type="PERSONA NATURAL",
            document_type="CÉDULA", document_number="999", document_relation="DUEÑO",
            phone="3009998877", email="nuevo@correo.com", transit_license="LIC9",
        )
        customer = db.get_customer(db_conn, customer_id)
        assert customer is not None
        assert customer.type == "PERSONA NATURAL"
        assert customer.document_type == "CÉDULA"
        assert customer.document_number == "999"
        assert customer.document_relation == "DUEÑO"
        assert customer.phone == "3009998877"
        assert customer.email == "nuevo@correo.com"
        assert customer.transit_license == "LIC9"

    def test_can_blank_out_a_previously_set_attribute(self, db_conn):
        """A real overwrite, not a COALESCE -- passing None for an attribute
        that already had a value must actually clear it, unlike
        resolve_or_create_customer."""
        customer_id = db.resolve_or_create_customer(db_conn, "PTO.V", phone="3001112233")
        db.update_customer(
            db_conn, customer_id, name="PTO.V", type=None, document_type=None,
            document_number=None, document_relation=None, phone=None, email=None,
            transit_license=None,
        )
        customer = db.get_customer(db_conn, customer_id)
        assert customer is not None
        assert customer.phone is None

    def test_can_rename_the_customer(self, db_conn):
        customer_id = db.resolve_or_create_customer(db_conn, "PTO.V VIEJO")
        db.update_customer(
            db_conn, customer_id, name="PTO.V NUEVO", type=None, document_type=None,
            document_number=None, document_relation=None, phone=None, email=None,
            transit_license=None,
        )
        customer = db.get_customer(db_conn, customer_id)
        assert customer is not None
        assert customer.name == "PTO.V NUEVO"

    def test_renaming_to_a_name_already_used_by_another_customer_raises(self, db_conn):
        db.resolve_or_create_customer(db_conn, "MUVA GRANADA")
        other_id = db.resolve_or_create_customer(db_conn, "MUVA SERGIO")
        db_conn.commit()
        with pytest.raises(psycopg.errors.UniqueViolation):
            db.update_customer(
                db_conn, other_id, name="MUVA GRANADA", type=None, document_type=None,
                document_number=None, document_relation=None, phone=None, email=None,
                transit_license=None,
            )
        db_conn.rollback()

