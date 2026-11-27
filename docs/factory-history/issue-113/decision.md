ESCALATE

## Summary
- Issue #113 is a refactor with no behaviour change. Login, the session check, `permission_required`, the users and roles admin screens, the permission catalog and the users/roles SQL moved into `app/auth/`. The 8 files moved with `git mv`, and git shows them as renames.
- Round 1 passed the gate: `GATE: PASS` with 1844 tests (the same count as `origin/main`), and both reviews are `VERDICT: PASS`.
- Spec criterion 13 is not fully met. `CLAUDE.md` has no `app/auth/` bullet, because agents may not edit that file. A comment in `web.Dockerfile` (line 21) still says `auth_routes.py`, because the edit was blocked.
- Login, sessions and route permissions are touched, so a person checks the PR before it merges.

## Why
I checked the code and it does what the spec asks. The decorators, session check and monkeypatch targets keep their behaviour, and the checks are green. Two things still need a person: one acceptance criterion (the `CLAUDE.md` bullet) is not met, and the project rules ask for a human check on any change to login, sessions and route permissions. No migration, `init.sql`, env var, `docker-compose.yml`, CI or dependency file changed.

## Acceptance criteria
1. New paths exist and the old ones are gone: met. git shows the 8 renames.
2. `python -m app.auth.hash_password secret` prints a werkzeug hash: met. I ran it and got `scrypt:32768:8:1$...`.
3. The `rg` for old import paths finds nothing: met. I ran it and it returned nothing.
4. The `rg` for old file paths finds nothing: met for the pattern (build.md and the code review). The `web.Dockerfile` comment says `auth_routes.py` without a directory, so the pattern doesn't catch it, but the comment is stale.
5. `app/core/db/__init__.py` no longer re-exports users and roles: met. I ran the `hasattr` check and it passed.
6. No `db.<user/role fn>` calls remain: met (build.md, both reviews).
7. Monkeypatch targets match the functions the code calls: met. I checked that `test_role_admin.py:306/327/338` patches `repository.delete_role` and `service.validate_role`, and that `admin_routes.py:212/242/267` calls those same names.
8. `app/auth` imports only `app.web.navigation` from other app packages: met (build.md).
9. Blueprint names are unchanged, and templates, migrations and `init.sql` are not touched: met. I checked that the diff on migrations and `init.sql` is empty.
10. Renames, not deletes: met (`git diff -M --stat`).
11. mypy strict override and coverage omit: met (build.md and `pyproject.toml`).
12. Same test count, no test file deleted: met. The count is 1844 before and after.
13. The docs name `app/auth`: not met. `docs/CORE.md`, `docs/WEB.md`, `docs/ARCHITECTURE.md`, `.env.example` and `README.md` are updated, but `CLAUDE.md` has no `app/auth/` bullet.

## For the human
Recommendation: don't merge yet

Reasons:
- Criterion 13 is not met. Before merging, add one line to the "Project" bullets in `CLAUDE.md`, for example: "- `app/auth/`: login, session check and `permission_required`, users and roles admin, their repository (`app/auth/repository/`)."
- Line 21 of `web.Dockerfile` still says "auth_routes.py/ingest.py/records.py". It is only a comment, but changing `web.Dockerfile` is itself a change you should look at, so make the edit yourself or leave it as it is (suggested text: "app/auth/routes.py and the ingest/records blueprints").
- The change moves login, sessions and route permissions, so the project rules ask for a person to check it. Read these to confirm the only changes are imports and `repository.`/`service.` call prefixes:
  - `app/auth/decorators.py` (was `app/web/auth.py`)
  - `app/auth/admin_routes.py` (was `app/web/blueprints/users.py`)
  - `app/auth/routes.py`
  - `app/auth/service.py`
  - `app/auth/repository/__init__.py`
  - `app/web/blueprints/declarations.py` (the `get_user_by_id` call)
  - `app/schema_migrate.py` (the admin seed import)
- Once those two lines are in and the PR still deploys, it can merge: it adds no migration, env var or dependency.
