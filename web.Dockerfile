FROM python:3.11-slim

WORKDIR /srv

# Dependencies first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code.
COPY app/ ./app/

EXPOSE 8000

# Serve the Flask app with gunicorn. `app.web.app:app` = module:Flask instance.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "120", "app.web.app:app"]
