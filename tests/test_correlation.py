import logging
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.correlation import VALID_ID
from app.main import create_app

logger = logging.getLogger("app.test")


def cid(record: logging.LogRecord) -> str:
    return str(record.__dict__["correlation_id"])


@pytest.fixture
def app() -> FastAPI:
    app = create_app()
    both_in_flight = threading.Barrier(2, timeout=5)

    @app.get("/overlap/{name}")
    def overlap(name: str) -> dict[str, str]:
        both_in_flight.wait()
        logger.info("handled %s", name)
        return {"name": name}

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("boom")

    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def test_a_valid_id_is_echoed(client: TestClient) -> None:
    response = client.get("/healthz", headers={"X-Correlation-Id": "abc-123"})

    assert response.headers["X-Correlation-Id"] == "abc-123"


def test_the_longest_valid_id_is_echoed(client: TestClient) -> None:
    response = client.get("/healthz", headers={"X-Correlation-Id": "x" * 128})

    assert response.headers["X-Correlation-Id"] == "x" * 128


@pytest.mark.parametrize(
    "sent",
    [
        None,
        b"",
        b"x" * 129,
        b"has space",
        b"tab\there",
        "caf\xe9".encode("latin-1"),
        "日本".encode(),
    ],
)
def test_a_missing_or_unusable_id_is_replaced(client: TestClient, sent: bytes | None) -> None:
    headers = [] if sent is None else [(b"X-Correlation-Id", sent)]

    response = client.get("/healthz", headers=headers)

    returned = response.headers["X-Correlation-Id"]
    assert VALID_ID.fullmatch(returned)
    assert returned.encode("latin-1") != sent


def test_generated_ids_differ(client: TestClient) -> None:
    ids = {client.get("/healthz").headers["X-Correlation-Id"] for _ in range(3)}

    assert len(ids) == 3


def test_the_header_is_on_a_404(client: TestClient) -> None:
    response = client.get("/nowhere", headers={"X-Correlation-Id": "lost"})

    assert response.status_code == 404
    assert response.headers["X-Correlation-Id"] == "lost"


def test_the_header_is_on_an_unhandled_exception(client: TestClient) -> None:
    response = client.get("/boom", headers={"X-Correlation-Id": "oops-1"})

    assert response.status_code == 500
    assert response.headers["X-Correlation-Id"] == "oops-1"
    assert response.json()["error"]["code"] == "internal_error"


def test_the_log_of_an_unhandled_exception_carries_the_id(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="app"):
        client.get("/boom", headers={"X-Correlation-Id": "oops-2"})

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert [cid(r) for r in errors] == ["oops-2"]


def test_concurrent_requests_log_their_own_ids(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    returned: dict[str, str] = {}

    def call(name: str) -> None:
        response = client.get(f"/overlap/{name}", headers={"X-Correlation-Id": f"id-{name}"})
        returned[name] = response.headers["X-Correlation-Id"]

    with caplog.at_level(logging.INFO, logger="app"):
        threads = [threading.Thread(target=call, args=(name,)) for name in ("a", "b")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    assert returned == {"a": "id-a", "b": "id-b"}
    handled = {r.getMessage(): cid(r) for r in caplog.records if r.name == "app.test"}
    assert handled == {"handled a": "id-a", "handled b": "id-b"}
    access = [r for r in caplog.records if r.name == "app.access"]
    assert sorted(cid(r) for r in access) == ["id-a", "id-b"]
