STATUS: READY

# Build: Parallel-safe test suite with one test database per xdist worker

## Summary
- Under `pytest -n auto` each xdist worker now uses its own test database `<base>_gwN` (e.g. `plates_test_12_gw0`). Plain `pytest` still runs serially against `<base>`.
- `pytest-xdist==3.8.0` is added to the dev requirements. `addopts` is unchanged.
- Full suite on 12 workers: 1739 passed in 127 s (warm), against 1739 passed in 1109 s serial. The proposed parallel `checks.sh` takes 137 s end to end, against 782 s today.
- The proposed `factory/checks.sh` diff is below and in draft PR #98. It isn't applied, because `factory/checks.sh` is protected.

## Changes
- `tests/conftest.py`:
  - New pure helpers. `_check_test_db_name` is the existing guard: the name starts with `plates_test` and is alphanumeric once `_` is removed. `_test_db_name(base, worker)` returns `base`, or `f"{base}_{worker}"` when `PYTEST_XDIST_WORKER` is set. Both the base and the final name are checked, and a bad one raises `RuntimeError`.
  - `_TEST_DB` and `os.environ["POSTGRES_DB"]` are still set at import time, before any `from app...` import.
  - `_create_database_if_missing` now suppresses `psycopg.errors.DuplicateDatabase` around `CREATE DATABASE`, so two sessions can create the same database at once.
  - The `clean_db` docstring no longer says parallel runs are out of scope.
- `tests/test_conftest_db_name.py` (new): 15 unit tests for `_test_db_name`, none of which need Postgres. They cover:
  - no worker, with the default and a custom base
  - a worker, with the default and a custom base
  - invalid bases, with and without a worker
  - worker ids that break the guard (`gw-0`, a quote/SQL injection, a space, a dot)
- `requirements-dev.txt`: `pytest-xdist==3.8.0`. It installs alongside `pytest==8.4.2` and pulls in `execnet 2.1.2`.
- `docs/LOCAL_DEVELOPMENT.md` (Spanish): the `TEST_POSTGRES_DB` bullet describes the per-worker `<base>_gwN` databases, which are created and migrated on first use and reused afterwards. The `clean_db` bullet no longer says parallel pytest is unsupported.
- `AGENTS.md`, "Setup for a fresh checkout": one sentence about `pytest -n auto` and `<TEST_POSTGRES_DB>_gwN`.

Shared-state audit of `tests/`. Nothing needed fixing:
- **Files.** Every file write goes through `tmp_path`. The upload tests point `UPLOADS_DIR` at a `tmp_path` folder via `monkeypatch`, and the PDF tests write to `tmp_path`.
- **Ports.** No test uses a fixed temp path, a fixed port or a socket.
- **App and env state.** `tests/web/conftest.py::client` restores `flask_app.config`, and env changes go through `monkeypatch`.
- **Databases.** Only conftest creates a database or role. The DB state (truncating in `clean_db`, the throwaway schemas in `test_schema_migrate.py`, DB-backed rate limiting) is isolated by the per-worker database.
- **Order.** The full suite passed on three runs with xdist's distribution: the warm-up, the timed run and the proposed `checks.sh`.
- **`app/`.** No changes.

