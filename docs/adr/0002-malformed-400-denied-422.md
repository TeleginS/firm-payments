---
status: accepted
---

# Malformed requests get 400, denied requests get 422

The brief assigns `422` to one outcome: the payer can't cover the request total. FastAPI also answers `422` by default when a request fails validation, which would make a typo in the JSON look the same as insufficient funds. We split refusals into two classes and override the framework default so each has its own status code.

- **Malformed → `400`.** The request breaks the format: unparseable JSON, a missing or unknown field at any level, an amount outside the grammar, a firm UUID that isn't a UUID, an empty `payments` list, more than 1,000 payments, a description that is empty or only whitespace, a bad `Idempotency-Key`. The client has a bug and must change the request.
- **Denied → `422`.** The request is well-formed and the platform refuses to carry it out. The body carries one code: `unknown_firm`, `self_payment`, `idempotency_key_reused` or the neutral `request_denied`. Nothing is applied.

A request that can't get its locks or a database connection in time is neither. It gets `503` with `Retry-After`, in the same error envelope, with code `lock_timeout`, `deadlock` (the retries from ADR 0001 ran out) or `pool_exhausted`. Nothing is wrong with the request and nothing was applied. A client treats all three the same way; the separate codes are for whoever reads the logs.

Every response carries `X-Correlation-Id`, and every log line for that request carries the same value. The service echoes the client's value when it is 1 to 128 visible ASCII characters and generates one otherwise.

## Order of checks

A response carries one reason, the first hit in this order:

1. Malformed.
2. `self_payment`. It needs no database.
3. `unknown_firm` because the payer is unknown. The body lists every missing UUID. This stops here: the idempotency key belongs to a payer, so without one there is nothing to replay.
4. Replay, or `idempotency_key_reused` (ADR 0003).
5. `unknown_firm` because a payee is unknown.
6. `request_denied`.

Steps 3 to 6 all read the result of the single lock query from ADR 0001. This is an order of evaluation, not an order of queries.

The replay is decided before the payees and the balance are looked at, because a replay must not be judged against today's state. An executed request returns its original result even if the payer's balance has since dropped below the request total, or one of its payees no longer resolves. The foreign key from `payments` stops a paid firm from being deleted, but nothing in the schema stops a firm's `uuid` from being changed, so the second case is defensive, not impossible.

## The funds denial is deliberately unnamed

A payer whose balance is below the request total gets `request_denied` with a generic message. The response never says "insufficient funds" and never includes the balance, the shortfall or the request total. The service has no authentication, so a response that named the cause would confirm a firm's financial position to anyone who asks. The three named codes are different: each describes something in the request that the client can see and fix.

This limits the leak; it does not close it. The brief fixes `422` for this case, and today `request_denied` has no other cause, so a careful caller can still infer it. What the neutral code buys is a contract that doesn't promise the meaning: later rules such as limits or a frozen firm can share the code without changing the API. The real cause is written to the server log under the correlation id, for support.

## Considered options

- **`422` for everything**, the framework default. Simplest, but a client can only tell "fix your code" from "top up and try again" by parsing the body.
- **`404` for an unknown payer.** Rejected: the URL names no firm, so no addressed resource is missing. An unknown payer and an unknown payee are the same kind of refusal.
- **An `insufficient_funds` code.** Clearer for an honest client, but it tells anyone who can reach the endpoint that a named firm is short of money.
- **Self-payment as Malformed**, since it can be spotted without the database. Rejected: the request reads fine and is refused by a platform rule, so the dividing line is "breaks the format" versus "refused by a rule", not "needs the database".
