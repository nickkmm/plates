ESCALATE

## Summary
- Intake cut issue #97 down to making the test suite safe to run in parallel. `factory/checks.sh` is protected, so the branch leaves it alone and the build report proposes a diff for you to apply by hand.
- `tests/conftest.py` gives each pytest-xdist worker its own database (`<base>_gwN`), applies the existing `plates_test` guard to both the base and the final name, and treats a `DuplicateDatabase` error from a concurrent create as success. Plain `pytest` behaves as before.
- Round 1: the gate passed (1739 passed, serial) and the code review passed with no findings. The builder reports 1739 passed under `-n auto` in 127 s, against 1109 s serial.
- Escalated because every decision in this project goes to a person, and because the change adds a dependency (`pytest-xdist==3.8.0`, dev-only).

## Why
Every acceptance criterion is met with convincing evidence, the gate and the review both pass, and the diff stays inside intake's Scope: only `tests/`, `requirements-dev.txt`, `AGENTS.md` and `docs/LOCAL_DEVELOPMENT.md`. No `app/` code, migrations, deploy/CI config, auth or business logic is touched. The only formal trigger is the new dev dependency.

## Acceptance criteria
1. `pytest-xdist==<version>` pinned and installs: met. `requirements-dev.txt` has `pytest-xdist==3.8.0`, and the builder installed it in a fresh `.venv`. The worktree venv imports it (I ran it with `-n 2`).
2. No protected, gate or CI files changed: met. `git diff --stat` lists only the 5 files above, and checks.md says "Protected paths: none changed".
3. `addopts` has no xdist option: met. I checked: `addopts = "-ra --strict-markers"`.
4. `-n auto` passes with the same passed count and no extra skips: met per build.md (1739 passed, 0 skipped in both runs).
5. Per-worker databases exist: met per build.md (`plates_test_12_gw0` to `gw11`, plus `plates_test_12` from the serial run).
6. A second run with the databases already present passes: met per build.md (1739 passed in 125.78 s, plus a third run inside the proposed checks.sh).
7. An unreachable database fails tests instead of skipping them: met per build.md, using the wrong-password case (0 skipped, `clean_db` calls `pytest.fail`). The spec allows either the stopped-Postgres case or the wrong-password case, and the builder chose wrong password so it wouldn't stop the Postgres other jobs share.
8. `TEST_POSTGRES_DB=plates ... -n 2` raises at conftest import: met. I reproduced it and got `RuntimeError: TEST_POSTGRES_DB must be plates_test or plates_test_<suffix>, got 'plates'`.
9. The helper tests pass without Postgres: met. I reproduced it: `env -u POSTGRES_PASSWORD ... tests/test_conftest_db_name.py` gave 15 passed in 0.03 s.
10. Docs updated: met. The Spanish `docs/LOCAL_DEVELOPMENT.md` describes the `<base>_gwN` databases and drops the "parallel not supported" text. `AGENTS.md` adds the `pytest -n auto` sentence.
11. Timings, counts, worker count and the proposed checks.sh diff are in build.md: met.

## For the human
Recommendation: merge

- **Decision needed:** accept the new dev-only dependency `pytest-xdist==3.8.0`, which pulls in `execnet 2.1.2`. It is only in `requirements-dev.txt`, so neither production nor `web.Dockerfile` changes. None of this project's do-not-merge-yet triggers apply: no migration, no new deploy env var, no docker or compose change, nothing touching login, roles, the declaration form, prices or cuadre. The docs were updated to match the change.
- **Your follow-up after merge:** the speed-up only reaches the gate once you apply, by hand, the `factory/checks.sh` diff in `build.md` (section "Proposed `factory/checks.sh` diff"). The round-1 gate ran the old serial `checks.sh`, so the parallel path was only exercised by the builder: 3 passing runs, plus a failure-path test in a throwaway repo. Before applying it, run `pip install -r requirements-dev.txt` in the main checkout's `.venv`. Otherwise the new `import ruff, mypy, pytest, xdist` check fails every gate run. The concurrent ruff/mypy/script part of that diff is optional. If you want a smaller change, apply only `-n auto` and the import check.
- **Expect** 12 `plates_test_<n>_gwN` databases per job in the local Postgres. They are reused across runs and never dropped, as intake intended.
- **Files to look at:** `tests/conftest.py` (`_test_db_name`, `_check_test_db_name`, the `DuplicateDatabase` suppression in `_create_database_if_missing`), `tests/test_conftest_db_name.py`, `requirements-dev.txt`, and `factory/jobs/012-speed-up-the-factory/build.md` for the timings and the checks.sh diff.
