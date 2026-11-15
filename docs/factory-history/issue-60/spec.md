# Used-plates photo on DETERIORO declaration cards

> Issue #60: Ask for a photo of the used plates on DETERIORO declaration cards

## Summary
- Every `DETERIORO` card on the public `/declaracion` form gets a third photo input, for the used plates. It is required for `DETERIORO` cards and never required for `PÉRDIDA` cards.
- The photo is stored in the new nullable column `declaration_cards.used_plates_photo_path` (migration 0014 plus `init.sql`). Staff can view it, download it and get it in the ZIP on the review screen.
- Everything reuses the existing card front/back photo path: `_save_photo`, `_validate_card`, `CARD_PHOTO_SIDES`, `_photo_entries` and the detail gallery.
- Existing test fixtures that build `DETERIORO` cards must gain the new photo, because validation now requires it.

## Context
- **Schema.** `init.sql` defines `declaration_cards` (around line 170, with `card_front_path` / `card_back_path` at ~188 tagged `-- 0012: ...`). Migrations live in `app/migrations/`, the last is `0013_declaration_data_policy.sql`, and `python -m app.schema_migrate` applies them. Use `app/migrations/0012_declaration_photos.sql` as the style model: a header comment, then `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`. Three tests in `tests/test_schema_migrate.py` must keep passing: `test_migrated_old_schema_matches_fresh_init_sql`, `test_rerun_applies_nothing_new` and `test_every_migration_file_is_recorded_as_applied`.
- **Core logic**, `app/core/declarations.py`:
  - The module docstring (lines 1-55) still says "Nothing is uploaded with a declaration other than the signature ... no card reason requires a file", which is already stale.
  - `OwnershipCardInput` (~line 185) has `front_file` / `back_file`.
  - `_validate_card` (~line 256) reports missing card photos. The `Tarjeta N: ` prefix is added by `validate_declaration`.
  - `LOSS_REASON = "PÉRDIDA"` is at ~line 105. Reasons are normalized with `normalize_choice(card.reason, DECLARATION_REASONS)`.
  - `_insert_card_row` (~line 537) passes `card_front_path=card.front_file, card_back_path=card.back_file`.
- **Data layer**, `app/core/db/declarations.py`: `DeclarationCardRecord` (~line 111) holds `card_front_path` / `card_back_path` with defaults, the card SELECT column string is at ~line 126, and `insert_declaration_card` is at ~line 168.
- **Public form**:
  - `app/web/blueprints/public_declaration.py::_save_files` (line 233) stores each card's `card_<i>_front` / `card_<i>_back` through `_save_photo(field, label, saved, errors)`.
  - In `app/web/templates/declaration.html`, the macro `photo_field` (line 110) hardcodes `required`, so the new input cannot reuse it unchanged.
  - The macro `card_block` (line 117) holds the `.photos` div (line 132) and the `_reason` radios (line 160).
  - The `<template id="cardTemplate">` (line 226) is rendered from the same `card_block` macro.
  - The script's `applyState()` (line 412) toggles the per-card `.loss-note` and is called on `_reason` changes (line 475) and from `renumber()`.
  - `shrinkPhoto` runs on any `.photo-input`.
- **Staff review**:
  - `app/web/blueprints/declarations.py` holds `CARD_PHOTO_SIDES` (line 129), `declaration_card_photo` (line 386), and `_photo_entries` (line 399), which feeds `declaration_photos_zip`.
  - In `app/web/templates/declaration_detail.html`, the `photo_cards` summary and the ZIP and "Sin archivos adjuntos" conditions are at lines 209-222, and the per-card gallery is at lines 252-262. Its condition is `card.card_front_path or card.card_back_path`, and it loops over `(side, label, path)` tuples with caption `Tarjeta N · <label>`.
- **Tests**:
  - `tests/core/test_declarations.py::_card` builds a `DETERIORO` card with front/back files. `tests/web/test_declarations.py` imports it, and has the `TestPhotos` class with `_with_photos` and the ZIP test at ~line 572.
  - `tests/web/test_public_declaration.py::_card` builds the multipart fields for a `DETERIORO` card (`reason`, `front`, `back`). It also has the `enabled` and `uploads` fixtures and `test_loss_declaration_is_accepted_without_a_file`.
  - `tests/web/test_declaration_template.py` renders the form and inspects tags (`render`, `tag`, `split`, `card_names`).
- **Docs**:
  - `docs/DATABASE.md` line 456 has the `declaration_cards` photo row.
  - `docs/WEB.md` line 21 has the `/declaracion` row and line 37 the file-download row.
  - `docs/CORE.md` line 451 describes `validate_declaration` and ends with "Una pérdida no exige ningún archivo."

## Requirements
**Schema**

