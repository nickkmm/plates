"""The app-wide CSRF protection (Flask-WTF), in its own module so blueprints
can use `csrf.exempt` / `csrf.protect()` without importing app.web.app.

app.web.app calls `csrf.init_app(app)`: from then on every POST/PUT/PATCH/
DELETE needs the session-bound token, sent as the `csrf_token` form field
(`{{ csrf_token() }}` in templates) or the `X-CSRFToken` header. See
docs/WEB.md, "Protección CSRF".
"""

from __future__ import annotations

from flask_wtf.csrf import CSRFProtect

csrf = CSRFProtect()
