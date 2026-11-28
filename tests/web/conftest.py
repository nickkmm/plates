"""Fixtures for app.web integration tests.

Safe to import app.web.app at module level here: pytest always imports the
parent tests/conftest.py (which sets SECRET_KEY/AUTH_USERS) before collecting
anything under tests/web/, so the module-level SECRET_KEY check in
app.web.app has already been satisfied by the time this file loads.
"""

from __future__ import annotations

import pytest

from app.web.app import app as flask_app
from tests.web._helpers import log_in


@pytest.fixture
def client():
    """CSRF is off here (tests/web/test_csrf.py tests it on), and the earlier
    value is restored on teardown."""
    previous_csrf = flask_app.config["WTF_CSRF_ENABLED"]
    flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    try:
        with flask_app.test_client() as c:
            yield c
    finally:
        flask_app.config["WTF_CSRF_ENABLED"] = previous_csrf


@pytest.fixture
def authenticated_client(client, test_credentials):
    """A test client that has already logged in as the fixture test user.

    The token cookies set by the login POST persist on `client` for
    subsequent requests (test_client() preserves cookies across calls within
    the same `with` block), so routes behind @permission_required work directly.
    """
    username, password = test_credentials
    log_in(client, username, password)
    return client


@pytest.fixture
def produccion_client(client, produccion_credentials):
    """A test client logged in as the read-only 'production'-role test user
    (see tests/conftest.py's PRODUCCION_USERNAME/PRODUCCION_PASSWORD and
    AUTH_ROLES).

    Used to assert the default-deny role restrictions: full-access-only
    routes must 403 for this client while the small consult-only allowlist
    (/records, /records/<id>) still returns 200.
    """
    username, password = produccion_credentials
    log_in(client, username, password)
    return client


@pytest.fixture
def punto_venta_client(client, punto_venta_credentials):
    """A test client logged in as the new (Fase 2) 'point_of_sale'-role test
    user (see tests/conftest.py's PUNTO_VENTA_USERNAME/PUNTO_VENTA_PASSWORD)
    -- its seeded role_permissions give it the point-of-sale screens, narrower
    than admin but wider than the read-only 'production' role."""
    username, password = punto_venta_credentials
    log_in(client, username, password)
    return client
