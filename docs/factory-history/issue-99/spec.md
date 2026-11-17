# Drop a finished job's test databases

> Issue #99: Drop a job's test databases when the job finishes

## Summary
- When a job reaches `merged` or `rejected`, `factory/bin/job` drops that job's `plates_test_<n>` and `plates_test_<n>_gw<digits>` databases, next to where it already removes the worktree and branch.
- Matching is exact against real names from `pg_database` and uses the same name rule as `tests/conftest.py`, so `plates`, `plates_test` and other jobs' databases are never touched.
- Cleanup is best effort. If Postgres is down, credentials are missing or psycopg can't be found, the transition still saves and a one-line note goes to stderr.
- A new `job cleanup-dbs` command drops the databases of every job that is already finished.
- New pytest tests cover name matching and the "Postgres down" path without a live database. AGENTS.md documents the command.

## Context
- `factory/bin/job` is the only writer of `factory/jobs/<id>/job.json`. `advance(job, stage)` handles every stage change. It already has a block for final stages that removes the worktree and deletes the branch: `if stage in TERMINAL and Path(job["worktree"]).exists(): ...` (`TERMINAL = {"merged", "rejected"}`). Every path to a final stage goes through `advance`:
  - `main` → `advance` (for example `job advance <id> rejected` from intake)
  - `cmd_merge`, which saves `advance(..., "merged")`
  - `cmd_reject`
  - `cmd_queue`, which auto-detects a PR merged on GitHub and saves `advance(sync(j), "merged")`

  Callers save the job after `advance` returns.
- `cmd_queue` prints tab-separated rows on **stdout**, and the `/factory` skill parses them (`.agents/skills/factory/SKILL.md`). Anything the cleanup prints during `queue` must go to stderr.
- `factory/bin/gate`:
  - `db_env(job_id)` reads the Postgres credentials from the environment, or from `POSTGRES_USER`/`POSTGRES_PASSWORD` in `REPO/.env`.
  - It checks reachability with `socket.create_connection((DB_HOST, DB_PORT), timeout=3)`, where `DB_HOST, DB_PORT = "127.0.0.1", 5433`.
  - It names the job database `plates_test_{int(job_id.split('-', 1)[0])}`.

  `job` should follow the same sources, host, port, timeout and job-number rule.
- `tests/conftest.py`:
  - `_check_test_db_name(name, what)` accepts a name only if it starts with `plates_test` and `name.replace("_", "").isalnum()`.
  - `_test_db_name` adds `_<worker>` (`gw0`, `gw1`, ...) under pytest-xdist.
  - `_create_database_if_missing` shows the psycopg pattern: connect to the `postgres` maintenance DB with `autocommit=True`.
  - `POSTGRES_USER` defaults to `plates_user`.
- Python environments: the `job` script runs under whatever Python the orchestrator uses. On this machine that is the system Python, which does **not** have psycopg. The main checkout's `.venv` (`.venv/Scripts/python` on Windows, `.venv/bin/python` elsewhere, created by the AGENTS.md setup) does have it, because `requirements.txt` pins `psycopg[binary,pool]`.
- The top of `factory/bin/job` has a Windows UTF-8 re-exec, `if sys.platform == "win32" and not sys.flags.utf8_mode: sys.exit(subprocess.call([sys.executable, "-X", "utf8", *sys.argv]))`. It runs on import, so pytest can't import the script on Windows today: it would re-launch pytest's own argv.
- Tests: pytest with `testpaths = ["tests"]` (`pyproject.toml`). `tests/test_conftest_db_name.py` is an example of pure name-logic tests. `factory/checks.sh` runs pytest, checks that every `factory/bin/*` file compiles, and lints and type-checks only `app/`.
- Docs: `AGENTS.md`, under "Setup for a fresh checkout (or a factory worktree)", explains `TEST_POSTGRES_DB=plates_test_<n>` and the `_gwN` databases. The usage docstring at the top of `factory/bin/job` lists every subcommand.

