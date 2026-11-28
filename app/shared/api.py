"""The shared plumbing of the JSON API under `/api` (docs/WEB.md, "JSON API").

`init_api(app)`, called from app/web/app.py, gives every error under `/api/`
a JSON body `{"error": {"code": ..., "message": ...}}` (a fixed English code,
a Spanish message, never HTML or the abort() description) and refuses a
POST/PUT/PATCH/DELETE under `/api/` without `X-Requested-With:
XMLHttpRequest` with a 403 before the view runs: a cross-site form can't set
that header, so it is the API's CSRF defence (the API blueprints are exempt
from Flask-WTF). Outside `/api/` every error keeps its usual response.

Depends on no domain package.
"""

from __future__ import annotations

from flask import Flask, Response, jsonify, request
from pydantic import BaseModel
from werkzeug.exceptions import HTTPException, InternalServerError, MethodNotAllowed

_CHECKED_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# status -> (code, message) of the JSON error answered under /api/.
HTTP_ERRORS: dict[int, tuple[str, str]] = {
    400: ("bad_request", "No pudimos procesar la solicitud. Revisa los datos e intenta de nuevo."),
    401: ("not_authenticated", "Tu sesión expiró. Inicia sesión de nuevo."),
    403: ("forbidden", "No tienes permiso para hacer esto."),
    404: ("not_found", "No encontramos lo que buscas."),
    405: ("method_not_allowed", "Esta acción no está permitida aquí."),
    409: ("conflict", "Alguien más cambió estos datos. Recarga la página e intenta de nuevo."),
    429: ("rate_limited", "Hiciste demasiadas solicitudes. Espera un momento e intenta de nuevo."),
    500: ("internal_error", "Algo salió mal. Intenta de nuevo en unos minutos."),
}
MSG_MISSING_REQUESTED_WITH = "No se pudo procesar la solicitud. Recarga la página e intenta de nuevo."


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


def is_api_request() -> bool:
    """True when the current request is under `/api`."""
    return request.path == "/api" or request.path.startswith("/api/")


def error_response(status: int, code: str, message: str) -> Response:
    """The JSON error body with that status, never cached."""
    body = ErrorResponse(error=ErrorDetail(code=code, message=message))
    response = jsonify(body.model_dump())
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    return response


def http_error_response(status: int) -> Response:
    """The JSON error of HTTP_ERRORS for that status."""
    code, message = HTTP_ERRORS[status]
    return error_response(status, code, message)


def _handle_http_error(exc: HTTPException) -> Response | HTTPException:
    if not is_api_request():
        return exc
    status = exc.code if exc.code in HTTP_ERRORS else 500
    response = http_error_response(status)
    if isinstance(exc, MethodNotAllowed) and exc.valid_methods:
        response.headers["Allow"] = ", ".join(exc.valid_methods)
    return response


def _require_requested_with() -> Response | None:
    if (
        is_api_request()
        and request.method in _CHECKED_METHODS
        and request.headers.get("X-Requested-With") != "XMLHttpRequest"
    ):
        return error_response(403, "missing_requested_with", MSG_MISSING_REQUESTED_WITH)
    return None


def init_api(app: Flask) -> None:
    """Register the JSON error handlers and the X-Requested-With check.
    500 is registered on InternalServerError, not on Exception, so Flask's
    exception logging and Sentry still see every unhandled error."""
    for status in HTTP_ERRORS:
        if status != 500:
            app.register_error_handler(status, _handle_http_error)
    app.register_error_handler(InternalServerError, _handle_http_error)
    app.before_request(_require_requested_with)
