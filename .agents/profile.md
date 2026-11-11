# Profile: FirmPayments

> **Precedence: observed code > this profile > the role prompts.** Agents follow the code
> when this file is wrong, and report the drift. Fix it when they do.
>
> Written before any code existed. Every "planned" item below is a target, not a fact:
> once the code lands, the code wins.

## Identity

- **Project**: FirmPayments: a web service that accepts a bulk payment request from one
  firm and executes it all-or-nothing. Built for the TaxDome "Firm Payments" technical
  challenge; the brief is the local, untracked `TA Firm Payments.pdf` in the repo root.
- **Repository root**: `/Users/sergei/Developer/FirmPayments`
- **Remote / tracker**: `TeleginS/firm-payments` (GitHub Issues, public repo)
- **Base branch**: `main`

## Stack

- **Language / runtime**: Python 3.12+ (planned)
- **Framework**: FastAPI (planned)
- **Minimum platform / target**: Linux/macOS server, several load-balanced instances in
  front of one PostgreSQL database
- **Architecture pattern**: thin HTTP layer → service function holding the transaction →
  SQL. No ORM-level magic around the money path.
- **Key dependencies**: FastAPI, psycopg 3 with `psycopg_pool` (plain SQL, no ORM,
  synchronous handlers), PostgreSQL 16 via docker compose, pytest, `uv`. Prefer commonly
  used libraries (the brief requires it).
- **Decisions**: `docs/adr/` (0001 locking, 0002 status codes, 0003 idempotency).
  Vocabulary: `GLOSSARY.md`.

## Layout

No code yet. Planned:

```
app/            # FastAPI app, routes, schemas, service, db
tests/          # pytest, including concurrency tests against real Postgres
migrations/     # schema.sql + seed.sql, loaded by the Postgres container; no migration tool
docker-compose.yml
Makefile
```

- **Source**: `app/` (planned)
- **Tests**: `tests/` (planned)
- **Resources / assets**: `migrations/` for schema and the three-firm seed from the brief.
  The schema keeps the brief's columns, adds constraints, widens the cents columns to
  `BIGINT`, and adds one `idempotency_keys` table (ADR 0003).
- **Naming trap**: requests identify firms by `uuid` (TEXT); the `payments` table stores
  the internal integer `firms.id`. Never write a UUID into `payer_firm_id` /
  `payee_firm_id`.

## Verification command

None yet. Adding it is the first improvement worth making. Planned:

```bash
make verify         # ruff + mypy + pytest against dockerized Postgres
make verify-full    # plus the multi-process concurrency test and the sample-request smoke test
```

- **What it enforces**: lint, types, unit + integration tests (planned)
- **Healthy output**: (fill in once it exists)
- **Typical duration**: (fill in once it exists; target < 1 minute)
- **QA variant**: `make verify-full`
- **Time budget**: 15 minutes
- **Base-version checks**: `git worktree add` at the base commit and run only the failing
  tests there
- **Shared resources**: Postgres container on port 5432. If another session holds it, use
  a separate compose project name (`COMPOSE_PROJECT_NAME`) and port.

## Commands

```bash
# Start database
docker compose up -d db

# Run service
uvicorn app.main:app --reload

# Test — the mandatory QA gate
pytest

# Lint / format
ruff check . && ruff format --check .

# Recreate the database from schema.sql + seed.sql (deletes the data volume).
# Postgres loads those files only into an empty volume.
make reset-db
```

- **Required prefix or environment**: `DATABASE_URL` (see `.env.example` once it exists)
- **Known-bad invocations**: tests run against SQLite. They hide every locking and
  isolation bug this service exists to get right. Always use Postgres.

## Architecture invariants

- **Core modules**: (planned) `app/api` (HTTP + validation), `app/payments` (bulk
  payment service), `app/db` (engine, sessions, schema)
