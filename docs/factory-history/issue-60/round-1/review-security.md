VERDICT: PASS

## Summary
- The public `/declaracion` form gets an extra photo per DETERIORO card. It is stored in the new nullable column `declaration_cards.used_plates_photo_path`.
- The upload goes through the existing `_save_photo` / `save_photo` path, so the same type and size checks, the same Turnstile and honeypot gate, `MAX_REQUEST_BYTES` and `MAX_CARDS` all apply. On any failure the stored files are deleted.
- The server stores the photo only when the normalized reason is `DETERIORO`. Uploads on other cards are never read.
- Staff get the photo through the existing `@pos_allowed` card-photo route, which looks the card up by declaration id, and in the ZIP. No new endpoint, no new access path.
- SQL stays parameterized, the template uses only autoescaped constants, and no secrets, `.env` or business data were committed.

## Findings
None.

## Notes
- `declaration_card_photo` (`app/web/blueprints/declarations.py:386`) only accepts a `side` listed in `CARD_PHOTO_SIDES`, and it looks the card up inside `list_declaration_cards(conn, declaration_id)`. The new `used_plates` side can't be used to read another declaration's card or an arbitrary attribute.
- A request can now carry up to `MAX_CARDS` more photos, but `MAX_REQUEST_BYTES` (40 MB) and the per-photo limit are unchanged, so the most the server stores per request is still bounded.
- The stored value is the server-generated name returned by `save_photo`, never the client's filename. When `_send_stored_file` serves it, it still goes through `resolve_upload_path`, so no path traversal is possible.
- No new dependencies.
