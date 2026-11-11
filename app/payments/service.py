"""The single decision point for a bulk payment request (ADR 0001, ADR 0002).

One READ COMMITTED transaction locks every involved firm row with one query ordered by
id, evaluates that query's result in the order of checks, then writes the payments and
the balance changes. This is the only code that reads or writes `balance_cents` for a
request. It knows nothing about HTTP.
"""

import hashlib
import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from psycopg import Connection
from psycopg.errors import DeadlockDetected, LockNotAvailable, UniqueViolation
from psycopg.rows import TupleRow
from psycopg_pool import PoolTimeout

from app.db import Pool

logger = logging.getLogger(__name__)

# The one locking query of the service. It stays one query, runs first, and always includes
# the payer: ADR 0001 for the lock order, ADR 0003 for the payer's row as the mutex.
LOCK_FIRMS = """
    SELECT id, uuid, balance_cents
    FROM firms
    WHERE uuid = ANY(%s::text[])
    ORDER BY id
    FOR UPDATE
"""
INSERT_PAYMENT = """
    INSERT INTO payments (payer_firm_id, payee_firm_id, amount_cents, description)
    VALUES (%s, %s, %s, %s)
    RETURNING id
"""
FIND_KEY = """
    SELECT fingerprint, payment_ids FROM idempotency_keys
    WHERE payer_firm_id = %s AND key = %s
"""
INSERT_KEY = """
    INSERT INTO idempotency_keys (payer_firm_id, key, fingerprint, payment_ids)
    VALUES (%s, %s, %s, %s)
"""
KEY_CONSTRAINT = "idempotency_keys_pkey"
APPLY_DELTA = "UPDATE firms SET balance_cents = balance_cents + %s WHERE id = %s"


@dataclass(frozen=True)
class Payment:
    payee_uuid: str
    amount_cents: int
    description: str


@dataclass(frozen=True)
class BulkPayment:
    """A request with lower-case firm UUIDs and amounts in integer cents."""

    payer_uuid: str
    payments: tuple[Payment, ...]
    idempotency_key: str | None = None


@dataclass(frozen=True)
class Executed:
    payment_ids: tuple[int, ...]
    """In request order: the n-th id belongs to the n-th payment."""


@dataclass(frozen=True)
class Replayed:
    """The request was executed earlier under the same key; nothing was applied now."""

    payment_ids: tuple[int, ...]


@dataclass(frozen=True)
class Denied:
    code: Literal["self_payment", "unknown_firm", "idempotency_key_reused", "request_denied"]
    unknown_firm_uuids: tuple[str, ...] = ()


def fingerprint(request: BulkPayment) -> bytes:
    """Hash of the payer and the payments in request order (ADR 0003).

    The JSON is ASCII-escaped and built from lists, so no description can pass for the
    boundary between two fields.
    """
    canonical = json.dumps(
        [
            request.payer_uuid,
            [[p.payee_uuid, p.amount_cents, p.description] for p in request.payments],
        ],
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("ascii")).digest()


class Unavailable(Exception):
    """Nothing is wrong with the request and nothing was applied; try again later."""

    def __init__(self, code: Literal["lock_timeout", "deadlock", "pool_exhausted"]) -> None:
        super().__init__(code)
        self.code = code


