---
status: accepted
---

# Lock every involved firm row in id order before checking funds

The service runs as several instances against one PostgreSQL database, so two bulk payment requests can touch the same firm at the same moment. Each request runs in one `READ COMMITTED` transaction that first locks the payer and every distinct payee with a single `SELECT ... FROM firms WHERE uuid = ANY(...) ORDER BY id FOR UPDATE`, and only then checks the request total against the payer's balance, inserts the payments, and applies one balance update per firm.

The funds check is correct because of how `FOR UPDATE` behaves under `READ COMMITTED`: a select that had to wait for another transaction returns the latest committed version of the row once it gets the lock. The balance we compare against is therefore the current one, and nobody else can change it before we commit.

Locking in ascending `id` is what stops two firms that pay each other at once from deadlocking. PostgreSQL sorts before it locks, and `id` never changes, so the locks are taken in that order. That is how the executor behaves, not a documented promise about lock order, and other parts of the platform also write to `firms`. So retrying on a deadlock (`40P01`) is a required part of this design, not an optimisation: the transaction rolls back and runs again, a bounded number of times, and then gives up with a `503` (ADR 0002). `lock_timeout` is set for the transaction only, never on the pooled connection, so a request never waits on a lock for more than a few seconds and the setting can't leak into another request.

## Considered options

- **Conditional debit** (`UPDATE ... WHERE balance_cents >= :total`, deny when no row changes). It protects the payer's balance but still takes payee row locks in write order, so two firms paying each other deadlock. It needs ordered locking anyway, and then the condition adds nothing.
- **`SERIALIZABLE` with retry.** Correct, but every conflict becomes a retry, and the reason the code is safe is no longer visible in the code.
- **One `SELECT ... FOR UPDATE` per firm, issued in sorted order.** The order is then guaranteed by our own code, at the cost of one round trip per firm. Rejected because the single query gives the same order in practice and the deadlock retry covers the rest.

## Consequences

- Requests that share any firm run one after another. A firm that many others pay at once is a hot row and bounds throughput for those requests.
- Payments are summed per payee before balances are updated, so each firm gets one lock and one update however many payments name it. The `payments` table still gets one row per payment.
- An unknown firm falls out of the lock query: it returns fewer rows than the request named.
- `firms.uuid` needs a unique index.
- The lock query must stay one query, run first, and always include the payer. ADR 0003 relies on the payer's row lock to keep two requests with the same idempotency key apart.
