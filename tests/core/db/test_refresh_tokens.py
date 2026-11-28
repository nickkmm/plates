"""Integration tests for app/auth/refresh_tokens.py and its SQL
(app/auth/repository/refresh_tokens.py): issue, rotate with reuse detection,
revoke. Needs a real Postgres connection (see tests/conftest.py's
`db_conn`/`clean_db`, which seeds the test users)."""

from __future__ import annotations

import hashlib
from datetime import timedelta

from app.auth import refresh_tokens, repository
from app.shared.timezones import utc_now
from tests.conftest import PRODUCCION_USERNAME, TEST_USERNAME


def _user_id(conn, username: str = TEST_USERNAME) -> int:
    user = repository.get_user_by_username(conn, username)
    assert user is not None
    return user.id


def _sha256(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _rows(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM refresh_tokens ORDER BY id")
        return [dict(r) for r in cur.fetchall()]


def _row(conn, raw: str) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM refresh_tokens WHERE token_hash = %s", (_sha256(raw),))
        row = cur.fetchone()
        assert row is not None
        return dict(row)


class TestIssue:
    def test_stores_only_the_hash(self, db_conn):
        user_id = _user_id(db_conn)
        raw = refresh_tokens.issue(db_conn, user_id)
        rows = _rows(db_conn)
        assert len(rows) == 1
        assert rows[0]["user_id"] == user_id
        assert rows[0]["token_hash"] == _sha256(raw)
        assert rows[0]["revoked_at"] is None
        assert all(raw not in str(value) for value in rows[0].values())

    def test_expires_about_30_days_ahead(self, db_conn):
        raw = refresh_tokens.issue(db_conn, _user_id(db_conn))
        delta = _row(db_conn, raw)["expires_at"] - utc_now()
        assert timedelta(days=30) - timedelta(minutes=1) < delta <= timedelta(days=30)

    def test_two_issues_give_different_families(self, db_conn):
        user_id = _user_id(db_conn)
        first = refresh_tokens.issue(db_conn, user_id)
        second = refresh_tokens.issue(db_conn, user_id)
        assert first != second
        assert _row(db_conn, first)["family_id"] != _row(db_conn, second)["family_id"]


class TestRotate:
    def test_returns_a_new_token_in_the_same_family(self, db_conn):
        user_id = _user_id(db_conn)
        old = refresh_tokens.issue(db_conn, user_id)
        result = refresh_tokens.rotate(db_conn, old)
        assert result is not None
        rotated_user_id, new = result
        assert rotated_user_id == user_id
        assert new != old
        old_row, new_row = _row(db_conn, old), _row(db_conn, new)
        assert old_row["revoked_at"] is not None
        assert new_row["revoked_at"] is None
        assert new_row["family_id"] == old_row["family_id"]
        assert new_row["user_id"] == user_id

    def test_reusing_a_rotated_token_revokes_the_whole_family(self, db_conn):
        user_id = _user_id(db_conn)
        first = refresh_tokens.issue(db_conn, user_id)
        second = refresh_tokens.rotate(db_conn, first)
        assert second is not None
        third = refresh_tokens.rotate(db_conn, second[1])
        assert third is not None
        other_family = refresh_tokens.issue(db_conn, user_id)

        assert refresh_tokens.rotate(db_conn, first) is None
        family = _row(db_conn, first)["family_id"]
        assert all(r["revoked_at"] is not None for r in _rows(db_conn) if r["family_id"] == family)
        assert _row(db_conn, third[1])["revoked_at"] is not None
        assert refresh_tokens.rotate(db_conn, third[1]) is None
        assert _row(db_conn, other_family)["revoked_at"] is None

    def test_expired_token_returns_none_without_revoking_its_family(self, db_conn):
        user_id = _user_id(db_conn)
        old = refresh_tokens.issue(db_conn, user_id)
        current = refresh_tokens.rotate(db_conn, old)
        assert current is not None
        with db_conn.cursor() as cur:
            cur.execute(
                "UPDATE refresh_tokens SET expires_at = %s WHERE token_hash = %s",
                (utc_now() - timedelta(seconds=1), _sha256(current[1])),
            )
        assert refresh_tokens.rotate(db_conn, current[1]) is None
        assert _row(db_conn, current[1])["revoked_at"] is None
        assert len(_rows(db_conn)) == 2

    def test_unknown_token_returns_none(self, db_conn):
        refresh_tokens.issue(db_conn, _user_id(db_conn))
        assert refresh_tokens.rotate(db_conn, "not-a-token") is None
        assert len(_rows(db_conn)) == 1


class TestRevoke:
    def test_revoked_token_triggers_reuse_detection(self, db_conn):
        raw = refresh_tokens.issue(db_conn, _user_id(db_conn))
        refresh_tokens.revoke(db_conn, raw)
        assert _row(db_conn, raw)["revoked_at"] is not None
        assert refresh_tokens.rotate(db_conn, raw) is None
        assert len(_rows(db_conn)) == 1

    def test_revoke_keeps_the_first_revoked_at(self, db_conn):
        raw = refresh_tokens.issue(db_conn, _user_id(db_conn))
        refresh_tokens.revoke(db_conn, raw)
        first = _row(db_conn, raw)["revoked_at"]
        refresh_tokens.revoke(db_conn, raw)
        assert _row(db_conn, raw)["revoked_at"] == first

    def test_revoke_unknown_token_is_a_noop(self, db_conn):
        raw = refresh_tokens.issue(db_conn, _user_id(db_conn))
        refresh_tokens.revoke(db_conn, "not-a-token")
        assert _row(db_conn, raw)["revoked_at"] is None

    def test_revoke_user_revokes_every_family_of_that_user_only(self, db_conn):
        user_id = _user_id(db_conn)
        other_id = _user_id(db_conn, PRODUCCION_USERNAME)
        first = refresh_tokens.issue(db_conn, user_id)
        rotated = refresh_tokens.rotate(db_conn, first)
        assert rotated is not None
        second_family = refresh_tokens.issue(db_conn, user_id)
        other = refresh_tokens.issue(db_conn, other_id)

        refresh_tokens.revoke_user(db_conn, user_id)
        assert all(r["revoked_at"] is not None for r in _rows(db_conn) if r["user_id"] == user_id)
        assert _row(db_conn, rotated[1])["revoked_at"] is not None
        assert _row(db_conn, second_family)["revoked_at"] is not None
        assert _row(db_conn, other)["revoked_at"] is None
