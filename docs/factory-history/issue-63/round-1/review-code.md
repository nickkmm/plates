VERDICT: PASS

## Summary
- Every protected route now uses `permission_required` with the code the spec maps to it. `/` uses `authenticated`. The route-by-route grep matches the mapping: 3 ingest routes, 2 cash_summary, 10 declarations, 4 review, 2 inventory, 2 pricing, 3 customers, and 5 records routes.
- `current_user()` loads the user and `effective_permissions` in one connection and caches both on `g`. Deactivation clears the session, a role change syncs `session["role"]`, and `has_permission` never raises without a session.
- `NAV_ITEMS`, `landing_url()`, the `/` 403 page, the context processor and the templates follow the spec. The processor only touches the DB when `user_id` is in the session.
- Old symbols are gone from `app/` and the docs. The `docs/factory-history` hits are the factory's own record.
- Tests: the old standalone-app tests are replaced with DB-backed ones. `test_roles.py` is untouched. `round-1/checks.md` shows the checks passing.

## Findings
None.

## Notes
- `/` decides "no permissions" by comparing `landing_url()` to `url_for("index")`. This works, but it is slightly indirect.
- The build log mentions one unrelated failure in the shared test DB, `test_schema_migrate`. It comes from another job's migration and is not caused by this change.