class PaymentService:
    def __init__(self, pool: Pool, *, lock_timeout_ms: int, max_attempts: int) -> None:
        self._pool = pool
        self._lock_timeout = f"{lock_timeout_ms}ms"
        self._max_attempts = max_attempts
        self._deadlocks_detected = 0
        # Guards the statistic only. Correctness comes from the database's row locks.
        self._counter_lock = threading.Lock()
        self.fault: Callable[[int], None] | None = None
        """Test seam: called with the attempt number after the writes, before the commit.
        A test raises `DeadlockDetected` from it to exercise the retry deterministically."""

    @property
    def deadlocks_detected(self) -> int:
        """Deadlocks that made a transaction roll back, whether or not a retry followed."""
        with self._counter_lock:
            return self._deadlocks_detected

    def execute(self, request: BulkPayment) -> Executed | Replayed | Denied:
        if any(payment.payee_uuid == request.payer_uuid for payment in request.payments):
            return Denied("self_payment")

        for attempt in range(1, self._max_attempts + 1):
            try:
                return self._run(request, attempt)
            except DeadlockDetected:
                with self._counter_lock:
                    self._deadlocks_detected += 1
                logger.warning("deadlock on attempt %d of %d", attempt, self._max_attempts)
            except UniqueViolation as error:
                if error.diag.constraint_name != KEY_CONSTRAINT:
                    raise
                logger.warning("idempotency key inserted by another request, attempt %d", attempt)
            except LockNotAvailable:
                logger.warning("lock timeout waiting for firm rows")
                raise Unavailable("lock_timeout") from None
            except PoolTimeout:
                logger.warning("no database connection became free in time")
                raise Unavailable("pool_exhausted") from None
        raise Unavailable("deadlock")

    def _run(self, request: BulkPayment, attempt: int) -> Executed | Replayed | Denied:
        with self._pool.connection() as conn:
            conn.execute("SELECT set_config('lock_timeout', %s, true)", (self._lock_timeout,))

            payee_uuids = list(dict.fromkeys(payment.payee_uuid for payment in request.payments))
            lock_uuids = [request.payer_uuid, *payee_uuids]
            rows = conn.execute(LOCK_FIRMS, (lock_uuids,)).fetchall()
            firms = {uuid: (firm_id, balance) for firm_id, uuid, balance in rows}

            # Order of checks (ADR 0002), steps 3 to 6, all read from the lock query's result.
            # Step 2, self_payment, was decided before a connection was taken.
            if request.payer_uuid not in firms:
                return Denied("unknown_firm", tuple(u for u in lock_uuids if u not in firms))

            # Step 4: replay or idempotency_key_reused (ADR 0003). It must come after the
            # lock query above, whose payer row keeps two requests with one key apart, and
            # before the payee and funds checks below.
            payer_id, balance = firms[request.payer_uuid]
            key = request.idempotency_key
            request_fingerprint = fingerprint(request) if key is not None else b""
            if key is not None:
                stored = conn.execute(FIND_KEY, (payer_id, key)).fetchone()
                if stored:
                    stored_fingerprint, stored_ids = stored
                    if bytes(stored_fingerprint) != request_fingerprint:
                        return Denied("idempotency_key_reused")
                    return Replayed(tuple(stored_ids))

            unknown_payees = tuple(u for u in payee_uuids if u not in firms)
            if unknown_payees:
                return Denied("unknown_firm", unknown_payees)

            total = sum(payment.amount_cents for payment in request.payments)
            if balance < total:
                logger.info(
                    "request denied: payer %s balance %d cents is below the request total %d",
                    request.payer_uuid,
                    balance,
                    total,
                )
                return Denied("request_denied")

            payment_ids = self._write(conn, request, payer_id, total, firms)
            if key is not None:
                conn.execute(INSERT_KEY, (payer_id, key, request_fingerprint, list(payment_ids)))
            if self.fault:
                self.fault(attempt)
            return Executed(payment_ids)

    @staticmethod
    def _write(
        conn: Connection[TupleRow],
        request: BulkPayment,
        payer_id: int,
        total: int,
        firms: dict[str, tuple[int, int]],
    ) -> tuple[int, ...]:
        # One row per payment, but one balance update per firm however often it is named.
        deltas = {payer_id: -total}
        for payment in request.payments:
            payee_id = firms[payment.payee_uuid][0]
            deltas[payee_id] = deltas.get(payee_id, 0) + payment.amount_cents

        with conn.cursor() as cur:
            cur.executemany(
                INSERT_PAYMENT,
                [
                    (payer_id, firms[p.payee_uuid][0], p.amount_cents, p.description)
                    for p in request.payments
                ],
                returning=True,
            )
            payment_ids: list[int] = []
            while True:
                ((payment_id,),) = cur.fetchall()
                payment_ids.append(payment_id)
                if not cur.nextset():
                    break
            cur.executemany(
                APPLY_DELTA, [(delta, firm_id) for firm_id, delta in sorted(deltas.items())]
            )
        return tuple(payment_ids)
