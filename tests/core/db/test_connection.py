"""Unit tests for app/shared/db.py's connection/pool logic --
_conninfo_kwargs() reads DB_HOST/DB_PORT/POSTGRES_* straight from
os.environ on every call (see the module docstring in
app/shared/db.py) and returns them as the kwargs get_conn()'s
connection pool connects with. These tests call _conninfo_kwargs()
directly to verify exactly which arguments it builds, including its
DB_HOST/DB_PORT fallback defaults -- which tests/conftest.py always
overrides for safety, so nothing else in this suite exercises that
fallback path. No real Postgres connection needed."""

from __future__ import annotations

import pytest
from psycopg.rows import dict_row

from app.core import db as core_db
from app.shared import db


class TestConninfoKwargs:
    def test_passes_through_all_env_vars(self, monkeypatch):
        monkeypatch.setenv("DB_HOST", "myhost")
        monkeypatch.setenv("DB_PORT", "1234")
        monkeypatch.setenv("POSTGRES_DB", "mydb")
        monkeypatch.setenv("POSTGRES_USER", "myuser")
        monkeypatch.setenv("POSTGRES_PASSWORD", "mypassword")

        assert db._conninfo_kwargs() == {
            "host": "myhost", "port": "1234", "dbname": "mydb",
            "user": "myuser", "password": "mypassword",
            "row_factory": dict_row,
        }

    def test_host_defaults_to_db_when_unset(self, monkeypatch):
        monkeypatch.delenv("DB_HOST", raising=False)
        monkeypatch.setenv("POSTGRES_DB", "mydb")
        monkeypatch.setenv("POSTGRES_USER", "myuser")
        monkeypatch.setenv("POSTGRES_PASSWORD", "mypassword")

        assert db._conninfo_kwargs()["host"] == "db"

    def test_port_defaults_to_5432_when_unset(self, monkeypatch):
        monkeypatch.delenv("DB_PORT", raising=False)
        monkeypatch.setenv("POSTGRES_DB", "mydb")
        monkeypatch.setenv("POSTGRES_USER", "myuser")
        monkeypatch.setenv("POSTGRES_PASSWORD", "mypassword")

        assert db._conninfo_kwargs()["port"] == "5432"

    def test_missing_required_postgres_db_raises(self, monkeypatch):
        monkeypatch.delenv("POSTGRES_DB", raising=False)
        monkeypatch.setenv("POSTGRES_USER", "myuser")
        monkeypatch.setenv("POSTGRES_PASSWORD", "mypassword")

        with pytest.raises(KeyError):
            db._conninfo_kwargs()


class TestGetConnReExport:
    def test_core_db_get_conn_is_shared_db_get_conn(self):
        # app.core.db re-exports the pool's get_conn rather than wrapping it,
        # so a patch or a call through either name reaches the same function.
        assert core_db.get_conn is db.get_conn
