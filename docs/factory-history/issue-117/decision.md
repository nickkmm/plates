ESCALATE

## Summary
- Issue #117 is the last step of phase 1. It moves the declaration screens, the public form, anti-spam, the service, the PDF and the SQL into `app/declarations/` with `git mv`, and deletes `app/core/` and the empty `app/web/blueprints/`.
- Behavior should not change: the `url_map` dump is byte-identical to `origin/main`, the test count stays at 1844 and all of them pass, and mypy strict now covers the four new non-route modules.
- Round 1 passed the gate and both reviews (code and security) found nothing. Docs and the one CLAUDE.md bullet were updated, and `docs/CORE.md` is now `docs/DOMAINS.md`.
- The PR changes no migration, `init.sql`, template, dependency, CI, Docker or `app/auth` file.
- It is escalated because a person always merges here, and because it touches the public declaration form (Turnstile, personal data), which this project wants checked by a person.

## Why
Every check the factory runs holds: all acceptance criteria are met, the gate and reviews pass, and the change stays inside intake's Scope. The project rules still ask for a person's check before merging, because the change edits the public declaration form's module (`app/declarations/public_routes.py`, which holds the Turnstile check, personal data and the data-policy authorization) and its anti-spam. No one has run the logged-in staff flow (approve, then `/pdf`) by hand against this branch.

## Acceptance criteria
1. Old paths gone, package lists exactly 7 files: met. I re-ran `test ! -e ...` (OK) and `git ls-files app/declarations` myself.
2. No `app.core`/`app/core` left outside history: met. I re-ran the rg: exit 1, no matches.
3. No old blueprint/antispam/CORE.md paths: met, with the one exception the spec forces. My re-run matches only the `docs/MIGRATION_PLAN.md` "Where today's code goes" table (lines 131-138), which Requirement 9 and Out of scope say not to edit.
4. `app/declarations/__init__.py` has no imports: met (build.md, rg exit 1).
5. All 12 modules import in a fresh process: met (build.md).
6. `url_map` identical to `origin/main`: met (build.md, byte comparison against a `git archive` export).
7. Renames shown and no diff to templates, migrations, `init.sql`, `csrf.py`, `helpers.py`, `navigation.py`: met. build.md lists the R-similarities, and my own name-only diff over migrations, `init.sql`, templates, auth, CI, Docker and requirements is empty.
8. Strict override lists the four `app.declarations` modules and mypy passes: met. I read the `pyproject.toml` diff, and checks.md shows mypy at "Success: no issues found in 61 source files".
9. Same collected count and no tests deleted or weakened: met. Both counts are 1844, and the only replaced test is `TestGetConnReExport`, which the spec allows.
10. The listed test files pass against `plates_test_22`: met (the gate run in checks.md passed all 1844).
11. Docs mention `app/declarations`, `docs/README.md` links `DOMAINS.md`, phase 1 is marked done: met (build.md counts).
12. CLAUDE.md changes only the `app/core/` bullet, and `setup.cfg` only `source_paths`: met. I read both diffs myself.
13. Manual page check: partially met. Curl checks passed (`/declaracion` 200 with the form rendered; staff pages redirect to login). The logged-in approve and `/pdf` flow was not run by hand; the spec lets the tests from criterion 10 stand in for it.

## For the human
Recommendation: don't merge yet

The code looks right to me and I found no problem. It only moves files and rewrites import lines and call prefixes: in the public-form diff, the only code change is `db.count_recent_by_ip`, now called as `declarations_repository.count_recent_by_ip`, and `antispam.py` is an exact copy. Still, this project asks a person to check any change that touches the public declaration form before merging, and merging to `main` deploys to production.

The check I need from you, run locally against this branch (`factory/bin/local-env`):
- Submit `/declaracion` once with Turnstile active (not bypassed), with photos and a signature. Confirm the data-policy authorization is recorded and the per-IP rate limit still applies.
- Logged in as admin: open `/declaraciones`, open a declaration's detail page, approve it, download its `/pdf`, then open the matching `/produccion` detail page.
- On production after the deploy: the gunicorn import path changes (`app.web.app` now imports from `app.declarations`), so confirm the app starts and `/healthz` answers.

Files to look at:
- `app/declarations/public_routes.py` (the public form: CSRF exemption, Turnstile, rate limit)
- `app/declarations/routes.py` (the staff screens; it has the largest diff of the moved files, about 90% similar to the original)
- `app/web/app.py`, `app/production/routes.py`, `app/orders/routes/ingest.py` (the callers)
- `tests/conftest.py` (it now patches `get_conn` only on `app.shared.db`)
- `tests/core/db/test_connection.py` (`TestGetConnReExport` is replaced by `TestNoCorePackage`)
- `CLAUDE.md` (the one bullet rewritten) and `docs/DOMAINS.md` (renamed from `docs/CORE.md`)

Follow-ups the spec names, which the PR description should list: the `app/web/` bullet in CLAUDE.md still mentions `app/web/blueprints/`, and the `declarations` <-> `production` two-way import is left for phase 4.
