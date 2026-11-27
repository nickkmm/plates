VERDICT: PASS

## Summary
- The eight files moved with git mv and show as renames. Login, session check, permission_required, users and roles admin, the permission catalog and the users/roles SQL now live in app/auth/.
- app/auth/repository/ is a package with explicit re-exports. app/core/db no longer exports users or roles. Callers use repository.<fn> and service.<fn>, so monkeypatches reach the code that calls them.
- The mypy strict override, the coverage omit and the hash_password command are updated. The docs and docstrings point at the new paths.
- checks.md is green: 1844 tests, the same count as origin/main. The spec's old-import and old-path rg checks (criteria 3 and 4) find nothing.
- Two spec items are left for a human because agents may not make them: the CLAUDE.md bullet and the web.Dockerfile comment (see Notes).

## Findings
None.

## Notes
- CLAUDE.md has no app/auth/ bullet yet (Requirement 10, criterion 13). Agents may not edit CLAUDE.md, so the human merging the PR should add one line under "Project", for example: "- `app/auth/`: login, session check and `permission_required`, users and roles admin, their repository (`app/auth/repository/`)."
- web.Dockerfile:21 still says "auth_routes.py/ingest.py/records.py". It is a comment only. Suggested change: "app/auth/routes.py and the ingest/records blueprints".
- app/auth/decorators.py has some docstring lines longer than the neighbouring code after the path substitution. This is cosmetic.
