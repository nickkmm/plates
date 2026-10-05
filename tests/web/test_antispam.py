"""Tests for app.web.antispam (no DB, no network)."""

from __future__ import annotations

import pytest
from flask import Flask, request

from app.web import antispam
from app.web.antispam import TokenCheck

SECRET = "unit-test-secret"


def _verify(token, now, **kw):
    kw.setdefault("min_age_seconds", 3)
    kw.setdefault("max_age_seconds", 7200)
    return antispam.verify_form_token(token, SECRET, now=lambda: now, **kw)


def test_token_ok():
    token = antispam.issue_form_token(SECRET, now=lambda: 1000.0)
    assert _verify(token, 1010.0) is TokenCheck.OK


def test_token_too_fast():
    token = antispam.issue_form_token(SECRET, now=lambda: 1000.0)
    assert _verify(token, 1001.0) is TokenCheck.TOO_FAST


def test_token_expired():
    token = antispam.issue_form_token(SECRET, now=lambda: 1000.0)
    assert _verify(token, 1000.0 + 7201) is TokenCheck.EXPIRED


def test_token_boundaries_inclusive():
    token = antispam.issue_form_token(SECRET, now=lambda: 1000.0)
    assert _verify(token, 1003.0) is TokenCheck.OK
    assert _verify(token, 1000.0 + 7200) is TokenCheck.OK


def test_token_tampered():
    token = antispam.issue_form_token(SECRET, now=lambda: 1000.0)
    assert _verify(token[:-2] + ("AA" if token[-2:] != "AA" else "BB"), 1010.0) is TokenCheck.INVALID


def test_token_wrong_secret():
    token = antispam.issue_form_token("other-secret", now=lambda: 1000.0)
    assert _verify(token, 1010.0) is TokenCheck.INVALID


@pytest.mark.parametrize("bad", [None, "", "garbage", "a.b.c"])
def test_token_missing_or_garbage(bad):
    assert _verify(bad, 1010.0) is TokenCheck.INVALID


def test_token_salt_isolated():
    from itsdangerous import URLSafeTimedSerializer

    foreign = URLSafeTimedSerializer(SECRET, salt="something-else").dumps({"iat": 1000.0})
    assert _verify(foreign, 1010.0) is TokenCheck.INVALID


def test_token_default_clock_works():
    token = antispam.issue_form_token(SECRET)
    assert antispam.verify_form_token(token, SECRET, min_age_seconds=0, max_age_seconds=60) is TokenCheck.OK


def test_honeypot():
    assert antispam.honeypot_triggered({"website": "http://spam"}) is True
    assert antispam.honeypot_triggered({"website": "   "}) is False
    assert antispam.honeypot_triggered({"website": ""}) is False
    assert antispam.honeypot_triggered({}) is False
    assert antispam.honeypot_triggered({"url": "x"}, field_name="url") is True


def test_client_ip_header_precedence():
    app = Flask(__name__)
    with app.test_request_context("/", headers={"X-Real-IP": "203.0.113.9"}, environ_base={"REMOTE_ADDR": "172.18.0.2"}):
        assert antispam.client_ip(request) == "203.0.113.9"
    with app.test_request_context("/", environ_base={"REMOTE_ADDR": "172.18.0.2"}):
        assert antispam.client_ip(request) == "172.18.0.2"
    with app.test_request_context("/", headers={"X-Real-IP": "  "}, environ_base={"REMOTE_ADDR": "10.0.0.1"}):
        assert antispam.client_ip(request) == "10.0.0.1"


def test_client_ip_unknown():
    class Req:
        headers: dict[str, str] = {}
        remote_addr = None

    assert antispam.client_ip(Req()) == "unknown"


def test_rate_limit_helper():
    assert antispam.is_rate_limited(4, 5) is False
    assert antispam.is_rate_limited(5, 5) is True
    assert antispam.is_rate_limited(9, 5) is True


@pytest.fixture
def clean_turnstile_env(monkeypatch):
    for var in ("TURNSTILE_BYPASS", "TURNSTILE_SECRET_KEY"):
        monkeypatch.delenv(var, raising=False)
    calls = []

    def boom(*a, **k):
        calls.append(a)
        raise AssertionError("network must not be called")

    monkeypatch.setattr(antispam, "_post", boom)
    return calls


@pytest.mark.parametrize("value", ["1", "true", "TRUE"])
def test_turnstile_bypass(clean_turnstile_env, monkeypatch, value):
    monkeypatch.setenv("TURNSTILE_BYPASS", value)
    assert antispam.verify_turnstile(None, None) is True


def test_turnstile_fails_closed_without_secret(clean_turnstile_env, caplog):
    with caplog.at_level("WARNING"):
        assert antispam.verify_turnstile("tok", "1.2.3.4") is False
    assert "TURNSTILE_SECRET_KEY" in caplog.text


def test_turnstile_missing_token(clean_turnstile_env, monkeypatch):
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "s")
    assert antispam.verify_turnstile("", "1.2.3.4") is False


def test_turnstile_success_sends_expected_fields(clean_turnstile_env, monkeypatch):
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "s3cret")
    seen = {}

    def fake_post(url, data, timeout):
        seen.update(url=url, data=data, timeout=timeout)
        return {"success": True}

    monkeypatch.setattr(antispam, "_post", fake_post)
    assert antispam.verify_turnstile("tok", "1.2.3.4") is True
    assert seen["url"] == antispam.TURNSTILE_VERIFY_URL
    assert seen["data"] == {"secret": "s3cret", "response": "tok", "remoteip": "1.2.3.4"}


def test_turnstile_omits_remoteip_when_none(clean_turnstile_env, monkeypatch):
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "s")
    seen = {}
    monkeypatch.setattr(antispam, "_post", lambda url, data, timeout: seen.update(data) or {"success": True})
    assert antispam.verify_turnstile("tok", None) is True
    assert "remoteip" not in seen


def test_turnstile_failure(clean_turnstile_env, monkeypatch):
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "s")
    monkeypatch.setattr(antispam, "_post", lambda *a, **k: {"success": False, "error-codes": ["x"]})
    assert antispam.verify_turnstile("tok", None) is False


def test_turnstile_network_error(clean_turnstile_env, monkeypatch):
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "s")

    def raising(*a, **k):
        raise OSError("down")

    monkeypatch.setattr(antispam, "_post", raising)
    assert antispam.verify_turnstile("tok", None) is False


def test_turnstile_bad_json(clean_turnstile_env, monkeypatch):
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "s")

    def raising(*a, **k):
        raise ValueError("bad json")

    monkeypatch.setattr(antispam, "_post", raising)
    assert antispam.verify_turnstile("tok", None) is False
