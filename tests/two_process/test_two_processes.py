"""Concurrent requests against two service processes that share one database.

The in-process tests can't show that two processes, each with its own pool, stay correct:
only the database's row locks keep them apart. Run by `make verify-full`.
"""

import os
import random
import socket
import subprocess
import sys
import threading
import time
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

import httpx2
import pytest

from tests.conftest import LOPEZ, NAIR, PINECREST, Conn
from tests.helpers import payment, request_body

pytestmark = pytest.mark.two_process

ROOT = Path(__file__).resolve().parents[2]
FIRM_IDS = {PINECREST: 1, LOPEZ: 2, NAIR: 3}
Spec = tuple[str, list[tuple[str, int]]]  # payer, [(payee, cents)]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def servers(db: Conn, test_database_url: str, tmp_path: Path) -> Iterator[list[str]]:
    """Two uvicorn processes on free ports, each with its own pool. Stopped by PID."""
    env = {**os.environ, "DATABASE_URL": test_database_url}
    started: list[tuple[subprocess.Popen[bytes], Path]] = []
    urls = []
    try:
        for index in range(2):
            port = free_port()
            log = tmp_path / f"server-{index}.log"
            command = [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)]
            command.append("--no-access-log")
            with log.open("wb") as output:
                process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=output, stderr=output)
            started.append((process, log))
            urls.append(f"http://127.0.0.1:{port}")

        for (process, log), url in zip(started, urls, strict=True):
            deadline = time.monotonic() + 30
            while True:
                assert process.poll() is None, log.read_text()
                try:
                    if httpx2.get(f"{url}/healthz").status_code == 200:
                        break
                except httpx2.TransportError:
                    pass
                assert time.monotonic() < deadline, f"{url} did not start:\n{log.read_text()}"
                time.sleep(0.1)
        yield urls
    finally:
        for process, _ in started:
            process.terminate()
        for process, _ in started:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def workload() -> list[Spec]:
    """A shuffled mix, so both processes get both kinds at the same time.

    - 30 requests of $150 from Nair, who holds $2,000 and so can cover 13.
    - 20 requests each way between Pinecrest and Lopez, listing the firms in opposite
      orders, so locks taken in request order would deadlock.
    """
    specs: list[Spec] = [(NAIR, [(PINECREST, 15_000)])] * 30
    specs += [(PINECREST, [(LOPEZ, 100), (NAIR, 100)])] * 20
    specs += [(LOPEZ, [(NAIR, 100), (PINECREST, 100)])] * 20
    random.Random(7).shuffle(specs)
    return specs


def test_two_processes_keep_the_invariants(servers: list[str], db: Conn) -> None:
    specs = workload()
    barrier = threading.Barrier(len(specs))
    statuses: list[int] = [0] * len(specs)
    failures: list[BaseException] = []

    def send(index: int) -> None:
        payer, payments = specs[index]
        body = request_body(
            payer,
            [payment(payee, f"{cents // 100}.{cents % 100:02d}") for payee, cents in payments],
        )
        try:
            with httpx2.Client(base_url=servers[index % 2], timeout=30) as client:
                barrier.wait(timeout=30)
                response = client.post(
                    "/bulk_payments", content=body, headers={"Content-Type": "application/json"}
                )
            statuses[index] = response.status_code
        except BaseException as error:
            failures.append(error)

    threads = [threading.Thread(target=send, args=(i,)) for i in range(len(specs))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not failures, failures

    assert set(statuses) <= {201, 422, 503}, Counter(statuses)

    # The balances are the seed plus exactly the requests that answered 201.
    executed = [spec for spec, status in zip(specs, statuses, strict=True) if status == 201]
    expected = {1: 5_000_000, 2: 50_000, 3: 200_000}
    for payer, payments in executed:
        for payee, cents in payments:
            expected[FIRM_IDS[payer]] -= cents
            expected[FIRM_IDS[payee]] += cents
    actual = {row[0]: row[1] for row in db.execute("SELECT id, balance_cents FROM firms")}
    assert actual == expected
    assert min(actual.values()) >= 0

    nair_executed = sum(1 for payer, _ in executed if payer == NAIR)
    assert nair_executed <= 13, "Nair's $2,000 covers 13 payments of $150"
    if 503 not in statuses:
        assert nair_executed == 13
    count = db.execute("SELECT count(*) FROM payments").fetchone()
    assert count is not None
    assert count[0] == sum(len(payments) for _, payments in executed)
