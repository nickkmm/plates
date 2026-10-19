-- Notarization flow: the person no longer uploads a notarized PDF. For a
-- declaration with a PÉRDIDA card, staff download the declaration as a PDF, hand
-- it to the client, and when the client comes back with it notarized they mark it
-- received here. "Crear pedido" is blocked until then (see docs/WEB.md).
-- NULL = not received yet. Safe to re-run; same end schema as init.sql.

ALTER TABLE declarations ADD COLUMN IF NOT EXISTS notarized_received_at TIMESTAMP;
ALTER TABLE declarations ADD COLUMN IF NOT EXISTS notarized_received_by INTEGER REFERENCES users(id);
