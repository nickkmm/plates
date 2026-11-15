DECISION: ACCEPT
RISK: high
SURFACES: ui, security
RESCOPED: no

## Summary
- Accepted as written. The issue is a complete spec, and its dependency (#62, job 003, PR #82) is merged.
- Every protected route will check a permission from the user's DB role on each request, in place of today's hard-coded role sets.
- The issue's route mapping matches today's access exactly for `admin`, `point_of_sale` and `production`, given the seeded `role_permissions` from #62.
- Risk is high because this changes auth on every route. The UI also changes, since the nav and template conditions become permission checks.

## Scope
Build what the issue specifies:

- **Auth helpers in `app/web/auth.py`.**
  - `current_user()` loads the user and the role's effective permissions once per request and caches them on `flask.g`. It uses `db.get_user_by_id`, `db.get_role_permissions` and `permissions.effective_permissions`, so admin always has every permission.
  - The `authenticated` decorator handles a stale session the same way `_session_is_stale` does today. If the user is missing or inactive, it clears the session and redirects to `/login`. If `users.role` changed, it refreshes `session["role"]`.
  - `permission_required(*codes)` does the same checks, then answers 403 unless the user has at least one of the codes.
  - `has_permission(code)` is for use inside views.
  - Remove `login_required`, `consulta_allowed`, `pos_allowed`, `FULL_ACCESS_ROLES` and `POS_ROLES`. Keep `ROLE_ADMIN`, `ROLES`, `load_users` and `load_roles`.
- **Route mapping, exactly as listed in the issue.**
  - `ingest.*` and `POST /records/<id>/save` need `orders.capture`.
  - `cash_summary.*` needs `cash_summary.view` and `declarations.*` needs `declarations.review`.
  - The record read routes need `records.view`, and `POST /records/delete` needs `records.delete`.
  - `review.*` needs `duplicates.review`, `inventory.*` needs `inventory.manage`, `pricing.*` needs `pricing.manage` and `customers.*` needs `customers.manage`.
  - `/` uses `authenticated`. `/login`, `/logout`, `/healthz` and `/declaracion` stay open.
  - No route that writes may accept a read-only permission.
- **Navigation.**
  - A new `app/web/navigation.py` holds `NAV_ITEMS` in today's order.
  - `_nav.html` renders those items filtered by permission, with the same markup.
  - `landing_url()` returns the first nav item the user can open. If there is none, `/` answers 403 with "Tu usuario no tiene permisos asignados. Pide a un administrador que te asigne un rol."
- **Templates.**
  - A context processor exposes `can(code)` and `nav_items`.
  - Every role check in a template becomes a `can(...)` check with the same meaning. That covers `_nav.html`, `declaration_detail.html`, `ingest.html` (the Clientes link becomes `can('customers.manage')`), `records.html` and `_orders_table.html` (the delete text and button become `can('records.delete')`), and `record_detail.html` (`can_edit` becomes `can('orders.capture')`).
- **Docs.** Rewrite `docs/WEB.md` `## Roles y permisos` in Spanish and update the Auth column of its routes table.
- **Tests.** The existing `tests/web/` tests stay green, changing only imports and fixture names. Add the new tests from acceptance criteria 2 to 6: a deactivated user, a role change taking effect on the next request, a custom role with only `inventory.manage`, a role with no permissions, and admin keeping full access with no stored rows.

Left out, as the issue says: screens to manage users or roles, new routes, CSRF tokens, password policy and login throttling.

## Why
The work is one coherent auth change and stays reviewable as one PR, even though it touches many files. Most of the route and template edits are mechanical decorator and condition swaps. Today `app/web/auth.py` gates routes with `login_required`, `consulta_allowed` and `pos_allowed`, checked against `session["role"]`. Templates read `full_access_roles`, `pos_roles` and `session.get('role')` in six places. Under the role seeds from #62, every one of these maps one-to-one to a permission code. For example, `records.py` uses `consulta_allowed` on the three read routes, `pos_allowed` on save and `login_required` on delete, which become `records.view`, `orders.capture` and `records.delete`.

One behaviour change to be aware of: a legacy role that #62 backfilled with no permissions could open `/records` through `consulta_allowed` until now. After this change it gets the 403 "no permissions" page. That is what the issue intends.

## Questions
