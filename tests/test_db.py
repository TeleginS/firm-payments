import pytest
from fastapi.testclient import TestClient

from app.db import create_pool
from app.main import create_app
from app.settings import Settings
from tests.conftest import LOPEZ, Conn
from tests.helpers import post, request_body


def test_healthz_needs_no_database() -> None:
    # No lifespan runs here, so there is no pool and no DATABASE_URL.
    response = TestClient(create_app()).get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_app_opens_the_pool_on_startup_and_closes_it_on_shutdown(settings: Settings) -> None:
    app = create_app(settings)

    with TestClient(app):
        pool = app.state.pool
        assert not pool.closed
        with pool.connection() as conn:
            assert conn.execute("SELECT 1").fetchone() == (1,)

    assert pool.closed


def test_settings_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://example/db")
    monkeypatch.setenv("DB_POOL_SIZE", "4")
    monkeypatch.setenv("DB_POOL_TIMEOUT_SECONDS", "0.5")

    settings = Settings.from_env()

    assert settings.database_url == "postgresql://example/db"
    assert settings.pool_size == 4
    assert settings.pool_timeout_seconds == 0.5
    assert settings.lock_timeout_ms == 3000


def test_the_pool_leaves_the_session_alone(settings: Settings, db: Conn) -> None:
    pool = create_pool(settings)
    pool.open(wait=True)
    try:
        with pool.connection() as conn:
            lock_timeout = conn.execute("SHOW lock_timeout").fetchone()
            isolation = conn.execute("SHOW transaction_isolation").fetchone()
    finally:
        pool.close()

    assert lock_timeout == ("0",)
    assert isolation == ("read committed",)


# The two tests below change balances and leave them changed. Each passes when run
# first or second, because `db` starts every test from the seed.
def test_a_test_can_move_money_1(db: Conn) -> None:
    db.execute("UPDATE firms SET balance_cents = balance_cents - 100 WHERE id = 1")
    db.execute("UPDATE firms SET balance_cents = balance_cents + 100 WHERE id = 2")

    assert db.execute("SELECT balance_cents FROM firms WHERE id = 2").fetchone() == (50100,)


def test_a_test_can_move_money_2(db: Conn) -> None:
    assert db.execute("SELECT balance_cents FROM firms WHERE id = 2").fetchone() == (50000,)
    db.execute("UPDATE firms SET balance_cents = balance_cents - 50000 WHERE uuid = %s", (LOPEZ,))
    db.execute("UPDATE firms SET balance_cents = balance_cents + 50000 WHERE id = 1")


def test_a_pooled_connection_the_database_closed_is_replaced(client: TestClient, db: Conn) -> None:
    """A database restart kills every pooled connection; requests after it must still succeed."""
    assert post(client, request_body()).status_code == 201
    db.execute(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
        " WHERE datname = current_database() AND pid <> pg_backend_pid()"
    )

    statuses = [post(client, request_body()).status_code for _ in range(3)]

    assert statuses == [201, 201, 201]
