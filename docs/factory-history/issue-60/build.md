STATUS: READY

# Build: Used-plates photo on DETERIORO declaration cards

## Summary
- Each card on `/declaracion` now has a "Foto de la(s) placa(s) usada(s)" input. The script shows it and makes it required only when the card's reason is Deterioro. The HTML never marks it `required`, so a form submitted without JS still works.
- The server stores the upload only for `DETERIORO` cards, in the new nullable column `declaration_cards.used_plates_photo_path` (migration 0014 plus `init.sql`). Validation reports `Tarjeta N: Falta la foto de la(s) placa(s) usada(s).` when the photo is missing.
- On the review screen, staff see a "Placas usadas" figure in the card gallery. The photo is served at `/cards/<id>/photo/used_plates` and goes into the ZIP as `tarjeta-N-PLACA-placas-usadas`.
- `factory/checks.sh` passes with Postgres up (1527 tests passed). The form behaviour was checked in headless Edge.
- Draft PR: https://github.com/nickkmm/plates/pull/72

## Changes
- `app/migrations/0014_declaration_used_plates_photo.sql` (new): a header comment in the 0012 style, then `ALTER TABLE declaration_cards ADD COLUMN IF NOT EXISTS used_plates_photo_path TEXT;`.
- `init.sql`: adds `used_plates_photo_path TEXT` after `card_back_path`, with a `-- 0014:` comment.
- `app/core/declarations.py`:
  - Updates the module docstring.
  - Adds `DETERIORATION_REASON = "DETERIORO"` next to `LOSS_REASON`.
  - Adds `OwnershipCardInput.used_plates_file`.
  - `_validate_card` adds the missing-photo error only when the normalized reason is `DETERIORO`.
  - `_insert_card_row` passes `used_plates_photo_path`.
- `app/core/db/declarations.py`: adds `DeclarationCardRecord.used_plates_photo_path`, the column in `_CARD_SELECT_COLUMNS`, and a keyword plus INSERT column in `insert_declaration_card`.
- `app/web/blueprints/public_declaration.py`: `_save_files` saves `card_<i>_used_plates` (label `Tarjeta N (placas usadas)`) only when the normalized reason is `DETERIORO`. For any other reason the upload is never read. The module and function docstrings are updated.
- `app/web/templates/declaration.html`:
  - `photo_field` gains optional `required=True` and `hint=''` parameters, so the front/back/cédula inputs are unchanged.
  - Each card block gets a `<div class="used-plates-field">` wrapping a non-required `card_<i>_used_plates` input with its label and hint. The `<template>` gets it too, since both come from `card_block`.
  - A small `.photo-hint` style.
  - `applyState()` shows the field and sets `required` for `DETERIORO`. Otherwise it hides the field, removes `required`, and clears both the value and the `.photo-info` text.
- `app/web/blueprints/declarations.py`: adds `"used_plates"` to `CARD_PHOTO_SIDES`, so the existing route serves it with the same decorator, headers and 404 rules. `_photo_entries` adds `tarjeta-N-PLACA-placas-usadas`. Both docstrings are updated.
- `app/web/templates/declaration_detail.html`: the `photo_cards` summary and the per-card gallery condition include the new path, and the gallery has a third `('used_plates', 'Placas usadas', ...)` tuple.
- Docs (Spanish):
  - `docs/DATABASE.md`: a new column row.
  - `docs/WEB.md`: the `/declaracion` row and the download/ZIP row.
  - `docs/CORE.md`: the `validate_declaration` row; it now says "Una pérdida no exige ningún archivo adicional ...".
- Tests:
  - Fixture defaults now include the photo: `tests/core/test_declarations.py::_card` gets `used_plates_file`, `tests/web/test_public_declaration.py::_card` gets `used_plates`, and `tests/web/test_declarations.py::_with_photos` stores a PNG used-plates photo.
  - Existing assertions are adjusted: the file counts, the ZIP name list, the `type="file"` count of 3 per block, and the "no photos" UPDATEs, which now also NULL the new column.
  - New tests are listed under the acceptance criteria below.

## Acceptance criteria
1. [x] Migration 0014 exists and is idempotent:
   - `tests/test_schema_migrate.py` passes with Postgres up, including the equivalence, rerun and recorded-as-applied tests.
   - The new `test_0014_used_plates_photo_sql_is_safe_to_apply_twice_directly` runs the SQL twice, then asserts the column exists as `text`, nullable.
