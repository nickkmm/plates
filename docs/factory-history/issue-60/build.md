STATUS: READY

# Build: Used-plates photo on DETERIORO declaration cards

## Summary
- Round 2 applies feedback-1: the used plates are handed in both for deterioro and for pérdida. The "Foto de la(s) placa(s) usada(s)" photo is now required on every card, whatever its reason.
- On `/declaracion`, each card has a third required photo input, `card_<i>_used_plates`, placed next to the front/back card photos. The add-card `<template>` has it too. The round-1 JS show/hide logic is gone.
- The server always stores the photo in `declaration_cards.used_plates_photo_path` (migration 0014 plus `init.sql`; NULL only on older declarations). A card without it gets `Tarjeta N: Falta la foto de la(s) placa(s) usada(s).`.
- Staff review is unchanged from round 1: a "Placas usadas" figure, the `/photo/used_plates` route, and the ZIP entry `tarjeta-N-PLACA-placas-usadas`.
- Round 3: on desktop, the used-plates photo now spans both columns of the card's photo grid instead of sitting alone in the left column (UI review finding).
- `factory/checks.sh` passes with Postgres up: 1528 passed, 0 skipped. Draft PR #72 is updated.

## Changes
- `app/migrations/0014_declaration_used_plates_photo.sql`: `ADD COLUMN IF NOT EXISTS used_plates_photo_path TEXT`. Round 2 only reworded the header comment: the photo is asked for on every card, and the column is NULL only on older declarations.
- `init.sql`: the column after `card_back_path`, with a `-- 0014:` comment (reworded the same way).
- `app/core/declarations.py`:
  - The module docstring now says each card, whatever its reason, carries a used-plates photo.
  - `OwnershipCardInput.used_plates_file`.
  - `_validate_card` always requires the photo. The round-1 `DETERIORATION_REASON` constant was removed because it is no longer used.
  - `_insert_card_row` passes the path.
- `app/core/db/declarations.py`: the record field, the SELECT column, and the `insert_declaration_card` keyword and INSERT column.
- `app/web/blueprints/public_declaration.py`: `_save_files` saves `card_<i>_used_plates` for every card. Docstrings updated.
- `app/web/templates/declaration.html`:
  - `photo_field` gains optional `hint` and `wide` parameters; every photo input stays `required`. `wide=True` adds the `photo-wide` class to the label.
  - The used-plates field is the third item in each card's `.photos` grid and is called with `wide=True`.
  - Two styles are added: `.photo-hint`, and `.photos .photo-wide { grid-column:1 / -1; }` (round 3).
  - `applyState()` is back to its original code.
- `app/web/blueprints/declarations.py`: `CARD_PHOTO_SIDES["used_plates"]` and the `_photo_entries` ZIP name. Round 2 only edited a docstring here.
- `app/web/templates/declaration_detail.html`: gallery and summary as in round 1.
- Docs (Spanish):
  - `docs/DATABASE.md`: the photo is required on every card.
  - `docs/WEB.md`: the photo is required for deterioro and for pérdida.
  - `docs/CORE.md`: the `validate_declaration` row now requires the photo for `DETERIORO` and `PÉRDIDA`.
- Tests:
  - `tests/core/test_declarations.py`: the missing-photo error is checked for both reasons, and a loss card with the photo is valid.
  - `tests/web/test_public_declaration.py`: the photo is stored for both reasons (stub and real DB), a card of either reason without it is rejected with 400 and no files left, and file counts are updated.
  - `tests/web/test_declaration_template.py`: the input is `required` in every block and in the `<template>`. Round 3 adds that its label, and only that one in the block, carries `photo-wide`.
  - The schema and staff-review tests from round 1 are unchanged.

## Acceptance criteria
Criteria 3, 4, 7 and 8 of the spec are superseded by feedback-1 (the photo is required on every card). The evidence below is for the new rule.
1. [x] Migration 0014 is idempotent. `tests/test_schema_migrate.py` passes with Postgres up, including the equivalence, rerun and recorded-as-applied tests and `test_0014_used_plates_photo_sql_is_safe_to_apply_twice_directly`.
2. [x] `test_a_card_stores_the_used_plates_photo[DETERIORO|PÉRDIDA]` (real DB): the column names an existing file in `UPLOADS_DIR` that holds the posted bytes. Passed.
3. [x] `test_a_card_without_the_used_plates_photo_is_rejected[DETERIORO|PÉRDIDA]`: 400, the body contains `Tarjeta 1: Falta la foto de la(s) placa(s) usada(s).`, no declaration row, and `UPLOADS_DIR` is empty. Passed.
4. [x] PÉRDIDA cards (per the feedback): they need the photo too, and it is stored.
   - Covered by criteria 2 and 3.
   - `test_used_plates_photo_is_stored_whatever_the_reason` (stub).
   - `test_loss_declaration_is_accepted_without_a_file` now counts 5 jpg + 1 png.
