"""X-Correlation-Id on every response and on every log line of the request (ADR 0002)."""

import logging
import re
import time
import uuid
from contextvars import ContextVar
from typing import Any

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.errors import error_response

HEADER = "X-Correlation-Id"
VALID_ID = re.compile(r"[\x21-\x7e]{1,128}")

correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")
logger = logging.getLogger("app.access")
_log_factory_installed = False


def configure_logging() -> None:
    """Stamps every log record with the current correlation id and logs `app.*` to stderr."""
    global _log_factory_installed
    if not _log_factory_installed:
        make_record = logging.getLogRecordFactory()

        def make_record_with_id(*args: Any, **kwargs: Any) -> logging.LogRecord:
            record = make_record(*args, **kwargs)
            record.correlation_id = correlation_id.get()
            return record

        logging.setLogRecordFactory(make_record_with_id)
        _log_factory_installed = True

    app_logger = logging.getLogger("app")
    if not app_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s [%(correlation_id)s] %(name)s: %(message)s"
            )
        )
        app_logger.addHandler(handler)
        app_logger.setLevel(logging.INFO)


class CorrelationIdMiddleware:
    """Echoes a usable client id or makes one, and logs one access line per request.

    It also turns an unhandled exception into a 500 here, inside the request's context,
    so the log line and the response both carry the id.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        sent = Headers(scope=scope).get(HEADER)
        cid = sent if sent and VALID_ID.fullmatch(sent) else str(uuid.uuid4())
        token = correlation_id.set(cid)
        started = time.perf_counter()
        status = 0

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message)[HEADER] = cid
            await send(message)

        try:
            try:
                await self.app(scope, receive, send_with_id)
            except Exception:
                logger.exception("unhandled exception")
                if status:
                    raise
                response = error_response(500, "internal_error", "The service failed.")
                await response(scope, receive, send_with_id)
        finally:
            logger.info(
                "%s %s -> %d in %.0f ms",
                scope["method"],
                scope["path"],
                status,
                (time.perf_counter() - started) * 1000,
            )
            correlation_id.reset(token)