1. Add `app/migrations/0014_declaration_used_plates_photo.sql`, numbered exactly 0014. It has a header comment in the style of 0012: the value is the stored name in the uploads volume, and it is NULL on older declarations and on `PÉRDIDA` cards. The file then runs `ALTER TABLE declaration_cards ADD COLUMN IF NOT EXISTS used_plates_photo_path TEXT;`.
2. `init.sql`: add `used_plates_photo_path TEXT` to `declaration_cards`, positioned so a fresh database matches a migrated one (after `card_back_path`, with a `-- 0014: ...` comment like the 0012 ones).

**Core**

3. `OwnershipCardInput.used_plates_file: str | None = None`.
4. `DeclarationCardRecord.used_plates_photo_path: str | None = None`. Add the column to the card SELECT column list. Add a keyword-only `used_plates_photo_path: str | None = None` to `insert_declaration_card` and include it in the INSERT. `_insert_card_row` passes `used_plates_photo_path=card.used_plates_file`.
5. `_validate_card`: when `normalize_choice(card.reason, DECLARATION_REASONS) == "DETERIORO"` and `card.used_plates_file` is blank, append `Falta la foto de la(s) placa(s) usada(s).`. Use a module constant next to `LOSS_REASON` for `"DETERIORO"`. A `PÉRDIDA` card, or a card with an invalid reason, never gets this error.
6. Update the module docstring of `app/core/declarations.py`. It must say that the declaration carries the signature, the cédula photos, each card's front/back photos, and, for a `DETERIORO` card, a photo of the used plates. `PÉRDIDA` still needs no extra file because the notarization happens offline.

**Public form**

7. `_save_files`: for each card, compute the normalized reason. Only when it is `DETERIORO`, set `card.used_plates_file = _save_photo(f"card_{i}_used_plates", f"Tarjeta {number} (placas usadas)", saved, errors)`. For any other reason, never read or store that upload, so `used_plates_file` stays None. Update the docstring of `_save_files`.
8. `declaration.html`, `card_block`: after the two card photo fields, add a file input named `card_<i>_used_plates`. It has the label `Foto de la(s) placa(s) usada(s)`, class `photo-input`, `accept="image/jpeg,image/png,image/webp"`, the `<span class="photo-info">` sibling, and a visible hint `Obligatoria si el motivo es deterioro: toma una foto donde se vean las placas que vas a entregar.`. Wrap it in an element the script can find per card, for example a class `used-plates-field`. It must **not** carry `required` in the HTML. Either give `photo_field` an optional `required=True` parameter or write the markup inline. The existing front/back/cédula inputs must stay `required`.
9. Because the `<template id="cardTemplate">` is rendered from `card_block`, it must contain `card___I___used_plates`.
10. Extend the script's `applyState()` per card. When the card's checked reason is `DETERIORO`, show the used-plates field and set `required` on its input. Otherwise, including when no reason is checked, hide the field, remove `required`, clear the input's value and clear its `.photo-info` text. Without JS the field is visible and optional.
11. `MAX_REQUEST_BYTES` and the photo size limits are unchanged.

**Staff review**

12. `CARD_PHOTO_SIDES` gains `"used_plates": "used_plates_photo_path"`. `GET /declaraciones/<id>/cards/<card_id>/photo/used_plates` then serves it with the same decorator, headers and 404 rules. Update the route docstring.
13. `_photo_entries`: after a card's front/back entries, add `tarjeta-<N>-<PLACA>-placas-usadas` when `used_plates_photo_path` is set. Update its docstring.
14. `declaration_detail.html`:
    - The per-card gallery shows when any of the three paths is set, and includes a third tuple `('used_plates', 'Placas usadas', card.used_plates_photo_path)`, which gives the caption `Tarjeta N · Placas usadas`.
    - The `photo_cards` summary counts a card that has only the used-plates photo.
    - The ZIP button and "Sin archivos adjuntos" conditions follow from `photo_cards`.

**Tests** (update existing fixtures, add new ones)

15. In `tests/core/test_declarations.py::_card`, the default includes `used_plates_file` (e.g. `"used-plates.jpg"`), so existing `DETERIORO` fixtures stay valid. In `tests/web/test_public_declaration.py::_card`, the default includes `"used_plates": _photo()`. Adjust any existing assertion that counts stored files or lists suffixes so it accounts for the extra photo.
16. Add tests for acceptance criteria 1-6 (details below).

**Docs** (Spanish prose)

17. `docs/DATABASE.md`: add a `used_plates_photo_path` row to the `declaration_cards` table, saying it is nullable, holds the photo of the used plates, is only for `DETERIORO`, and is NULL on `PÉRDIDA` cards and on declarations before 0014.
18. `docs/WEB.md`:
    - In the `/declaracion` row, mention the used-plates photo, required only when the card's reason is deterioro.
    - In the download row, list `<front|back|used_plates>` and the ZIP name `tarjeta-N-PLACA-placas-usadas`.
