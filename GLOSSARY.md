# Firm Payments

Firms on the platform settle what they owe each other by moving money between their balances. This context covers one firm paying several others in a single request.

## Language

### Parties

**Firm**:
An accounting practice on the platform that holds a balance.
_Avoid_: Company, account, customer

**Balance**:
The money a firm holds on the platform. It is never below zero.
_Avoid_: Funds, wallet

**Payer**:
The firm whose balance funds a bulk payment request.
_Avoid_: Sender, source, paying firm

**Payee**:
The firm whose balance receives a payment.
_Avoid_: Recipient, receiver, beneficiary

### Payments

**Payment**:
One amount moved from the payer's balance to one payee's balance, with a description.
_Avoid_: Transfer, transaction

**Bulk payment request**:
One payer's instruction to make one or more payments, carried out all or nothing. The same payee may appear in several of its payments.
_Avoid_: Batch, bulk payment, payout

**Request total**:
The sum of the amounts of every payment in a bulk payment request.

**Self-payment**:
A payment whose payee is the payer. A bulk payment request that contains one is denied.

### Retries

**Idempotency key**:
A token the payer's client chooses to mark one bulk payment request, so that sending it again cannot execute it twice. It is optional and belongs to one payer.
_Avoid_: Request id, dedup key

**Replay**:
A bulk payment request that repeats an already executed one under the same idempotency key: the same payer and the same payments in the same order. It moves no money and gets the original result back.
_Avoid_: Duplicate, retry

### Outcomes

**Executed**:
A bulk payment request whose payments are all recorded and whose balances have all moved.
_Avoid_: Accepted, created, processed

**Denied**:
A well-formed bulk payment request that the platform refuses to carry out. Nothing in it is applied. The reasons are: the payer's balance is below the request total, a firm is unknown, it contains a self-payment, or its idempotency key was already used for different payments.
_Avoid_: Rejected, declined, failed

**Malformed**:
A request that does not conform to the bulk payment request format, so the platform cannot read it as a set of payments.
_Avoid_: Invalid, bad request
