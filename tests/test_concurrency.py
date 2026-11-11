"""Concurrency and failure paths, through the endpoint, against real Postgres.

Threads are released together by a threading.Barrier, and each test's pool is at least as
large as its thread count, so requests contend in the database and not in the application.
"""

import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import cast

import psycopg
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response
from psycopg.errors import DeadlockDetected

from app.db import Pool
from app.payments import PaymentService
from app.settings import Settings
from tests.conftest import LOPEZ, NAIR, PINECREST, Conn
from tests.helpers import (
    SAMPLE,
    assert_nothing_written,
    balances,
    payment,
    post,
    request_body,
)


def service_of(client: TestClient) -> PaymentService:
    service: PaymentService = cast(FastAPI, client.app).state.payments
    return service


def pool_of(client: TestClient) -> Pool:
    pool: Pool = cast(FastAPI, client.app).state.pool
    return pool


@contextmanager
def holding_firm_lock(url: str, firm_uuid: str) -> Iterator[Callable[[], None]]:
    """Another transaction, outside the app, that has locked one firm's row.

    Yields the function that releases it; leaving the block releases it too.
    """
    with psycopg.connect(url) as holder:
        holder.execute("SELECT 1 FROM firms WHERE uuid = %s FOR UPDATE", (firm_uuid,))
        yield holder.commit


def waiting_for_row_locks(db: Conn) -> int:
    row = db.execute(
        "SELECT count(*) FROM pg_stat_activity"
        " WHERE datname = current_database() AND wait_event_type = 'Lock'"
        " AND query ILIKE '%FOR UPDATE%'"
    ).fetchone()
    assert row
    return int(row[0])


def wait_until_queued(db: Conn, count: int) -> None:
    deadline = time.monotonic() + 10
    while waiting_for_row_locks(db) < count:
        assert time.monotonic() < deadline, "the requests never queued behind the lock"
        time.sleep(0.01)


def fire(
    client: TestClient,
    bodies: list[bytes],
    settings: Settings,
    while_in_flight: Callable[[], None],
    **headers: str,
) -> list[Response]:
    """Sends every body from its own thread, released together by a barrier.

    `while_in_flight` runs once all the threads have started.
    """
    assert settings.pool_size >= len(bodies), "a smaller pool would queue them in the app"
    barrier = threading.Barrier(len(bodies))
    responses: list[Response | None] = [None] * len(bodies)
    failures: list[BaseException] = []

    def send(index: int) -> None:
        try:
            barrier.wait(timeout=10)
            responses[index] = post(client, bodies[index], **headers)
        except BaseException as error:
            failures.append(error)

    threads = [threading.Thread(target=send, args=(i,)) for i in range(len(bodies))]
    for thread in threads:
        thread.start()
    try:
        while_in_flight()
    finally:
        for thread in threads:
            thread.join(timeout=30)
    assert not failures, failures
    assert None not in responses
    return [response for response in responses if response is not None]


def test_concurrent_requests_cannot_overspend_the_payer(
    client: TestClient, db: Conn, settings: Settings
) -> None:
    # Each request costs $12,000 and is affordable alone; Pinecrest's $50,000 covers four.
    bodies = [
        request_body(
            payments=[payment(LOPEZ if i % 2 else NAIR, "7000"), payment(NAIR, "5000")],
        )
        for i in range(10)
    ]

    def release_once_queued() -> None:
        wait_until_queued(db, len(bodies))
        release()

    with holding_firm_lock(settings.database_url, PINECREST) as release:
        responses = fire(client, bodies, settings, release_once_queued)

    results = Counter(response.status_code for response in responses)
    assert results == {201: 4, 422: 6}
    executed_total = results[201] * 1_200_000
    assert executed_total <= 5_000_000
    payer_balance = balances(db)[0][1]
    assert payer_balance == 5_000_000 - executed_total
    count = db.execute("SELECT count(*) FROM payments").fetchone()
    assert count == (8,)
    assert service_of(client).deadlocks_detected == 0


def test_two_firms_paying_each_other_never_deadlock(
    client: TestClient, db: Conn, settings: Settings
) -> None:
    rounds = 15
    # Four requests each way per round. Pinecrest pays Lopez then Nair; Lopez pays Nair
    # then Pinecrest, so a lock taken in request order would deadlock.
    to_lopez = request_body(PINECREST, [payment(LOPEZ, "1"), payment(NAIR, "1")])
    to_pinecrest = request_body(LOPEZ, [payment(NAIR, "1"), payment(PINECREST, "1")])
    bodies = [to_lopez, to_pinecrest] * 4

    for _ in range(rounds):
        responses = fire(client, bodies, settings, lambda: None)
        assert {response.status_code for response in responses} == {201}

    requests_each_way = 4 * rounds
    assert service_of(client).deadlocks_detected == 0
    assert balances(db) == [
        (1, 5_000_000 - 200 * requests_each_way + 100 * requests_each_way),
        (2, 50_000 + 100 * requests_each_way - 200 * requests_each_way),
        (3, 200_000 + 200 * requests_each_way),
    ]


def test_a_deadlock_is_retried_and_the_request_executes_once(client: TestClient, db: Conn) -> None:
    def deadlock_once(attempt: int) -> None:
        if attempt == 1:
            raise DeadlockDetected

    service_of(client).fault = deadlock_once

    response = post(client, SAMPLE)

    assert response.status_code == 201
    assert len(response.json()["payment_ids"]) == 3
    assert service_of(client).deadlocks_detected == 1
    assert db.execute("SELECT count(*) FROM payments").fetchone() == (3,)
    assert balances(db) == [(1, 3_674_875), (2, 170_075), (3, 1_405_050)]


def test_a_deadlock_on_every_attempt_is_a_503_and_applies_nothing(
    client: TestClient, db: Conn
) -> None:
    def always_deadlock(attempt: int) -> None:
        raise DeadlockDetected

    service_of(client).fault = always_deadlock

    response = post(client, SAMPLE)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "deadlock"
    assert response.headers["Retry-After"] == "1"
    assert service_of(client).deadlocks_detected == 3
    assert_nothing_written(db)


def test_a_lock_that_stays_held_is_a_503_after_the_lock_timeout(
    make_client: Callable[..., TestClient], db: Conn, settings: Settings
) -> None:
    client = make_client(lock_timeout_ms=300)

    started = time.monotonic()
    with holding_firm_lock(settings.database_url, PINECREST):
        response = post(client, SAMPLE)
    elapsed = time.monotonic() - started

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "lock_timeout"
    assert response.headers["Retry-After"] == "1"
    assert elapsed < 3
    assert_nothing_written(db)


def test_no_free_connection_is_a_503_and_the_service_recovers(
    make_client: Callable[..., TestClient], db: Conn
) -> None:
    client = make_client(pool_size=1, pool_timeout_seconds=0.3)

    with pool_of(client).connection():
        response = post(client, SAMPLE)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "pool_exhausted"
        assert response.headers["Retry-After"] == "1"
        assert_nothing_written(db)

    assert post(client, SAMPLE).status_code == 201
