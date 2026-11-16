ESCALATE

## Summary
- Built #88 as specified: the new `user_roles` table (migration `0018`) replaces `users.role`, permissions are the union over a user's roles, and the session no longer stores the role.
- Round 1 gate passed on 309162a (ruff, mypy, 1651 tests). All four reviews (code, security, UI, UX) are PASS with no findings.
- Every spec acceptance criterion has evidence. I re-checked the grep, the `ROLE_*` constants, the migration diff and `set_user_roles` myself.
- Escalated because the change adds a production migration that drops a column and touches login, sessions and user roles. It also adds a new `init.sql` convention (Assumption 1) that a person must approve.

## Why
The work matches the spec and the scope, and the gate and reviews are clean. Still, it adds a database migration that drops `users.role` and runs on deploy, it changes `init.sql`, and it rewrites the login and session path and how roles are stored. This project's rules require a person to check all of those before merging. The spec's Assumption 1, which records every migration version in `init.sql`, is a new repo-wide convention, and the spec itself asks for a human to confirm it.

## Acceptance criteria
1. 0018 exists and its migration test passes: met. The migration is guarded by `information_schema.columns` on `current_schema()`. build.md reports `TestUserRoles::test_0018_backfills_user_roles_and_drops_users_role` passing, and the gate passed the full suite.
2. Schema invariant test passes with `user_roles` in `_APP_TABLES`: met (full suite green in checks.log).
3. A brand-new test DB works: met per build.md (fresh `plates_test_7_fresh`: 59 + 130 passed, including the fresh-init.sql test).
4. Only `0018_user_roles.sql` changes under `app/migrations/`: met. I checked: the diffstat shows only that file.
5. Pre-existing route-access tests pass with `test_roles.py` unchanged: met (diffstat has no `tests/web/test_roles.py`, and the suite is green).
6. Web tests (a) to (d) exist in `tests/web/test_permissions.py` and pass: met per build.md and the gate.
7. Access-unchanged test passes for the three seeded users: met per build.md and the gate.
8. The grep hits only migration comments and historical fixtures or docstrings: met. I checked: outside `app/migrations/`, the only hits are docstrings, comments and the pre-0018 fixture in `tests/test_schema_migrate.py`.
9. `ROLES`, `ROLE_PRODUCCION` and `ROLE_PUNTO_VENTA` are gone, and `ROLE_ADMIN` is kept in both files: met. I checked: only `AUTH_ROLES` text remains in `app/web/auth.py`, and `ROLE_ADMIN` is in `auth.py` and `schema_migrate.py`.
10. The login session has no `role`, and the log line is `login: user=testuser roles=admin`: met (`tests/web/test_login.py`, suite green).
11. The core permission, user and role tests pass: met (59 passed, and the suite is green).
12. The new functions import from `app.core.db`: met per build.md, and the `__init__.py` diff adds them.
13. The docs contain the new content (`DATABASE.md`, `WEB.md`, `CORE.md`, `AGENTS.md`): met per build.md and the diffstat.

## For the human
Recommendation: don't merge yet

The work looks correct and complete. The gate is green, all four reviews pass with no findings, and every acceptance criterion is met. Merge it once you have made these checks:

1. **Production migration (`app/migrations/0018_user_roles.sql`).** The next push to `main` runs it in production. It copies each `users.role` into `user_roles`, then drops `users.role`. The column can't be restored without a backup. Take a database backup before deploying. After deploy, check `SELECT u.username, ur.role FROM users u LEFT JOIN user_roles ur ON ur.user_id = u.id` and confirm every user has exactly their old role.
2. **New `init.sql` convention (spec Assumption 1, `init.sql` near `CREATE TABLE schema_migrations`).** `init.sql` now inserts all 18 migration filenames into `schema_migrations`, so a fresh database never replays 0004 and 0015 (both need `users.role`). From now on, every new migration must also add its row there. `AGENTS.md` and `docs/DATABASE.md` document the rule. Confirm you accept this convention. The only alternative the spec saw was guarding 0004 and 0015, which the issue forbids.
3. **Login, sessions and roles (`app/web/auth.py`, `app/web/blueprints/auth_routes.py`, `app/core/db/users.py`, `app/core/permissions.py`).** The session drops `role`, and old cookies that still carry it are accepted and the value is ignored. Permissions are now the union over all roles, and `admin` in any role grants everything. The security review notes that the future admin screens (#65, #67) that call `assign_role` or `set_user_roles` must require admin, must block removing the last active admin, and must not let anyone create or rename a role to `admin`.
4. **Deploy.** After deploying, log in once as each role (admin, production, point_of_sale) and confirm the nav and access match what they were before.

Cosmetic only: two docstrings have awkward line wraps (`backfill_users` in `app/schema_migrate.py`, `permission_required` in `app/web/auth.py`).
