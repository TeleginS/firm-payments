from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.schemas import Malformed
from app.payments import Unavailable

UNAVAILABLE_MESSAGES = {
    "lock_timeout": "The firms in the request are busy with other requests. Retry later.",
    "deadlock": "The request kept conflicting with another one. Retry later.",
    "pool_exhausted": "The service is busy. Retry later.",
}


def error_response(
    status_code: int,
    code: str,
    message: str,
    *,
    headers: Mapping[str, str] | None = None,
    **fields: Any,
) -> JSONResponse:
    """The envelope every refusal uses: {"error": {"code": ..., "message": ..., ...}}."""
    body = {"error": {"code": code, "message": message, **fields}}
    return JSONResponse(body, status_code=status_code, headers=headers)


def register_error_handlers(app: FastAPI) -> None:
    """Maps Malformed and Unavailable to their status codes (ADR 0002).

    FastAPI's own validation failure is mapped to the same 400, so the framework's
    default 422 never reaches a client.
    """

    @app.exception_handler(Malformed)
    def malformed(request: Request, error: Malformed) -> JSONResponse:
        return error_response(400, "malformed_request", str(error))

    @app.exception_handler(RequestValidationError)
    def invalid(request: Request, error: RequestValidationError) -> JSONResponse:
        return error_response(400, "malformed_request", "The request does not match the format.")

    @app.exception_handler(Unavailable)
    def unavailable(request: Request, error: Unavailable) -> JSONResponse:
        retry_after = request.app.state.settings.retry_after_seconds
        return error_response(
            503,
            error.code,
            UNAVAILABLE_MESSAGES[error.code],
            headers={"Retry-After": str(retry_after)},
        )
