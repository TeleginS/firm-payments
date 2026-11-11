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
| Tests | pytest, run against real Postgres |

## API

### `POST /bulk_payments` _(path TBD)_

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
| `payer_firm_uuid` | Public UUID of the paying firm (`firms.uuid`) |
| `payments[].amount` | US dollars as a string, positive, at most 2 decimal places (`"300"`, `"5800.5"`, `"1200.75"`) |
| `payments[].payee_firm_uuid` | Public UUID of the receiving firm |
| `payments[].description` | Free text |

| Response | When |
|---|---|
| `201 Created` | The payments are recorded and the balances are updated |
| `422 Unprocessable Entity` | The payer's balance doesn't cover the request total, so nothing is written |
| _TBD_ | Malformed input, unknown firm UUIDs (see [Assumptions](#assumptions)) |

### Data model

The brief gives this schema:

```
firms    (id INTEGER, name TEXT, balance_cents INTEGER, uuid TEXT)
payments (id INTEGER, payer_firm_id INTEGER, payee_firm_id INTEGER,
          amount_cents INTEGER, description TEXT)
```

Requests use firm **UUIDs**. The tables store internal integer **ids**. Money is stored
and computed as **integer cents**.

## Approach

_TBD: fill in as the implementation lands._ The planned shape:

1. **Validate and normalize at the edge.** The service parses each `amount` string into
   integer cents with a strict pattern. It never goes through a float.
2. **Resolve firms.** UUIDs are mapped to `firms.id`.
3. **One transaction per request.** The funds check, the inserts into `payments` and
   every balance change commit together or not at all.
4. **Concurrency control in the database.** _(Decision pending, to be recorded as an ADR.)_
   - **Option A:** lock the payer and payee rows with `SELECT ... FOR UPDATE` in
     ascending `id` order, so that two firms paying each other at once can't deadlock.
   - **Option B:** debit with a conditional
     `UPDATE firms SET balance_cents = balance_cents - :total WHERE id = :payer AND balance_cents >= :total`,
     and deny the request when no row is updated.

## Issues I ran into

_TBD_

## Assumptions

_TBD: confirm or revise each one during the implementation._

- An unknown payer or payee UUID is a client error, not a 422 "insufficient funds".
  _(Status code TBD.)_
- A request with an empty `payments` list is rejected.
- The same payee may appear more than once in one request (the sample request does
  this).
- _Open:_ can a firm pay itself?
- No authentication or authorization. The brief doesn't cover it.

## Possible improvements

_TBD._ Candidates:

- An idempotency key, so a client retry doesn't apply the same request twice.
- A ledger or double-entry model instead of mutable balances.
- Audit fields (`created_at`, request id) on `payments`.

## How to run

_TBD: commands get finalized together with the code._

```bash
docker compose up -d db                 # Postgres with schema + seed
uvicorn app.main:app --reload           # service on http://localhost:8000
```

The seed loads the three firms from the brief:

| id | Firm | Balance |
|---|---|---|
| 1 | Pinecrest CPA Group | $50,000.00 |
| 2 | Lopez Bookkeeping | $500.00 |
| 3 | Nair Tax Services | $2,000.00 |

## How to verify

### Sample request (expects `201`)

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

_TBD._ Planned:

```bash
make verify        # lint, types, unit and integration tests against Postgres
make verify-full   # plus the concurrent-request test and the sample-request smoke test
```

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
