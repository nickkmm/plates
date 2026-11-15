ESCALATE

## Summary
- Adds the data layer for roles: a pure permission catalog, `roles`/`role_permissions` tables with seed rows, a new FK `users.role -> roles(name)`, and CRUD functions. No route, session or template changes.
- New production migration `0015_roles_and_permissions.sql` plus matching `init.sql`. Every push to `main` runs it against live `users` data.
- Gate PASS, code and security reviews PASS with no findings. The gate run skipped the 977 DB-backed tests, but I re-ran the full suite with Postgres up: 1572 passed, 0 skipped.
- The change stays inside intake's Scope and every acceptance criterion is met. A person still has to check it because it adds a migration and touches user roles and permissions.

## Why
The work matches the spec and the evidence holds up. I re-ran the migration, roles, users and permissions tests against the real test database myself: 79 passed, 0 skipped. The project rules still say a person must check any change that adds a database migration or touches user roles and permissions before merging, and this change does both: it adds a FK on `users.role` in production and creates the tables that will later decide access.

## Acceptance criteria
1. 0015 applied by the runner, rerun is a no-op: met. `test_rerun_applies_nothing_new`/`test_rerun_is_a_no_op` passed in my DB-backed run of `tests/test_schema_migrate.py`.
2. Schema invariant covers `roles`/`role_permissions`: met. Both are in `_APP_TABLES`, and `TestSchemaInvariant` passed in my run.
3. Seed matches requirement 2 exactly: met. I read the migration and `init.sql` seed rows (13 for admin, 6 for point_of_sale, 4 for production), and the exact-seed test passed.
4. `legacy_role` backfill test: met. It passed in my run. The migration inserts unknown roles before adding the FK, and its guard uses `conrelid = to_regclass('users')`.
5. `users_role_fkey` is `ON UPDATE CASCADE`: met. A test asserts the constraint definition, and it passed.
6. `tests/core/test_permissions.py`: met. It passed, and the code returns all of `PERMISSION_CODES` for admin and `stored & PERMISSION_CODES` for other roles.
7. `test_roles.py`/`test_users.py`: met. They passed with the DB reachable, and I read `roles.py`/`users.py`: every query is parameterized and nothing commits.
8. `TestTranslateRoleValuesToEnglish` and the `backfill_users` tests: met. They passed in my run.
9. No diff in `app/web`, `app/schema_migrate.py`, `tests/web`, `tests/conftest.py`: met. I checked `git diff origin/main --stat` on those paths and it is empty. I also ran the full suite with the DB up: 1572 passed, 0 skipped, which includes every `tests/web/` login and route test.
10. Re-exports, and `permissions.py` imports neither Flask nor the db: met. I read the file: it imports only `collections.abc`.
11. Docs: met. `docs/DATABASE.md` has both tables in the erDiagram and their column sections, and the `users.role` row now describes the FK. `docs/CORE.md` has the new sections. The prose is in Spanish.

## For the human
Recommendation: don't merge yet

The code is in good shape. The project rules still require a person to check two things before the merge deploys:

1. **Production migration (`app/migrations/0015_roles_and_permissions.sql`, `init.sql`).** It adds `users_role_fkey` on the live `users` table. First run `SELECT DISTINCT role FROM users;` on production. Any value other than `admin`/`point_of_sale`/`production` gets backfilled as a non-system role with no permissions. That is harmless today, but you should know about it before the permissions task starts reading these tables.
2. **The roles and permissions model (`app/core/permissions.py`, `app/core/db/roles.py`, `app/core/db/users.py`).** Confirm the seeded permission sets match what you intend each role to have once routes enforce them, in particular `point_of_sale` getting `production.view` and `production.deliver`. The security review left notes for the follow-up task, not for this PR:
   - the future screen must stop anyone from creating or renaming a role to `admin`, because `effective_permissions` trusts the role name;
   - `update_user`/`set_user_password` must sit behind `users.manage`;
   - a user who is deactivated or demoted should have their session ended, because `session["role"]` is stored at login;
   - user-list templates must not show the whole `UserRecord`, which includes `password_hash`.

One more point: the gate's `checks.sh` run skipped every DB-backed test because Postgres was unreachable. The builder and I both ran the full suite with the DB up, and all 1572 tests passed. Also note the builder's warning: the shared `plates_test` database now has 0015 applied, so other branches may see migration-test failures until this PR merges or the test DB is reset.
