"""Application errors and how they become HTTP responses.

Code below the API raises these (or plain exceptions); the handlers in register() turn every
failure into the same JSON shape, so clients get one contract and never see internals:

    {"detail": "human-readable message", "code": "machine_readable", "request_id": "..."}

`detail` is what the frontend shows (FastAPI's own convention, so HTTPException keeps working).
The full exception, with stack trace, goes to the log under the same request_id.
"""

import logging
import math

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.logging import request_id

log = logging.getLogger("app.errors")


class AppError(Exception):
    status = 500
    code = "internal_error"
    message = "Something went wrong. Please try again."
    headers: dict[str, str] | None = None  # extra response headers, e.g. Retry-After

    def __init__(self, message: str | None = None):
        super().__init__(message or self.message)
        self.message = message or self.message


class NotFound(AppError):
    status, code, message = 404, "not_found", "Not found."


class Conflict(AppError):
    status, code, message = 409, "conflict", "This conflicts with existing data."


class InvalidInput(AppError):
    status, code, message = 422, "invalid_input", "The request is not valid."


class Blocked(AppError):
    """A guardrail refused the request (app/guardrails/). The message tells the user why, never which rule."""

    status, code, message = 400, "blocked", "This message can't be answered."


class TooManyRequests(AppError):
    """A rate limit was hit (app/core/rate_limit.py)."""

    status, code = 429, "rate_limited"

    def __init__(self, retry_after: int):
        wait = f"{retry_after} seconds" if retry_after < 120 else f"{math.ceil(retry_after / 60)} minutes"
        super().__init__(f"Too many requests. Please try again in {wait}.")
        self.headers = {"Retry-After": str(retry_after)}


class DependencyUnavailable(AppError):
    """A backing service (Ollama, Qdrant, MinIO, PostgreSQL) failed. Raise it `from` the original error."""

    status, code = 503, "dependency_unavailable"

    def __init__(self, what: str):
        super().__init__(f"{what} is temporarily unavailable. Please try again shortly.")


def _body(status: int, detail, code: str) -> JSONResponse:
    return JSONResponse({"detail": detail, "code": code, "request_id": request_id()}, status_code=status)


async def _app_error(_: Request, e: AppError) -> JSONResponse:
    if e.status >= 500:
        log.error("%s: %s", e.code, e.message, exc_info=e.__cause__ or e)
    response = _body(e.status, e.message, e.code)
    response.headers.update(e.headers or {})
    return response


async def _http_error(_: Request, e: HTTPException) -> JSONResponse:
    response = _body(e.status_code, e.detail, f"http_{e.status_code}")
    for k, v in (e.headers or {}).items():
        response.headers[k] = v
    return response


async def _validation_error(_: Request, e: RequestValidationError) -> JSONResponse:
    return _body(422, e.errors(), "invalid_input")


def unhandled(e: Exception) -> JSONResponse:
    """Any other exception: log it in full, tell the client nothing about it.
    Called by RequestContextMiddleware (app/core/middleware.py), so the response still carries the request id."""
    log.exception("unhandled error", exc_info=e)
    return _body(500, AppError.message, AppError.code)


def register(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error)
    app.add_exception_handler(HTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
