"""Request helpers shared by the tests/api modules."""

from __future__ import annotations

XHR = {"X-Requested-With": "XMLHttpRequest"}


def api_post(client, path, **kwargs):
    """POST with the X-Requested-With header every state-changing API call needs."""
    headers = {**XHR, **kwargs.pop("headers", {})}
    return client.post(path, headers=headers, **kwargs)


def api_login(client, username, password):
    return api_post(client, "/api/auth/login", json={"username": username, "password": password})
