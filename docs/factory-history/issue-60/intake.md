DECISION: ACCEPT
RISK: high
SURFACES: ui, security
RESCOPED: no

## Summary
- Accepted as written: one used-plates photo per `DETERIORO` card on `/declaracion`, stored with the card and shown to staff on the review screen.
- The issue already sets every business rule: when the photo is required, the Spanish copy, and the migration number.
- I checked the code the issue points to. `_save_files`, `_validate_card`, `CARD_PHOTO_SIDES` and migrations up to 0013 are all where the issue says, so 0014 is the next free number.
- Risk is high because of the schema migration and the public file upload. The new column is nullable and only added, and the upload goes through the existing `save_photo` path.

## Scope
Build exactly what issue #60 specifies:
- **Schema**: migration `0014_declaration_used_plates_photo.sql` adds a nullable `declaration_cards.used_plates_photo_path` (idempotent, header comment in the style of 0012). The same column goes into `init.sql` so the schema-equivalence test keeps passing.
- **Core**: the card input and record gain the photo name/path, and it is persisted on insert. For a `DETERIORO` card with no photo, validation adds `Falta la foto de la(s) placa(s) usada(s).` with the existing `Tarjeta N: ` prefix. A `PÉRDIDA` card never requires it. The module docstring is updated.
- **Public form**: a per-card file input `card_<i>_used_plates` with the label and hint text from the issue, and no `required` in the HTML. The existing per-card reason script shows the input and makes it required only for `DETERIORO`. For any other reason it hides the input and clears it. The add-card `<template>` includes the input. On the server, the file is stored only when the card's normalized reason is `DETERIORO` and is ignored otherwise.
- **Staff review**: `/declaraciones/<id>/cards/<card_id>/photo/used_plates` serves the photo with the same headers and 404 rules as front/back. The card gallery shows a `Placas usadas` figure when the photo exists. The photo summary and the ZIP-button condition count it, and the ZIP gets `tarjeta-<N>-<PLACA>-placas-usadas.<ext>`.
- **Docs** (Spanish prose): `docs/DATABASE.md` and `docs/WEB.md`.
- **Tests** for acceptance criteria 1 to 6. `factory/checks.sh` must pass.

Left out, as the issue says: more than one photo per card, any change to the PDF, `/ingest` or the approval logic, and any change to the size limits or `MAX_REQUEST_BYTES`.

## Why
This extends an existing pattern. The card front/back photos already run through `_save_photo` in `app/web/blueprints/public_declaration.py`, through `_validate_card` in `app/core/declarations.py`, and through `CARD_PHOTO_SIDES` and `_photo_entries` in `app/web/blueprints/declarations.py`. A third photo slots into each of these. The issue settles the one business rule involved (required only for `DETERIORO`) and gives the exact user-facing copy, so nothing is left for INGSE to decide. It is one coherent change that fits in a single pull request.

## Questions
