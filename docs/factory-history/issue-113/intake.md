DECISION: ACCEPT
RISK: medium
SURFACES: security
RESCOPED: no

## Summary
- Accepted as written: a pure move of the login, users, roles and permissions code into a new `app/auth/` package, with no behavior change.
- Its dependency #112 (`app/shared`) is merged on `main`, so this can start now.
- The change is mechanical but touches many files: every blueprint imports the decorator, and many tests import the moved modules or monkeypatch them.
- Rated medium risk and `security`: no logic should change, but the permission decorator and session check are moving, and a wrong import or monkeypatch target could weaken access control without anyone noticing.

## Scope
Build exactly the moves in the issue, following docs/MIGRATION_PLAN.md phase 1:
- `app/auth/` gets `routes.py` (from `app/web/blueprints/auth_routes.py`, blueprint `auth_routes`), `admin_routes.py` (from `app/web/blueprints/users.py`, blueprint `users`), `decorators.py` (from `app/web/auth.py`), `service.py` (from `app/core/user_admin.py`), `permissions.py` (from `app/core/permissions.py`), a repository (from `app/core/db/users.py` and `roles.py`, as one module or a `repository/` package), and `hash_password.py` (from `app/web/hash_password.py`).
- Use `git mv` so history follows the files. Every URL, `url_for` endpoint, template path, flash and log message stays exactly the same. Templates and `helpers.py`, `navigation.py` and `csrf.py` stay in `app/web/`.
- Remove the users/roles re-exports from `app/core/db/__init__.py` and point every caller (blueprints, core, `app/web/app.py`, tests) at the new modules. Monkeypatch targets in tests must follow the code to its new path, so that patched functions are still the ones being called.
- Extend the strict mypy override in `pyproject.toml` to the modules that came from `app/core/`, and update the coverage `omit` for `hash_password`.
- Update the `python -m app.auth.hash_password` command in `.env.example`, `README.md` and `docs/`, and the paths in `docs/CORE.md`, `docs/WEB.md`, `docs/ARCHITECTURE.md` and the module docstrings (including the `app/core/db/__init__.py` docstring).
- Leave `docs/factory-history/` alone. It is a historical record, so the acceptance-criterion `rg` check does not apply there.
- Left out, as the issue says: JWT or any change to auth behavior (phase 2), and moving any other domain.

## Why
The issue is a clear, fully specified step of the agreed migration plan, and its dependency #112 has landed (`app/shared/` exists on `main`). The files to move exist as listed, and the old paths are referenced across about 80 files: every blueprint in `app/web/blueprints/`, `app/web/app.py`, `app/core/db/__init__.py`, and many tests under `tests/core` and `tests/web`. That is a large but mechanical diff that fits in one pull request, like #112 did. It touches the code that enforces permissions, so it gets medium risk and the `security` surface, even though no logic should change.

## Questions
