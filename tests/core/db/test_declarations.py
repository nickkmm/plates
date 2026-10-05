"""Integration tests for app/core/db/declarations.py. Needs a real Postgres
connection (see tests/conftest.py's `db_conn`/`clean_db`)."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.core import db
from tests.conftest import TEST_USERNAME


def _insert(db_conn, **overrides):
    kwargs = {
        "reason": "DETERIORO", "plate_quantity": 1, "acting_as": "PROPIETARIO",
        "status": "pending_review",
        "submitted_data": {"owner": {"name": "ANA", "document_number": "123"},
                           "vehicle": {"plate_number": "ABC123", "transit_license": "LIC-9"}},
    }
    kwargs.update(overrides)
    return db.insert_declaration(db_conn, **kwargs)


def _set_created_at(db_conn, declaration_id: int, when: datetime) -> None:
    with db_conn.cursor() as cur:
        cur.execute("UPDATE declarations SET created_at = %s WHERE id = %s", (when, declaration_id))


class TestInsertAndGet:
    def test_roundtrips_every_field(self, db_conn):
        customer_id = db.resolve_or_create_customer(db_conn, "ANA")
        gestor_id = db.resolve_or_create_customer(db_conn, "GESTORIA")
        card_id = db.create_ownership_card(db_conn, customer_id, transit_license="L1", plate_number="ABC123")
        accepted = datetime(2026, 1, 2, 3, 4, 5)
        declaration_id = _insert(
            db_conn, status="approved", review_flags=["a", "b"], customer_id=customer_id,
            ownership_card_id=card_id, gestor_id=gestor_id, owner_signature_path="o.png",
            gestor_signature_path="g.png", notarized_pdf_path="n.pdf", submitter_ip="1.2.3.4",
            user_agent="UA", accepted_terms_at=accepted, reason="PÉRDIDA", plate_quantity=2,
            acting_as="GESTOR",
        )
        record = db.get_declaration(db_conn, declaration_id)
        assert record is not None
        assert record.reason == "PÉRDIDA"
        assert record.plate_quantity == 2
        assert record.acting_as == "GESTOR"
        assert record.status == "approved"
        assert record.review_flags == ["a", "b"]
        assert (record.customer_id, record.ownership_card_id, record.gestor_id) == (
            customer_id, card_id, gestor_id)
        assert (record.owner_signature_path, record.gestor_signature_path,
                record.notarized_pdf_path) == ("o.png", "g.png", "n.pdf")
        assert (record.submitter_ip, record.user_agent) == ("1.2.3.4", "UA")
        assert record.accepted_terms_at == accepted
        assert record.submitted_data["owner"]["name"] == "ANA"
        assert record.created_at is not None
        assert record.reviewed_by is None and record.reviewed_at is None

    def test_defaults(self, db_conn):
        record = db.get_declaration(db_conn, _insert(db_conn))
        assert record is not None
        assert record.review_flags == []
        assert record.customer_id is None
        assert record.ownership_card_id is None
        assert record.gestor_id is None

    def test_get_unknown_returns_none(self, db_conn):
        assert db.get_declaration(db_conn, 999999) is None


class TestListDeclarations:
    def test_pending_first_then_newest(self, db_conn):
        base = datetime(2026, 3, 1, 12, 0)
        approved_new = _insert(db_conn, status="approved")
        pending_old = _insert(db_conn, status="pending_review")
        pending_new = _insert(db_conn, status="pending_review")
        rejected = _insert(db_conn, status="rejected")
        _set_created_at(db_conn, approved_new, base + timedelta(days=5))
        _set_created_at(db_conn, pending_old, base)
        _set_created_at(db_conn, pending_new, base + timedelta(days=1))
        _set_created_at(db_conn, rejected, base + timedelta(days=9))
        ids = [d.id for d in db.list_declarations(db_conn)]
        assert ids == [pending_new, pending_old, rejected, approved_new]

    def test_filter_by_status(self, db_conn):
        pending = _insert(db_conn, status="pending_review")
        _insert(db_conn, status="approved")
        assert [d.id for d in db.list_declarations(db_conn, status="pending_review")] == [pending]

    def test_query_matches_submitted_data_case_insensitive(self, db_conn):
        data = {"owner": {"name": "Maria Lopez", "document_number": "98765"},
                "gestor": {"name": "Gestoria Sur", "document_number": "555"},
                "vehicle": {"plate_number": "XYZ789", "transit_license": "LIC-777"}}
        match = _insert(db_conn, submitted_data=data)
        _insert(db_conn)  # the default one, must never match these terms
        for term in ("maria", "98765", "xyz789", "lic-777", "gestoria", "555"):
            assert [d.id for d in db.list_declarations(db_conn, query=term)] == [match], term

    def test_query_matches_linked_rows(self, db_conn):
        customer_id = db.resolve_or_create_customer(db_conn, "JUAN PEREZ", document_number="4242")
        card_id = db.create_ownership_card(db_conn, customer_id, transit_license="LNK-1", plate_number="LNK123")
        linked = _insert(db_conn, status="approved", customer_id=customer_id, ownership_card_id=card_id,
                         submitted_data={"owner": {"name": "otra cosa"}})
        for term in ("juan perez", "4242", "lnk123", "lnk-1"):
            assert [d.id for d in db.list_declarations(db_conn, query=term)] == [linked], term

    def test_query_and_status_combine_and_blank_query_ignored(self, db_conn):
        a = _insert(db_conn, status="approved")
        b = _insert(db_conn, status="pending_review")
        assert [d.id for d in db.list_declarations(db_conn, query="ana", status="approved")] == [a]
        assert {d.id for d in db.list_declarations(db_conn, query="  ")} == {a, b}


class TestCountRecentByIp:
    def test_counts_only_that_ip_since_the_cutoff(self, db_conn):
        since = datetime(2026, 5, 1, 12, 0)
        old = _insert(db_conn, submitter_ip="9.9.9.9")
        boundary = _insert(db_conn, submitter_ip="9.9.9.9")
        recent = _insert(db_conn, submitter_ip="9.9.9.9")
        other_ip = _insert(db_conn, submitter_ip="8.8.8.8")
        _insert(db_conn, submitter_ip=None)
        _set_created_at(db_conn, old, since - timedelta(seconds=1))
        _set_created_at(db_conn, boundary, since)
        _set_created_at(db_conn, recent, since + timedelta(hours=1))
        _set_created_at(db_conn, other_ip, since + timedelta(hours=1))
        assert db.count_recent_by_ip(db_conn, "9.9.9.9", since=since) == 2
        assert db.count_recent_by_ip(db_conn, "8.8.8.8", since=since) == 1
        assert db.count_recent_by_ip(db_conn, "7.7.7.7", since=since) == 0


class TestSetDeclarationReview:
    def test_records_review_and_fks(self, db_conn):
        user = db.get_user_by_username(db_conn, TEST_USERNAME)
        assert user is not None
        customer_id = db.resolve_or_create_customer(db_conn, "ANA")
        declaration_id = _insert(db_conn)
        db.set_declaration_review(db_conn, declaration_id, status="approved", reviewed_by=user.id,
                                  note="ok", customer_id=customer_id)
        record = db.get_declaration(db_conn, declaration_id)
        assert record is not None
        assert record.status == "approved"
        assert record.reviewed_by == user.id
        assert record.reviewed_at is not None
        assert record.review_note == "ok"
        assert record.customer_id == customer_id
