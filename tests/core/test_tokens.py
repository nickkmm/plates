"""Tests for app/shared/tokens.py -- issuing and verifying JWT access tokens.
Pure: no database. JWT_SECRET is forced by tests/conftest.py."""

from __future__ import annotations

import base64
import json
import os
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.shared import tokens
from app.shared.tokens import (
    ACCESS_TOKEN_LIFETIME,
    ExpiredTokenError,
    InvalidTokenError,
    decode_access_token,
    issue_access_token,
)

_NOW = datetime(2026, 11, 27, 15, 30, 0)


def _payload(token: str) -> dict:
    return jwt.decode(token, options={"verify_signature": False})


def _sign(payload: dict, *, secret: str | None = None, algorithm: str = "HS256") -> str:
    return jwt.encode(payload, secret or os.environ["JWT_SECRET"], algorithm=algorithm)


def _valid_payload() -> dict:
    iat = int(_NOW.replace(tzinfo=UTC).timestamp())
    return {"sub": "7", "username": "ana", "perms": ["a"], "iat": iat, "exp": iat + 300}


class TestRoundTrip:
    def test_issue_then_decode(self):
        token = issue_access_token(7, "ana", ["records.view", "orders.capture", "records.view"], _NOW)
        claims = decode_access_token(token, _NOW)
        assert claims.user_id == 7
        assert claims.username == "ana"
        assert list(claims.perms) == ["orders.capture", "records.view"]
        assert claims.issued_at == _NOW
        assert claims.expires_at == _NOW + ACCESS_TOKEN_LIFETIME

    def test_payload_claims(self):
        payload = _payload(issue_access_token(7, "ana", ["b", "a", "b"], _NOW))
        assert payload["sub"] == "7"
        assert payload["perms"] == ["a", "b"]
        assert isinstance(payload["iat"], int) and isinstance(payload["exp"], int)
        assert payload["exp"] - payload["iat"] == 5 * 60

    def test_header_is_hs256(self):
        assert jwt.get_unverified_header(issue_access_token(1, "u", [], _NOW))["alg"] == "HS256"

    def test_aware_now_is_converted_to_utc(self):
        bogota = datetime(2026, 11, 27, 10, 30, 0, tzinfo=UTC).astimezone()
        token = issue_access_token(1, "u", [], bogota)
        claims = decode_access_token(token, datetime(2026, 11, 27, 10, 30, 0))
        assert claims.issued_at == datetime(2026, 11, 27, 10, 30, 0)


class TestExpiry:
    def test_one_second_before_exp_is_valid(self):
        token = issue_access_token(1, "u", [], _NOW)
        decode_access_token(token, _NOW + ACCESS_TOKEN_LIFETIME - timedelta(seconds=1))

    def test_at_exp_is_expired(self):
        token = issue_access_token(1, "u", [], _NOW)
        with pytest.raises(ExpiredTokenError):
            decode_access_token(token, _NOW + ACCESS_TOKEN_LIFETIME)

    def test_past_exp_is_expired(self):
        token = issue_access_token(1, "u", [], _NOW)
        with pytest.raises(ExpiredTokenError):
            decode_access_token(token, _NOW + timedelta(hours=1))

    def test_expiry_ignores_the_wall_clock(self):
        """A token issued long ago is still valid at a `now` inside its lifetime."""
        long_ago = datetime(2001, 1, 1)
        decode_access_token(issue_access_token(1, "u", [], long_ago), long_ago)


class TestInvalid:
    def test_other_secret(self):
        token = _sign(_valid_payload(), secret="another-secret-that-is-at-least-32-chars")
        with pytest.raises(InvalidTokenError):
            decode_access_token(token, _NOW)

    def test_tampered_payload(self):
        header, _payload_part, signature = issue_access_token(1, "u", ["a"], _NOW).split(".")
        forged = {**_valid_payload(), "sub": "1", "perms": ["users.manage"]}
        body = base64.urlsafe_b64encode(json.dumps(forged).encode()).rstrip(b"=").decode()
        with pytest.raises(InvalidTokenError):
            decode_access_token(f"{header}.{body}.{signature}", _NOW)

    def test_alg_none(self):
        token = jwt.encode(_valid_payload(), None, algorithm="none")
        with pytest.raises(InvalidTokenError):
            decode_access_token(token, _NOW)

    def test_hs512(self):
        with pytest.raises(InvalidTokenError):
            decode_access_token(_sign(_valid_payload(), algorithm="HS512"), _NOW)

    def test_malformed(self):
        with pytest.raises(InvalidTokenError):
            decode_access_token("not-a-jwt", _NOW)

    @pytest.mark.parametrize("claim", ["sub", "username", "perms", "iat", "exp"])
    def test_missing_claim(self, claim):
        payload = _valid_payload()
        del payload[claim]
        with pytest.raises(InvalidTokenError):
            decode_access_token(_sign(payload), _NOW)

    @pytest.mark.parametrize(
        ("claim", "value"),
        [("sub", 7), ("sub", "abc"), ("username", 3), ("perms", "a"), ("perms", [1])],
    )
    def test_ill_typed_claim(self, claim, value):
        payload = {**_valid_payload(), claim: value}
        with pytest.raises(InvalidTokenError):
            decode_access_token(_sign(payload), _NOW)

    def test_errors_share_the_base_class(self):
        assert issubclass(ExpiredTokenError, tokens.TokenError)
        assert issubclass(InvalidTokenError, tokens.TokenError)


class TestSecret:
    def test_missing_secret_raises_runtime_error(self, monkeypatch):
        token = issue_access_token(1, "u", [], _NOW)
        monkeypatch.delenv("JWT_SECRET")
        with pytest.raises(RuntimeError):
            issue_access_token(1, "u", [], _NOW)
        with pytest.raises(RuntimeError):
            decode_access_token(token, _NOW)

    def test_empty_secret_raises_runtime_error(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET", "")
        with pytest.raises(RuntimeError):
            issue_access_token(1, "u", [], _NOW)

    def test_secret_is_read_at_call_time(self, monkeypatch):
        token = issue_access_token(1, "u", [], _NOW)
        monkeypatch.setenv("JWT_SECRET", "a-different-secret-set-after-issuing-0123")
        with pytest.raises(InvalidTokenError):
            decode_access_token(token, _NOW)
