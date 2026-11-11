-- Empties every table so seed.sql can run again. Used by `make seed`; the container's
-- first start doesn't need it.
TRUNCATE idempotency_keys, payments, firms RESTART IDENTITY;
