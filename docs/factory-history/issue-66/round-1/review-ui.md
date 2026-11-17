VERDICT: CHANGES

## Summary
- The branch adds the `Producción` board and order detail, a shared `_production_badge.html` partial, and `prod-<status>` badges on the declarations list/detail and `/records/<id>`.
- The new screens reuse the declarations list pattern (tabs, pills, search, `data-label` cards under 700px) and the record_detail badge placement, so layout and rhythm match their neighbours.
- Two text-contrast problems in the new detail page: greyed annulled plate rows (about 2.9:1) and the white-on-green advance button (about 3.5:1).
- The annulled-plate marking differs from `record_detail.html`, which marks annulled plates with a `Sí` cell and no greyed text.
- I did not look at the rendered pages: `agent-browser` is not installed on this machine, and the screens sit behind a login I may not use. The review is from the templates, the inline CSS and the spec.

## Findings
1. `app/web/templates/production_detail.html:` `tr.annulled td { color:#999; }` (plates table): #999 on white is about 2.85:1, which fails WCAG AA for the plate, quantity and city text of annulled rows. Drop the greyed text and keep the `Sí` tag in the "Placa anulada" column, the way `record_detail.html` (line 245) marks it. If a dimmed look is still wanted, use `#777` at most, which is about 4.5:1.
2. `app/web/templates/production_detail.html:` `.advance { background:#1a9c5b; color:#fff; }`: white on #1a9c5b is about 3.5:1. The label is 1.05rem at weight 600, which is not "large text" under WCAG (that needs about 18.7px bold), so it fails AA. Use the green already in the palette for ready badges, `#1a7a45` (about 5.4:1 with white), or another green at or below that lightness.

## Notes
- `production.html` and `production_detail.html` use `#777` for `.muted` text (empty states, the intro paragraph, the "Sin cambios todavía." line). That is about 4.48:1, just under AA for small text. The neighbouring list screens use the same value, so this is a shared choice, not a regression from this branch.
- Colours are hard-coded hex throughout the new templates (`#0b6bcb`, `#1a9c5b`), while `record_detail.html` and `declaration.html` use `:root` variables such as `var(--blue)`. `declarations.html` is hard-coded too, so this matches the nearest neighbour; it is fine to leave as is.
- `production.html` sets `autofocus` on the search input. On a phone that opens the keyboard as soon as the board loads, hiding the cards. Consider removing it.
- `production.html` carries a `.tabs` rule that nothing uses (the page uses `.filters`). It is harmless, but it can be removed.
- `production_detail.html` puts an `h3` under the `h1` and inside each declaration's `fieldset` without an `h2`, so the heading levels skip. Using `h2` for the declaration cards would keep the outline clean.
- The badge partial's colours match the spec exactly, and the four `prod-*` pairs all pass AA (the lowest is `prod-in_progress`, which is above 5:1). Good.
- Not visually checked in a browser (see Summary). If `agent-browser` becomes available, check the board and the detail page at 1280px and 390px wide, and the `Estado en producción:` line on `/declaraciones/<id>`.
