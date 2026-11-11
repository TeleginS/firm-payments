---
status: accepted
---

# Optional idempotency key, remembered only for executed requests

Behind a load balancer a client can time out on a request that did commit, retry it, and pay everyone twice. No lock prevents that. So a bulk payment request may carry an `Idempotency-Key` header, and an executed request under a key is never executed again. This adds one table, `idempotency_keys`, to the two the brief defines, which is a deliberate departure from its schema: the key is unique per payer, and `payments` has many rows per request, so the key can't live there.

- The header is **optional**. The brief's sample requests carry none and must work as printed; without a key there is no retry protection. A key is 1 to 255 visible ASCII characters with no spaces.
- Only **executed** requests are remembered. The key, a fingerprint of the request and the created payment ids are written in the same transaction as the payments, so they commit or roll back together. A denied request leaves no trace, and sending it again evaluates it again.
- A **replay** returns the original `201` and the same payment ids, with `Idempotent-Replayed: true`. It is decided before the payees and the balance are checked (ADR 0002).
- The same key with **different content** is denied (`idempotency_key_reused`).

## What counts as the same content

The fingerprint covers the payer and the payments **in request order**, compared after parsing: amounts as integer cents, so `"300"` and `"300.00"` match; UUIDs lower-cased; descriptions byte for byte.

Order is part of the content because `payment_ids` are positional: the n-th id belongs to the n-th entry. If a reordered request counted as a replay, the client would get the original order's ids and attach them to the wrong payments, with no error to tell it so. We chose a loud failure over a silent misattribution. A false `idempotency_key_reused` applies nothing and tells the client exactly what happened, and a transport-level retry sends the same bytes anyway.

## Why two requests with the same key can't both execute

The key is looked up after the single lock query from ADR 0001, and that query always includes the payer. A key belongs to one payer, so two concurrent requests with the same key wait on the same payer row. The second reaches the lookup only after the first has committed, sees its key, and replays. No "in progress" state is needed.

That argument holds only while the lock query stays whole and stays first. If someone splits it, for example to answer replays without taking locks, the payer row stops being the mutex and two requests can both pass the lookup. The unique constraint on `(payer_firm_id, key)` is the backstop for that day: a unique violation on the key is handled like a deadlock, by rolling back and running the transaction again, so the second pass finds the key and replays instead of returning a `500`.

## Considered options

- **No idempotency, documented as an improvement.** Cheapest, but it leaves double payment on retry open, which is the most likely way this service loses money in production.
- **Remember every outcome, including denials**, as some payment APIs do. Rejected: a payer who was short, got paid, and retries would be told the request is denied forever under that key. It would also need the key stored outside the payment transaction, which brings back the "in progress" state.
- **Required key.** Rejected because the brief's sample requests would stop working.
- **Insert the key row first and let the unique index be the mutex.** Rejected: it adds a second mutex beside the payer row lock we already hold, and an extra query to resolve the payer before anything else.
- **Order-insensitive comparison** (sort the payments, then fingerprint). Rejected for the reason above. It would only be safe with a `201` body that describes each payment instead of listing ids by position.

## Consequences

- Keys never expire. Expiry is a follow-up, not built here.
- The lookup must stay after the lock query. Moving it earlier reopens the race between two requests with the same key.