- **Single decision points**: the funds check and the balance updates for a bulk
  request happen in exactly one function, inside one database transaction. Nothing
  else touches `balance_cents`.
- **State and persistence**: PostgreSQL. Money is integer cents end to end. The decimal
  string `amount` is parsed once at the boundary (a full match of `[0-9]{1,10}(\.[0-9]{1,2})?`, > 0), never via float. `\d` and `$`
  are wrong here: `\d` accepts non-ASCII digits that `int()` then converts, and `$`
  accepts a trailing newline.
- **Data model layering**: request (UUIDs, decimal strings) → resolved (firm ids,
  integer cents) in the service before any write.
- **Concurrency**: the service runs as several instances. Correctness comes from the
  database, never from in-process locks: one `READ COMMITTED` transaction locks every
  involved firm row with a single `SELECT ... ORDER BY id FOR UPDATE`, then checks funds
  (ADR 0001). Retry on deadlock (`40P01`) and a per-transaction `lock_timeout` are
  required parts of that design.
- **Idempotency**: the `Idempotency-Key` lookup happens after the lock query, in the
  same transaction (ADR 0003). Don't move it earlier, and don't split the lock query:
  the payer's row lock is what keeps two requests with the same key apart. A unique
  violation on the key is the backstop and reruns the transaction like a deadlock.
- **Order of checks**: Malformed → `self_payment` → unknown payer → replay or
  `idempotency_key_reused` → unknown payee → `request_denied` (ADR 0002). One lock
  query; this is the order its result is evaluated in.
- **Correlation**: every response carries `X-Correlation-Id` and every log line for the
  request carries the same value. The real cause of a `request_denied` goes to the log,
  never to the response.
- **Does NOT exist**: everything. No code has been written yet.

## Critical flags and release gates

- A denied request returns **422** and writes **nothing**: no payments rows, no balance
  change. A malformed request returns **400**. A request that can't get its locks or
  a connection returns **503** with `Retry-After` and code `lock_timeout`, `deadlock` or
  `pool_exhausted`, in the same error envelope (ADR 0002).
- A denial for lack of funds returns the neutral code `request_denied`. The response
  never names the cause and never includes a balance, a shortfall or the request total.
- An accepted request returns **201** and writes all payments rows, the payer debit and
  every payee credit in one transaction.
- The sum of all `balance_cents` is unchanged by any request.

## Human gates

- **Merge**: the owner merges; `main` and `master` are protected.
- **Release**: no release process. `docker push`, `twine upload` and `gh release create`
  are blocked for agents.
- **Secrets**: `.env` and `.env.*`; agents read `.env.example` instead.
- **Enforcement installed**: Claude Code PreToolUse hooks (merge, release, secrets gates)
  in `.claude/settings.json`; git `pre-push` in `.githooks/` once the owner runs
  `git config core.hooksPath .githooks`. No server-side branch rule yet.
- **Known exceptions**: None.

## Access control / gating rules

None. There is no authentication in the brief; the payer is whoever `payer_firm_uuid`
names.

## Localization

None. API only, English.

## Review checklist additions

- 🔴 **Critical**: a response that says or implies "insufficient funds" or exposes a
  balance; any float or `Decimal`→float on money; balance read-then-write without
  a lock or conditional update; partial writes on a denied request; locks taken in
  request order (deadlock risk when two firms pay each other at once).
- 🟠 **Architectural**: balance mutation outside the payment service; business logic in
  route handlers.
- 🟡 **Correctness**: duplicate payees in one request; payer paying itself; unknown
  payer or payee UUID; empty `payments`; amounts like `"0"`, `"1.234"`, `"-5"`, `"1e3"`,
  `" 5"`; integer overflow of cents.
- 🟢 **Quality**: assumptions recorded in the README, not only in code comments.

## Required tests

Agreed during design. All run against real Postgres.

