VERDICT: PASS

## Summary
- Adds the `refresh_tokens` table, an HS256 JWT access-token module (`app/shared/tokens.py`) and a refresh-token service with rotation and family reuse detection. No route uses any of it yet.
- Refresh tokens are `secrets.token_urlsafe(32)`. Only their SHA-256 hash is stored and compared, and the raw value never reaches SQL or logs.
- JWT verification pins `algorithms=["HS256"]`, requires all five claims, type-checks them and turns every PyJWT error into typed errors. Expiry is checked against `now`.
- `JWT_SECRET` is a dedicated secret, never falls back to `SECRET_KEY`, and is enforced at startup. No secrets or business data are committed.
- All SQL is parameterized with psycopg placeholders, and rotation locks the row with `FOR UPDATE`.

## Findings
None.

## Notes
- Deploy risk, not a vulnerability: the app refuses to start until `JWT_SECRET` is in the `ENV_FILE_B64` GitHub secret. docker-compose loads `.env` through `env_file`, so adding it there is enough. The PR states this.
- `decode_access_token` doesn't reject an `iat` in the future. That's harmless while only this server issues tokens with this secret. If key rotation or other issuers come later, consider a small leeway check.
- There is no minimum length for `JWT_SECRET`; a weak value like `y` is accepted. A length check (for example 32 or more characters) at startup would harden it. Not blocking, since `.env.example` and the docs give the `token_hex(32)` command.
- Once endpoints exist, the next task should make sure refresh and login are rate-limited, and call `revoke_user` on deactivation and on password or role change, as planned.
- PyJWT 2.15.1 is a well-maintained, widely used library and it is pinned exactly. It's needed.
