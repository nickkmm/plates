VERDICT: PASS

## Summary
- Adds the "Roles y permisos" section: list, create, edit and delete routes in the users blueprint, behind `users.manage`, with the new `roles.html` and `role_form.html` templates.
- The pure rules (label, accent- and case-folded uniqueness, permission cleaning, delete guards, generated internal name) are in `app/core/user_admin.py` with the exact Spanish messages.
- Admin is read-only (400 on POST), other system roles keep their label, and the UniqueViolation and ForeignKeyViolation races map to 400 messages. PRG redirects, flash notices and log lines match the spec.
- Docs and docstrings are updated. Tests cover the rules and the routes. Protected files (`navigation.py`, `app.py`, `permissions.py`, migrations) are untouched.

## Findings
None.

## Notes
- The delete confirm uses `confirm("¿Eliminar el rol " + {{ label|tojson }} + "?")`. This is safe, and the page still contains the required `¿Eliminar el rol ` text.
- A failed edit re-renders with the submitted permission codes, including unknown ones. These never match a checkbox, so this is harmless.