5. [x] `test_used_plates_photo_is_required_whatever_the_reason` (both reasons; None, "", spaces) and `test_a_loss_card_with_the_used_plates_photo_is_valid`. Passed.
6. [x] Staff review tests from round 1 still pass: the `used_plates` route returns 200 with the PNG bytes and `no-store`, or 404 when NULL; the ZIP has `tarjeta-1-ABC123-placas-usadas.png`; the detail page shows `Tarjeta 1 · Placas usadas`; a card whose only photo is the used-plates one still counts.
7. [x] `test_used_plates_photo_is_required_in_every_card_block`:
   - `card_0_used_plates` (DETERIORO) and `card_2_used_plates` (PÉRDIDA) both carry `required`.
   - The label and hint appear in every block.
   - The `<template>` has `name="card___I___used_plates"`.
8. [x] Manual check. AGENTS.md "Checking a page" setup, port 5110, local Postgres; headless Edge driven over CDP, since `agent-browser` is not installed.
   - With JS off, the field is visible and `required`.
   - With JS on, the field is visible and required with no reason picked, with Deterioro and with Pérdida.
   - For both reasons, the empty input fails `checkValidity()` and clicking submit stays on the form with it among the `:invalid` controls.
   - A chosen file shows "✓ 1 KB" and is kept when the reason switches.
   - A card added with "+ Agregar tarjeta de propiedad" behaves the same.
   - Screenshot: the field sits under the front/back photos with its hint.
   - Round 3 layout, measured through CDP:
     - Desktop (1200px): front and back are side by side (281px each). The used-plates field sits on its own row spanning the full grid width (575px).
     - Phone (390px, mobile emulation): all three stack in one 293px column, and the page does not overflow horizontally.
     - Screenshots of both looked right.
9. [x] `grep used_plates_photo_path docs/DATABASE.md` and `grep used_plates docs/WEB.md` both match. The `validate_declaration` row in `docs/CORE.md` mentions the used-plates photo.

## Validation
- Round 3: `bash <main>/factory/checks.sh` from the worktree, with `POSTGRES_PASSWORD` from the main `.env`: exit 0. ruff passed, mypy found no issues, pytest gave **1528 passed** (none skipped), and the shell and factory-script checks passed.
- Round 3: `pytest tests/web/test_declaration_template.py`: 29 passed.
- Headless Edge CDP run of criterion 8: every check behaved as expected. The dev server and Edge were stopped by PID, and their temporary files were removed.

## Log
### Round 1
- Set up `.venv` and implemented the spec in 4 commits: schema, core, public form, staff review, docs and tests. The photo was required only for `DETERIORO`. A JS toggle showed it and made it required for Deterioro; for any other reason it hid and cleared it.
- The C: drive was nearly full: one test run failed with "No space left on device". After I freed my own scratch files, the gate passed (1527 passed). `agent-browser` is not installed, so the page was checked with headless Edge over CDP.
- Opened draft PR #72.

### Round 2
- **feedback-1.md** (nickkmm): "las placas viejas tienen que venir tanto en pérdida como por deterioro". This overrides the spec's DETERIORO-only rule. The used-plates photo is now required on every card:
  - Validation no longer checks the reason.
  - `_save_files` always stores the photo.
  - The input is a normal `required` photo in the card's photo grid; the JS toggle and the `used-plates-field` wrapper were removed.
  - The hint drops "Obligatoria si el motivo es deterioro:" and now reads "Toma una foto donde se vean las placas que vas a entregar.".
  - The migration and `init.sql` comments, the docstrings, the three docs and the tests were updated to match.
- The round-1 reviews had no findings. Their notes (field placement after the reason radios, the hint inside the label) no longer apply or need no change: the field now sits with the other card photos.
- The decision's request to run the DB-backed tests with Postgres up is done: the gate ran with Postgres, 1528 passed, 0 skipped.
- Migration 0014's SQL is unchanged; only its comment changed. The local dev database that already has 0014 applied needs nothing.
- One commit, `Require the used-plates photo on PÉRDIDA cards too`, pushed. The PR #72 body was updated.

### Round 3
- **review-ui.md (CHANGES), finding 1.** At 520px and up, the used-plates field, as the third item in the two-column `.photos` grid, sat alone in the left column. Fixed as the reviewer suggested:
  - `photo_field` gained a `wide` parameter that adds the `photo-wide` class.
  - New style: `.photos .photo-wide { grid-column:1 / -1; }`.
  - The used-plates call passes `wide=True`.
  - On a phone the grid is a single column, so nothing changes there.
  - A template test now checks the class.
- **Phone-width note (UI review).** Checked at 390px with CDP mobile emulation: no horizontal overflow, and the three photos stack in one column.
- **Not changed:** the truncated "No se ha seleccionado ningún archivo" text, which the reviewer called pre-existing and out of scope. The code, UX and security reviews were PASS with no findings.
- The gate passed with Postgres up (1528 passed). The dev server on port 5110 and headless Edge were stopped by PID.
- Commit `72ff694 Span the used-plates photo across the card photo grid` pushed.
