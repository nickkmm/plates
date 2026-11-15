VERDICT: PASS

## Summary
- Adds a used-plates photo for DETERIORO cards: migration 0014 plus init.sql, core validation, the public form (with JS show/require/clear), staff download, ZIP and gallery.
- Follows every spec requirement; it reuses `_save_photo`, `CARD_PHOTO_SIDES`, `_photo_entries` and the gallery loop, with no duplicated code.
- The upload is read and stored only when the normalized reason is DETERIORO, and the HTML never marks the field `required`, so the no-JS path works.
- Tests cover all acceptance criteria and existing fixtures were extended rather than weakened. Docs (DATABASE, WEB, CORE) are updated.
- Commits are short one-liners with nothing stray.

## Findings
None.

## Notes
- The used-plates field is placed after the reason radios rather than directly under the front/back photos. It is still after both photo fields and the build log explains the choice, so this is acceptable.
- In `public_declaration.py`, the docstring line wrap ("as a PDF. The files" on its own line) is slightly ragged. This is cosmetic.
