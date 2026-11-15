ESCALATE

## Summary
- Round 4 follows feedback-2 from nickkmm. Each card on `/declaracion` now has a "Perdí mis placas" checkbox, and ticking it is the only way to send a card without the used-plates photo. The photo is still required for both DETERIORO and PÉRDIDA (feedback-1).
- The checkbox is saved as `plates_lost` in the card's `submitted_data`, so there is no new column. When it is ticked, the server never reads or stores that card's upload, and the staff detail page shows "Sin foto de las placas usadas: el cliente marcó «Perdí mis placas»."
- The round-4 gate is PASS and all four reviews are PASS with no findings. The gate ran without Postgres again (588 passed, 948 skipped). The only run that covers the migration and the end-to-end DB tests is the builder's own run with Postgres up (1536 passed).
- Escalated because project rules require a person to check a new migration (`0014`, run on deploy) and any change to the public declaration form.

## Why
The code is small and follows the existing front/back photo path. The new flag only relaxes one validation rule and never stores an extra file. Docs (`DATABASE.md`, `WEB.md`, `CORE.md`) are updated in Spanish. I found no defects. However, the change adds a migration that runs in production on every push to `main`, and it changes the public form that collects personal data. Both always need a person's check in this project. Also, the business rule now comes from the owner's two feedback comments, not from issue #60 or the spec, which still say DETERIORO only and have no checkbox.

## Acceptance criteria
1. Migration 0014 is idempotent and the schema-equivalence tests pass: met per build.md (Postgres-up run, plus a new apply-twice test). I confirmed the SQL is a single `ADD COLUMN IF NOT EXISTS ... TEXT` and that `init.sql` line 190 puts the column after `card_back_path`. The recorded gate skipped these tests.
2. A card with the photo stores a path to an existing file: met per build.md (`test_a_card_stores_the_used_plates_photo[DETERIORO|PÉRDIDA]`). This test needs the real DB, so the gate skipped it.
3. A card without the photo gets a 400 with `Tarjeta 1: Falta la foto de la(s) placa(s) usada(s).` and leaves no files: met per build.md, now for both reasons unless `plates_lost` is set. I spot-checked `_validate_card` in the diff: `if not card.plates_lost and _blank(card.used_plates_file)`.
4. PÉRDIDA without the photo is accepted, and a posted photo is never stored: replaced by the owner's feedback. Now any card marked "Perdí mis placas" is accepted without the photo (column NULL, `plates_lost: true` in `submitted_data`). A photo posted on such a card is never read (`_save_files` skips it). Covered by tests per build.md, DB-backed.
5. Core validation: met. I ran the used-plates, plates-lost and loss tests in `tests/core/test_declarations.py` and `tests/web/test_declaration_template.py` myself: 13 passed.
6. Staff review (route 200/404, ZIP name, `Placas usadas` caption, lost-plates note): met per build.md. In the diff I confirmed `CARD_PHOTO_SIDES`, `_photo_entries`, and the `plates_lost` lookup in `_render_detail`, which checks the index is in range.
7. Template has `card_0_used_plates` without `required`: replaced by the owner's feedback. The input is now `required`, and the box disables it with JS. The checkbox is in every block and in the `<template>`, and it stays ticked after a rejected submission (template tests passed in my run).
8. Manual browser check: met per build.md (headless Edge over CDP). The UI review did not re-run the checkbox behaviour in a browser, could not render a real 390px viewport, and did not view the logged-in detail page.
9. Docs grep checks: met. All three docs describe the photo rule and the «Perdí mis placas» exception.

## For the human
Recommendation: don't merge yet

The code looks correct and the reviews found nothing. Project rules still require your check on these points first:

1. **The business rule.** Confirm that this is the rule you want: the used-plates photo is required on every card (DETERIORO and PÉRDIDA), and any customer can skip it by ticking "Perdí mis placas". Staff only see a note on the review page and decide at approval, so nothing enforces it. Issue #60 and the spec still say "DETERIORO only, no checkbox". Consider updating the issue so the record matches. Files: `app/core/declarations.py`, `app/web/blueprints/public_declaration.py`, `app/web/templates/declaration.html`.
2. **Migration on deploy.** The gate skipped every DB-backed test. Before merging, run them once with Postgres up: `pytest tests/test_schema_migrate.py tests/web/test_public_declaration.py tests/web/test_declarations.py`. The migration only adds one nullable column and is safe to re-run. Its header comment doesn't mention that the column is also NULL when "Perdí mis placas" is ticked. That's cosmetic, but fix it now, because an applied migration shouldn't be edited later. Files: `app/migrations/0014_declaration_used_plates_photo.sql`, `init.sql`.
3. **Public form and personal data.** The form now collects one more photo per card under the existing data-policy authorization, and the policy text is unchanged. Confirm that is fine. Someone who loaded the old form before the deploy and submits it afterwards will get a 400 asking for the photo.
4. **Quick visual check.** Open `/declaracion` on a real phone, tick and untick "Perdí mis placas" (watch the negative-margin spacing the reviewers flagged), and look at a declaration's detail page while logged in. Files: `app/web/templates/declaration.html`, `app/web/templates/declaration_detail.html`.
5. **Small notes.** One overlong docstring line in `public_declaration.py` (flagged by the code review). The PR also commits `docs/factory-history/issue-60/`, the factory's history files.

If these check out, it's ready to merge.
