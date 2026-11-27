"""The auth domain: login and logout, the per-request session check, the
permission_required decorator, the users and roles admin screens, and their
SQL (app/auth/repository/).

Dependency rule: other domains may import app.auth, and app.auth imports no
other domain package. app.web.navigation is the one allowed exception until
it moves (phase 6 of docs/MIGRATION_PLAN.md). app.shared and stdlib or
third-party libraries are always fine.
"""
