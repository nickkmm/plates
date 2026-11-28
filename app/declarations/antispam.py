"""Anti-spam building blocks for the public (no login) declaration form.

DB-free and side-effect-free except `verify_turnstile` (one outbound HTTPS
call). The form blueprint composes these; the per-IP counter query lives with
the declarations DB layer and only uses `is_rate_limited` for the decision.
"""

from __future__ import annotations

import enum
import json
import logging
import os
import secrets
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

from itsdangerous import BadData, URLSafeTimedSerializer

logger = logging.getLogger(__name__)

FORM_TOKEN_SALT = "declaration-form-token"
DEFAULT_MIN_AGE_SECONDS = 3
DEFAULT_MAX_AGE_SECONDS = 2 * 60 * 60  # 2 h
TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
TURNSTILE_TIMEOUT_SECONDS = 5


class TokenCheck(enum.Enum):
    OK = "ok"
    TOO_FAST = "too_fast"  # submitted before min_age_seconds: bot-like
    EXPIRED = "expired"  # older than max_age_seconds
    INVALID = "invalid"  # missing, malformed, tampered or wrong secret


# Clock design: the issue time is stored *inside* the signed payload ("iat")
# and the age is computed against an injectable `now` callable (default
# time.time). We do not use itsdangerous's own max_age, because it cannot be
# faked in tests and cannot tell "too fast" apart from "expired".
def issue_form_token(secret_key: str, *, now: Callable[[], float] = time.time) -> str:
    payload = {"iat": now(), "n": secrets.token_hex(8)}
    return URLSafeTimedSerializer(secret_key, salt=FORM_TOKEN_SALT).dumps(payload)


def verify_form_token(
    token: str | None,
    secret_key: str,
    *,
    min_age_seconds: int = DEFAULT_MIN_AGE_SECONDS,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
    now: Callable[[], float] = time.time,
) -> TokenCheck:
    if not token or not isinstance(token, str):
        return TokenCheck.INVALID
    try:
        payload = URLSafeTimedSerializer(secret_key, salt=FORM_TOKEN_SALT).loads(token)
    except BadData:
        return TokenCheck.INVALID
    if not isinstance(payload, dict) or not isinstance(payload.get("iat"), int | float):
        return TokenCheck.INVALID
    age = now() - payload["iat"]
    if age < min_age_seconds:
        return TokenCheck.TOO_FAST
    if age > max_age_seconds:
        return TokenCheck.EXPIRED
    return TokenCheck.OK


def honeypot_triggered(form: Mapping[str, Any], field_name: str = "website") -> bool:
    """True if the hidden trap field carries any non-blank value."""
    value = form.get(field_name)
    return value is not None and str(value).strip() != ""


def client_ip(request: Any) -> str:
    """Best-effort client IP.

    Reads `X-Real-IP` first: the shared nginx in front of the app sets it,
    while `request.remote_addr` would be the nginx container. The header is
    trusted ONLY because the app container's port is bound to localhost and is
    otherwise reachable only through that nginx (over the `edge` network), so
    a client cannot reach the app directly and spoof it.
    """
    real_ip = (request.headers.get("X-Real-IP") or "").strip()
    if real_ip:
        return str(real_ip)
    return str(request.remote_addr or "unknown")


def is_rate_limited(recent_count: int, limit: int) -> bool:
    """Single definition of the per-IP rule: blocked once `limit` is reached."""
    return recent_count >= limit


def _post(url: str, data: dict[str, str], timeout: float) -> dict[str, Any]:
    """HTTP POST (form-encoded) returning parsed JSON. Monkeypatched in tests."""
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        result = json.loads(resp.read().decode())
    if not isinstance(result, dict):
        raise ValueError("unexpected Turnstile response")
    return result


def verify_turnstile(token: str | None, remote_ip: str | None) -> bool:
    """Server-side Cloudflare Turnstile check. Fails closed."""
    if os.environ.get("TURNSTILE_BYPASS", "").strip().lower() in ("1", "true"):
        return True  # dev/tests only
    secret = os.environ.get("TURNSTILE_SECRET_KEY", "").strip()
    if not secret:
        logger.warning("TURNSTILE_SECRET_KEY is not set and TURNSTILE_BYPASS is off: rejecting")
        return False
    if not token:
        return False
    data = {"secret": secret, "response": token}
    if remote_ip:
        data["remoteip"] = remote_ip
    try:
        result = _post(TURNSTILE_VERIFY_URL, data, TURNSTILE_TIMEOUT_SECONDS)
    except Exception:
        logger.warning("Turnstile verification request failed", exc_info=True)
        return False
    return result.get("success") is True
