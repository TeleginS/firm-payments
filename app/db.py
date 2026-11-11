from psycopg import Connection, IsolationLevel
from psycopg_pool import ConnectionPool

from app.settings import Settings


def _read_committed(conn: Connection[tuple[object, ...]]) -> None:
    # Client-side only: psycopg puts it in each BEGIN. Nothing is SET on the session.
    conn.isolation_level = IsolationLevel.READ_COMMITTED


def create_pool(settings: Settings) -> ConnectionPool[Connection[tuple[object, ...]]]:
    """A closed pool; the app opens it on startup and closes it on shutdown."""
    return ConnectionPool(
        settings.database_url,
        min_size=1,
        max_size=settings.pool_size,
        timeout=settings.pool_timeout_seconds,
        configure=_read_committed,
        open=False,
    )
