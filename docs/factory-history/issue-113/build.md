STATUS: READY

# Build: Move authentication, users and roles into app/auth

## Summary
- Login, the session check, `permission_required`, the users/roles admin screens, the permission catalog and the users/roles SQL now live in `app/auth/` (8 files moved with `git mv`, shown as renames).
- `app/auth/repository/` is a package (`users.py`, `roles.py`, explicit re-exports); `app/core/db` no longer exports users/roles, and callers use `repository.<fn>` / `service.<fn>` so monkeypatches reach the call.
- Strict mypy now covers `app.auth.service`, `app.auth.permissions`, `app.auth.repository(.*)`; docs and docstrings point at the new paths.
- `factory/checks.sh` passes (1844 tests, same as `origin/main`). Draft PR #192.
- Not done: the `CLAUDE.md` "Project" bullet and the `web.Dockerfile` comment. The agent may not edit CLAUDE.md, and the permission classifier blocked the `web.Dockerfile` edit, so a human needs to make these two one-line changes.

## Changes
- `app/auth/__init__.py` (new): a docstring only, giving the package's scope and dependency rule.
- `app/auth/routes.py` (was `app/web/blueprints/auth_routes.py`): imports `app.auth.decorators as auth`; docstring.
- `app/auth/admin_routes.py` (was `app/web/blueprints/users.py`): imports `repository, service`, `permission_required`, `ADMIN_ROLE`, `RoleRecord/UserRecord` from `app.auth` and `db` (for `get_conn`) from `app.shared`; `db.<user/role fn>` becomes `repository.<fn>` and `user_admin.` becomes `service.`; docstring.
- `app/auth/decorators.py` (was `app/web/auth.py`): imports `permissions, repository` from `app.auth` and `db` from `app.shared`; `repository.get_user_by_*` / `get_user_permissions`; docstrings, including the hash_password command.
- `app/auth/service.py` (was `app/core/user_admin.py`): `repository.<fn>`, imports from `app.auth`.
- `app/auth/permissions.py`, `app/auth/hash_password.py`, `app/auth/repository/users.py` and `roles.py`: docstrings, plus the `PERMISSION_CODES` import in `roles.py`.
- `app/auth/repository/__init__.py` (new): explicit re-export of the 22 names, with `__all__`.
- `app/core/db/__init__.py`: drops the users/roles imports, their `__all__` entries and the docstring entries, and adds one pointer line.
- `app/web/app.py`: imports from `app.auth.*` (registration order unchanged); docstring.
- `app/schema_migrate.py` and every blueprint in `app/web/blueprints/`: `from app.auth.decorators import ...`. `declarations.py` calls `repository.get_user_by_id`.
- `pyproject.toml`: the mypy strict override is extended and the coverage omit is now `app/auth/hash_password.py`.
- `tests/`: imports, `db.` → `repository.` for user/role calls, `user_admin` → `service`, monkeypatch targets, docstrings. No test is moved, removed or weakened.
- Docs: `docs/CORE.md`, `WEB.md`, `DATABASE.md`, `ARCHITECTURE.md`, `docs/README.md`, `README.md`, `.env.example`.

## Acceptance criteria
1. [x] New paths exist and old ones are gone: `git diff -M --stat` shows all 8 renames, and the old files are absent (the git mv commit).
2. [x] `.venv/Scripts/python -m app.auth.hash_password secret` prints `scrypt:32768:8:1$...` and exits 0.
3. [x] The old-import `rg` from the spec finds nothing.
4. [x] The old-path `rg` from the spec finds nothing. (The `web.Dockerfile` comment still says `auth_routes.py` but doesn't match the pattern; see note.)
5. [x] `rg "roles|users|UserRecord|RoleRecord" app/core/db/__init__.py` gives only line 25, "The users and roles tables are read and written by app/auth/repository/." The `hasattr` check exits 0.
6. [x] The `db.<user/role fn>` `rg` over `app tests` finds nothing (the conftest comment now says `repository.create_user()`).
7. [x] `test_role_admin.py:306 setattr(repository, "delete_role", ...)`, `:327/:338 setattr(service, "validate_role", ...)`; `admin_routes.py` calls `service.validate_role` (212, 242) and `repository.delete_role` (267).
8. [x] The import `rg` over `app/auth` gives only `decorators.py:47 from app.web.navigation import NAV_ITEMS`.
9. [x] `Blueprint("users"` is in `admin_routes.py:47` and `Blueprint("auth_routes"` in `routes.py:15`. The diff over templates, migrations and `init.sql` is empty (0 lines).
10. [x] `git diff -M --stat origin/main...HEAD` shows the 8 `=>` renames.
11. [x] `pyproject.toml` override module list includes `app.auth.service`, `app.auth.permissions`, `app.auth.repository`, `app.auth.repository.*` with `disallow_untyped_defs = true`; omit is `app/auth/hash_password.py`.
12. [x] `pytest --collect-only` gives 1844 on `origin/main` (before the move) and 1844 on the branch. `--diff-filter=D -- tests` is empty.
13. [ ] `rg app/auth` matches `docs/CORE.md`, `docs/WEB.md` and `docs/ARCHITECTURE.md`, and `python -m app.auth.hash_password` matches `.env.example` and `README.md`. **`CLAUDE.md` is not updated:** this agent is not allowed to modify CLAUDE.md, so the one-line `app/auth/` bullet is left for the human reviewer. Suggested text: "- `app/auth/`: login, session check and `permission_required`, users and roles admin, their repository (`app/auth/repository/`)."

Note (Requirement 10): the `web.Dockerfile` comment on line 21 still says "auth_routes.py/ingest.py/records.py". The edit was blocked by the permission classifier. It is a comment only, so the gate and criteria 3 and 4 are unaffected. Suggested text: "the app.logger.info calls in app/auth/routes.py and the ingest/records blueprints".

## Validation
- `bash factory/checks.sh` (with POSTGRES credentials and `TEST_POSTGRES_DB=plates_test_18`, `TEST_REQUIRE_DB=1`): exit 0. ruff and mypy are clean, and 1844 tests pass.
- `ruff check --select I --fix` only on the changed Python files (import blocks); `ruff check app/` passes.
- The spec's `rg` checks 3 to 9: as above.
- No browser check: nothing changed on any page, and the login and /usuarios flows are covered by the test suite.

## Log
### Round 1
- Set up `.venv` in the worktree and recorded the baseline test count (1844).
- Commit 1: the pure `git mv` of the 8 files. Commit 2: imports, calls through `repository.`/`service.`, the new `__init__` files, `app/core/db/__init__.py`, pyproject and the tests. Commit 3: docs and docstrings.
- Left CLAUDE.md alone (agents may not change it) and the `web.Dockerfile` comment (edit denied by the permission classifier). Both are flagged in the PR body for the reviewer.
- Pushed and opened draft PR https://github.com/nickkmm/plates/pull/192.
