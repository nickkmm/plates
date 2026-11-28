"""Fixtures for the JSON API tests (`/api`).

Safe to import app.web.app at module level: tests/conftest.py (which sets
SECRET_KEY/JWT_SECRET) is always imported first. CSRF is left ON here, to
prove the API blueprint is exempt from it.
"""

from __future__ import annotations

import pytest

from app.web.app import app as flask_app


@pytest.fixture
def client():
    """A test client with CSRF on and COOKIE_SECURE off; both settings are
    restored on teardown. Not held in a `with` block, so no app context
    (and no user cached on flask.g) leaks into another client's requests."""
    previous = {key: flask_app.config[key] for key in ("WTF_CSRF_ENABLED", "COOKIE_SECURE")}
    flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=True, COOKIE_SECURE=False)
    try:
        yield flask_app.test_client()
    finally:
        flask_app.config.update(previous)
