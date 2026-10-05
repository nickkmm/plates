-- Customer model change: new `customers.type` vocabulary, person fields, and
-- `transit_license` removed from customers.
--
--   * New vocabulary (normalize.CUSTOMER_TYPES): TRÁNSITO / EMPRESA / GESTOR /
--     CLIENTE. CLIENTE is the old PERSONA NATURAL renamed; OTROS is gone (its
--     rows go back to NULL = "not classified yet", which is what the form
--     shows as the empty option).
--   * New nullable person columns: address, neighborhood, municipality. Only
--     meaningful for GESTOR/CLIENTE (enforced in the app, see
--     app/web/helpers.py::customer_fields_from_form, not by a CHECK -- same
--     convention as document_relation).
--   * customers.transit_license is dropped: production had 0 of 205 customers
--     with a value, so nothing is lost. The license number is now an
--     identifier of the vehicle's ownership card, not of the customer.
--
-- Applied to an ALREADY-INITIALIZED database by app/schema_migrate.py, same
-- as 0001-0004. Every statement is naturally idempotent (ADD COLUMN IF NOT
-- EXISTS; UPDATE ... WHERE matches zero rows the second time; DROP COLUMN IF
-- EXISTS), so no guard is needed.

ALTER TABLE customers ADD COLUMN IF NOT EXISTS address TEXT;
ALTER TABLE customers ADD COLUMN IF NOT EXISTS neighborhood TEXT;
ALTER TABLE customers ADD COLUMN IF NOT EXISTS municipality TEXT;

UPDATE customers SET type = 'CLIENTE' WHERE type = 'PERSONA NATURAL';
UPDATE customers SET type = NULL WHERE type = 'OTROS';

ALTER TABLE customers DROP COLUMN IF EXISTS transit_license;
