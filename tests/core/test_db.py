"""Unit tests for app.core.db that don't need a real Postgres connection.

get_conn() reads DB_HOST/DB_PORT/POSTGRES_* straight from os.environ on every
call (see module docstring in app/core/db.py) and passes them through to
psycopg.connect(). These tests mock psycopg.connect to verify exactly which
arguments get_conn() builds, including its DB_HOST/DB_PORT fallback defaults
-- which tests/conftest.py always overrides for safety, so nothing else in
this suite exercises that fallback path.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.core import db


class TestGetConn:
    def test_passes_through_all_env_vars(self, monkeypatch):
        monkeypatch.setenv("DB_HOST", "myhost")
        monkeypatch.setenv("DB_PORT", "1234")
        monkeypatch.setenv("POSTGRES_DB", "mydb")
        monkeypatch.setenv("POSTGRES_USER", "myuser")
        monkeypatch.setenv("POSTGRES_PASSWORD", "mypassword")

        with patch("app.core.db.psycopg.connect") as mock_connect:
            db.get_conn()

        mock_connect.assert_called_once_with(
            host="myhost", port="1234", dbname="mydb",
            user="myuser", password="mypassword",
        )

    def test_host_defaults_to_db_when_unset(self, monkeypatch):
        monkeypatch.delenv("DB_HOST", raising=False)
        monkeypatch.setenv("POSTGRES_DB", "mydb")
        monkeypatch.setenv("POSTGRES_USER", "myuser")
        monkeypatch.setenv("POSTGRES_PASSWORD", "mypassword")

        with patch("app.core.db.psycopg.connect") as mock_connect:
            db.get_conn()

        assert mock_connect.call_args.kwargs["host"] == "db"

    def test_port_defaults_to_5432_when_unset(self, monkeypatch):
        monkeypatch.delenv("DB_PORT", raising=False)
        monkeypatch.setenv("POSTGRES_DB", "mydb")
        monkeypatch.setenv("POSTGRES_USER", "myuser")
        monkeypatch.setenv("POSTGRES_PASSWORD", "mypassword")

        with patch("app.core.db.psycopg.connect") as mock_connect:
            db.get_conn()

        assert mock_connect.call_args.kwargs["port"] == "5432"

    def test_missing_required_postgres_db_raises(self, monkeypatch):
        monkeypatch.delenv("POSTGRES_DB", raising=False)
        monkeypatch.setenv("POSTGRES_USER", "myuser")
        monkeypatch.setenv("POSTGRES_PASSWORD", "mypassword")

        with patch("app.core.db.psycopg.connect"), pytest.raises(KeyError):
            db.get_conn()
