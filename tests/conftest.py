import os
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path

import psycopg
import psycopg.conninfo
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"

# The brief's three firms (migrations/seed.sql).
PINECREST = "3f1c9a2e-7b4d-4c1e-9a55-2d8e6f0b7c41"
LOPEZ = "8b2e4c71-0d3a-4f6e-b1c9-5a7d2e9f4c10"
NAIR = "e5f18b3c-2a9d-4c07-8e6b-1d4a7f9c3b25"

Conn = psycopg.Connection[tuple[object, ...]]


@pytest.fixture(scope="session")
def test_database_url() -> str:
    url = os.environ["TEST_DATABASE_URL"]
    dbname = str(psycopg.conninfo.conninfo_to_dict(url)["dbname"])
    # The schema is dropped below: never let that reach the service database.
    assert dbname.endswith("_test"), f"refusing to reset {dbname!r}: not a test database"
    return url


@pytest.fixture(scope="session")
def schema(test_database_url: str) -> None:
    """Applies schema.sql from here, never the container's init scripts."""
    with psycopg.connect(test_database_url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        conn.execute((MIGRATIONS / "schema.sql").read_bytes().decode())


def _balances(conn: Conn) -> list[int]:
    return [row[0] for row in conn.execute("SELECT balance_cents FROM firms")]  # type: ignore[misc]


@pytest.fixture
def db(test_database_url: str, schema: None) -> Iterator[Conn]:
    """A connection to a database holding the brief's three firms and nothing else.

    After the test, the sum of all balances must be what it was at the start and no
    balance may be negative. Every DB-backed test gets this check by using this fixture,
    directly or through `client`.
    """
    with psycopg.connect(test_database_url, autocommit=True) as conn:
        for name in ("reset.sql", "seed.sql"):
            conn.execute((MIGRATIONS / name).read_bytes().decode())
        total = sum(_balances(conn))

        yield conn

        balances = _balances(conn)
        assert sum(balances) == total, "the sum of all balances changed"
        assert min(balances) >= 0, "a balance went negative"


@pytest.fixture
def settings(test_database_url: str) -> Settings:
    # Generous waits: tests that need a short lock timeout or pool timeout shrink it themselves.
    return Settings(
        database_url=test_database_url,
        pool_size=10,
        pool_timeout_seconds=10,
        lock_timeout_ms=10_000,
    )


@pytest.fixture
def make_client(db: Conn, settings: Settings) -> Iterator[Callable[..., TestClient]]:
    """Builds clients of the app running against the test database, with settings overridden."""
    with ExitStack() as stack:

        def make(**overrides: object) -> TestClient:
            app = create_app(replace(settings, **overrides))  # type: ignore[arg-type]
            return stack.enter_context(TestClient(app, raise_server_exceptions=False))

        yield make


@pytest.fixture
def client(make_client: Callable[..., TestClient]) -> TestClient:
    return make_client()
