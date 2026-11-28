VERDICT: PASS

## Summary
- Round 2 makes one change, which the owner asked for on PR #199: both token cookies (`access_token`, `refresh_token`) are now `SameSite=Lax` instead of `Strict`, so a link opened from outside the app (for example WhatsApp) keeps the user signed in. Docs and tests were updated to match.
- `Lax` still keeps the cookies off cross-site POSTs, `fetch` calls and iframes. Jinja forms still need the Flask-WTF token, and non-GET `/api` requests still need `X-Requested-With`, so the CSRF protection for state-changing requests is unchanged.
- Every staff GET route is read-only. The only GETs that change state are the two Jinja-only `continue` endpoints. `refresh/continue` only rotates tokens and redirects on-site through `is_safe_next`. `logout/continue` can sign a user out from a cross-site link, which is low impact and documented.
- My round-1 review had no findings. Round 1's access control, redirect guard, login enumeration resistance and no-database guard are all unchanged.

## Findings
None.

## Notes
- `Lax` matches what the Flask session cookie (`SESSION_COOKIE_SAMESITE=Lax`) gave before this branch, when auth lived in the session. Compared with `main`, this round puts the CSRF posture back where it was rather than weakening it.
- Logout CSRF through `GET /api/auth/logout/continue` is now reachable from any site through a top-level link, not just as the cookie-clearing response it was under `Strict`. The impact is still only signing the user out. `docs/WEB.md` records this as the accepted cost. If it ever matters, check `Sec-Fetch-Site` there, or make it POST once phase 6 removes the Jinja pages.
- A cross-site top-level GET to `/api/auth/refresh/continue` rotates the victim's refresh token. The attacker never sees the new tokens, and the token family stays valid because the browser keeps the new cookie. This is not exploitable.
- Keep every GET route free of state changes from now on. With `Lax`, a GET that changes state is reachable from any cross-site link.