## Requirements
1. **Name matching.** Add a pure function to `factory/bin/job` that takes a job number `n` and an iterable of existing database names, and returns the names to drop. A name is kept only if:
   - it is exactly `plates_test_<n>` or fully matches `plates_test_<n>_gw<one or more digits>`, where `<n>` is the decimal job number with no leading zeros, and
   - it also passes the same rule as `_check_test_db_name` (re-implemented in `job`, since `job` can't import conftest).

   This function must never return `plates`, `plates_test`, `postgres`, or another job's names: job 1 never matches `plates_test_12`, `plates_test_1x`, `plates_test_1_gw` or `plates_test_1_gwx`.
2. **Job number.** Compute the job number like `gate`: `int(job_id.split("-", 1)[0])`.
3. **Dropping.**
   - Credentials come from the same sources as `gate.db_env`: the environment first, then `POSTGRES_USER`/`POSTGRES_PASSWORD` from the main checkout's `.env`. `POSTGRES_USER` defaults to `plates_user`.
   - Check that Postgres is reachable at `127.0.0.1:5433` with a 3-second socket timeout.
   - Connect to the `postgres` database with psycopg using `autocommit=True` and a short `connect_timeout`.
   - Read `SELECT datname FROM pg_database`, filter the names with requirement 1, and run `DROP DATABASE IF EXISTS` on each match. Quote identifiers with `psycopg.sql.Identifier`; never interpolate unchecked text into SQL.
   - Return or report which names were dropped and which drops failed, with the error.
4. **psycopg availability.**
   - If `import psycopg` works in the running Python, do the drop in-process.
   - Otherwise, run the drop step with the main checkout's `.venv` Python (`.venv/Scripts/python` or `.venv/bin/python`, whichever exists) as a subprocess. It calls `job` with an internal subcommand, `job drop-dbs <n> [<n>...]`, and has a timeout of about 60 s.
   - That subcommand only takes job numbers. It must not call `load`, `all_jobs` or `resolve`, because the parent process holds the job's lock and the child would deadlock.
   - If neither Python has psycopg, that is a "couldn't clean up" case under requirement 6.
5. **Drop on finish.** In `advance`, in the existing final-stage block (where the worktree and branch are removed), drop the job's test databases whenever the new stage is in `TERMINAL`. This covers `advance`, `merge`, `reject` and the auto-merge in `queue`. Nothing is dropped for any other stage, so a running job keeps reusing its databases across rounds.
6. **Best effort.** The transition must complete and the job must be saved whatever happens with the cleanup:
   - no password
   - Postgres unreachable
   - psycopg unavailable
   - a connection error
   - a failed `DROP` (for example, a session is still connected)
   - a subprocess timeout

   Catch every exception from the cleanup. In each case, print one line to **stderr** that starts with `job: ` and says the test databases of job `<n>` weren't cleaned up and why. When databases were dropped, print one line to stderr naming them. Nothing from the cleanup goes to stdout, so `job queue` output stays parseable.
7. **Backfill command.** Add `job cleanup-dbs` with no arguments.
   - It drops the test databases of every job in `factory/jobs/` whose stage is `merged` or `rejected`. Jobs in any other stage are skipped and their databases are left alone.
   - Its output goes to stdout: one line per final-stage job, saying either what was dropped or "nothing to drop".
   - It exits 0 when the cleanup ran. When it couldn't connect (any requirement 6 reason), it exits 1 with the one-line reason on stderr.
   - Running it twice is safe; the second run reports nothing to drop.
8. **Make `job` importable for tests.** Change only the UTF-8 re-exec guard at the top of `factory/bin/job` so that it runs only when the file is executed as a script (`__name__ == "__main__"`). Running the script directly must behave exactly as before.
9. **Usage text.** Add `cleanup-dbs` to the usage docstring of `factory/bin/job`. Also add `drop-dbs`, marked as internal (used by `job` itself).
10. **AGENTS.md.** Next to the existing `plates_test_<n>` / `_gwN` paragraph, add one or two English sentences (this is developer docs, like the surrounding text) covering:
    - that a job's test databases are dropped when it reaches `merged` or `rejected`
    - that `factory/bin/job cleanup-dbs` drops those of jobs that finished earlier
    - that dropping needs psycopg, either in the running Python or in the main checkout's `.venv`
11. **Tests.** Add `tests/test_factory_test_dbs.py`. It loads `factory/bin/job` as a module (via `importlib.machinery.SourceFileLoader` plus `importlib.util`, since the file has no `.py` extension). It needs no live Postgres and must never connect to one: point the port at a closed one, or remove the credentials with `monkeypatch`. It covers:
    - exact-number matching, including job 1 versus `plates_test_12`, `plates_test_1x` and `plates_test_10_gw0`
    - the `_gw<digits>` variants, both kept and rejected (`_gw`, `_gwx`, `_gw0x`)
    - refusal of `plates`, `plates_test`, `postgres`, and names that break the conftest rule
    - Postgres unreachable or no password: the cleanup returns or prints a note and doesn't raise
    - a transition to a final stage completes when Postgres is down. Point the module's `JOBS` at `tmp_path` and stub `gh`/`comment`/`label` with `monkeypatch`. Then `advance(job, "rejected")` on an intake job whose `intake.md` says `DECISION: REJECT` and whose worktree doesn't exist returns the job with `stage == "rejected"`, and a `job: ` note appears on stderr (`capsys`).

## Acceptance criteria
1. `pytest tests/test_factory_test_dbs.py -q` passes without Postgres running and without `POSTGRES_PASSWORD` set.
2. In `tests/test_factory_test_dbs.py`, the matching function, given `["plates", "plates_test", "postgres", "plates_test_1", "plates_test_1_gw0", "plates_test_1_gw11", "plates_test_12", "plates_test_1x", "plates_test_1_gwx", "plates_test_10_gw0"]` and job number 1, returns exactly `plates_test_1`, `plates_test_1_gw0` and `plates_test_1_gw11`. Check by reading the test and running it.
3. `python factory/bin/job --help` lists `cleanup-dbs` and an internal `drop-dbs`.
4. With Postgres stopped (or `POSTGRES_PASSWORD` unset and no `.env`), `python factory/bin/job cleanup-dbs` exits 1 within a few seconds and prints one `job: ` line on stderr giving the reason.
5. With the local Postgres up (`docker compose up -d db`), take a finished job, for example a `merged` job in `factory/jobs/` with number `<n>`. Running `CREATE DATABASE plates_test_<n>` and `CREATE DATABASE plates_test_<n>_gw0` by hand, then `python factory/bin/job cleanup-dbs`, prints both names as dropped, and `SELECT datname FROM pg_database` no longer lists them. `plates` and `plates_test` are still present. Running the command again reports nothing to drop.
6. In the same setup, a database `plates_test_<m>` for a job `<m>` in a non-final stage (for example `build`) is still present after `cleanup-dbs`.
7. `factory/bin/job` contains the drop call inside `advance`'s `TERMINAL` handling, and every cleanup message there is written to `sys.stderr`. Check with `grep -n "TERMINAL" factory/bin/job` and by reading the block.
8. The UTF-8 re-exec at the top of `factory/bin/job` is guarded by `__name__ == "__main__"`. Check with `sed -n 1,45p factory/bin/job`.
9. `AGENTS.md` mentions `cleanup-dbs` next to the `plates_test_<n>` note: `grep -n "cleanup-dbs" AGENTS.md`.
10. `git diff --name-only origin/main...HEAD` lists neither `factory/checks.sh` nor `factory/config.json`.

## Out of scope
- Sweeping orphan `plates_test_<n>*` databases whose job folder was deleted (for example, a rejected job removed to retry). Intake left this for a possible follow-up.
- Any change to how `gate` or `tests/conftest.py` create or name databases. Do not refactor `gate.db_env` into a shared module.
- Dropping the bare `plates_test` developer database, `plates`, or any database that isn't a job's.
- Forcing drops (`WITH (FORCE)`, terminating backends) or retrying failed drops.
- A `cleanup-dbs` that targets one job, or flags such as `--dry-run`.
- Changing `factory/checks.sh`, `factory/config.json`, the `/factory` skill, the dashboard or any app code.
- Installing psycopg into the system Python, or making `job` re-exec itself wholesale under the `.venv` Python.

## Assumptions
- **Plain `DROP DATABASE IF EXISTS`, not `WITH (FORCE)`.** Intake treats a still-connected session as a reportable failure, and FORCE would kill sessions it shouldn't.
- **Fall back to the main checkout's `.venv` Python when the running Python lacks psycopg.** On this machine the system Python that runs `job` has no psycopg, so without this the feature would never work.
- **Reuse `job` with an internal `drop-dbs` subcommand instead of adding a new script.** It keeps the logic and its tests in one file.
- **Cleanup notes go to stderr.** `job queue` stdout is parsed by the orchestrator, and auto-merge cleanup runs inside `queue`.
- **`cleanup-dbs` exits 1 when it can't connect.** Unlike a transition, it is an explicit request, so failing loudly is more useful. It exits 0 otherwise, including when there was nothing to drop.
- **AGENTS.md text is in English.** The surrounding setup section is developer docs written in English, so it isn't Spanish `docs/*.md` prose.
- **Guard the UTF-8 re-exec with `__name__ == "__main__"`.** It is the smallest change that lets pytest import `job` on Windows, and it doesn't change how the script behaves when run directly.
