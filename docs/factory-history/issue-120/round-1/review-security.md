VERDICT: PASS

## Summary
- Flask pages now check users and permissions from the signed `access_token` JWT (`app/shared/auth.py`), with no database read. Forged and invalid tokens go to `/login`, `/api` gets a JSON 401, and a missing code gets a 403.
- Every route keeps its permission codes. Session user reads became `signed_in_user()`, which raises if a route ever lacks its guard. The route-walk test covers more than 50 guarded route and method pairs.
- The new GET `/api/auth/refresh/continue` and `/api/auth/logout/continue` check `next` with `is_safe_next` and never redirect off-site. Tests cover `//`, `/\\`, `http:` and absolute URLs.
- Jinja login and logout use the same `login.py` code as the API. Login failures look the same for unknown, wrong-password and inactive users, and `POST /logout` still needs CSRF. No secrets, data files or new dependencies were added.

## Findings
None.

## Notes
- Accepted trade-off (spec, Assumptions 1 and 8): deactivation, role changes and permission edits now take effect within 5 minutes (the access-token lifetime) instead of on the next request. `admin_routes.py` still revokes refresh tokens on role change, deactivation and password reset, so the window is capped at 5 minutes. Permission edits on a role still don't revoke tokens (#119 decided this, and it is out of scope), so they also apply at the next refresh. The PR description should state this.
- The `access_token` cookie now lives 30 days (`app/auth/login.py` `_set_cookie`). The JWT's own `exp` still limits it to 5 minutes, and `decode_access_token` enforces that, so a stolen cookie is worth no more than before.
- Both `continue` endpoints are state-changing GETs. A cross-site link can't rotate or revoke a token, because `SameSite=Strict` keeps the refresh cookie off cross-site requests. It can still answer with the cookie-clearing headers, which is a logout CSRF (it signs the user out and nothing more). This is low impact and matches spec Assumption 2. Logout already had this exposure in spirit, so it does not block.
- `_nav.html` puts only an integer (`token_seconds_left`) into a `data-` attribute. The freshness script calls `POST /api/auth/refresh` same-origin with `X-Requested-With` and keeps the existing CSRF fields. Nothing user-controlled reaches the DOM.
- Log lines now record `signed_in_user().username` where they used to record `session["user"]`. These are staff usernames, the same data as before, and no customer personal data was added to any log.
