from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.correlation import CorrelationIdMiddleware, configure_logging
from app.db import create_pool
from app.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Settings come from the environment unless given (tests use their own database)."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        resolved = settings or Settings.from_env()
        pool = create_pool(resolved)
        pool.open(wait=True, timeout=resolved.pool_timeout_seconds)
        app.state.pool = pool
        try:
            yield
        finally:
            pool.close()

    configure_logging()
    app = FastAPI(title="Firm Payments", lifespan=lifespan)
    app.add_middleware(CorrelationIdMiddleware)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        """Liveness probe for the container and the load balancer. Doesn't touch the database."""
        return {"status": "ok"}

    return app


app = create_app()
