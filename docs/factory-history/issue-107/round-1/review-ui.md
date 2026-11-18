VERDICT: PASS

## Summary
- The change adds one standalone Spanish 404 page (`not_found.html`) that copies the card layout and styles of `csrf_error.html`.
- Rendered at 1280 px and at 390 px: the card is centred, nothing overflows, and the "Volver al inicio" button and the error box fit the card.
- Text contrast is readable: white on the brand blue is about 5.3:1, and the dark red message on its pink box is about 8:1.
- Both back links (`/` and `/declaracion`) are as the spec asks; the signed-out `/declaracion/xyz` page renders the same card.
- No visual regressions found, so the verdict is PASS.

## Findings
None.

## Notes
- `agent-browser` is not installed on this machine, so I took the screenshots with Edge headless instead. The 390 px phone view was checked inside a 390 px iframe, because a bare headless window is wider than 390 px and crops the card.
- The page reuses the palette hard-coded in `csrf_error.html` (`--blue`, `--red`, `--ink`) rather than theme tokens. This matches the standalone-page convention in `docs/WEB.md`, so it is not a finding.
- The "not found" message sits in the red error box, as the CSRF page does for its expired-session message. That is consistent, but a neutral notice style would fit a plain 404 better. This is a matter of taste.
- Screenshots are in the reviewer's scratch folder only; nothing was committed.
