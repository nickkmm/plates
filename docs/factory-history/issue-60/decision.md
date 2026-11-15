ESCALATE

## Summary
- Adds a required "Foto de la(s) placa(s) usada(s)" upload to every DETERIORO card on the public `/declaracion` form. Staff can view it, download it and get it in the ZIP on the review screen.
- Adds migration `0014_declaration_used_plates_photo.sql` (one nullable `TEXT` column) and the same column in `init.sql`. A push to `main` runs it in production.
- All four round-1 reviews say PASS and the gate says PASS. But the gate ran without Postgres: 584 passed, 943 skipped. The migration and DB-backed tests were only run by the builder (who reports 1527 passed).
- The change stays inside intake's Scope, and `docs/` (DATABASE, WEB, CORE) is updated.
- Escalated because project rules require a person to check a migration and any change to the public declaration form.

## Why
The code follows the spec closely and reuses the existing photo path everywhere. I found no defects. Still, it adds a database migration that runs on deploy, and it changes the public declaration form (a new personal-data upload). This project's rules say a person must check both before merge. Also, the gate's own test run skipped every Postgres test, so the migration criteria have no independent evidence beyond build.md.

## Acceptance criteria
1. Migration 0014 is idempotent and the schema-equivalence tests pass: met per build.md (a new twice-apply test was added). I confirmed the SQL is a single `ADD COLUMN IF NOT EXISTS` and that `init.sql` puts the column after `card_back_path`. The gate skipped these tests because Postgres was unreachable.
2. A DETERIORO card with the photo stores a path to an existing file: met per build.md (`test_deterioration_card_stores_the_used_plates_photo`, real DB, skipped in the gate).
3. A DETERIORO card without the photo returns 400 with `Tarjeta 1: Falta la foto de la(s) placa(s) usada(s).` and leaves no files behind: met per build.md. I spot-checked `_validate_card`: the error is added only when the normalized reason is `DETERIORO` and the file is blank.
4. A PÉRDIDA card is accepted without the photo, and a posted photo is never stored: met. I spot-checked `_save_files`: `_save_photo` for `card_<i>_used_plates` is called only when the normalized reason is `DETERIORO`, so other uploads are never read.
5. Core validation tests for DETERIORO and PÉRDIDA: met per build.md.
6. Staff review: the route returns 200 or 404, the ZIP has the right name, and the detail page and summary show the photo. Met per build.md. I confirmed `CARD_PHOTO_SIDES`, `_photo_entries` and both template conditions in the diff.
7. The template's `card_0_used_plates` has no `required`, the `<template>` includes it, and front/back stay required: met. The `photo_field(required=False, ...)` call is in the diff.
8. Manual browser check: met per build.md (headless Edge over CDP; `agent-browser` is not installed). The UI review did not see a real 390px phone viewport or the logged-in staff page.
9. Docs grep checks: met. All three docs rows are updated in Spanish.

## For the human
Recommendation: don't merge yet

What a person needs to check before merging:
1. **Migration on deploy.** Run the DB-backed tests once with Postgres up (`pytest tests/test_schema_migrate.py tests/web/test_public_declaration.py tests/web/test_declarations.py`). The gate skipped them, so only the builder's run covers them. The migration itself is additive, nullable and re-runnable, so it is low risk. Files: `app/migrations/0014_declaration_used_plates_photo.sql`, `init.sql`.
2. **Public declaration form (personal data).** Confirm you're happy to collect one more customer photo per DETERIORO card under the existing data-policy authorization, with no change to the policy text. Also note: when this deploys, anyone who loaded the old form and submits a DETERIORO card afterwards gets a 400 asking for the photo. Files: `app/web/templates/declaration.html`, `app/web/blueprints/public_declaration.py`, `app/core/declarations.py`.
3. **Optional visual check.** Look at the form at a real phone width with "Deterioro" picked (the reviewer's Edge render was clipped at 390px). Look at the "Placas usadas" figure on `/declaraciones/<id>` while logged in. File: `app/web/templates/declaration_detail.html`.

If those check out, this is ready to merge: the reviews found no problems and the change matches issue #60 exactly.