19. `docs/CORE.md`, `validate_declaration` row: mention the used-plates photo for `DETERIORO`. Reword "Una pérdida no exige ningún archivo" so it stays true (a loss needs no extra file).

## Acceptance criteria
1. `app/migrations/0014_declaration_used_plates_photo.sql` exists with the `ADD COLUMN IF NOT EXISTS` statement. To verify, run `pytest tests/test_schema_migrate.py` with Postgres up: it passes, including `test_migrated_old_schema_matches_fresh_init_sql`, `test_rerun_applies_nothing_new` and `test_every_migration_file_is_recorded_as_applied`. A new test applies the 0014 SQL directly twice and then asserts that the column exists (same pattern as `test_0001_pos_fields_sql_is_safe_to_apply_twice_directly`).
2. A new test in `tests/web/test_public_declaration.py`, using the real DB through the `enabled` fixture, posts a valid declaration with one `DETERIORO` card plus `card_0_used_plates`. It asserts that `declaration_cards.used_plates_photo_path` is non-NULL and names an existing file in `UPLOADS_DIR`.
3. A new test posts a `DETERIORO` card without `card_0_used_plates`. It asserts a 400 response, that the body contains `Tarjeta 1: Falta la foto de la(s) placa(s) usada(s).` (HTML-escaped as rendered), and that `UPLOADS_DIR` is empty.
4. A new test posts a `PÉRDIDA` card without the used-plates photo and gets a successful response with a stored declaration. Another test posts a `PÉRDIDA` card that includes `card_0_used_plates`. It asserts that the column is NULL and that the number of files in `UPLOADS_DIR` equals that of the same submission without it, so no extra file was stored.
5. In `tests/core/test_declarations.py`, `validate_declaration` of a `DETERIORO` card with `used_plates_file=None` contains `Tarjeta 1: Falta la foto de la(s) placa(s) usada(s).`. The same card with `reason="PÉRDIDA"` and no file has no such error.
6. In `tests/web/test_declarations.py`:
   - A point_of_sale client (`punto_venta_client`) gets `GET /declaraciones/<id>/cards/<card_id>/photo/used_plates` with status 200, the stored photo's mimetype and bytes, and `Cache-Control: no-store`.
   - The same request for a card whose `used_plates_photo_path` is NULL returns 404.
   - The ZIP from `/declaraciones/<id>/photos.zip` contains `tarjeta-1-<PLACA>-placas-usadas.<ext>`.
   - The detail page shows `Placas usadas` for that card.
7. In `tests/web/test_declaration_template.py`, the rendered form contains an `<input>` named `card_0_used_plates` whose tag has no `required` attribute. The `<template>` contains `name="card___I___used_plates"`. The `card_0_front` / `card_0_back` inputs still carry `required`.
8. Manual check: run the app from the worktree as described in AGENTS.md "Checking a page" and open `/declaracion`.
   - With motivo "Deterioro" selected, the field "Foto de la(s) placa(s) usada(s)" and its hint are visible, and submitting without it is blocked by the browser.
   - Switching to "Pérdida" hides the field and clears a chosen file.
   - A card added with "+ Agregar tarjeta de propiedad" behaves the same.
9. `grep -n used_plates_photo_path docs/DATABASE.md` and `grep -n used_plates docs/WEB.md` both match. The `validate_declaration` row in `docs/CORE.md` mentions the used-plates photo.

## Out of scope
- More than one used-plates photo per card, or one photo per plate.
- Any change to the declaration PDF (`app/core/declaration_pdf.py` or its route), to `/ingest`, or to the approve/reject logic.
- Changing `MAX_REQUEST_BYTES`, the per-photo size limit, or `app/web/uploads.py`.
- Backfilling or requiring the photo on existing declarations, or editing an already-applied migration.
- Showing the photo in the declarations list page, or adding a new review flag.
- Any change to `factory/checks.sh` or `factory/config.json`.

## Assumptions
- The gallery caption is `Tarjeta N · Placas usadas`, following the existing `Tarjeta N · <label>` pattern. It contains the issue's `Placas usadas` text and keeps the gallery consistent.
- The gallery heading stays "Fotos de la tarjeta de propiedad" even when it includes the used-plates photo. That is the smallest change, and the caption already disambiguates.
- `docs/CORE.md` is updated even though the issue lists only DATABASE.md and WEB.md. Its `validate_declaration` row would otherwise become false.
- A card with an invalid or missing reason neither stores nor requires the used-plates photo. The issue ties both to the normalized reason being `DETERIORO`, and the reason error is reported anyway.
- When JS clears a hidden used-plates input it also clears that input's `.photo-info` text. This avoids a stale "foto reducida" note next to an empty input.
