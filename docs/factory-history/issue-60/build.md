STATUS: READY

# Build: Used-plates photo on DETERIORO declaration cards

## Summary
- Every card on `/declaracion` asks for a required "Foto de la(s) placa(s) usada(s)", whatever its reason (feedback-1). The field spans the card's photo grid.
- Round 4 (feedback-2): each card has a "Perdí mis placas" checkbox. Only when it is checked can the card go without the used-plates photo. The browser then disables the photo input (not required, not sent), and the server never stores one for that card.
- The photo goes in `declaration_cards.used_plates_photo_path` (migration 0014 plus `init.sql`). The checkbox is kept as `plates_lost` in the card's `submitted_data`, with no new column.
- Staff review shows the "Placas usadas" figure, the `/photo/used_plates` route and the ZIP entry `tarjeta-N-PLACA-placas-usadas`. A card marked lost now shows "Sin foto de las placas usadas: el cliente marcó «Perdí mis placas»."
- `factory/checks.sh` passes with Postgres up: 1536 passed, 0 skipped. Draft PR #72 is updated.

## Changes
- `app/migrations/0014_declaration_used_plates_photo.sql`: `ADD COLUMN IF NOT EXISTS used_plates_photo_path TEXT`. Its comment says the photo is asked for on every card and the column is NULL on older declarations. No change in round 4.
- `init.sql`: the column after `card_back_path`, with a `-- 0014:` comment.
- `app/core/declarations.py`:
  - The module docstring mentions the photo and the "Perdí mis placas" exception.
  - `OwnershipCardInput` gains `used_plates_file` and `plates_lost: bool = False` (round 4).
  - `_validate_card` requires the photo unless `plates_lost`.
  - `_insert_card_row` passes the path.
  - `plates_lost` reaches `submitted_data` through the existing `asdict(card)`.
- `app/core/db/declarations.py`: the record field, the SELECT column, and the `insert_declaration_card` keyword and INSERT column.
- `app/web/blueprints/public_declaration.py`:
  - `_build_card` parses `card_<i>_plates_lost`.
  - `_save_files` saves `card_<i>_used_plates` only for cards not marked lost; on a lost card the upload is never read.
  - Docstrings updated.
- `app/web/templates/declaration.html`:
  - `photo_field(name, label, hint='', wide=False)`; every photo input is `required`.
  - The used-plates field spans the photo grid (`.photos .photo-wide`). Its hint now reads "Toma una foto donde se vean las placas que vas a entregar. Si las perdiste, marca «Perdí mis placas»."
  - A `card_<i>_plates_lost` checkbox labelled "Perdí mis placas", sticky after a rejected submission. It sits in the grid right under the photo, with a small `.plates-lost` spacing rule.
  - `applyState()` disables the photo input when the box is checked and clears its value and `.photo-info`. It re-enables the input when unchecked, and runs on `_plates_lost` changes too.
- `app/web/blueprints/declarations.py`:
  - `CARD_PHOTO_SIDES["used_plates"]` and the `_photo_entries` ZIP name.
  - `_render_detail` sets `plates_lost` per card from `submitted_data["cards"][position]`, only when the card has no photo (round 4).
- `app/web/templates/declaration_detail.html`: the gallery and summary include the photo, and a lost-plates note shows on cards marked lost (round 4).
- Docs (Spanish):
  - `docs/DATABASE.md`: the photo is required unless «Perdí mis placas»; `plates_lost` lives in `submitted_data`; the column is NULL in that case.
  - `docs/WEB.md`: the `/declaracion` row describes `card_<i>_plates_lost`.
  - `docs/CORE.md`: the `validate_declaration` row adds the exception.
- Round 4 tests:
  - Core: `plates_lost` waives the photo for both reasons.
  - Public parse-level: `plates_lost` is parsed per card, and a photo posted on a lost card is never stored.
  - End-to-end: a lost card is accepted without the photo, the column is NULL, and `submitted_data` has `plates_lost: true`.
  - Template: the checkbox is in every block and in the `<template>`, it is not required, it stays checked after a rejected submission, and the hint text is checked.
  - Staff: the detail shows the lost-plates note only for the card marked lost.

