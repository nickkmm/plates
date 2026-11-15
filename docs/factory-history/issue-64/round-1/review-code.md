VERDICT: PASS

## Summary
- Migration 0016 and init.sql give the same end schema (column, index, events table, index). The backfill is `delivered`, and the default then becomes `pending`.
- The new data layer `app/core/db/production.py` matches the spec: constants, the two dataclasses, the shared fully-voided fragment, the conditional update that writes an event only when one row changed, and the batch lookup.
- `app/core/production.py` raises the exact messages in the spec's order of checks. It has no Flask import, no roles and no commits.
- Exports, tests, docs and `_APP_TABLES` are covered. checks.md passed.

## Findings
None.

## Notes
- Revert from `delivered` to `ready` is allowed. The spec lists this as an assumption for the approver to confirm.
