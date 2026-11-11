"""The optional Idempotency-Key (ADR 0003), through the endpoint, against real Postgres."""

from collections import Counter
from collections.abc import Callable

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.errors import UniqueViolation

from app.settings import Settings
from tests.conftest import LOPEZ, NAIR, PINECREST, Conn
from tests.helpers import (
    SAMPLE,
    UNKNOWN,
    assert_nothing_written,
    balances,
    key_count,
    payment,
    post,
    request_body,
)
from tests.test_bulk_payments import assert_error
from tests.test_concurrency import fire, holding_firm_lock, service_of, wait_until_queued

KEY = {"Idempotency-Key": "k-1"}
SAMPLE_BALANCES = [(1, 3_674_875), (2, 170_075), (3, 1_405_050)]


def payment_count(db: Conn) -> int:
    row = db.execute("SELECT count(*) FROM payments").fetchone()
    assert row
    return int(row[0])


def unique_violation(db: Conn, constraint: str) -> UniqueViolation:
    """A real unique violation raised for a constraint with the given name."""
    db.execute(f"CREATE TEMP TABLE scratch (k int, CONSTRAINT {constraint} UNIQUE (k))")
    try:
        db.execute("INSERT INTO scratch VALUES (1)")
        with pytest.raises(UniqueViolation) as error:
            db.execute("INSERT INTO scratch VALUES (1)")
    finally:
        db.execute("DROP TABLE scratch")
    return error.value


def test_a_replay_returns_the_original_result_and_applies_nothing(
    client: TestClient, db: Conn
) -> None:
    first = post(client, SAMPLE, **KEY)
    second = post(client, SAMPLE, **KEY)

    assert first.status_code == 201
    assert first.json() == {"payment_ids": [1, 2, 3]}
    assert "Idempotent-Replayed" not in first.headers
    assert second.status_code == 201
    assert second.json() == {"payment_ids": [1, 2, 3]}
    assert second.headers["Idempotent-Replayed"] == "true"
    assert payment_count(db) == 3
    assert key_count(db) == 1
    assert balances(db) == SAMPLE_BALANCES


@pytest.mark.parametrize(
    "body",
    [
        request_body(payments=[payment(NAIR, "11")]),
        request_body(payments=[payment(NAIR, "10", description="Other")]),
        request_body(payments=[payment(LOPEZ, "10")]),
        request_body(payments=[payment(), payment()]),
    ],
    ids=["amount", "description", "payee", "extra payment"],
)
def test_the_same_key_with_a_different_payload_is_reused(
    client: TestClient, db: Conn, body: bytes
) -> None:
    first = post(client, request_body(), **KEY)
    second = post(client, body, **KEY)

    assert first.status_code == 201
    error = assert_error(second, 422, "idempotency_key_reused")
    assert "Idempotent-Replayed" not in second.headers
    assert error["message"]
    assert payment_count(db) == 1
    assert key_count(db) == 1


def test_the_same_payments_in_another_order_are_reused(client: TestClient, db: Conn) -> None:
    first = request_body(payments=[payment(NAIR, "10"), payment(LOPEZ, "20")])
    reordered = request_body(payments=[payment(LOPEZ, "20"), payment(NAIR, "10")])

    assert post(client, first, **KEY).status_code == 201
    assert_error(post(client, reordered, **KEY), 422, "idempotency_key_reused")
    assert payment_count(db) == 2
    assert key_count(db) == 1
    assert balances(db) == [(1, 4_997_000), (2, 52_000), (3, 201_000)]


def test_a_description_cannot_blur_into_the_next_field(client: TestClient, db: Conn) -> None:
    first = request_body(payments=[payment(NAIR, "1", description='a","b')])
    second = request_body(payments=[payment(NAIR, "1", description="a"), payment(NAIR, "1")])

    assert post(client, first, **KEY).status_code == 201
    assert_error(post(client, second, **KEY), 422, "idempotency_key_reused")


def test_amounts_and_uuid_case_do_not_change_the_fingerprint(client: TestClient) -> None:
    first = request_body(payments=[payment(NAIR, "300")])
    second = request_body(PINECREST.upper(), [payment(NAIR.upper(), "300.00")])

    assert post(client, first, **KEY).json() == {"payment_ids": [1]}
    replay = post(client, second, **KEY)

    assert replay.status_code == 201
    assert replay.json() == {"payment_ids": [1]}
    assert replay.headers["Idempotent-Replayed"] == "true"


def test_a_key_belongs_to_one_payer(client: TestClient, db: Conn) -> None:
    from_pinecrest = post(client, request_body(payments=[payment(NAIR, "10")]), **KEY)
    from_lopez = post(client, request_body(LOPEZ, [payment(NAIR, "10")]), **KEY)

    assert from_pinecrest.status_code == 201
    assert from_lopez.status_code == 201
    assert "Idempotent-Replayed" not in from_lopez.headers
    assert from_pinecrest.json() != from_lopez.json()
    assert key_count(db) == 2


