import logging
from collections.abc import Iterator
from dataclasses import replace

import pytest
from psycopg.errors import DeadlockDetected

from app.db import Pool, create_pool
from app.payments import BulkPayment, Denied, Executed, Payment, PaymentService, Unavailable
from app.settings import Settings
from tests.conftest import LOPEZ, NAIR, PINECREST, Conn

SEED = [(1, 5_000_000), (2, 50_000), (3, 200_000)]
UNKNOWN = "00000000-0000-4000-8000-000000000001"
UNKNOWN_2 = "00000000-0000-4000-8000-000000000002"

SAMPLE = BulkPayment(
    PINECREST,
    (
        Payment(NAIR, 625_000, "Overflow returns, August 2026"),
        Payment(NAIR, 580_050, "Amended returns, August 2026"),
        Payment(LOPEZ, 120_075, "Bookkeeping cleanup, 3 clients"),
    ),
)


@pytest.fixture
def pool(settings: Settings, db: Conn) -> Iterator[Pool]:
    pool = create_pool(replace(settings, pool_size=1))
    pool.open(wait=True)
    yield pool
    pool.close()


@pytest.fixture
def service(pool: Pool) -> PaymentService:
    return PaymentService(pool, lock_timeout_ms=5000, max_attempts=3)


def balances(db: Conn) -> list[tuple[int, int]]:
    rows = db.execute("SELECT id, balance_cents FROM firms ORDER BY id")
    return [(row[0], row[1]) for row in rows]


def payment_count(db: Conn) -> int:
    row = db.execute("SELECT count(*) FROM payments").fetchone()
    assert row
    return int(row[0])


def assert_nothing_applied(db: Conn) -> None:
    assert payment_count(db) == 0
    assert balances(db) == SEED


def test_the_sample_request_executes(service: PaymentService, db: Conn) -> None:
    db.execute("""
        CREATE TABLE firm_updates (firm_id INTEGER);
        CREATE FUNCTION log_firm_update() RETURNS trigger LANGUAGE plpgsql AS
            $$ BEGIN INSERT INTO firm_updates VALUES (NEW.id); RETURN NEW; END $$;
        CREATE TRIGGER log_firm_update AFTER UPDATE ON firms
            FOR EACH ROW EXECUTE FUNCTION log_firm_update();
    """)
    try:
        outcome = service.execute(SAMPLE)

        updates = [row[0] for row in db.execute("SELECT firm_id FROM firm_updates ORDER BY 1")]
    finally:
        db.execute("DROP TABLE firm_updates; DROP FUNCTION log_firm_update CASCADE")

    assert outcome == Executed(payment_ids=(1, 2, 3))
    assert balances(db) == [(1, 3_674_875), (2, 170_075), (3, 1_405_050)]
    assert updates == [1, 2, 3], "one update per firm, Nair's two payments summed"
    rows = db.execute(
        "SELECT id, payer_firm_id, payee_firm_id, amount_cents, description"
        " FROM payments ORDER BY id"
    ).fetchall()
    assert rows == [
        (1, 1, 3, 625_000, "Overflow returns, August 2026"),
        (2, 1, 3, 580_050, "Amended returns, August 2026"),
        (3, 1, 2, 120_075, "Bookkeeping cleanup, 3 clients"),
    ]


def test_a_balance_equal_to_the_total_executes(service: PaymentService, db: Conn) -> None:
    outcome = service.execute(BulkPayment(LOPEZ, (Payment(NAIR, 50_000, "All of it"),)))

    assert isinstance(outcome, Executed)
    assert balances(db) == [(1, 5_000_000), (2, 0), (3, 250_000)]


def test_one_cent_over_the_balance_is_denied(service: PaymentService, db: Conn) -> None:
    outcome = service.execute(BulkPayment(LOPEZ, (Payment(NAIR, 50_001, "One cent too many"),)))

    assert outcome == Denied("request_denied")
    assert_nothing_applied(db)


def test_a_self_payment_is_denied(service: PaymentService, db: Conn) -> None:
    request = BulkPayment(PINECREST, (Payment(NAIR, 100, "ok"), Payment(PINECREST, 100, "self")))

    assert service.execute(request) == Denied("self_payment")
    assert_nothing_applied(db)


def test_an_unknown_payer_is_denied(service: PaymentService, db: Conn) -> None:
    outcome = service.execute(BulkPayment(UNKNOWN, (Payment(NAIR, 100, "x"),)))

    assert outcome == Denied("unknown_firm", (UNKNOWN,))
    assert_nothing_applied(db)


def test_an_unknown_payee_is_denied(service: PaymentService, db: Conn) -> None:
    request = BulkPayment(PINECREST, (Payment(NAIR, 100, "x"), Payment(UNKNOWN, 100, "y")))

    assert service.execute(request) == Denied("unknown_firm", (UNKNOWN,))
    assert_nothing_applied(db)


def test_an_unknown_payer_and_unknown_payees_are_all_listed(
    service: PaymentService, db: Conn
) -> None:
    request = BulkPayment(
        UNKNOWN,
        (Payment(UNKNOWN_2, 100, "x"), Payment(NAIR, 100, "y"), Payment(UNKNOWN_2, 1, "z")),
    )

    assert service.execute(request) == Denied("unknown_firm", (UNKNOWN, UNKNOWN_2))
    assert_nothing_applied(db)


def test_a_self_payment_wins_over_an_unknown_payer(service: PaymentService, db: Conn) -> None:
    request = BulkPayment(UNKNOWN, (Payment(UNKNOWN, 100, "self"),))

    assert service.execute(request) == Denied("self_payment")
    assert_nothing_applied(db)


def test_an_unknown_payee_wins_over_a_missing_balance(service: PaymentService, db: Conn) -> None:
    request = BulkPayment(LOPEZ, (Payment(UNKNOWN, 100, "x"), Payment(NAIR, 99_999_999, "big")))

    assert service.execute(request) == Denied("unknown_firm", (UNKNOWN,))
    assert_nothing_applied(db)


def test_the_funds_denial_logs_the_cause_and_carries_none(
    service: PaymentService, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="app.payments"):
        outcome = service.execute(BulkPayment(LOPEZ, (Payment(NAIR, 60_000, "too much"),)))

    assert outcome == Denied("request_denied")
    assert repr(outcome) == "Denied(code='request_denied', unknown_firm_uuids=())"
    (record,) = caplog.records
    assert "50000" in record.getMessage()
    assert "60000" in record.getMessage()


def test_lock_timeout_does_not_outlive_the_transaction(service: PaymentService, pool: Pool) -> None:
    service.execute(SAMPLE)

    # The pool holds one connection, so this is the one the request used.
    with pool.connection() as conn:
        assert conn.execute("SHOW lock_timeout").fetchone() == ("0",)


def test_a_deadlock_is_retried(service: PaymentService, db: Conn) -> None:
    def deadlock_once(attempt: int) -> None:
        if attempt == 1:
            raise DeadlockDetected

    service.fault = deadlock_once

    assert isinstance(service.execute(SAMPLE), Executed)
    assert service.deadlocks_detected == 1
    assert payment_count(db) == 3, "the first attempt's writes were rolled back"
    assert balances(db) == [(1, 3_674_875), (2, 170_075), (3, 1_405_050)]


def test_retries_are_bounded(service: PaymentService, db: Conn) -> None:
    def always_deadlock(attempt: int) -> None:
        raise DeadlockDetected

    service.fault = always_deadlock

    with pytest.raises(Unavailable) as error:
        service.execute(SAMPLE)

    assert error.value.code == "deadlock"
    assert service.deadlocks_detected == 3
    assert_nothing_applied(db)
