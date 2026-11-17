DECISION: ACCEPT
RISK: medium
SURFACES: none
RESCOPED: no

## Summary
- Accepted as written: drop a job's `plates_test_<n>` and `plates_test_<n>_gw*` databases when it reaches `merged` or `rejected`, plus a command to clean up jobs that already finished.
- This is factory tooling only. No app code, schema, migrations or staff-facing pages change.
- Risk is medium because it runs `DROP DATABASE`. The name matching has to be exact so it never touches `plates`, the shared `plates_test` or another job's databases.
- `factory/checks.sh` and `factory/config.json` stay unchanged, as the issue requires.

## Scope
Build:
- **Drop on finish.** Whenever a job moves to a final stage (`merged` or `rejected`, by any path in `factory/bin/job`: `advance`, `merge`, `reject`, or the auto-merge detection in `queue`), drop every test database of that job. That means `plates_test_<n>` and every `plates_test_<n>_gw<digits>` that exists, where `<n>` is the job number exactly as `factory/bin/gate` computes it (`int` of the id prefix, so `013-...` gives `plates_test_13`). List the real names from `pg_database` and keep only exact matches. Job 1 must never match `plates_test_12` or `plates_test_1x`.
- **Safety.** Every name dropped must pass the same rule as `_check_test_db_name` in `tests/conftest.py` (starts with `plates_test`, only alphanumerics and `_`) and must have a job-number suffix. The bare `plates_test` (the developers' database), `plates` and every other database are never dropped. Quote identifiers safely and never build SQL from unchecked text.
- **Active jobs are untouched.** Nothing is dropped for a job that is not in a final stage, so a running job's databases are reused across rounds as they are now.
- **Best effort.** If Postgres is unreachable, credentials are missing (same sources as `gate`'s `db_env`: the environment or the main checkout's `.env`), or a drop fails (for example because a session is still connected), the transition still completes and saves. The command prints a one-line note saying the cleanup didn't happen and why, and doesn't wait long (use a short connect timeout like `gate`).
- **Backfill command.** Add a subcommand (for example `job cleanup-dbs`) that drops the test databases of every job in `factory/jobs/` whose stage is final. It is safe to run repeatedly and prints what it dropped and what it skipped. Document it in the script's usage text and in `AGENTS.md` next to the existing `plates_test_<n>` note.
- **Tests** for the name-matching logic: exact-number matching, the `_gw*` variants, and refusal of `plates`, `plates_test` and other jobs' names. Also cover the "Postgres down doesn't block the transition" path. These tests must run without a live Postgres.

Left out:
- Databases of jobs whose folder was deleted (for example a rejected job removed to retry). They have no job record, so the backfill command doesn't know about them. A sweep of orphan `plates_test_<n>*` databases can be a follow-up if wanted.
- No change to how `gate` or `conftest.py` create the databases.

## Why
`factory/bin/gate` (`db_env`) gives each job `plates_test_<n>`, and since #97 `tests/conftest.py` (`_test_db_name`) adds one `_gwN` database per xdist worker. Nothing in `factory/bin/job` drops them when `advance` reaches `TERMINAL`, which already removes the worktree and branch at that point. So this is a small, contained addition to existing factory tooling with clear rules from the issue, and it fits one PR.
