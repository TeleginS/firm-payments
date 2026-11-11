"""Request builders and checks shared by the HTTP tests."""

import json

from fastapi.testclient import TestClient
from httpx2 import Response

from tests.conftest import LOPEZ, NAIR, PINECREST, Conn

UNKNOWN = "00000000-0000-4000-8000-000000000001"
UNKNOWN_2 = "00000000-0000-4000-8000-000000000002"
SEED = [(1, 5_000_000), (2, 50_000), (3, 200_000)]


def payment(payee: str = NAIR, amount: object = "10", **fields: object) -> dict[str, object]:
    return {"payee_firm_uuid": payee, "amount": amount, "description": "Invoice"} | fields


def request_body(payer: str = PINECREST, payments: list[dict[str, object]] | None = None) -> bytes:
    if payments is None:
        payments = [payment()]
    return json.dumps({"payer_firm_uuid": payer, "payments": payments}).encode()


SAMPLE = request_body(
    payments=[
        payment(NAIR, "6250", description="Overflow returns, August 2026"),
        payment(NAIR, "5800.5", description="Amended returns, August 2026"),
        payment(LOPEZ, "1200.75", description="Bookkeeping cleanup, 3 clients"),
    ]
)


def post(client: TestClient, body: bytes, **headers: str) -> Response:
    response = client.post(
        "/bulk_payments", content=body, headers={"Content-Type": "application/json", **headers}
    )
    assert response.headers["X-Correlation-Id"], "every response carries the header"
    return response


def balances(db: Conn) -> list[tuple[int, int]]:
    rows = db.execute("SELECT id, balance_cents FROM firms ORDER BY id")
    return [(row[0], row[1]) for row in rows]


def assert_nothing_written(db: Conn) -> None:
    count = db.execute("SELECT count(*) FROM payments").fetchone()
    assert count == (0,)
    assert balances(db) == SEED
