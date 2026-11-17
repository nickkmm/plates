VERDICT: PASS

## Summary
- `advance` now calls `cleanup_test_dbs` for every final stage, next to the worktree and branch removal. Every message goes to stderr and every exception is caught, so the transition still saves.
- `job_db_names` does an exact full match on `plates_test_<n>(_gw<digits>)?` and re-applies the conftest rule. The listed names, including job 1 versus `plates_test_12`, `plates_test_1x` and `plates_test_10_gw0`, behave as the spec says.
- The psycopg path is in-process when psycopg imports. Otherwise it runs `job drop-dbs` under the main checkout's `.venv` with a 60 s timeout. `drop-dbs` takes only numbers and never loads jobs, so it can't deadlock on the job lock.
- `cleanup-dbs` exits 1 with a one-line note when it can't connect, and prints one stdout line per final job. The UTF-8 re-exec is guarded by `__name__ == "__main__"`. The usage text and AGENTS.md are updated.
- The tests cover matching, Postgres down, no password, the `cleanup-dbs` exit code and the `advance(..., "rejected")` transition. They never touch a live database. I did not re-run them, as checks already passed.

## Findings
None.

## Notes
- If two job folders ever shared a number, `cmd_cleanup_dbs` would pass duplicate numbers. That is harmless, because the second drop is `IF EXISTS`.
- A failed `DROP` in `cleanup-dbs` makes it exit 1 after printing the per-job lines. That is reasonable and matches "couldn't clean up".
