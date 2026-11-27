VERDICT: PASS

## Summary
- Adds `app/shared/` (db pool, normalize, timezones, models, uploads) with a package docstring stating the no-domain-dependency rule.
- The four modules moved with `git mv` (shown as renames); the pool moved verbatim into `app/shared/db.py`, and `app/core/db/__init__.py` re-exports `get_conn` with its per-table re-exports and `__all__` unchanged.
- Imports, docstrings and docs were updated everywhere. My spec criterion 2 `rg` finds nothing, and the blueprint diffs are import lines only.
- `clean_db` patches `get_conn` on both modules and takes the pool from `app.shared.db`. A new identity test locks the re-export. The mypy override covers `app.shared.*`, and `send_stored_file` has a return annotation.
- Checks passed (1844 tests against 1843 on main); no test was removed or weakened.

## Findings
None.

## Notes
- The `app/shared/db.py` docstring refers to "every SELECT in app/core/db/", which is accurate for today's layout. Update it when the table modules move out in later phases.
