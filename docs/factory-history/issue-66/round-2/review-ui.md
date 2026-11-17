VERDICT: CHANGES

## Summary
- Both round-1 contrast findings are fixed: the greyed annulled plate rows are gone (annulled plates are marked only by the `Sí` tag, as in `record_detail.html`), and the advance button is `#1a7a45` (about 5.4:1 with white).
- The `Producción` board and order detail reuse the declarations list pattern (pill filters, search, `data-label` cards under 700px), so layout and rhythm match their neighbours.
- One new visible problem: on the declaration detail, the `in_progress` badge uses the same background as the `attended` block it sits in, so its chip disappears.
- Not checked in a browser: `agent-browser` is not installed on this machine, and I did not sign in or start the dev server, because every page sits behind the login. This review is from the templates, the inline CSS and the spec.

## Findings
1. `app/web/templates/_production_badge.html:` `.badge.prod-in_progress` has background `#e6f0fb`, which is the same as the `.next.done` block in `app/web/templates/declaration_detail.html` (line 52, `background:#e6f0fb`). On the attended declaration with an order `En realización`, the badge has no visible chip and reads as bare blue text, unlike every other badge on the page. Fix: give the prod badges a visible edge (for example `box-shadow: inset 0 0 0 1px currentColor` or a 1px border in its own colour), or put the `Estado en producción:` line outside the `.next.done` box. Keep the colours from the spec.

## Notes
- Still open from round 1, not blocking: `production_detail.html` puts `h3.sub` under the `h1` and under each declaration's `fieldset` with no `h2`, so the heading levels skip. Use `h2` for the declaration sections to keep the outline clean.
- Still open from round 1, not blocking: `production.html` sets `autofocus` on the search input. On a phone this opens the keyboard as soon as the board loads and hides the cards. `declarations.html` does the same, so it is a shared choice; consider removing it on the new screen.
- `production.html` and `production_detail.html` carry a `.tabs` rule that nothing on those pages uses (the board uses `.filters`; the detail page has no tabs). Harmless, but it can be removed.
- `.muted` is `#777` on white (about 4.48:1), just under AA for small text. The neighbouring screens use the same value, so this is a shared choice, not a regression from this branch.
- Colours are hard-coded hex in the new templates, while `declaration.html` and `csrf_error.html` use `:root` variables. `declarations.html` is hard-coded too, so this matches the nearest neighbour.
- The plates table on the detail page scrolls sideways inside `.table-wrap` on phones instead of stacking. That keeps the page from overflowing, which is acceptable for an 8-column table.
- Not visually checked. If `agent-browser` becomes available, check `/produccion` and `/produccion/<id>` at 1280px and 390px wide, and the `Estado en producción:` line on `/declaraciones/<id>` for the finding above.
