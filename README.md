# Firm Payments: bulk payment service

> Written as the pull-request description the brief asks for. Jump to
> [How to run](#how-to-run) and [How to verify](#how-to-verify) to try it, or read on
> for the design.

**Contents:** [Summary](#summary) · [API](#api) · [Data model](#data-model) ·
[Approach](#approach) · [Issues I ran into](#issues-i-ran-into) ·
[Assumptions](#assumptions) · [Possible improvements](#possible-improvements) ·
[How to run](#how-to-run) · [How to verify](#how-to-verify) ·
[How I worked](#how-i-worked) · [Two quick questions](#two-quick-questions)

## Summary

A web service that accepts a **bulk payment request** from one firm and runs it
**all or nothing**:

- If the paying firm's balance covers the **total** of all payments in the request, the
  service:
  - records every payment;
  - debits the payer;
  - credits each payee;
  - returns **`201 Created`**.
- If the balance doesn't cover the total, the service denies the whole request, writes
  nothing, and returns **`422 Unprocessable Entity`**.

The service runs as **several load-balanced instances** that share one relational
database, so correctness under concurrent requests comes from the database, not from
the application process.

| | |
|---|---|
| Language | Python 3.12+ |
| Web framework | FastAPI |
| Database | PostgreSQL 16 (Docker Compose) |
| Data access | psycopg 3, plain SQL, no ORM |
| Tests | pytest, run against real Postgres |

## API

### `POST /bulk_payments`

```json
{
  "payer_firm_uuid": "3f1c9a2e-7b4d-4c1e-9a55-2d8e6f0b7c41",
  "payments": [
    {
      "amount": "1200.75",
      "payee_firm_uuid": "8b2e4c71-0d3a-4f6e-b1c9-5a7d2e9f4c10",
      "description": "Bookkeeping cleanup, 3 clients"
    }
  ]
}
```

| Field | Rules |
|---|---|
| `payer_firm_uuid` | Public UUID of the payer (`firms.uuid`), hyphenated form, any letter case |
| `payments` | 1 to 1,000 entries. The same payee may appear more than once |
| `payments[].amount` | US dollars as a string that is exactly `[0-9]{1,10}(\.[0-9]{1,2})?`, above zero (`"300"`, `"5800.5"`, `"1200.75"`). ASCII digits only |
| `payments[].payee_firm_uuid` | Public UUID of the payee. It can't be the payer |
| `payments[].description` | 1 to 1,000 characters, not only whitespace, no NUL character, stored exactly as sent |

Fields the format doesn't define are refused.

| Response | When |
|---|---|
| `201 Created` | **Executed.** The payments are recorded and the balances are updated. Body: `{"payment_ids": [...]}`. The ids are positional: the n-th id belongs to the n-th entry of `payments`. A replay of a request sent with an `Idempotency-Key` is also `201` with the original ids and the header `Idempotent-Replayed: true`; nothing is applied again. |
| `400 Bad Request` | **Malformed.** The request breaks the format above, or carries a bad `Idempotency-Key`. Code `malformed_request`; the message names the field, never its value. Nothing is written |
| `422 Unprocessable Entity` | **Denied.** The request is well-formed and the platform refuses it. Nothing is written |
| `503 Service Unavailable` | The service couldn't get its row locks or a database connection in time. Nothing is written; retry after `Retry-After`. Codes: `lock_timeout`, `deadlock`, `pool_exhausted` |

Every refusal body is `{"error": {"code": "...", "message": "..."}}`. The `422` codes
(`400` and `503` use the codes above):

| Code | Meaning |
|---|---|
| `self_payment` | A payment names the payer as its payee |
| `unknown_firm` | A firm UUID isn't on the platform. `error.unknown_firm_uuids` lists every missing one |
| `idempotency_key_reused` | The `Idempotency-Key` was already used by this payer for a different request. Nothing is written |
| `request_denied` | The platform won't execute the request. Today that means the payer's balance is below the request total |

A response carries one code, the first that applies: `self_payment`, then `unknown_firm`
for the payer, then (with a key that was used before) a replay or
`idempotency_key_reused`, then `unknown_firm` for a payee, then `request_denied`. The
replay check comes after the payer is known and before the payees and the balance are
looked at, so a replay still succeeds after the payer's balance has dropped.

Every `503` carries `Retry-After` (seconds), and the code says why: `lock_timeout` (a
firm's row stayed locked past the lock timeout), `deadlock` (every attempt hit a
deadlock) or `pool_exhausted` (no database connection became free in time).

`request_denied` is neutral on purpose. The response never names the cause and never
includes a balance, because the service has no authentication. See
[ADR 0002](docs/adr/0002-malformed-400-denied-422.md).

### Safe retries: `Idempotency-Key`

Send an optional `Idempotency-Key` header (1 to 255 visible ASCII characters, no spaces;
a bad key is `400`) and a request that was executed is never executed again under that
key ([ADR 0003](docs/adr/0003-optional-idempotency-key.md)):

- the same request again returns the original `201` and the same `payment_ids` with
  `Idempotent-Replayed: true`, and applies nothing;
- the same key with different content is `422` `idempotency_key_reused`;
- a key belongs to one payer.

Without the header there is no protection: a client that times out and sends the same
request again can pay everyone twice.

"The same content" is the payer and the payments **in request order**, compared after
parsing: `"300"` and `"300.00"` match, UUID case doesn't matter, descriptions match byte
for byte. The same payments in another order are a different request, because the ids
are positional.

### Correlation id

Every response carries an `X-Correlation-Id` header, and the service writes the same
value on every log line for that request. Send your own (1 to 128 visible ASCII
characters) and it is echoed; otherwise the service generates one.

## Data model

The brief gives this schema:

```
firms    (id INTEGER, name TEXT, balance_cents INTEGER, uuid TEXT)
payments (id INTEGER, payer_firm_id INTEGER, payee_firm_id INTEGER,
          amount_cents INTEGER, description TEXT)
```

Requests use firm **UUIDs**. The tables store internal integer **ids**. Money is stored
and computed as **integer cents**.

The service keeps the brief's columns and departs from the printed schema in three ways:

- It adds constraints: primary and foreign keys, `UNIQUE (uuid)`,
  `CHECK (balance_cents >= 0)` and `CHECK (amount_cents > 0)`.
- It widens both cents columns to `BIGINT`. A 32-bit `INTEGER` tops out near $21.4M.
- It adds one table, `idempotency_keys`
  ([ADR 0003](docs/adr/0003-optional-idempotency-key.md)). One row per executed
  request that carried a key: payer, key, fingerprint and the payment ids, written in the
  same transaction as the payments.

## Approach

The handler is thin: parse, call the payment service, map the outcome to a status code.
All of the money logic sits in one function that holds one transaction
(`app/payments/service.py`).

1. **Parse at the edge** (`app/api/schemas.py`). The raw body goes through strict pydantic
   models: unknown fields and wrong JSON types are refused at every level, UUIDs are
   lower-cased, and each `amount` is matched against the grammar with `re.fullmatch` and
   an explicit `[0-9]` class, then turned into integer cents. No float is involved.
   Every violation is one `Malformed` error, mapped to `400`. FastAPI's own `422` for
   validation never reaches a client.
2. **One `READ COMMITTED` transaction per request.** The funds check, the inserts into
   `payments` and every balance change commit together or not at all.
3. **One ordered lock query.** `SELECT ... WHERE uuid = ANY(...) ORDER BY id FOR UPDATE`
   locks the payer and every distinct payee before anything is checked, so two firms
   paying each other at once take their locks in the same order. Under `READ COMMITTED`
   the rows it returns are the latest committed versions, so the funds check sees the
   current balance and nobody can change it before the commit. It stays one query, runs
   first and always includes the payer
   ([ADR 0001](docs/adr/0001-lock-firm-rows-in-id-order.md)).
4. **Evaluate its result in the order of checks**
   ([ADR 0002](docs/adr/0002-malformed-400-denied-422.md)): `self_payment` (no database
   needed, so before a connection is taken), unknown payer, the `Idempotency-Key` lookup,
   unknown payee, then the funds check. A denial returns before anything is written.
5. **Write.** One `payments` row per entry, in request order, with internal firm ids.
   Amounts are summed per payee, so each firm gets one `UPDATE` however often it is
   named. With a key, its row (payer, key, fingerprint, payment ids) is inserted in the
   same transaction.
6. **Deadlocks and waits.** Postgres sorts before it locks, but that is how the executor
   behaves rather than a promise, so a deadlock (`40P01`) rolls back and reruns the
   transaction up to a bounded number of attempts, then answers `503` `deadlock`. A
   unique violation on the `idempotency_keys` primary key is rerun the same way, so the
   next pass replays instead of failing; it can't happen while the lock query is whole,
   and is the backstop for the day someone splits it. It is not counted as a deadlock.
   `lock_timeout` is set with `set_config(..., true)` inside the transaction, never with
   `SET` on a pooled connection, so it can't leak into another request. A wait that
   exceeds it is `503` `lock_timeout`; no free connection is `503` `pool_exhausted`.
   The pool tests a connection when it hands it out, so after a database restart the dead
   ones are replaced before a request uses them, not one failed request each. That check
   happens before anything is sent, so it is safe. A connection lost *during* a
   transaction is deliberately not retried or mapped to `503`: after `COMMIT` the payment
   may have been applied, and only a client that sent an `Idempotency-Key` can retry it
   safely. It stays a `500`.
7. **Correlation.** A middleware echoes a usable `X-Correlation-Id` or generates one, and
   a log record factory stamps it on every line, including the access line the app writes
   itself (uvicorn's access log is off).
8. **Safe retries.** The `Idempotency-Key` lookup runs after the lock query, so two
   requests with the same key wait on the same payer row: the second one sees the first
   one's key and replays. No "in progress" state is needed
   ([ADR 0003](docs/adr/0003-optional-idempotency-key.md)).

## Issues I ran into

- **The framework's default `422`.** FastAPI answers `422` when validation fails, which
  is the status the brief gives to a payer who can't cover the total. The route takes the
  raw body and parses it itself, so every format error is one `Malformed` and a typo can't
  look like a denial.
- **The correlation id on a `500`.** Starlette's error middleware sits outside user
  middleware, so a handler for `Exception` runs after the request's context is gone, and
  uvicorn then logs the traceback with no id. The correlation middleware turns an
  unhandled exception into the `500` itself, inside the context, and writes the access
  line itself because uvicorn's access line is written outside it.
- **Reading ids back in order.** `INSERT ... RETURNING` through `executemany(...,
  returning=True)` gives the ids in parameter order in one batch, which the positional
  `payment_ids` need. Folding the rows into one multi-row `INSERT` would not promise the
  order.
- **A test that deadlocked itself.** My first overspend test released its lock only after
  joining the request threads, so every request sat out the 10 s lock timeout. The lock is
  now released from inside the run, once the requests are queued behind it.
- **Proving the tests bite.** Removing `FOR UPDATE` makes the overspend test and the
  two-process test fail, and locking in request order makes the mutual-payment test fail.
  I checked each by hand before keeping the tests.
- **`httpx2`.** The test client the installed Starlette expects is `httpx2`, not `httpx`.

## Assumptions

Decided before the implementation; the terms are defined in [GLOSSARY.md](GLOSSARY.md).

- A balance equal to the request total is enough. The payer ends at zero.
- An unknown payer or payee denies the whole request (`422`, `unknown_firm`).
- A firm can't pay itself (`422`, `self_payment`).
- An empty `payments` list is malformed (`400`), and so is one with more than 1,000
  entries.
- The same payee may appear more than once in one request (the sample request does
  this). Each entry becomes its own payment.
- One amount has at most 10 integer digits, so no request can overflow `BIGINT`.
- Stored firm UUIDs are lower case, as in the seed. Request UUIDs are lower-cased before
  lookup.
- Decided for the idempotency key
  ([ADR 0003](docs/adr/0003-optional-idempotency-key.md)):
  - only executed requests are remembered, so a denied request can be sent again with the
    same key and is evaluated again;
  - the same payments in a different order are a different request, because `payment_ids`
    are positional and a reordered replay would hand back ids attached to the wrong
    payments;
  - a firm's `uuid` could change after it was paid, and a replay would still return its
    original result.
- Idempotency keys never expire.
- A firm UUID is the hyphenated 8-4-4-4-12 form. Other forms `uuid.UUID` accepts (no
  hyphens, braces, `urn:uuid:`) are malformed.
- A description is not "empty" if it holds any non-whitespace character; whitespace is
  whatever Python's `str.strip()` strips. It is stored exactly as sent. A NUL character
  (`\u0000`) is malformed because PostgreSQL text can't hold it; a lone surrogate
  (`\ud800`) is malformed too, and the JSON parser already refuses it.
- The body is read as JSON whatever its `Content-Type`.
- No authentication or authorization. The brief doesn't cover it.

### Numeric settings

Implementation choices within the ADRs, read from the environment
([`app/settings.py`](app/settings.py), [`.env.example`](.env.example)):

| Setting | Variable | Default | Why |
|---|---|---|---|
| Lock timeout | `LOCK_TIMEOUT_MS` | 3000 | A request never waits on a row lock for more than a few seconds |
| Deadlock attempts | `DEADLOCK_MAX_ATTEMPTS` | 3 | One retry rarely meets the same conflict twice; three bounds the work |
| `Retry-After` on a `503` | `RETRY_AFTER_SECONDS` | 1 | The causes pass in about a lock timeout or less |
| Pool size, per process | `DB_POOL_SIZE` | 10 | Two instances use 20 of Postgres's 100 connections |
| Wait for a connection | `DB_POOL_TIMEOUT_SECONDS` | 5 | Past this a request is `503` `pool_exhausted` rather than queued for ever |
| Database | `DATABASE_URL` | none | Required |

## Possible improvements

- Authentication, so that only the payer can send its own requests.
- Expiry for idempotency keys.
- A ledger or double-entry model instead of mutable balances.
- Audit fields (`created_at`, request id) on `payments`.
- A metric for `deadlocks_detected` and the `503` codes; today they are log lines.

## How to run

Requirements: Docker with Compose v2, GNU Make, and [uv](https://docs.astral.sh/uv/)
for the local workflow (uv installs Python 3.12 itself if it's missing). `make help`
lists every target.

### The whole stack in Docker

```bash
make up
```

This builds the image and starts, in the shape the brief describes:

- `db`: PostgreSQL 16 with the schema and the seed, on `localhost:5432`;
- `app`: **two** instances of the service (`APP_REPLICAS`), sharing that database;
- `lb`: nginx, round-robin across the instances, on `http://localhost:8000`.

nginx never resends a `POST` to a second instance: a request that timed out may have
committed, and only the client can decide to retry it (which is only safe with an
`Idempotency-Key`). `make logs` follows every container; `make down` stops them and
keeps the data.

### One instance, locally, with reload

```bash
make install   # uv sync: creates .venv with the runtime and dev dependencies
make run       # starts Postgres, then uvicorn --reload on http://localhost:8000
```

`make run` uses port 8000 too, so stop the Docker stack's app first (`make down`).

### Database and seed

| Target | What it does |
|---|---|
| `make seed` | Empties every table and loads the three firms again. Keeps the schema |
| `make reset-db` | **Deletes the data volume** and recreates the database from `migrations/schema.sql` and `migrations/seed.sql`. Postgres loads those files only into an empty volume, so run this after editing them |
| `make balances` | Prints every firm's balance |
| `make psql` | Opens `psql` on the service database |

The same Postgres also holds an empty `firm_payments_test` database for the tests,
which apply the schema themselves.

Ports and connection strings can be overridden from the environment; the defaults are in
[`.env.example`](.env.example). If 5432 or 8000 is taken, run for example
`POSTGRES_PORT=5433 APP_PORT=8001 make up`.

The seed ([`migrations/seed.sql`](migrations/seed.sql)) is the brief's, verbatim:

| id | Firm | Balance |
|---|---|---|
| 1 | Pinecrest CPA Group | $50,000.00 |
| 2 | Lopez Bookkeeping | $500.00 |
| 3 | Nair Tax Services | $2,000.00 |

## How to verify

### Sample request (expects `201`)

```bash
make seed      # start from the brief's balances
make sample    # sends the request below, then prints the balances
```

The script is [`scripts/sample-request.sh`](scripts/sample-request.sh); extra arguments
go to `curl`:

```bash
scripts/sample-request.sh http://localhost:8000 -H 'X-Correlation-Id: sample-1'
```

The same request by hand:

```bash
curl -i -X POST http://localhost:8000/bulk_payments \
  -H 'Content-Type: application/json' \
  -d '{
    "payer_firm_uuid": "3f1c9a2e-7b4d-4c1e-9a55-2d8e6f0b7c41",
    "payments": [
      {"amount": "6250",    "payee_firm_uuid": "e5f18b3c-2a9d-4c07-8e6b-1d4a7f9c3b25", "description": "Overflow returns, August 2026"},
      {"amount": "5800.5",  "payee_firm_uuid": "e5f18b3c-2a9d-4c07-8e6b-1d4a7f9c3b25", "description": "Amended returns, August 2026"},
      {"amount": "1200.75", "payee_firm_uuid": "8b2e4c71-0d3a-4f6e-b1c9-5a7d2e9f4c10", "description": "Bookkeeping cleanup, 3 clients"}
    ]
  }'
```

The total is $13,251.25. Balances after the request:

| Firm | Before | After |
|---|---|---|
| Pinecrest CPA Group | $50,000.00 | $36,748.75 |
| Lopez Bookkeeping | $500.00 | $1,700.75 |
| Nair Tax Services | $2,000.00 | $14,050.50 |

### Tests

```bash
make verify    # ruff (lint + format check), mypy --strict, pytest against Postgres
```

`make verify` starts Postgres if it isn't running. Tests always run against real
Postgres, never SQLite, because the locking and isolation this service relies on only
exist there. The pieces run on their own as `make lint`, `make typecheck` and
`make test`; `make fmt` fixes what ruff can.

What the tests cover, all through the endpoint where it matters:

- the amount grammar and every `400` case, and that a refusal writes nothing;
- each `422` code and the neutral `request_denied` body;
- concurrency: an overspend queued behind the payer's row lock, and two firms paying each
  other in a loop. Threads start from a `threading.Barrier` and the pool is at least as
  large as the thread count, so the requests contend in the database;
- failure paths: a deadlock injected once (the request executes, the retry counter reads
  1) and on every attempt (`503`), a held lock (`503` `lock_timeout`), and an exhausted
  pool (`503` `pool_exhausted`);
- after every database test, the sum of all balances is unchanged and none is negative.

```bash
make verify-full   # make verify, then the two parts below
```

`make verify-full` stops at the first failure and prints a pass line for each part:

1. `make verify`.
2. **Two-process test** (`make two-process`): starts two uvicorn processes, each with its
   own pool, against the test database; sends 70 concurrent requests split across them;
   stops them by PID; then checks that every answer is `201`, `422` or `503`, that the
   balances equal the seed plus exactly the requests that answered `201`, and that none is
   negative. It is marked `two_process`, so plain `make verify` skips it.
3. **Sample smoke** (`make smoke`): rebuilds and starts the Docker stack, then **re-seeds
   the service database `firm_payments`** (it empties every table), sends the brief's
   sample request through the load balancer and fails unless the answer is `201` and the
   balances are $36,748.75, $1,700.75 and $14,050.50.

## How I worked

The brief asks for the commit history and the AI tool usage.

- **Commit history:** kept as it was made. No squashing. Each feature landed through a
  pull request.
- **AI tool:** Claude Code (desktop app) was the only one. It fit in at every step:
  grilling the brief into the glossary and ADRs, setting up the tooling, then planning,
  implementation, review and QA through the
  [agent-orchestration](https://github.com/TeleginS/agent-orchestration) pipeline
  (vendored as `agent-orchestration/`; setup in [AGENTS.md](AGENTS.md)).
- **Raw AI conversation logs** in [`docs/ai-logs/`](docs/ai-logs/), one per session,
  exported by script and not edited (each file's header lists its few redactions, such as
  e-mail addresses):

  | Log | Session |
  |---|---|
  | [`00-init-session.md`](docs/ai-logs/00-init-session.md) | Agent skills and repo setup |
  | [`01-planning-session.md`](docs/ai-logs/01-planning-session.md) | Grilling the brief: glossary and ADRs 0001 to 0003 |
  | [`02-tooling-setup.md`](docs/ai-logs/02-tooling-setup.md) | Docker, Makefile, seed |
  | [`03-bulk-payments.md`](docs/ai-logs/03-bulk-payments.md) | The `POST /bulk_payments` endpoint |
  | [`04-idempotency-key.md`](docs/ai-logs/04-idempotency-key.md) | Implementing the optional `Idempotency-Key` (ADR 0003) |
  | [`05-readme-polish.md`](docs/ai-logs/05-readme-polish.md) | Polishing this README |

---

## Two quick questions

**How much time did you spend on this task?** Less than 3h.

**How proud are you of your work?** Good enough.
