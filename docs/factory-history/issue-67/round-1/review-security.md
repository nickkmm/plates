VERDICT: PASS

## Summary
- Adds list, create, edit and delete for roles under `/usuarios/roles`, all in the users blueprint.
- All four routes use `@permission_required("users.manage")`, which only admin holds. Tests check that `produccion_client` and `punto_venta_client` get 403 and that nothing changes.
- `admin` can't be edited or deleted (a POST returns 400 and writes nothing), system roles can't be deleted, and the FK on `user_roles` stops a role from being deleted while a user still holds it.
- Every query in `app/core/db/roles.py` uses `%s` placeholders. The role name in the URL only goes into parameterized lookups and `url_for`.
- No new dependencies, secrets, migrations or data files are in the diff.

## Findings
None.

## Notes
- XSS: role labels are user-supplied. The pages render them with Jinja autoescape (h1, title, table cell, `value=` attribute). The delete confirm uses `{{ role.label|tojson }}` inside a single-quoted `onsubmit` attribute. Flask's `tojson` escapes `<`, `>`, `&` and `'` as `\uXXXX`, so a label can't break out of the attribute or the JS string. Keep that attribute single-quoted if the template is edited later.
- CSRF: both POST forms in `role_form.html` (save and delete) include the hidden `csrf_token` field.
- No lockout: `effective_permissions` always gives `admin` every code, and the admin role can't be edited. So removing `users.manage` from other roles can't lock everyone out of the screen.
- If an admin later adds `users.manage` to a custom role, its holders can manage roles and permissions too. That isn't an escalation, because `users.manage` already lets them give the admin role to any user through the existing user form. Still, the docs could say that `users.manage` is effectively admin-level.
- The log lines record only the acting username and the internal role name. They contain no customer personal data.
- The server only accepts permission codes that are in the `permissions` catalog (`clean_permissions`), and the data layer checks them again (`PERMISSION_CODES` plus the FK).
