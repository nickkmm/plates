"""Fixtures for app.web integration tests.

Safe to import app.web.app at module level here: pytest always imports the
parent tests/conftest.py (which sets SECRET_KEY/AUTH_USERS) before collecting
anything under tests/web/, so the module-level SECRET_KEY check in
app.web.app has already been satisfied by the time this file loads.
"""

from __future__ import annotations

import pytest

from app.web.app import app as flask_app


@pytest.fixture
def client():
    flask_app.config.update(TESTING=True)
    with flask_app.test_client() as c:
        yield c