def test_a_replay_still_succeeds_after_the_payer_can_no_longer_afford_it(
    client: TestClient, db: Conn
) -> None:
    # Lopez holds $500.00 and pays $400.00. The balance now covers $100.00 only.
    body = request_body(LOPEZ, [payment(NAIR, "400")])
    assert post(client, body, **KEY).status_code == 201

    replay = post(client, body, **KEY)

    assert replay.status_code == 201
    assert replay.headers["Idempotent-Replayed"] == "true"
    assert balances(db)[1] == (2, 10_000)
    assert payment_count(db) == 1


def test_a_denied_request_is_evaluated_again_under_the_same_key(
    client: TestClient, db: Conn
) -> None:
    body = request_body(LOPEZ, [payment(NAIR, "600")])

    assert_error(post(client, body, **KEY), 422, "request_denied")
    assert key_count(db) == 0
    assert post(client, request_body(payments=[payment(LOPEZ, "100")])).status_code == 201

    retry = post(client, body, **KEY)

    assert retry.status_code == 201
    assert "Idempotent-Replayed" not in retry.headers
    assert key_count(db) == 1
    assert balances(db) == [(1, 4_990_000), (2, 0), (3, 260_000)]


def test_an_unknown_payer_with_a_key_is_denied_without_a_lookup(
    client: TestClient, db: Conn
) -> None:
    error = assert_error(
        post(client, request_body(UNKNOWN, [payment(NAIR)]), **KEY), 422, "unknown_firm"
    )

    assert error["unknown_firm_uuids"] == [UNKNOWN]
    assert_nothing_written(db)


def test_a_reused_key_is_decided_before_the_payees_are_checked(
    client: TestClient, db: Conn
) -> None:
    assert post(client, request_body(), **KEY).status_code == 201

    unknown_payee = request_body(payments=[payment(UNKNOWN)])

    assert_error(post(client, unknown_payee, **KEY), 422, "idempotency_key_reused")


def test_a_request_without_a_key_writes_no_key_row(client: TestClient, db: Conn) -> None:
    assert post(client, request_body()).status_code == 201
    assert post(client, request_body()).status_code == 201

    assert key_count(db) == 0
    assert payment_count(db) == 2


def test_a_denied_or_unavailable_request_with_a_key_writes_no_key_row(
    make_client: Callable[..., TestClient], db: Conn, settings: Settings
) -> None:
    client = make_client(lock_timeout_ms=300)

    assert post(client, request_body(UNKNOWN), **KEY).status_code == 422
    with holding_firm_lock(settings.database_url, PINECREST):
        response = post(client, SAMPLE, **KEY)

    assert_error(response, 503, "lock_timeout")
    assert_nothing_written(db)


def test_the_same_key_sent_in_parallel_executes_once(
    client: TestClient, db: Conn, settings: Settings
) -> None:
    bodies = [SAMPLE] * 8

    def release_once_queued() -> None:
        wait_until_queued(db, len(bodies))
        release()

    with holding_firm_lock(settings.database_url, PINECREST) as release:
        responses = fire(client, bodies, settings, release_once_queued, **KEY)

    assert Counter(response.status_code for response in responses) == {201: 8}
    assert {tuple(response.json()["payment_ids"]) for response in responses} == {(1, 2, 3)}
    replayed = [r for r in responses if r.headers.get("Idempotent-Replayed") == "true"]
    assert len(replayed) == 7
    assert payment_count(db) == 3
    assert key_count(db) == 1
    assert balances(db) == SAMPLE_BALANCES
    assert service_of(client).deadlocks_detected == 0


def test_a_unique_violation_on_the_key_reruns_the_transaction(client: TestClient, db: Conn) -> None:
    violation = unique_violation(db, "idempotency_keys_pkey")

    def lose_the_race_once(attempt: int) -> None:
        if attempt == 1:
            raise violation

    service_of(client).fault = lose_the_race_once

    response = post(client, SAMPLE, **KEY)

    assert response.status_code == 201
    assert "Idempotent-Replayed" not in response.headers
    # The rolled-back attempt used up ids 1 to 3: sequences don't roll back.
    assert response.json() == {"payment_ids": [4, 5, 6]}
    assert payment_count(db) == 3
    assert key_count(db) == 1
    assert balances(db) == SAMPLE_BALANCES
    assert service_of(client).deadlocks_detected == 0


def test_a_unique_violation_on_another_constraint_is_not_retried(
    client: TestClient, db: Conn
) -> None:
    violation = unique_violation(db, "payments_pkey")
    attempts: list[int] = []

    def fail(attempt: int) -> None:
        attempts.append(attempt)
        raise violation

    service_of(client).fault = fail

    response = post(client, SAMPLE, **KEY)

    assert response.status_code == 500
    assert attempts == [1]
    assert_nothing_written(db)


def test_the_error_names_a_constraint(db: Conn) -> None:
    # The service tells the key's violation from others by this name.
    with pytest.raises(psycopg.errors.UniqueViolation) as error:
        db.execute(
            "INSERT INTO idempotency_keys (payer_firm_id, key, fingerprint, payment_ids)"
            " VALUES (1, 'k', '\\x00', '{}'), (1, 'k', '\\x00', '{}')"
        )

    assert error.value.diag.constraint_name == "idempotency_keys_pkey"
