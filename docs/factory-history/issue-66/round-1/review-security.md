VERDICT: PASS

## Summary
- Adds the `/produccion` board, the order detail page, two photo routes and the `advance`/`undo` POSTs. Every route has `permission_required`, and the role check runs on the server.
- The photo routes check that the declaration is linked to the order and that the card belongs to the declaration. Only `id_front`/`id_back` and the card photo sides are served. Signature and PDF are never served here.
- `send_stored_file` moved to `uploads.py` with the same behaviour (stored-name regex via `resolve_upload_path`, `nosniff`, `no-store`, 404s).
- POST forms carry CSRF tokens. Templates use autoescaped Jinja with no `|safe`. Logs hold only the username, order id and statuses, no customer personal data.
- No secrets, data files or new dependencies are committed.

## Findings
None.

## Notes
- `advance`/`undo` decide 403 from the posted `expected_status`, not the stored status. This is safe because the core `advance_order`/`revert_order` refuse the change unless the stored status equals `expected_status`. So a user with only `production.deliver` can only move `ready -> delivered` and `delivered -> ready`. This depends on `_move_order` doing that comparison atomically (in a guarded UPDATE or under a row lock), which is existing core code and not part of this change.
- The production role can now see declarants' names, ID numbers, phones and cédula photos for orders linked to declarations. The spec asks for this, and the data was collected with the data-policy authorization. Prices, payment, the signature and the notarized PDF stay hidden.
- The badge macro puts `status` into a class attribute. It comes from the database's fixed status set and is autoescaped, so there is no injection risk.
- The `Ver en Producción` link on `/records/<id>` only shows when the user has `can('production.view')`. Who can open `/records` itself is unchanged.
