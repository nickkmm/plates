ESCALATE

## Summary
- Every ownership card on the public `/declaracion` form now needs a "Foto de la(s) placa(s) usada(s)" photo, for both DETERIORO and PÉRDIDA (this follows feedback-1 from nickkmm). Staff can view it, download it and get it in the ZIP on the review screen.
- Adds migration `0014_declaration_used_plates_photo.sql` (one nullable `TEXT` column) and the same column in `init.sql`. A push to `main` runs it in production.
- Round 3 only made the photo span the full width of the photo grid on desktop. The round-3 gate is PASS and all four reviews are PASS with no findings.
- The round-3 gate ran without Postgres again (585 passed, 943 skipped). Only the builder's run (1528 passed, 0 skipped) covers the migration and DB tests.
- Escalated because project rules require a person to check a migration and any change to the public declaration form.

## Why
The code is small, follows the existing front/back photo path everywhere, and updates `docs/DATABASE.md`, `docs/WEB.md` and `docs/CORE.md`. I found no defects. But it adds a migration that runs on deploy and changes the public declaration form, which collects personal data. Both need a person's check here. Also, spec criteria 3, 4, 7 and 8 (photo only for DETERIORO, JS toggle, no `required` in the HTML) were knowingly replaced by the owner's feedback. So the final rule rests on that feedback, not on the spec or on issue #60 as written.

## Acceptance criteria
1. Migration 0014 is idempotent and schema-equivalence tests pass: met per build.md, which reports a run with Postgres up and a new apply-twice test. I confirmed the SQL is a single `ADD COLUMN IF NOT EXISTS` and that `init.sql` puts the column right after `card_back_path`. The gate skipped these tests.
2. A card with the photo stores a path to an existing file: met per build.md (`test_a_card_stores_the_used_plates_photo[DETERIORO|PÉRDIDA]`, real DB, skipped in the gate).
3. A card without the photo returns 400 with `Tarjeta 1: Falta la foto de la(s) placa(s) usada(s).` and leaves no files: met per build.md, now for both reasons. I spot-checked `_validate_card`: the error is added whenever `used_plates_file` is blank, whatever the reason.
4. PÉRDIDA cards are accepted without the photo and never store it: superseded by feedback-1. PÉRDIDA cards now require it and store it. `_save_files` always saves `card_<i>_used_plates`.
5. Core validation tests: met in the new form. I ran the used-plates and loss tests in `tests/core/test_declarations.py` and `tests/web/test_declaration_template.py` myself: 10 passed.
6. Staff review (route 200/404, ZIP name, detail figure, summary): met per build.md. I confirmed `CARD_PHOTO_SIDES`, `_photo_entries` and both `declaration_detail.html` conditions in the diff.
7. Template has `card_0_used_plates` without `required`: superseded. The input is now `required` in every block and in the `<template>`, and front/back stay required. Confirmed in the diff and by the template tests.
8. Manual browser check: met per build.md (headless Edge over CDP, desktop and 390px emulation). The UI reviewer could not confirm a real 390px viewport or the logged-in detail page.
9. Docs grep checks: met. All three docs are updated in Spanish and describe the rule as "deterioro y pérdida".

## For the human
Recommendation: don't merge yet

What to check before merging:
1. **The rule change itself.** Confirm the photo should be required on every card, PÉRDIDA included. That was your feedback ("las placas viejas tienen que venir tanto en pérdida como por deterioro"). Issue #60's title and body still say DETERIORO only, so consider updating the issue to match. The hint now reads "Toma una foto donde se vean las placas que vas a entregar." Also note: a customer who lost a plate entirely may have no used plate to photograph. Confirm that a PÉRDIDA declarant always hands in the old plates, or they can't submit the form at all.
2. **Migration on deploy.** Run the DB-backed tests once with Postgres up, because the gate skipped them: `pytest tests/test_schema_migrate.py tests/web/test_public_declaration.py tests/web/test_declarations.py`. The migration is additive, nullable and safe to re-run, so the risk is low. Files: `app/migrations/0014_declaration_used_plates_photo.sql`, `init.sql`.
3. **Public form (personal data).** Confirm you're fine collecting one more customer photo per card under the existing data-policy authorization, with the policy text unchanged. Anyone who loaded the old form before the deploy and submits afterwards will get a 400 asking for the photo. Files: `app/web/templates/declaration.html`, `app/web/blueprints/public_declaration.py`, `app/core/declarations.py`.
4. **Optional visual check.** Look at `/declaracion` at a real phone width, and at the "Placas usadas" figure on `/declaraciones/<id>` while logged in. File: `app/web/templates/declaration_detail.html`.

If those check out, it's ready to merge: the reviews found nothing, and the code matches the rule you asked for.