- **Format**: the amount grammar, including refusal of `"٣٠٠"`, `"１۲۰۰"` and `"300\n"`;
  a description of only spaces, tabs, carriage returns or newlines; an idempotency key
  with a space; an unknown field inside a payment entry; `payments: []`.
- **Outcomes over HTTP**: the brief's sample request with its before and after balances;
  each Malformed case; each Denied code.
- **A refusal writes nothing**: after a `400`, a `422` and a `503`, there are no new
  `payments` rows, no key row, and no balance has changed.
- **Idempotency**:
  - sequential replay: same `201`, same `payment_ids`, `Idempotent-Replayed: true`;
  - same key with a different payload, and with the same payments reordered:
    `idempotency_key_reused`;
  - a replay still returns `201` after the payer's balance has dropped below the request
    total (this pins the order of checks);
  - a denied request sent again with the same key is evaluated again;
  - the same key sent in parallel executes once.
- **Concurrency**: threads fire from behind a `threading.Barrier`, and the pool is at
  least as large as the thread count.
  - Overspend: the test holds the payer's row lock, queues the requests behind it, then
    releases, so the contention is certain.
  - Two firms paying each other in a loop: the deadlock-retry counter stays at zero.
  - These run with a `lock_timeout` well above the test's own hold time, so they can
    assert the exact mix of results. Only the lock-timeout test shrinks it.
- **Retry, by injected fault** (a real deadlock's victim depends on timing and would
  flake):
  - `DeadlockDetected` once: the request is executed and the retry counter reads 1;
  - `DeadlockDetected` on every attempt: `503` with code `deadlock`, nothing applied;
  - a unique violation on the key once: the transaction reruns and completes.
- **Lock timeout**: an outside transaction holds the payer's row: `503` with code
  `lock_timeout`.
- **Invariants after every test**: the sum of all balances is unchanged and none is
  negative. In concurrent tests every result is one of `201`, `422`, `503`, and the
  executed requests never total more than the payer's starting balance.
- **Two processes** (`make verify-full`): concurrent requests against two service
  processes, then the same invariants.

## Review exclusions

- `migrations/` seed data: matches the brief verbatim.
- `docs/ai-logs/` (if added): raw AI transcripts the brief requires unedited.

## Anti-patterns

- In-process locks (`threading.Lock`, `asyncio.Lock`) used as a concurrency control.
- `SELECT balance` then `UPDATE balance = <python value>` without `FOR UPDATE`.
- Mocking the database in tests that claim to cover concurrency.
- `SET lock_timeout` on a pooled connection. Use `set_config('lock_timeout', %s, true)`
  inside the transaction; `SET LOCAL` takes no bind parameter.
- "Parallel" tests whose threads start one after another, or whose pool is smaller than
  the thread count. They queue in the application and prove nothing about the database.
- Relying on the container's init scripts in tests. Tests apply `schema.sql` themselves.

## Known risk areas

- Concurrent bulk requests from the same payer: double-spend.
- Two firms paying each other at the same time: deadlock.
- Amount parsing at the edges of the allowed format.
- Status codes and the order of checks are decided in ADR 0002. Don't let a stage
  choose its own.
- A replay must still succeed after the payer's balance has dropped, so the key lookup
  precedes the funds check.

## Production error source

None.

## Output language

- **Tracker issues and PR text**: English
- **Code, identifiers, commit messages**: English
- **Technical terms**: keep in their original form regardless

## Harness settings

- **Local permission config**: `.claude/settings.local.json` (untracked)
- **Habitual command prefixes** the optimizer must preserve in generalized patterns:
  `docker compose`, `uv run`

## Submission constraints

From the brief, and easy to break:

- The commit history is part of the deliverable. Keep commits meaningful; no squashing
  history away.
- Raw, unedited AI conversation logs must be included. Never summarize or clean them up.
- The README is written like a PR description. It covers the approach, issues, assumptions,
  improvements and how to run and verify the service. It ends with the time spent and
  how proud the author is.
