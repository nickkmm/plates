VERDICT: PASS

## Summary
- `tests/conftest.py` gets a pure `_test_db_name(base, worker)` helper. It applies the existing guard to both the base and the final name, and the result is set in `POSTGRES_DB` at import time as before.
- `_create_database_if_missing` tolerates `DuplicateDatabase`. The `_db_ready` and `clean_db` paths and the `TEST_REQUIRE_DB` handling are unchanged.
- `pytest-xdist==3.8.0` is pinned and `addopts` is untouched. No protected or gate files are in the diff.
- The 15 unit tests cover every case in requirement 8 and need no Postgres.
- The docs, `AGENTS.md` and the `clean_db` docstring are updated. The build report has the timings and a proposed `checks.sh` diff.

## Findings
None.

## Notes
- I did not re-run anything. `checks.md` and the build report show the full suite passing in parallel.
- The `DuplicateDatabase` suppression sits inside the `if fetchone() is None` branch. That is correct, and the race window is covered.
- The shared-state audit found nothing to fix. That matches the spec's own pre-audit, and the passing parallel runs support it.
