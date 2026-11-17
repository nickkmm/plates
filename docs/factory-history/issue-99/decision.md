ESCALATE

## Summary
- When a job reaches `merged` or `rejected`, `factory/bin/job` now drops its `plates_test_<n>` and `plates_test_<n>_gw<digits>` databases. This is best effort: notes go to stderr and the transition always saves.
- The new `job cleanup-dbs` command drops the databases of jobs that had already finished. An internal `job drop-dbs` runs the drop under the `.venv` Python when the system Python has no psycopg.
- Round 1 passed: `GATE: PASS` (1836 tests passed) and the code review is `VERDICT: PASS` with no findings. The change stays in intake's scope, and `checks.sh` and `config.json` are untouched.
- Acceptance criteria 5 and 6, the actual `DROP DATABASE` against a live Postgres, were never run. Criterion 4 was checked only through a unit test.

## Why
The code is sound and stays in scope. It touches no app code, migrations, environment variables, Docker files or security rules. But it runs `DROP DATABASE`, and the live drop path (criteria 5 and 6) hasn't been exercised at all, so not every criterion has convincing evidence. A person should run that check before merging.

## Acceptance criteria
1. Tests pass without Postgres and without `POSTGRES_PASSWORD`: met. I re-ran `env -u POSTGRES_PASSWORD .venv/Scripts/python -m pytest tests/test_factory_test_dbs.py -q` and got 30 passed.
2. The matcher returns exactly `plates_test_1`, `plates_test_1_gw0` and `plates_test_1_gw11` for the 10-name list: met. I read the test (`tests/test_factory_test_dbs.py` line 57) and it passes. The regex is a `fullmatch` on `plates_test_{int(n)}(_gw[0-9]+)?` plus the conftest rule.
3. `--help` lists `cleanup-dbs` and an internal `drop-dbs`: met. I checked by running it.
4. `cleanup-dbs` with Postgres down exits 1 quickly with one `job: ` line: partly met. A unit test calls `cmd_cleanup_dbs()` and asserts exit 1 and a single stderr line. The CLI itself was not run, because the build's permission classifier refused it. The code path (3 s socket timeout, then `die`) supports it.
5. Live drop of `plates_test_<n>` and `_gw0` for a merged job, with `plates` and `plates_test` kept and a second run reporting nothing to drop: not met. It was never run.
6. A non-final job's `plates_test_<m>` survives `cleanup-dbs`: not met. Not run live, though the code only passes job numbers whose stage is in `TERMINAL`.
7. The drop call is in `advance`'s `TERMINAL` block and its messages go to stderr: met. I read the diff: `cleanup_test_dbs(job["id"])` runs inside `if stage in TERMINAL:`, and every message uses `file=sys.stderr`.
8. The UTF-8 re-exec is guarded by `__name__ == "__main__"`: met, according to the diff.
9. AGENTS.md mentions `cleanup-dbs`: met. The new sentence follows the `plates_test_<n>` / `_gwN` paragraph.
10. Protected files are unchanged: met. Only `AGENTS.md`, `factory/bin/job` and `tests/test_factory_test_dbs.py` changed.

## For the human
Recommendation: don't merge yet

The code reads correctly and every automated check passed. But this feature runs `DROP DATABASE` against your local Postgres, and that path was never run for real. Please do the live check from the spec before merging:

1. Start the database: `docker compose up -d db`.
2. Pick a `merged` job in `factory/jobs/` with number `<n>` and a non-final job with number `<m>`. Create `plates_test_<n>`, `plates_test_<n>_gw0` and `plates_test_<m>` by hand.
3. Run `python factory/bin/job cleanup-dbs` from the main checkout, using the system Python so the `.venv` fallback through `drop-dbs` is exercised. Check that it reports both `<n>` names as dropped, and that `plates`, `plates_test` and `plates_test_<m>` are still in `pg_database`.
4. Run it again and check that it reports nothing to drop.
5. Optionally, stop Postgres and confirm that `cleanup-dbs` exits 1 within a few seconds with one `job: ` line (criterion 4).

Note that the first run in step 3 will also drop the leftover test databases of every job that has already finished. That is the intended backfill.

If those steps behave as described, it is fine to merge. It touches no app code, migrations, environment variables, Docker or deploy config.

Files to look at:
- `factory/bin/job`: the "Test databases" section (`job_db_names`, `db_credentials`, `drop_with_psycopg`, `drop_test_dbs`, `cleanup_test_dbs`), the `TERMINAL` block in `advance`, and `cmd_cleanup_dbs` / `cmd_drop_dbs`
- `tests/test_factory_test_dbs.py`
- `AGENTS.md`, lines 50 to 56
