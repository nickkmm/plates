#!/usr/bin/env bash
# The checks for plates. Exit 0 only when the branch is fit to review.
#
# The gate runs this script with the job's worktree as the working directory.
# Same steps as .github/workflows/test.yml (ruff, mypy, pytest), plus a few
# repo rules. Tests that need Postgres run against plates_test on
# 127.0.0.1:5433 (tests/conftest.py). Run by hand without a reachable DB and
# POSTGRES_PASSWORD they skip; the gate passes the credentials, a per-job
# TEST_POSTGRES_DB and TEST_REQUIRE_DB=1, so there they must run.
set -euo pipefail

# A Python that really runs: on Windows `python3` can be the Microsoft Store
# stub, which exists on PATH but fails.
PY=""
for candidate in "${PYTHON:-}" .venv/bin/python .venv/Scripts/python python3 python; do
  if [ -n "$candidate" ] && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))' >/dev/null 2>&1; then
    PY="$candidate"
    break
  fi
done
if [ -z "$PY" ]; then
  echo "No Python 3.11+ found (set PYTHON=/path/to/python)" >&2
  exit 1
fi
echo "== Using $("$PY" --version) at $PY"

echo "== No secrets or real business data are tracked"
if git ls-files | grep -E '(^|/)\.env$|(^|/)\.env\.[^/]*$|\.pem$|(^|/)id_ed25519|_deploy(\.pub)?$|(^|/)dozzle_users\.yml$|^data/|\.xlsx?$' | grep -v '^\.env\.example$'; then
  echo "These files must never be committed (see .gitignore)" >&2
  exit 1
fi

echo "== Dev dependencies"
# Installed by the setup in AGENTS.md (a .venv in the worktree); never here,
# so the gate never touches a global Python.
if ! "$PY" -c 'import ruff, mypy, pytest' >/dev/null 2>&1; then
  echo "ruff/mypy/pytest missing for $PY: run the AGENTS.md setup in this checkout" >&2
  exit 1
fi

echo "== Lint (ruff)"
"$PY" -m ruff check app/

echo "== Type-check (mypy)"
"$PY" -m mypy app/

echo "== Tests (pytest)"
"$PY" -m pytest -q -p no:cacheprovider

echo "== Check shell scripts"
for f in scripts/*.sh factory/checks.sh; do bash -n "$f"; done

echo "== Factory scripts"
# compile() checks the syntax without writing __pycache__ into the worktree
for f in factory/bin/*; do [ -f "$f" ] || continue; "$PY" -c 'import sys; compile(open(sys.argv[1]).read(), sys.argv[1], "exec")' "$f"; done
"$PY" factory/bin/sync-agents --check
if ! cmp -s .agents/skills/factory/SKILL.md .claude/skills/factory/SKILL.md; then
  echo ".claude/skills/factory/SKILL.md must be a copy of .agents/skills/factory/SKILL.md" >&2
  exit 1
fi
