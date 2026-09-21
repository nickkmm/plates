FROM python:3.11-slim

WORKDIR /srv

# Dependencies first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code.
COPY app/ ./app/

EXPOSE 8000

# Serve the Flask app with gunicorn. `app.web.app:app` = module:Flask instance.
# --access-logfile - : every HTTP request (method/path/status/timing) to
# stdout, visible in `docker logs`/Dozzle -- off by default in gunicorn, so
# without this flag normal traffic produced NO log output at all (only the
# app's own current_app.logger.info calls and unhandled-exception tracebacks
# would show up). Doesn't include the username (gunicorn's %(u)s comes from
# WSGI's REMOTE_USER, which our session-cookie auth never sets) -- that's
# what the app.logger.info calls in auth_routes.py/ingest.py/records.py are
# for.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "120", \
     "--access-logfile", "-", "app.web.app:app"]
