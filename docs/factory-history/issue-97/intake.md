DECISION: ACCEPT
RISK: medium
SURFACES: none
RESCOPED: yes

## Summary
- Accepted the main part of the speed-up: making the test suite safe and fast to run in parallel with pytest-xdist, with each worker on its own test database.
- Left out the edits to `factory/checks.sh` itself (adding `-n`, running steps concurrently). That file is in `protected_paths` in `factory/config.json`, so the gate fails any branch that changes it. Also, the gate runs the main checkout's copy, not the branch's.
- The PR will include the exact `checks.sh` change for you to apply by hand, plus before/after timings measured on the job's worktree.
- Rated medium risk because this is shared test infrastructure: a mistake could leak state between tests or skip DB tests without anyone noticing.

## Scope
Build (outside protected paths):
- Add `pytest-xdist` (pinned) to `requirements-dev.txt`.
- In `tests/conftest.py`, when running under an xdist worker, give each worker its own test database. Derive the name from the existing base name (`TEST_POSTGRES_DB`, default `plates_test`) plus the worker id, e.g. `plates_test_12_gw0`. Each worker creates its database if it's missing and brings it to the current schema the same way `_db_ready` does today (init.sql + `schema_migrate.main()`). The name must still pass the existing `plates_test` prefix and alphanumeric guard. The suite must never be able to reach the real `plates` database.
- Without xdist (plain `pytest`), behaviour is unchanged: one database named as it is today.
- `TEST_REQUIRE_DB=1` must still make an unreachable database a failure in every worker, never a skip.
- Audit the suite for shared state outside Postgres that would break under parallel runs (temp files at fixed paths, fixed ports, module-level state that tests change). Fix whatever is found so `pytest -n auto` passes reliably, with the same test count and no new skips compared to a serial run.
- Don't enable xdist by default for plain `pytest` (no `-n` in `addopts`), so developers and the container run aren't forced onto it.
- In the PR description: serial vs `pytest -n auto` wall-clock timings from the worktree, and the exact `factory/checks.sh` change you can apply after merge. That change: run pytest with `-n auto` (or a fixed worker count), add `xdist` to the dev-dependency import check, and optionally run ruff, mypy and the script checks concurrently, with each step's output and exit status kept separate so `checks.log` still shows which step failed.

Left out:
- Any edit to `factory/checks.sh`, `factory/config.json` or `factory/bin/gate` (protected, or the gate's own machinery).
- Changing `.github/workflows/test.yml` to run in parallel.
- Dropping or cleaning up old per-worker test databases. Reusing them across rounds is fine and saves setup time.

## Why
`factory/config.json` lists `factory/checks.sh` under `protected_paths`. `factory/bin/gate` (`protected_changes`) marks any branch that modifies it as FAIL, and `run_checks` always runs the main checkout's `checks.sh`. So the factory can't land or even exercise a `checks.sh` change. The test-isolation work is what actually needs building and reviewing, though. `tests/conftest.py` already says true parallelism "would need a separate schema/DB per pytest-xdist worker", and `TEST_POSTGRES_DB` gives a natural base name to extend per worker. Once that's in, the protected change left for you is only a few lines.
