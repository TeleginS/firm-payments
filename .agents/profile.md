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
- **Key dependencies**: FastAPI, SQLAlchemy 2 (or psycopg 3 directly), PostgreSQL 16 via
  docker compose, pytest. Prefer commonly used libraries (the brief requires it).

## Layout

No code yet. Planned:

```
app/            # FastAPI app, routes, schemas, service, db
tests/          # pytest, including concurrency tests against real Postgres
migrations/     # schema + seed (firms, payments)
docker-compose.yml
Makefile
```

- **Source**: `app/` (planned)
- **Tests**: `tests/` (planned)
- **Resources / assets**: `migrations/` for schema and the three-firm seed from the brief
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
  string `amount` is parsed once at the boundary (≤ 2 decimal places, > 0), never via
  float.
- **Data model layering**: request (UUIDs, decimal strings) → resolved (firm ids,
  integer cents) in the service before any write.
- **Concurrency**: the service runs as several instances. Correctness must come from the
  database (row locks taken in a consistent order, or a conditional
  `UPDATE ... WHERE balance_cents >= :total`), never from in-process locks.
- **Does NOT exist**: everything. No code has been written yet.

## Critical flags and release gates

- A denied request (insufficient funds) returns **422** and writes **nothing**: no
  payments rows, no balance change.
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

- 🔴 **Critical**: any float or `Decimal`→float on money; balance read-then-write without
  a lock or conditional update; partial writes on a denied request; locks taken in
  request order (deadlock risk when two firms pay each other at once).
- 🟠 **Architectural**: balance mutation outside the payment service; business logic in
  route handlers.
- 🟡 **Correctness**: duplicate payees in one request; payer paying itself; unknown
  payer or payee UUID; empty `payments`; amounts like `"0"`, `"1.234"`, `"-5"`, `"1e3"`,
  `" 5"`; integer overflow of cents.
- 🟢 **Quality**: assumptions recorded in the README, not only in code comments.

## Review exclusions

- `migrations/` seed data: matches the brief verbatim.
- `docs/ai-logs/` (if added): raw AI transcripts the brief requires unedited.

## Anti-patterns

- In-process locks (`threading.Lock`, `asyncio.Lock`) used as a concurrency control.
- `SELECT balance` then `UPDATE balance = <python value>` without `FOR UPDATE`.
- Mocking the database in tests that claim to cover concurrency.

## Known risk areas

- Concurrent bulk requests from the same payer: double-spend.
- Two firms paying each other at the same time: deadlock.
- Amount parsing at the edges of the allowed format.
- Status codes for invalid input versus insufficient funds: undecided. Decide in Step 0
  and record it in an ADR; don't let each stage choose its own.

## Production error source

None.

## Output language

- **Tracker issues and PR text**: English
- **Code, identifiers, commit messages**: English
- **Technical terms**: keep in their original form regardless

## Harness settings

- **Local permission config**: `.claude/settings.local.json` (untracked)
- **Habitual command prefixes** the optimizer must preserve in generalized patterns:
  `docker compose`, `uv run` (if adopted)

## Submission constraints

From the brief, and easy to break:

- The commit history is part of the deliverable. Keep commits meaningful; no squashing
  history away.
- Raw, unedited AI conversation logs must be included. Never summarize or clean them up.
- The README is written like a PR description. It covers the approach, issues, assumptions,
  improvements and how to run and verify the service. It ends with the time spent and
  how proud the author is.
