ESCALATE

## Summary
- Adds the admin-only `/usuarios` screen (new `users` blueprint): list accounts, create one with roles and a password, edit roles and `Activo`, and reset a password.
- The validation rules live in a new pure `app/core/user_admin.py`: username format, password length and match, valid roles, no self-lockout, and at least one active admin.
- Round 1 had one UI finding (phone layout), fixed in round 2. The final round's gate passed (1702 tests with the DB) and all four reviews say PASS.
- No migration, env var, dependency or deploy config changes. The docs (`docs/WEB.md`) are updated.
- Escalated because the change manages logins, user roles and passwords. This project requires a person to check those.

## Why
Every spec criterion is met, with tests behind each one. The final round is clean and the diff stays inside intake's Scope. But the change lets an admin create accounts, grant any role (including `admin`), deactivate users and reset passwords, which falls under "login, sessions, user roles and route permissions". In this project a person has to check that before merging.

## Acceptance criteria
1. Admin creates a user who logs in and lands on `/ingest`: met (`TestCreate::test_admin_creates_a_user_who_can_log_in`, plus a manual curl run).
2. Role change and deactivation take effect on the next request: met (`TestEdit::test_role_change_and_deactivation_apply_on_the_next_request`).
3. Each validation message returns 400: met (parametrized create cases, `TestEditValidation`, and the core tests). I checked `test_last_active_admin` myself: it uses a `user_manager` role so no self rule fires first, it is rejected both for deactivation and for dropping `admin`, and the same change succeeds once a second admin exists.
4. The database is unchanged after a failure, the username is kept and the password is never echoed: met (`_snapshot` comparisons, body assertions).
5. Unknown id returns 404 on GET, POST and password POST: met (`TestNotFound`, and `_get_user_or_404` runs before any write).
6. Production and point_of_sale get 403 on all six requests and nothing changes: met (`test_non_admins_get_403_and_change_nothing`).
7. The nav shows `Usuarios` after `Clientes` for admin only: met (nav tests; `navigation.py` diff checked).
8. The list never shows a hash or an internal role name, and shows the labels and `Sin roles`: met. I read `test_shows_labels_never_hashes_or_role_names`, which asserts every user's `password_hash`, `point_of_sale` and `production` are absent from the page and the labels are present.
9. Two roles give the union of their access: met (`TestEdit::test_two_roles_give_the_union`).
10. Password reset redirects with the notice, the new password works and the old one fails: met (`TestPassword::test_reset_password`).
11. Log lines are emitted with no password or hash: met (caplog assertions in the create, update and reset tests).
12. `docs/WEB.md` has the four route rows and two template rows: met. I read the diff.

## For the human
Recommendation: merge

This is a well-tested change with no schema, env or deploy impact. Before merging, please check these three things:

1. **Who can grant `admin`.** Anyone holding `users.manage` can grant any role, including `admin`, and can reset any user's password, including another admin's. Today only `admin` holds `users.manage`, so this is fine for now. The roles-screen task must not hand `users.manage` to a non-admin role without revisiting this. Is that acceptable to you? (spec Assumptions, `app/web/blueprints/users.py`)
2. **Accepted risks the security review noted.** Please confirm you are fine with each:
   - There is no CSRF token. Only `SameSite=Lax` protects these forms, and these are now the most valuable forms in the app.
   - The last-active-admin check runs without row locks, so two admins demoting each other at the same instant could race.
   - A password reset doesn't end the user's existing sessions. Deactivation does take effect on the next request.
3. **Visual check.** No one saw the screens in a browser (`agent-browser` is not installed). Please open `/usuarios`, `/usuarios/nuevo` and `/usuarios/<id>` at about 375px and 1280px wide.

Files to look at: `app/core/user_admin.py`, `app/web/blueprints/users.py`, `app/web/templates/users.html`, `app/web/templates/user_form.html`, `app/web/navigation.py`, `tests/web/test_users.py`.
