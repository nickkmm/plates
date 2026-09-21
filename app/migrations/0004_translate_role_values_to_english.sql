-- Translates the two Spanish `users.role` values still live in production
-- ('produccion', 'punto_venta') to their English equivalents ('production',
-- 'point_of_sale'). See CLAUDE.md's naming-convention section: users.role
-- is never rendered to a user (verified against every template), so it's an
-- internal identifier, not Spanish business-domain data -- it should have
-- been English from the start, same as the table/column names Fase 1c
-- already renamed (app/migrations/0003_rename_to_english.sql). 'admin' and
-- the already-removed 'consulta' role are untouched (admin was already
-- English; consulta is gone as of the previous PR).
--
-- Applied to an ALREADY-INITIALIZED database by app/schema_migrate.py, same
-- as 0001/0002/0003 -- a plain UPDATE ... WHERE is naturally idempotent (a
-- second run matches zero rows and changes nothing), no guard needed.
--
-- app/web/auth.py's ROLE_PRODUCCION/ROLE_PUNTO_VENTA constants are updated
-- to these new values in the same change that adds this migration -- an
-- existing session's cookie still has the OLD Spanish role value cached
-- (sessions aren't re-validated against the DB per request), so anyone
-- logged in as 'produccion'/'punto_venta' at deploy time must log out and
-- back in to pick up the new value and regain their normal access.

UPDATE users SET role = 'production' WHERE role = 'produccion';
UPDATE users SET role = 'point_of_sale' WHERE role = 'punto_venta';
