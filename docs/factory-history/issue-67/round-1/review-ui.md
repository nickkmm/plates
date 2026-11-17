VERDICT: PASS

## Summary
- The branch adds two new pages (`roles.html`, `role_form.html`) and a toolbar link on `users.html`; all three reuse the existing users-screen structure, colours, radii and button classes.
- The role list reuses the users table, including the phone-width card layout (`table.list` with `data-label`), so it reflows like `/usuarios`.
- The admin read-only state, system-role disabled label and custom-role delete button render as the spec asks, with the delete confirm escaped through `|tojson`.
- Visual check was not possible: `agent-browser` is not installed on this machine and the pages sit behind a login I was not allowed to use. The review is from the templates and the existing styles, and the rendered pages were not screenshotted.

## Findings
None.

## Notes
- Visual check not done. `agent-browser` is not on PATH (`npx --no-install agent-browser` also fails), and I did not sign in. Before merge, a person should look at `/usuarios/roles`, `/usuarios/roles/nuevo`, `/usuarios/roles/production` and `/usuarios/roles/admin` at about 1280px and 375px wide.
- `.muted` text (`#777` on white, used for the role-list intro, "(del sistema)" tags and the system-role note) is about 4.5:1, right at the AA limit for text at 0.9rem. It matches the existing `users.html` and `user_form.html` pages, so I'd keep it for consistency rather than change it only here.
- The top "Usuarios" tab is not highlighted on the role pages. The spec accepts this because `app/web/navigation.py` is out of scope.
- The role pages carry unused `.tabs` CSS. It is copied from `user_form.html`, so the pattern matches, but it is dead CSS.
- The admin page renders the label and checkboxes as disabled inputs with no form, and the Volver link sits in the same spot as on the editable pages. That is consistent.
