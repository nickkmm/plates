STATUS: READY

# Build: Users admin screen at /usuarios

## Summary
- New admin-only `/usuarios` screen: list accounts, create one with roles and a password, edit roles and `Activo`, and reset a password.
- The validation rules live in `app/core/user_admin.py` (no Flask, never writes). They cover username format, password length and match, valid roles, no self-lockout, and at least one active admin.
- Notices use Flask `flash`. There is one log line per write and it never includes a password or hash. A `Usuarios` nav entry comes after `Clientes`, and `docs/WEB.md` documents the screen in Spanish.
- Round 2: the list stacks into labelled blocks under 700px, long usernames wrap, and the nav scrolls sideways on phones (UI review). The password form now shows the 10-character hint.
- The gate passes. Round 1 ran it with the test Postgres (1702 passed). Round 2 ran it without the DB and ran the 51 users tests against the DB. Draft PR #91.

## Changes
- `app/core/user_admin.py`: message constants, `validate_password`, `validate_new_user` and `validate_user_update`, checked in the order the spec gives.
- `app/web/blueprints/users.py`: the endpoints `users.users`, `users.user_new`, `users.user_edit` and `users.user_password`, all `@permission_required("users.manage")`. An unknown id returns 404. A validation error returns 400 and writes nothing. Each write is followed by `flash` and a redirect, plus a log line.
- `app/web/templates/users.html`, `app/web/templates/user_form.html`: the list, and the create/edit forms with the `Cambiar contraseña` form. Password inputs never get a `value`. In round 2, `users.html` got `table.list` with `data-label` cells and the `@media (max-width:700px)` stacked layout copied from `declarations.html`, plus `overflow-wrap:anywhere` on the username cell and `.tabs { overflow-x:auto; }`. `user_form.html` got the same nav rule, a wrapping `h1`, and the `Contraseña: al menos 10 caracteres.` hint in `Cambiar contraseña`.
- `app/web/app.py`: imports and registers the blueprint and updates the docstring route list.
- `app/web/navigation.py`: adds the `Usuarios` entry after `Clientes`.
- `docs/WEB.md`: four route rows, two template rows, and the "Administración de usuarios" bullet in `## Roles y permisos`.
- `tests/web/test_users.py`, `tests/core/test_user_admin.py`: route and core tests.

## Acceptance criteria
1. [x] Admin creates a user who can then log in and lands on `/ingest`: `TestCreate::test_admin_creates_a_user_who_can_log_in` passes. A manual curl run also gave 302 to `/usuarios` and the list showed `Usuario creado.`.
2. [x] Role change and deactivation apply on the user's next request: `TestEdit::test_role_change_and_deactivation_apply_on_the_next_request` passes (403 then 200 on `/ingest`, then a redirect to `/login`).
3. [x] Each validation message is returned with status 400. In `TestCreateValidation::test_rejected_with_400_and_nothing_written` the 7 parametrized cases are ab, "a b c", testuser, a 9-character password, mismatch, no role and superuser. `TestEditValidation` covers no_role, forged_role, self-deactivate, self admin removal and `test_last_active_admin`, which uses a `user_manager` role holding only `users.manage`; once a second admin exists the change succeeds. The core tests in `tests/core/test_user_admin.py` also check that the last-admin rule does not fire when another active admin exists.
4. [x] The database is unchanged after each failure: the tests compare `_snapshot(db_conn)` before and after. The create form keeps the username (`value="..."`), and the tests assert the password strings are absent from the body.
5. [x] 404 for `GET /usuarios/999999`, `POST /usuarios/999999` and `POST /usuarios/999999/password`: `TestNotFound::test_unknown_user_is_404` passes, and a curl GET returned 404.
6. [x] 403 for `produccion_client` and `punto_venta_client` on all 6 requests, with nothing changed: `test_non_admins_get_403_and_change_nothing` passes for both.
7. [x] Nav: `test_admin_sees_usuarios_after_clientes` and `test_non_admins_do_not_see_usuarios` pass.
8. [x] The list shows no hashes or internal role names, shows the labels, and shows `Sin roles`: `test_shows_labels_never_hashes_or_role_names` passes. In the manual curl run, grep for `point_of_sale|pbkdf2|scrypt` on `/usuarios` returned 0 matches.
9. [x] Holding two roles gives the union of their access: `TestEdit::test_two_roles_give_the_union` passes (`/ingest` and `/records` both return 200).
10. [x] Password reset redirects with `Contraseña actualizada.`, and the new password logs in while the old one fails: `TestPassword::test_reset_password` passes.
11. [x] Log lines: the create test asserts `user created: by=testuser user=cajero.nuevo`, with no password, no hash and no `password_hash`. The update and reset log lines are asserted too.
12. [x] `docs/WEB.md` has the four route rows and the `users.html` / `user_form.html` template rows. I checked by reading the diff.

## Validation
- `bash factory/checks.sh` without the DB credentials: passed (598 passed, the DB tests skipped).
- `bash factory/checks.sh` with `POSTGRES_USER/PASSWORD` from the main `.env`, `TEST_POSTGRES_DB=plates_test_008` and `TEST_REQUIRE_DB=1`: passed. ruff and mypy were clean, and pytest gave 1702 passed.
- Dev server on port 5180 against `plates_test_008`, driven with curl: login, create, list, a bad create (400), an unknown id (404) and the edit page all behaved correctly. `agent-browser` is not installed on this machine, so I checked the HTML with curl rather than in a browser. The server was stopped afterwards.

- Round 2: `bash factory/checks.sh` (no DB credentials): passed. ruff and mypy were clean, and pytest gave 598 passed and the DB tests skipped. The full gate with the DB takes about 15 minutes, so it was not rerun here.
- Round 2: `pytest tests/web/test_users.py tests/core/test_user_admin.py` with `TEST_POSTGRES_DB=plates_test_008 TEST_REQUIRE_DB=1`: 51 passed.
- Round 2: `agent-browser` is still not installed, so the 375px layout was not checked in a browser. The CSS is the same block that `declarations.html` uses.

## Log
### Round 1
The previous builder committed the validation module, the blueprint and templates, the nav entry, the tests and the docs, but stalled before pushing. This session:
- Reviewed the three commits against the spec.
- Ran the gate twice, the second time with the test database required.
- Checked the pages over HTTP on port 5180.
- Pushed the branch and opened draft PR #91.

I made no code changes. The PR body leaves out the "Generated with" line because `CLAUDE.md` and `AGENTS.md` forbid AI-attribution lines.

### Round 2
Gate: passed in round 1. Only review-ui asked for changes. Code, security and UX passed.
- UI finding 1 (the list table overflows at 375px, and long usernames don't wrap): fixed. The table is now `table.list` and every cell has a `data-label`. Under 700px it stacks into blocks, using the same CSS as `declarations.html`. The username cell has `overflow-wrap:anywhere`.
- UI finding 2 (the nine-tab nav runs off phone screens): fixed. Both templates add `.tabs { overflow-x:auto; }` under 700px, as `declarations.html` does.
- UI note (the password form lacks the 10-character hint): added `Contraseña: al menos 10 caracteres.` under the `Cambiar contraseña` fields. The edit page `h1` also wraps long usernames. I left the other notes (palette contrast, `a.new`) alone because they are app-wide or need no change.
- I committed `7d99b9d Fit the users screens on phone widths` and pushed it to draft PR #91.