## Acceptance criteria
1. [x] `requirements-dev.txt` has `pytest-xdist==3.8.0`. `.venv/Scripts/python -m pip install -r requirements-dev.txt` succeeded in a fresh `.venv` in the worktree.
2. [x] `git diff origin/main --name-only` lists only `AGENTS.md`, `docs/LOCAL_DEVELOPMENT.md`, `requirements-dev.txt`, `tests/conftest.py` and `tests/test_conftest_db_name.py`.
3. [x] `addopts` in `pyproject.toml` is untouched (`-ra --strict-markers`): no `-n` and no other xdist option.
4. [x] `TEST_POSTGRES_DB=plates_test_12 TEST_REQUIRE_DB=1 .venv/Scripts/python -m pytest -q -p no:cacheprovider -n auto` gave 1739 passed, 0 skipped. The serial run with the same env vars gave the same: 1739 passed, 0 skipped.
5. [x] After that run, `SELECT datname FROM pg_database WHERE datname LIKE 'plates_test_12%'` lists `plates_test_12` (from the serial run) and `plates_test_12_gw0` through `plates_test_12_gw11` (12 workers).
6. [x] A second `-n auto` run, with the worker databases already in place, also passed: 1739 passed in 125.78 s. A third run, inside the proposed `checks.sh`, passed too.
7. [x] The same command with `POSTGRES_PASSWORD=wrong` gave `2 failed, 614 passed, 1123 errors`, 0 skipped: in every worker, `clean_db` turns the unreachable database into `pytest.fail`. I didn't stop Postgres because other jobs share it, so I tested the wrong-password case, which the criterion also allows.
8. [x] `TEST_POSTGRES_DB=plates .venv/Scripts/python -m pytest -q -n 2` stopped at conftest import with `RuntimeError: TEST_POSTGRES_DB must be plates_test or plates_test_<suffix>, got 'plates'` (exit 4) and ran no tests.
9. [x] `env -u POSTGRES_PASSWORD .venv/Scripts/python -m pytest -q -p no:cacheprovider tests/test_conftest_db_name.py`: 15 passed in 0.03 s. The worktree has no `.env`, so no password was available.
10. [x] `docs/LOCAL_DEVELOPMENT.md` no longer says parallel pytest is unsupported, and it describes the `<base>_gwN` databases in Spanish. `AGENTS.md` mentions `pytest -n auto` and `<TEST_POSTGRES_DB>_gwN`.
11. [x] The timings, counts, worker count and the proposed diff are below.

### Timings
Measured on the worktree against the local Postgres (127.0.0.1:5433), with `TEST_POSTGRES_DB=plates_test_12 TEST_REQUIRE_DB=1`. The machine has 12 CPUs (Windows), so `-n auto` started 12 workers.

| Run | Result | Wall clock |
|---|---|---|
| serial `python -m pytest -q -p no:cacheprovider` | 1739 passed, 0 skipped | 1108.69 s (18:28) |
| `-n auto`, first run (creates and migrates 12 DBs) | 1739 passed, 0 skipped | 214 s |
| `-n auto`, warm (DBs already exist) | 1739 passed, 0 skipped | 127 s (pytest: 125.78 s) |
| current `factory/checks.sh` (serial) | exit 0, 1739 passed | 782 s (pytest 763.32 s) |
| proposed `factory/checks.sh` (concurrent, `-n auto`) | exit 0, 1739 passed | 137 s (pytest 135.61 s) |

### Proposed `factory/checks.sh` diff (not applied: protected path)
The diff makes three changes:
- pytest runs with `-n auto`.
- The dev-dependency check imports `ruff, mypy, pytest, xdist`.
- ruff, mypy, pytest and the two script checks run concurrently, each writing to its own file, with each exit status collected separately. The outputs are then printed in order under the existing `== <step>` headers. A failing step prints `== <step> FAILED (exit N)` and a final `Failed: ...` line, and the script exits 1.

I checked the failure path in a throwaway repo with a ruff error and no tests. It printed `== Lint (ruff) FAILED (exit 1)`, `== Tests (pytest) FAILED (exit 5)` and `Failed: Lint (ruff) Tests (pytest)`, and exited 1. The other steps still printed under their own headers.

