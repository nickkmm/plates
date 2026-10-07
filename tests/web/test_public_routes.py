"""Pins "the only public routes are these": every URL rule of the app that is
not in the explicit allowlist must refuse an UNAUTHENTICATED request
(redirect to login, 401 or 403 -- never 200 or any other success).

If you add a public route on purpose, add it to PUBLIC_RULES here, in the
same PR, so the exposure is a reviewed decision."""

from __future__ import annotations

import re

from app.web.app import app as flask_app

PUBLIC_RULES = {"/login", "/logout", "/healthz", "/declaracion", "/static/<path:filename>"}
REFUSED = (302, 401, 403)


def _url_for_rule(rule: str) -> str:
    return re.sub(r"<[^>]+>", "1", rule)


def test_every_non_public_route_refuses_anonymous_requests(client):
    checked = 0
    for rule in flask_app.url_map.iter_rules():
        if rule.rule in PUBLIC_RULES:
            continue
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            response = client.open(_url_for_rule(rule.rule), method=method)
            assert response.status_code in REFUSED, (
                f"{method} {rule.rule} answered {response.status_code} to an anonymous request"
            )
            checked += 1
    assert checked > 10  # sanity: the loop really visited the app's routes


def test_public_allowlist_has_no_stale_entries():
    rules = {rule.rule for rule in flask_app.url_map.iter_rules()}
    assert PUBLIC_RULES - {"/static/<path:filename>"} <= rules
