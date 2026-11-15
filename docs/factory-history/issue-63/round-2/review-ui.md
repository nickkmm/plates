VERDICT: PASS

## Summary
- Round 1's one finding (no heading on `no_permissions.html`, message in `.muted` grey) is fixed: the page now has `<h1>Sin permisos</h1>` at 1.4rem, the message is a plain paragraph in `#222`, and the body width is 1040px. It matches `records.html` and the other list pages.
- The nav is now generated from `NAV_ITEMS`. Its markup, labels, order and `on` classes are unchanged from the old hand-written links, and the `<style>` and logout form are untouched.
- The `can(...)` template swaps (`_orders_table.html`, `records.html`, `ingest.html`, `declaration_detail.html`, `record_detail.html`) only change which block shows. They add no new markup or classes.
- No new hard-coded colours or one-off CSS in the branch. The only new template is `no_permissions.html`, which reuses the page-level pattern of the other pages.
- I could not look at the pages in a browser. `agent-browser` is not installed here, and the rules forbid signing in. The logged-in review is from the diff and the markup; `/login`, `/` (302 to `/login`) and `/declaracion` (200) behave as expected without a session.

## Findings
None.

## Notes
- Not checked visually: the logged-in nav, the Registros-only nav for `production`, and the 403 page for a user with no permissions. Someone with an admin login should open `/` and one no-permission account once in a browser before merge.
- The nav has 8 links plus Salir for admin, as before this branch. It does not wrap at phone width. That overflow predates this branch and is out of scope here.
- `.muted` (`#777`) on white is about 4.5:1, just under AA for small text. It is the app-wide grey, so I did not flag it again.
- `_nav.html` still renders `class=""` on inactive links, as the old markup did. Harmless.
