# Firm Payments: bulk payment service

> **Status: work in progress.** This README follows the structure the brief asks for:
> a pull-request description for teammates. Sections marked _TBD_ are filled in as the
> work lands.

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

An optional `Idempotency-Key` header (1 to 255 visible ASCII characters, no spaces)
makes a retry safe: a request that was already executed under that key is not executed
again.

Every response carries an `X-Correlation-Id` header, and the service writes the same
value on every log line for that request. Send your own (1 to 128 visible ASCII
characters) and it is echoed; otherwise the service generates one.

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
| `payments[].description` | 1 to 1,000 characters, not only whitespace, stored exactly as sent |

Fields the format doesn't define are refused.

| Response | When |
|---|---|
| `201 Created` | **Executed.** The payments are recorded and the balances are updated. Body: `{"payment_ids": [...]}`. The ids are positional: the n-th id belongs to the n-th entry of `payments`. A replay under the same key returns the same body with `Idempotent-Replayed: true` |
| `400 Bad Request` | **Malformed.** The request breaks the format above. Nothing is written |
| `422 Unprocessable Entity` | **Denied.** The request is well-formed and the platform refuses it. Nothing is written |
| `503 Service Unavailable` | The service couldn't get its row locks or a database connection in time. Nothing is written; retry after `Retry-After`. Codes: `lock_timeout`, `deadlock`, `pool_exhausted` |

Every refusal body is `{"error": {"code": "...", "message": "..."}}`. The `422` codes:

| Code | Meaning |
|---|---|
| `self_payment` | A payment names the payer as its payee |
| `unknown_firm` | A firm UUID isn't on the platform. The body lists which |
| `idempotency_key_reused` | The key was already used for different content. The same payments in a different order count as different |
| `request_denied` | The platform won't execute the request. Today that means the payer's balance is below the request total |

A response carries one code, the first that applies: `self_payment`, then `unknown_firm`
for the payer, then a replay or `idempotency_key_reused`, then `unknown_firm` for a
payee, then `request_denied`. A replay is answered before the payees and the balance are
checked, so an executed request keeps returning its original result.

`request_denied` is neutral on purpose. The response never names the cause and never
includes a balance, because the service has no authentication. See
[ADR 0002](docs/adr/0002-malformed-400-denied-422.md).

### Data model

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
  ([ADR 0003](docs/adr/0003-optional-idempotency-key.md)).

## Approach

_TBD: fill in as the implementation lands._ The planned shape:

1. **Validate and normalize at the edge.** The service parses each `amount` string into
   integer cents with a strict pattern. It never goes through a float.
2. **Resolve firms.** UUIDs are mapped to `firms.id`.
3. **One transaction per request.** The funds check, the inserts into `payments` and
   every balance change commit together or not at all.
4. **Concurrency control in the database.** One query locks the payer and every payee
   with `SELECT ... ORDER BY id FOR UPDATE` before the funds check, so two firms paying
   each other at once can't deadlock. A deadlock is retried, and `lock_timeout` bounds
   the wait. See [ADR 0001](docs/adr/0001-lock-firm-rows-in-id-order.md) for why this
   beat a conditional `UPDATE` and `SERIALIZABLE`.
5. **Safe retries.** With an `Idempotency-Key`, the key is stored in the same
   transaction as the payments, and looked up after the locks are held.

## Issues I ran into

_TBD_

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
- Only executed requests are remembered under an idempotency key. A denied request can
  be sent again with the same key and is evaluated again.
- Under one idempotency key, the same payments in a different order are a different
  request. `payment_ids` are positional, so treating a reordered request as a replay
  would hand back ids attached to the wrong payments
  ([ADR 0003](docs/adr/0003-optional-idempotency-key.md)).
- A firm's `uuid` could change after it was paid. A replay still returns its original
  result.
- No authentication or authorization. The brief doesn't cover it.

## Possible improvements

_TBD._ Candidates:

- Authentication, so that only the payer can send its own requests.
- Expiry for idempotency keys. They are kept forever today.
- A ledger or double-entry model instead of mutable balances.
- Audit fields (`created_at`, request id) on `payments`.

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
committed, and only the client can decide to retry it (safely, with an
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
go to `curl`, for example `scripts/sample-request.sh http://localhost:8000 -H 'Idempotency-Key: sample-1'`.
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

_TBD:_ `make verify-full` adds the concurrent-request test across two service processes
and the sample-request smoke test once the endpoint lands.

## How I worked

The brief asks for the commit history and the AI tool usage.

- **Commit history:** kept as it was made. No squashing.
- **AI tools:**
  - **Claude Code**: planning, implementation, review and QA through the
    [agent-orchestration](https://github.com/TeleginS/agent-orchestration) pipeline
    (vendored as `agent-orchestration/`; setup in [AGENTS.md](AGENTS.md)).
  - _Others: TBD._
- **Raw AI conversation logs:** `docs/ai-logs/` _(TBD)_. These are unedited, as the
  brief requires.

---

## Two quick questions

**How much time did you spend on this task?**
_TBD: less than 3h / 3h to 6h / more than 6h (how long?)_

**How proud are you of your work?**
_TBD: very proud / fairly proud / good enough / somewhat disappointed_