```diff
--- a/factory/checks.sh
+++ b/factory/checks.sh
@@ -33,28 +33,49 @@ fi
 echo "== Dev dependencies"
 # Installed by the setup in AGENTS.md (a .venv in the worktree); never here,
 # so the gate never touches a global Python.
-if ! "$PY" -c 'import ruff, mypy, pytest' >/dev/null 2>&1; then
-  echo "ruff/mypy/pytest missing for $PY: run the AGENTS.md setup in this checkout" >&2
+if ! "$PY" -c 'import ruff, mypy, pytest, xdist' >/dev/null 2>&1; then
+  echo "ruff/mypy/pytest/pytest-xdist missing for $PY: run the AGENTS.md setup in this checkout" >&2
   exit 1
 fi
 
-echo "== Lint (ruff)"
-"$PY" -m ruff check app/
+check_scripts() {
+  for f in scripts/*.sh factory/checks.sh; do bash -n "$f"; done
+}
 
-echo "== Type-check (mypy)"
-"$PY" -m mypy app/
-
-echo "== Tests (pytest)"
-"$PY" -m pytest -q -p no:cacheprovider
+check_factory() {
+  # compile() checks the syntax without writing __pycache__ into the worktree
+  for f in factory/bin/*; do [ -f "$f" ] || continue; "$PY" -c 'import sys; compile(open(sys.argv[1]).read(), sys.argv[1], "exec")' "$f"; done
+  "$PY" factory/bin/sync-agents --check
+  if ! cmp -s .agents/skills/factory/SKILL.md .claude/skills/factory/SKILL.md; then
+    echo ".claude/skills/factory/SKILL.md must be a copy of .agents/skills/factory/SKILL.md" >&2
+    return 1
+  fi
+}
 
-echo "== Check shell scripts"
-for f in scripts/*.sh factory/checks.sh; do bash -n "$f"; done
+# The steps run at the same time, each into its own output file; their
+# output is then printed in order under its own header, and every failing
+# step is named, so checks.log still shows which step broke.
+OUT="$(mktemp -d)"
+trap 'rm -rf "$OUT"' EXIT
+STEPS=("Lint (ruff)" "Type-check (mypy)" "Tests (pytest)" "Check shell scripts" "Factory scripts")
+("$PY" -m ruff check app/) >"$OUT/0" 2>&1 & PIDS=($!)
+("$PY" -m mypy app/) >"$OUT/1" 2>&1 & PIDS+=($!)
+("$PY" -m pytest -q -p no:cacheprovider -n auto) >"$OUT/2" 2>&1 & PIDS+=($!)
+(set -e; check_scripts) >"$OUT/3" 2>&1 & PIDS+=($!)
+(set -e; check_factory) >"$OUT/4" 2>&1 & PIDS+=($!)
 
-echo "== Factory scripts"
-# compile() checks the syntax without writing __pycache__ into the worktree
-for f in factory/bin/*; do [ -f "$f" ] || continue; "$PY" -c 'import sys; compile(open(sys.argv[1]).read(), sys.argv[1], "exec")' "$f"; done
-"$PY" factory/bin/sync-agents --check
-if ! cmp -s .agents/skills/factory/SKILL.md .claude/skills/factory/SKILL.md; then
-  echo ".claude/skills/factory/SKILL.md must be a copy of .agents/skills/factory/SKILL.md" >&2
+FAILED=()
+for i in "${!STEPS[@]}"; do
+  status=0
+  wait "${PIDS[$i]}" || status=$?
+  echo "== ${STEPS[$i]}"
+  cat "$OUT/$i"
+  if [ "$status" -ne 0 ]; then
+    echo "== ${STEPS[$i]} FAILED (exit $status)" >&2
+    FAILED+=("${STEPS[$i]}")
+  fi
+done
+if [ "${#FAILED[@]}" -ne 0 ]; then
+  echo "Failed: ${FAILED[*]}" >&2
   exit 1
 fi
```

## Validation
- `bash factory/checks.sh` from the worktree, with the gate's env (`TEST_POSTGRES_DB=plates_test_12 TEST_REQUIRE_DB=1`, credentials from the main checkout's `.env`): **passed**, exit 0. ruff and mypy were clean, and pytest gave 1739 passed.
- `ruff check tests/conftest.py tests/test_conftest_db_name.py`: passed. `ruff format --diff` on the new test file: clean.
- The serial run, the two `-n auto` runs, the wrong-password run, the invalid-base run and the no-DB helper tests: results under Acceptance criteria.
- The proposed `checks.sh`: exit 0 on the worktree, and exit 1 with the failing steps named in a throwaway failing repo, as expected.

## Log
### Round 1
- Set up `.venv`, installed `requirements-dev.txt` and pinned `pytest-xdist==3.8.0`.
- Added `_test_db_name` and the `DuplicateDatabase` tolerance to `tests/conftest.py`, plus the new helper tests.
- Updated the docs, `AGENTS.md` and the `clean_db` docstring.
- Audited `tests/` for shared state outside Postgres and found nothing that needed fixing.
- Measured the serial and `-n auto` runs. Drafted and tested the `checks.sh` diff in the scratchpad, not in the repo.
- Committed in two commits, pushed, and opened draft PR #98 (`Closes #97`, with the timings and the proposed diff).