2. [x] `test_deterioration_card_stores_the_used_plates_photo` (real DB): the column is non-NULL, names a file in `UPLOADS_DIR`, and the file holds the posted bytes. Passed.
3. [x] `test_deterioration_card_without_the_used_plates_photo_is_rejected`: 400, the body contains `Tarjeta 1: Falta la foto de la(s) placa(s) usada(s).`, no declaration row, and `UPLOADS_DIR` is empty. Passed.
4. [x] The `PÉRDIDA` cases pass:
   - `test_loss_card_is_accepted_without_the_used_plates_photo`: 200, one declaration, column NULL.
   - `test_loss_card_never_stores_a_posted_used_plates_photo`: column NULL, and the file count with the photo equals the count without it (5).
   - Parse-level tests: `test_used_plates_photo_is_ignored_unless_the_reason_is_deterioration` (PÉRDIDA, an invalid reason, no reason) and `test_a_bad_used_plates_photo_is_rejected_and_everything_is_cleaned`.
5. [x] `test_used_plates_photo_is_required_for_deterioration` (None, "", spaces) and `test_used_plates_photo_is_not_required_otherwise` (PÉRDIDA, "perdida", an invalid reason). Passed.
6. [x] The staff-review tests pass:
   - `test_serves_the_used_plates_photo` (`punto_venta_client`): 200, `image/png`, the PNG bytes, `Cache-Control: no-store`.
   - `test_a_card_without_the_used_plates_photo_is_404`: 404.
   - `test_zip_has_every_photo_with_readable_names`: the ZIP includes `tarjeta-1-ABC123-placas-usadas.png`.
   - `test_the_detail_shows_the_used_plates_photo`: the page shows the photo URL and `Tarjeta 1 · Placas usadas`.
   - `test_a_card_with_only_the_used_plates_photo_counts_as_having_photos`: the summary link and ZIP button show, and "Sin archivos adjuntos" does not.
7. [x] `test_used_plates_photo_is_in_every_card_block_and_optional_without_js`:
   - The `card_0_used_plates` tag has no `required`; the `card_0/2_front/back` tags still do.
   - The `<template>` has `name="card___I___used_plates"`.
   - The label and hint appear in every block. Passed.
8. [x] Manual check. AGENTS.md "Checking a page" setup: the app ran from the worktree on port 5110 against the local Postgres. `agent-browser` is not installed here, so headless Edge was driven through CDP with a stdlib script.
   - Without JS, the field is visible and not required.
   - With no reason picked, the field is hidden.
   - With "Deterioro": the field and hint are visible and `required` is set. The empty input fails `checkValidity()`, and clicking submit keeps the page on the form with `card_0_used_plates` among the `:invalid` controls. Screenshot taken; the field sits right under the reason radios.
   - After choosing a file (shows "✓ 1 KB"), switching to "Pérdida" hides the field, `files.length` drops to 0, the info text is cleared, and `required` is removed.
   - A card added with "+ Agregar tarjeta de propiedad" behaves the same: hidden, then shown and required for Deterioro, then hidden and cleared for Pérdida. Card 0 is unaffected.
9. [x] `grep -n used_plates_photo_path docs/DATABASE.md` and `grep -n used_plates docs/WEB.md` both match. The `validate_declaration` row in `docs/CORE.md` mentions "la foto de la(s) placa(s) usada(s)".

## Validation
- `cd <worktree> && bash <main>/factory/checks.sh` with `POSTGRES_PASSWORD` from the main `.env` and `plates-db-1` up: exit 0. ruff passed, mypy found no issues, pytest gave **1527 passed**, and the shell and factory-script checks passed.
- `ruff check app/` passed after fixing one import-order finding (I001) in `public_declaration.py`.
- Headless Edge CDP run of criterion 8: every check behaved as expected (see above).

## Log
### Round 1
- Set up `.venv` and installed `requirements-dev.txt`, then implemented the spec: schema, core, public form, staff review, docs and tests, in 4 commits.
- Placement choice: the used-plates field sits right after the reason radios and the PÉRDIDA note, not directly under the front/back photos. It is still after the two card photo fields, as the spec asks. The script reveals it when Deterioro is picked, so it appears next to the control the user just touched instead of several fields above, out of view.
- Environment notes:
  - The C: drive is almost full (tens of MB free). One early test run failed with `OSError: No space left on device` while writing a pytest temp file. After I deleted my own scratch files, the full gate passed.
  - `agent-browser` and Node are not installed. Installing Playwright failed for lack of disk space, so the page check used Edge over CDP with a stdlib Python script.
  - The dev server (port 5110) and headless Edge were stopped by PID afterwards, and their temporary profile and uploads were deleted.
- Draft PR #72 opened against `main` (`Closes #60`).