## Acceptance criteria
Updated for feedback-1 (photo on every card) and feedback-2 ("Perdí mis placas" is the only way to skip it).
1. [x] Migration 0014 is idempotent. `tests/test_schema_migrate.py` passes with Postgres up, including the equivalence and rerun tests and `test_0014_used_plates_photo_sql_is_safe_to_apply_twice_directly`.
2. [x] `test_a_card_stores_the_used_plates_photo[DETERIORO|PÉRDIDA]` (real DB): the stored file exists and holds the posted bytes. Passed.
3. [x] `test_a_card_without_the_used_plates_photo_is_rejected[DETERIORO|PÉRDIDA]`: 400, `Tarjeta 1: Falta la foto de la(s) placa(s) usada(s).` shown, no declaration row, and `UPLOADS_DIR` is empty. Passed.
4. [x] "Perdí mis placas" cases. Passed:
   - `test_a_plates_lost_card_is_accepted_without_the_used_plates_photo[DETERIORO|PÉRDIDA]`: 200, column NULL, and `submitted_data.cards[0].plates_lost` is true.
   - `test_a_used_plates_photo_on_a_plates_lost_card_is_never_stored`: no extra file is stored and `used_plates_file` stays None.
5. [x] Core validation. Passed:
   - `test_used_plates_photo_is_required_whatever_the_reason` (both reasons; None, "", spaces).
   - `test_a_loss_card_with_the_used_plates_photo_is_valid`.
   - `test_plates_lost_waives_the_used_plates_photo` (both reasons).
6. [x] Staff review. Passed:
   - The `used_plates` route returns 200 with the PNG bytes and `no-store`, or 404 when NULL.
   - The ZIP has `tarjeta-1-ABC123-placas-usadas.png`, and the page shows the caption `Tarjeta 1 · Placas usadas`.
   - A card whose only photo is the used-plates one still counts.
   - `test_the_detail_says_when_the_client_marked_plates_lost`: the note is shown only on the lost card's declaration.
7. [x] Template. Passed:
   - `test_used_plates_photo_is_required_in_every_card_block`: required in every block and in the `<template>`, and `photo-wide` is set.
   - `test_plates_lost_checkbox_in_every_card_block`: the checkbox is in each block and in the `<template>`, it is not required, it is sticky when checked, and the disabling script is present.
8. [x] Manual check. AGENTS.md "Checking a page" setup, port 5110, local Postgres; headless Edge over CDP, since `agent-browser` is not installed.
   - With "Pérdida" picked and a file chosen, checking "Perdí mis placas" clears the file and disables the input. The input is then valid, not among `:invalid`, and absent from `FormData`.
   - Unchecking re-enables it, and it is required again (invalid while empty).
   - A card added with "+ Agregar tarjeta de propiedad" behaves the same.
   - Phone-width screenshot (420px): the checkbox sits under the greyed-out photo input.
   - Round 2 and 3 checks (required for both reasons, grid layout at 1200px and 390px) still hold.
9. [x] `grep used_plates_photo_path docs/DATABASE.md` and `grep used_plates docs/WEB.md` both match. The CORE.md `validate_declaration` row mentions the photo and the «Perdí mis placas» exception.

## Validation
- Round 4: `bash <main>/factory/checks.sh` from the worktree, with `POSTGRES_PASSWORD` from the main `.env`: exit 0. ruff passed, mypy found no issues, pytest gave **1536 passed** (none skipped), and the shell and factory-script checks passed.
- Round 4 targeted run before the gate (declarations core, web, public and template tests): 430 passed.
- Headless Edge CDP run of criterion 8 (round 4): every check behaved as expected. The dev server on port 5110 and Edge were stopped by PID afterwards.

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

### Round 4
- **feedback-2.md** (nickkmm): "pon un campo ahí que diga perdí mis placas para marcar y solo así te deje seguir sin subir foto de las placas". Added a per-card "Perdí mis placas" checkbox (`card_<i>_plates_lost`):
  - Unchecked, the used-plates photo stays required for both reasons, in the browser and on the server.
  - Checked, the script disables and clears the photo input, so it is neither required nor sent.
  - The server skips the photo requirement (`OwnershipCardInput.plates_lost`) and never reads a used-plates upload on that card.
- The choice is recorded as `plates_lost` in `submitted_data`, so the migration stays as it is. The staff detail page shows "Sin foto de las placas usadas: el cliente marcó «Perdí mis placas»." on such a card.
- The hint now also says "Si las perdiste, marca «Perdí mis placas»."
- Round 3 reviews had no CHANGES verdicts to address.
- Without JS, the photo input keeps `required`, so checking the box would not unblock it. The form already needs JS for the signature pad, so this changes nothing in practice.
- Commit `c9ea8a4 Let a card skip the used-plates photo when the plates were lost` pushed. The PR #72 body was updated.
