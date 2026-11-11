from collections.abc import Mapping
from typing import Any

from fastapi.responses import JSONResponse


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
