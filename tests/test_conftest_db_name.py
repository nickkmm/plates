"""Tests for tests/conftest.py's `_test_db_name`: the per-xdist-worker test
database name. Pure string logic, no Postgres needed."""

from __future__ import annotations

import pytest

from tests.conftest import _test_db_name


class TestTestDbName:
    def test_no_worker_keeps_the_default_base(self):
        assert _test_db_name("plates_test", None) == "plates_test"

    def test_no_worker_keeps_a_custom_base(self):
        assert _test_db_name("plates_test_12", None) == "plates_test_12"

    def test_worker_suffixes_the_default_base(self):
        assert _test_db_name("plates_test", "gw3") == "plates_test_gw3"

    def test_worker_suffixes_a_custom_base(self):
        assert _test_db_name("plates_test_12", "gw0") == "plates_test_12_gw0"

    @pytest.mark.parametrize("base", ["plates", "postgres", "plates_test-1", "plates_test;drop", ""])
    def test_invalid_base_raises(self, base):
        with pytest.raises(RuntimeError):
            _test_db_name(base, None)

    @pytest.mark.parametrize("base", ["plates", "plates_test-1"])
    def test_invalid_base_raises_with_a_worker(self, base):
        with pytest.raises(RuntimeError):
            _test_db_name(base, "gw0")

    @pytest.mark.parametrize("worker", ["gw-0", 'gw0"; DROP DATABASE plates; --', "gw 1", "gw.1"])
    def test_worker_that_breaks_the_guard_raises(self, worker):
        with pytest.raises(RuntimeError):
            _test_db_name("plates_test_12", worker)
