"""Tests for app.web.auth -- user store parsing, credential checks, and the
open-redirect guard (is_safe_next). These don't need a Flask app/request
context: load_users/verify_credentials only touch os.environ + werkzeug's
password hashing, and is_safe_next is pure string/urlparse logic.
"""

from __future__ import annotations

import pytest
from werkzeug.security import generate_password_hash

from app.web import auth

# --------------------------------------------------------------------------
# load_users
# --------------------------------------------------------------------------


class TestLoadUsers:
    def test_parses_multiple_users_splitting_on_first_colon_only(self, monkeypatch):
        """The hash itself contains ':' (pbkdf2:sha256:...) -- must split on
        the FIRST ':' only, keeping the rest of the hash intact.

        Uses monkeypatch (like every other test here) rather than mutating
        os.environ directly: a bare `del os.environ[...]` in a finally block
        discards whatever AUTH_USERS was set to by tests/conftest.py instead
        of restoring it, which is invisible under a single plain pytest
        invocation (file collection order happens to run test_app.py's
        login tests first) but corrupts later runs whenever the test suite
        executes more than once in the same process -- e.g. under mutmut,
        which reruns pytest in-process per mutant.
        """
        raw = "alice:pbkdf2:sha256:abc$def,bob:pbkdf2:sha256:xyz"
        monkeypatch.setenv("AUTH_USERS", raw)
        users = auth.load_users()
        assert users == {
            "alice": "pbkdf2:sha256:abc$def",
            "bob": "pbkdf2:sha256:xyz",
        }

    def test_ignores_blank_entries(self, monkeypatch):
        monkeypatch.setenv("AUTH_USERS", "alice:hash1,,   ,bob:hash2")
        users = auth.load_users()
        assert users == {"alice": "hash1", "bob": "hash2"}

    def test_ignores_malformed_entry_without_colon(self, monkeypatch):
        monkeypatch.setenv("AUTH_USERS", "alice:hash1,malformed,bob:hash2")
        users = auth.load_users()
        assert users == {"alice": "hash1", "bob": "hash2"}

    def test_ignores_entry_with_empty_username(self, monkeypatch):
        monkeypatch.setenv("AUTH_USERS", ":hash1,bob:hash2")
        users = auth.load_users()
        assert users == {"bob": "hash2"}

    def test_ignores_entry_with_empty_hash(self, monkeypatch):
        monkeypatch.setenv("AUTH_USERS", "alice:,bob:hash2")
        users = auth.load_users()
        assert users == {"bob": "hash2"}

    def test_unset_env_var_returns_empty_dict(self, monkeypatch):
        monkeypatch.delenv("AUTH_USERS", raising=False)
        assert auth.load_users() == {}

    def test_strips_whitespace_around_username_and_hash(self, monkeypatch):
        monkeypatch.setenv("AUTH_USERS", " alice : hash1 ")
        users = auth.load_users()
        assert users == {"alice": "hash1"}


# --------------------------------------------------------------------------
# verify_credentials
# --------------------------------------------------------------------------


class TestVerifyCredentials:
    @pytest.fixture(autouse=True)
    def _one_user(self, monkeypatch):
        pw_hash = generate_password_hash("correct-horse-battery-staple")
        monkeypatch.setenv("AUTH_USERS", f"alice:{pw_hash}")

    def test_correct_password_is_true(self):
        assert auth.verify_credentials("alice", "correct-horse-battery-staple") is True

    def test_wrong_password_is_false(self):
        assert auth.verify_credentials("alice", "wrong-password") is False

    def test_unknown_username_is_false(self):
        assert auth.verify_credentials("bob", "correct-horse-battery-staple") is False

    def test_empty_password_is_false(self):
        assert auth.verify_credentials("alice", "") is False


# --------------------------------------------------------------------------
# is_safe_next -- security-critical open-redirect guard
# --------------------------------------------------------------------------


class TestIsSafeNext:
    def test_none_is_unsafe(self):
        assert auth.is_safe_next(None) is False

    def test_empty_string_is_unsafe(self):
        assert auth.is_safe_next("") is False

    def test_relative_path_without_leading_slash_is_unsafe(self):
        assert auth.is_safe_next("relative") is False

    def test_protocol_relative_is_unsafe(self):
        assert auth.is_safe_next("//evil.com") is False

    def test_backslash_trick_is_unsafe(self):
        assert auth.is_safe_next("/\\evil.com") is False

    def test_absolute_url_is_unsafe(self):
        assert auth.is_safe_next("http://evil.com") is False

    def test_absolute_https_url_is_unsafe(self):
        assert auth.is_safe_next("https://evil.com") is False

    def test_simple_path_is_safe(self):
        assert auth.is_safe_next("/inventory") is True

    def test_path_with_query_string_is_safe(self):
        assert auth.is_safe_next("/records/5?x=1") is True

    def test_root_path_is_safe(self):
        assert auth.is_safe_next("/") is True
