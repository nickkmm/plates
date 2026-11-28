VERDICT: PASS

## Summary
- Adds the refresh_tokens table (migration 0019, init.sql, version row, schema tests) matching the spec's columns and indexes.
- Adds app/shared/tokens.py: pure HS256 access tokens, expiry checked against the passed `now`, PyJWT errors translated, JWT_SECRET read per call.
- Adds the refresh-token service and hash-only repository: FOR UPDATE lookup, same-family rotation, reuse revokes the family, expired tokens return None without revoking.
- Startup check, conftest secret, docs, .env.example and README are updated; nothing in web/routes uses the new code.
- Tests cover every case in Requirement 10; checks already passed.

## Findings
None.

## Notes
- app/shared/tokens.py: `sub.isdigit()` is true for characters like "²", where `int()` raises ValueError. Only reachable with a token signed by us, so harmless; `isdecimal()` would be tighter.
- The PR must have JWT_SECRET added to ENV_FILE_B64 before merge (build.md says PR #197 states this).
