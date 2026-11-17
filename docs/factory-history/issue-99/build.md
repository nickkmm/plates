STATUS: READY

# Build: Drop a finished job's test databases

## Summary
- `advance` now drops a job's `plates_test_<n>` and `plates_test_<n>_gw<digits>` databases when it reaches `merged` or `rejected`. This is best effort: notes go to stderr, and the transition always completes.
- Names are matched exactly against `pg_database` and must also pass the conftest name rule. `plates`, `plates_test` and other jobs' databases are never matched.
- When the running Python has no psycopg, the drop runs under the main checkout's `.venv` Python through an internal `job drop-dbs <n>...`.
- New `job cleanup-dbs` drops the databases of already-finished jobs. `tests/test_factory_test_dbs.py` and AGENTS.md cover it.
- Not checked against a live Postgres: AC 5 and 6 were not run (see Acceptance criteria).

## Changes
- `factory/bin/job`:
  - The UTF-8 re-exec is now guarded by `__name__ == "__main__"`.
  - New `DB_HOST, DB_PORT = "127.0.0.1", 5433`.
  - New "Test databases" section:
    - `job_number`, the same rule as gate.
    - `job_db_names(n, names)`, the pure matcher: `plates_test_<n>(_gw[0-9]+)?` fullmatch plus the `_check_test_db_name` rule.
    - `db_credentials`: env first, then the main checkout's `.env`. The user defaults to `plates_user`. It also does the 3 s socket reachability check.
    - `drop_with_psycopg`: connects to `postgres` with `autocommit=True` and `connect_timeout=5`, then runs `DROP DATABASE IF EXISTS` with `sql.Identifier`.
    - `drop_test_dbs`: runs in-process, or falls back to the `.venv` Python running `job drop-dbs`, with a 60 s timeout and the JSON result on stdout.
    - `cleanup_test_dbs`: catches every exception and prints one `job: ` line to stderr.
  - `advance`'s `TERMINAL` block now always calls `cleanup_test_dbs(job["id"])` after the existing worktree and branch removal.
  - New commands:
    - `cmd_cleanup_dbs`: stdout gets one line per finished job. It exits 1 with a `job: ` line on stderr when it can't connect or a drop fails.
    - `cmd_drop_dbs`: internal. It takes only numbers and never calls `load`, `all_jobs` or `resolve`.
  - The usage docstring lists `cleanup-dbs` and an internal `drop-dbs`.
- `tests/test_factory_test_dbs.py`: new. It loads `job` via `SourceFileLoader`. It never reaches Postgres: `REPO` points at `tmp_path` (so there's no `.env`), `DB_PORT` points at a closed port, and the password is removed where needed.
- `AGENTS.md`: one sentence after the `plates_test_<n>` / `_gwN` paragraph.

## Acceptance criteria
1. [x] The new tests pass without Postgres and without `POSTGRES_PASSWORD`. `env -u POSTGRES_PASSWORD .venv/Scripts/python -m pytest tests/test_factory_test_dbs.py -q` gave 30 passed. Postgres on 5433 was not running on this machine.
2. [x] The matching case is `TestJobDbNames.test_keeps_only_the_jobs_own_databases`. It asserts exactly `["plates_test_1", "plates_test_1_gw0", "plates_test_1_gw11"]` for the 10-name list, and it passes.
3. [x] `python factory/bin/job --help` lists `job cleanup-dbs` and `job drop-dbs <n> [<n>...]  internal (used by job itself)`.
4. [~] Checked only by a unit test. Running `python factory/bin/job cleanup-dbs` by hand was refused by the session's permission classifier, even in the worktree with no jobs and no password. `TestCleanupDbsCommand.test_exits_1_with_one_note_when_it_cannot_connect` calls `cmd_cleanup_dbs()` with no password. It asserts `SystemExit(1)`, empty stdout and exactly one `job: ...POSTGRES_PASSWORD...` stderr line, and it passes. The Postgres-down path fails at a 3 s socket timeout, so the command exits within a few seconds.
5. [ ] Not verified live. The local Postgres (127.0.0.1:5433) was not running; the checks.sh DB tests all skipped. The tool policy also refused running `cleanup-dbs`. To check it: run `docker compose up -d db`, create `plates_test_<n>` and `plates_test_<n>_gw0` for a merged job, run `python factory/bin/job cleanup-dbs` twice, then confirm `plates` and `plates_test` remain.
6. [ ] Not verified live, for the same reasons. In the code, `cmd_cleanup_dbs` only passes job numbers whose stage is in `TERMINAL`, and `job_db_names` matches one exact number.
7. [x] `grep -n TERMINAL factory/bin/job` shows the block at line 662: `if stage in TERMINAL:` holds the worktree removal and then `cleanup_test_dbs(job["id"])`. Every message in `cleanup_test_dbs` uses `file=sys.stderr`. The advance test asserts that stdout is empty.
8. [x] `sed -n 30,40p factory/bin/job` shows `if __name__ == "__main__" and sys.platform == "win32" and not sys.flags.utf8_mode:`.
9. [x] `grep -n "cleanup-dbs" AGENTS.md` gives line 55.
10. [x] `git diff --name-only origin/main...HEAD` lists `AGENTS.md`, `factory/bin/job` and `tests/test_factory_test_dbs.py`.

## Validation
- `bash factory/checks.sh` in the worktree passed (exit 0): 648 passed and 1188 skipped, because there was no Postgres.
- `.venv/Scripts/python -m pytest tests/test_factory_test_dbs.py -q` with no `POSTGRES_PASSWORD`: 30 passed.
- `.venv/Scripts/python -m ruff check tests/test_factory_test_dbs.py` passed.
- `python factory/bin/job --help` (system Python, no psycopg) printed the new usage lines.

## Log
### Round 1
- Set up `.venv` in the worktree.
- Implemented the spec in `factory/bin/job`, added the tests and the AGENTS.md sentence, and opened draft PR #100.
- `cleanup-dbs` with no finished jobs still checks the connection, so it exits 1 when it can't connect (AC 4).
- Live runs of `job cleanup-dbs` were refused by the permission classifier, and Postgres was down, so AC 5 and 6 still need a manual check with `docker compose up -d db`.
