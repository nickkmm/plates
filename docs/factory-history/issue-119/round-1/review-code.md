VERDICT: PASS

## Summary
- Adds the /api/auth login, refresh, logout and me endpoints (app/auth/api.py, app/auth/schemas.py). They follow the spec: cookie attributes, identical 401s, rotation with reuse detection, and no database read in /me.
- Adds shared /api plumbing (app/shared/api.py): JSON errors under /api/, the X-Requested-With check, and a CSRF exemption for the API blueprint only. The 500 handler is registered on InternalServerError, so Sentry and logging still see unhandled errors.
- /usuarios revokes refresh tokens on a role change, a deactivation and a password reset, in the same transaction as the write. Saving the form unchanged does not revoke.
- The tests (tests/api, CSRF on), docs and CLAUDE.md match the requirements. The checks passed.

## Findings
None.

## Notes
- The extra `Cache-Control: no-store` on /api/auth responses and the `Allow` header on the JSON 405 are small additions beyond the spec. Both are harmless and listed in build.md.
- A POST to an unknown /api path without the header gets 403 `missing_requested_with` instead of 404. This follows from the before_request hook and is acceptable.
