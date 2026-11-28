"""The auth domain: login, refresh and logout (app/auth/login.py, the Jinja
routes and the JSON API), the users and roles admin screens, and their SQL
(app/auth/repository/). The per-request access check, permission_required,
lives in app/shared/auth.py.

Dependency rule: other domains may import app.auth, and app.auth imports no
other domain package. app.web.navigation is the one allowed exception until
it moves (phase 6 of docs/MIGRATION_PLAN.md). app.shared and stdlib or
third-party libraries are always fine.
"""
