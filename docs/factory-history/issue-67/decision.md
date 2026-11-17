ESCALATE

## Summary
- Adds a "Roles y permisos" section under `/usuarios`: list, create, edit and delete roles, all in the users blueprint behind `users.manage`. There is no schema change, migration, dependency or deploy change.
- All 13 spec criteria are met. Gate passed (1773 tests), and all four reviews (code, security, UI, UX) are PASS with no findings.
- I re-ran `tests/web/test_role_admin.py` and `tests/core/test_user_admin.py` against the job database: 70 passed.
- Escalated with "don't merge yet" because the change touches user roles and route permissions. Project rules require a person to check that before merging.

## Why
The work matches the spec and stays inside intake's Scope. Protected files (`navigation.py`, `app.py`, `permissions.py`, migrations) are untouched, and `docs/WEB.md` is updated. But the feature lets an admin change which permissions each role grants, which is exactly the "user roles and route permissions" area this project reserves for a human check. Also, no one has looked at the pages in a browser yet.

## Acceptance criteria
1. Create and assign `Bodega`: met. `TestCreateAndAssign` passes. It uses `/precios`, the real pricing route, because `/pricing` doesn't exist.
2. A permission removal applies on the next request: met. `TestPermissionChanges` checks both the `records.view` 403 and the `production.manage` removal.
3. Each rule returns 400 with its exact message and writes nothing: met. `TestValidation` is parametrized over empty, whitespace-only, 61-character, duplicate with different case and accents, no permission, and only unknown codes.
4. Unknown codes are ignored, a role can take its own label with other casing, and a 60-character label is stored trimmed: met. Covered by web tests and by the core `TestCleanPermissions` and `TestValidateRole`.
5. Admin and system-role protection: met. `TestSystemRoles` passes. I also read `role_edit`: an admin POST returns the read-only page with 400 before anything is written, and a system role keeps its stored label.
6. Delete: met. `TestDelete` covers a role held by an inactive user, an unused role, the FK race, and that the button shows only for custom roles.
7. List page, the users-page link, and 404s: met. `TestList` passes.
8. Non-admins get 403: met. I spot-checked `TestAccess` myself. GET and POST on every route return 403 for `produccion_client` and `punto_venta_client`, and a DB snapshot is unchanged. GET on the POST-only delete route is a 405, which is reasonable.
9. No internal names in visible text: met. `TestNoInternalNames` checks all six pages.
10. Logging: met. `TestLogging` passes, and the generated name matches `^custom_[0-9a-f]{8}$`.
11. Rule functions: met. Core tests pass.
12. Docs: met. `docs/WEB.md` has the route and template rows and the Spanish "Roles y permisos" text.
13. Protected files unchanged: met. The gate found no protected paths changed, and the diff stat confirms it.

## For the human
Recommendation: don't merge yet

The code looks correct and well tested. I'd merge once you have done these checks, which this project requires for any change to user roles and permissions:

1. **Permission model.** Once merged, any admin can edit what `Punto de venta` and `Producción` can do in production. Removing a permission takes effect on those users' next request. Confirm this is what you want. The security review also notes that giving `users.manage` to a custom role effectively makes it admin-level, because its holders could then assign the admin role. Consider saying so in `docs/WEB.md`.
2. **Locking down admin.** Check `role_edit` and `role_delete` in `app/web/blueprints/users.py` (bottom of the file). An admin POST must return 400 before any write, and system roles must never be deleted.
3. **Visual check.** No browser check was done, by the builder or the reviewers. On a local stack running this branch, look at `/usuarios`, `/usuarios/roles`, `/usuarios/roles/nuevo`, `/usuarios/roles/production` and `/usuarios/roles/admin` at about 1280px and 375px wide. Then try creating, editing and deleting a test role, including the delete confirm dialog.

Files to look at:
- `app/web/blueprints/users.py` (new role routes)
- `app/core/user_admin.py` (`validate_role`, `clean_permissions`, `validate_role_delete`)
- `app/web/templates/role_form.html` (the read-only admin page and the single-quoted `onsubmit` with `|tojson`)
- `app/web/templates/roles.html`, `app/web/templates/users.html`
- `docs/WEB.md`
- `tests/web/test_role_admin.py`
