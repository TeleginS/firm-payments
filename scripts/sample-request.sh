#!/usr/bin/env bash
# Sends the brief's sample bulk payment request. With the seed loaded it expects 201,
# and leaves Pinecrest at $36,748.75, Lopez at $1,700.75 and Nair at $14,050.50.
#
# Usage: scripts/sample-request.sh [base_url]   (default http://localhost:8000)
# Extra curl options can follow, e.g. -H 'Idempotency-Key: sample-1'.
set -euo pipefail

base_url="${1:-http://localhost:8000}"
shift || true

curl -sS -i -X POST "$base_url/bulk_payments" \
  -H 'Content-Type: application/json' \
  "$@" \
  -d '{
    "payer_firm_uuid": "3f1c9a2e-7b4d-4c1e-9a55-2d8e6f0b7c41",
    "payments": [
      {"amount": "6250",    "payee_firm_uuid": "e5f18b3c-2a9d-4c07-8e6b-1d4a7f9c3b25", "description": "Overflow returns, August 2026"},
      {"amount": "5800.5",  "payee_firm_uuid": "e5f18b3c-2a9d-4c07-8e6b-1d4a7f9c3b25", "description": "Amended returns, August 2026"},
      {"amount": "1200.75", "payee_firm_uuid": "8b2e4c71-0d3a-4f6e-b1c9-5a7d2e9f4c10", "description": "Bookkeeping cleanup, 3 clients"}
    ]
  }'
