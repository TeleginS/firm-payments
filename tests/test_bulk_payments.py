import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response

from app.api.routes import payment_service
from app.payments import Unavailable
from tests.conftest import LOPEZ, NAIR, PINECREST, Conn
from tests.helpers import (
    SAMPLE,
    UNKNOWN,
    UNKNOWN_2,
    assert_nothing_written,
    balances,
    payment,
    post,
    request_body,
)


def assert_error(response: Response, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status
    body = response.json()
    assert list(body) == ["error"], "the envelope, not the framework's {'detail': [...]}"
    assert body["error"]["code"] == code
    assert body["error"]["message"]
    error: dict[str, Any] = body["error"]
    return error


def test_the_sample_request_executes(client: TestClient, db: Conn) -> None:
    response = post(client, SAMPLE)

    assert response.status_code == 201
    assert response.json() == {"payment_ids": [1, 2, 3]}
    assert balances(db) == [(1, 3_674_875), (2, 170_075), (3, 1_405_050)]


def test_the_response_echoes_the_correlation_id(client: TestClient) -> None:
    response = post(client, SAMPLE, **{"X-Correlation-Id": "sample-1"})

    assert response.headers["X-Correlation-Id"] == "sample-1"


MALFORMED = {
    "unparseable JSON": b"{not json",
    "empty body": b"",
    "a JSON array": b"[]",
    "invalid UTF-8": b"\xff\xfe",
    "an unknown top-level field": json.dumps(
        {"payer_firm_uuid": PINECREST, "payments": [payment()], "extra": 1}
    ).encode(),
    "an unknown field in a payment": request_body(payments=[payment(extra=1)]),
    "a missing payer": json.dumps({"payments": [payment()]}).encode(),
    "a missing amount": request_body(
        payments=[{"payee_firm_uuid": NAIR, "description": "Invoice"}]
    ),
    "an empty payments list": request_body(payments=[]),
    "1,001 payments": request_body(payments=[payment()] * 1001),
    "a trailing newline in the amount": request_body(payments=[payment(amount="300\n")]),
    "a numeric amount": request_body(payments=[payment(amount=300)]),
    "three decimals": request_body(payments=[payment(amount="1.234")]),
    "a zero amount": request_body(payments=[payment(amount="0.00")]),
    "Arabic-Indic digits": request_body(payments=[payment(amount="٣٠٠")]),
    "a blank description": request_body(payments=[payment(description=" \t\r\n")]),
    "a UUID without hyphens": request_body(payer="3f1c9a2e7b4d4c1e9a552d8e6f0b7c41"),
    "a payee that is not a UUID": request_body(payments=[payment(payee="nobody")]),
}


@pytest.mark.parametrize("body", MALFORMED.values(), ids=MALFORMED.keys())
def test_a_malformed_request_is_a_400_and_writes_nothing(
    client: TestClient, db: Conn, body: bytes
) -> None:
    response = post(client, body)

    assert_error(response, 400, "malformed_request")
    assert_nothing_written(db)


@pytest.mark.parametrize(
    "key", [b"", b"has space", b"x" * 256, b"tab\there", "caf\xe9".encode("latin-1")]
)
def test_a_bad_idempotency_key_is_a_400_and_writes_nothing(
    client: TestClient, db: Conn, key: bytes
) -> None:
    response = client.post("/bulk_payments", content=SAMPLE, headers=[(b"Idempotency-Key", key)])

    assert_error(response, 400, "malformed_request")
    assert_nothing_written(db)


def test_a_valid_idempotency_key_is_accepted_and_ignored_until_adr_0003(
    client: TestClient, db: Conn
) -> None:
    # ADR 0003 (#12) will make the second request a replay. Until then a key protects nothing.
    first = post(client, SAMPLE, **{"Idempotency-Key": "sample-1"})
    second = post(client, SAMPLE, **{"Idempotency-Key": "sample-1"})

    assert first.json() == {"payment_ids": [1, 2, 3]}
    assert second.json() == {"payment_ids": [4, 5, 6]}
    assert "Idempotent-Replayed" not in second.headers
    assert balances(db)[0] == (1, 5_000_000 - 2 * 1_325_125)


def test_a_self_payment_is_a_422(client: TestClient, db: Conn) -> None:
    response = post(client, request_body(payments=[payment(NAIR), payment(PINECREST)]))

    assert_error(response, 422, "self_payment")
    assert_nothing_written(db)


def test_an_unknown_payer_is_a_422_listing_every_missing_firm(client: TestClient, db: Conn) -> None:
    body = request_body(UNKNOWN, [payment(UNKNOWN_2), payment(NAIR), payment(UNKNOWN_2)])

    error = assert_error(post(client, body), 422, "unknown_firm")

    assert error["unknown_firm_uuids"] == [UNKNOWN, UNKNOWN_2]
    assert_nothing_written(db)


def test_an_unknown_payee_is_a_422(client: TestClient, db: Conn) -> None:
    error = assert_error(
        post(client, request_body(payments=[payment(NAIR), payment(UNKNOWN)])),
        422,
        "unknown_firm",
    )

    assert error["unknown_firm_uuids"] == [UNKNOWN]
    assert_nothing_written(db)


def test_a_payer_who_cannot_cover_the_total_is_a_422_that_says_nothing_about_money(
    client: TestClient, db: Conn
) -> None:
    # Lopez holds $500.00 and asks to pay $600.00: $100.00 short.
    response = post(client, request_body(LOPEZ, [payment(NAIR, "400"), payment(NAIR, "200")]))

    assert_error(response, 422, "request_denied")
    text = response.text.lower()
    for word in (
        "insufficient",
        "funds",
        "balance",
        "short",
        "total",
        "50000",
        "500",
        "600",
        "100",
    ):
        assert word not in text
    assert_nothing_written(db)


@pytest.mark.parametrize("code", ["lock_timeout", "deadlock", "pool_exhausted"])
def test_an_unavailable_service_is_a_503_with_retry_after(
    client: TestClient, db: Conn, code: str
) -> None:
    class Unavailable503:
        def execute(self, request: object) -> None:
            raise Unavailable(code)  # type: ignore[arg-type]

    client.app.dependency_overrides[payment_service] = Unavailable503  # type: ignore[attr-defined]

    response = post(client, SAMPLE)

    assert_error(response, 503, code)
    assert response.headers["Retry-After"] == "1"
    assert_nothing_written(db)


def test_a_422_and_a_400_carry_the_correlation_id(client: TestClient) -> None:
    denied = post(client, request_body(UNKNOWN), **{"X-Correlation-Id": "denied-1"})
    malformed = post(client, b"{", **{"X-Correlation-Id": "malformed-1"})

    assert denied.headers["X-Correlation-Id"] == "denied-1"
    assert malformed.headers["X-Correlation-Id"] == "malformed-1"
