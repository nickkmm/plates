VERDICT: CHANGES

## Summary
- The branch changes the nav (now `_nav.html` loops over `nav_items`), four template role checks (now `can(...)`), and adds one new page, `no_permissions.html`, for users with no permissions.
- The nav entries, labels, order and active logic match the old markup. For admin, point_of_sale and production the visible links are the same as before.
- The new 403 page has no heading, unlike almost every other page, and its one message is a small grey paragraph. That is the one visible inconsistency: fix it.
- I could not look at the logged-in pages: `agent-browser` is not installed in this environment, and the rules forbid signing in. The logged-in review is from the diff and the template markup. `/login` renders unchanged, and `/` and `/declaracion` behave as expected without a session.

## Findings
1. `app/web/templates/no_permissions.html:18-19` The page has no `<h1>`. 15 of the 19 templates open with one (`records.html` uses `1.4rem`, `h1 { font-size: 1.4rem; }`). Here the only content is `<p class="muted">` in `#777`, so the message reads like a footnote. Add `<h1>Sin permisos</h1>` with the same `h1` rule as `records.html` (or a `h1` style in this page's `<style>` block), and show the required sentence in the default `#222` ink, not the `.muted` grey. Keep the exact text `Tu usuario no tiene permisos asignados. Pide a un administrador que te asigne un rol.`

## Notes
- Not checked visually: the logged-in nav and the 403 page. The author's build notes say the same. Someone with an admin login should open `/` and a 403 page once in a browser before merge.
- `no_permissions.html:7` sets `body { max-width: 1000px }`. Most list pages use `1040px` (`records.html`, and others). Harmless, but matching the common value would be tidier.
- `.muted { color: #777 }` on white is about 4.5:1, just under the AA threshold for small text. It is the same grey `records.html` uses, so it is consistent with the app; I did not flag it. Darkening it app-wide would be a separate change.
- The nav has 8 links plus Salir for admin, the same count as before this branch, and it does not wrap at phone width. That overflow predates this branch and is out of scope here.
- `_nav.html` renders `class=""` on inactive links, as the old markup did. Harmless.
