DECISION: ACCEPT
RISK: high
SURFACES: security
RESCOPED: no

## Summary
- Accepted as written: a backend-only base for JWT access tokens and rotating refresh tokens, with no endpoint or page using them yet.
- The issue spells out the schema, the module APIs, the lifetimes, the rotation and reuse rules, and the tests, and it matches docs/MIGRATION_PLAN.md ("Decisions" and "Phase 2").
- Its dependency #117 is merged, nothing token-related exists in `app/` yet, and the next free migration number is 0019.
- Risk is high because the change touches auth, a migration and row locking. It is still one contained PR.
- One deploy note: `JWT_SECRET` must be set on the VPS before this merges, because the app will refuse to start without it.

## Scope
Build what the issue specifies:
- Migration `0019_refresh_tokens.sql`, idempotent, creating `refresh_tokens` with the listed columns, the FK to `users` with `ON DELETE CASCADE`, `token_hash` unique, and indexes on `user_id` and `family_id`. The same schema goes in `init.sql` with its version row, plus `docs/DATABASE.md`, `TABLES` in `tests/conftest.py` and the schema tests.
- `app/shared/tokens.py` (pure, strictly typed): `issue_access_token` and `decode_access_token`, HS256 with PyJWT pinned in `requirements.txt`. It uses claims `sub` (user id as a string), `username`, `perms` (sorted), `iat` and `exp`, and raises a typed error on a bad signature or an expired token. Both functions take `now` explicitly. Lifetimes are constants in one place: 5 minutes for access, 30 days for refresh.
- `JWT_SECRET` is a dedicated secret, separate from `SECRET_KEY`. The app refuses to start without it, the same way it does for `SECRET_KEY`. Add it to `.env.example` with the command that generates it.
- A refresh-token service in `app/auth/` plus repository functions:
  - `issue` stores only the SHA-256 hex of a `secrets.token_urlsafe(32)` value and starts a new family.
  - `rotate` locks the row (`FOR UPDATE`). A valid token is revoked and replaced in the same family. A revoked token presented again revokes the whole family and returns `None`. An expired or unknown token returns `None`.
  - `revoke` and `revoke_user`.
- Tests cover every case the issue lists, including that the raw value is never stored.

Left out, as the issue says: the login, refresh, logout and me endpoints; any change to how pages authenticate; and the revocation hooks on user deactivation, role change or password change (these belong with the endpoints task).

## Why
The request is precise, matches the plan in `docs/MIGRATION_PLAN.md`, and fits one PR: one migration, one pure module, one small service and its tests. Today `app/auth/` has only session-based login (`service.py`, `repository/users.py`), so this duplicates nothing. Risk is high because it adds a migration and security-critical token code. A new required env var can also break the deploy on push to `main` if the VPS `.env` is not updated first. The PR description should say so prominently.
