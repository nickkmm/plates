VERDICT: PASS

## Summary
- The six declaration modules were moved with git mv into app/declarations/ and app/core/ and app/web/blueprints/ are gone.
- Callers use app.shared.db for get_conn and declarations_repository for the SQL. The conftest patch now targets only app.shared.db.
- mypy strict list, setup.cfg, CLAUDE.md bullet and the docs (CORE.md renamed to DOMAINS.md) match the spec.
- TestGetConnReExport is replaced by a same-count test, and the test count stays 1844. My greps for app.core and app/core found no hits.

## Findings
None.

## Notes
- The only remaining hits of the old paths are in the docs/MIGRATION_PLAN.md mapping table, which the spec says not to edit.
- The replacement test checks the filesystem rather than find_spec, to satisfy the no-"app.core" grep. That is fine.
