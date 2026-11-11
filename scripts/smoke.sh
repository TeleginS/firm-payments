#!/usr/bin/env bash
# Sends the brief's sample request and checks the answer: 201, and Pinecrest, Lopez and
# Nair left at $36,748.75, $1,700.75 and $14,050.50. Exits non-zero otherwise.
#
# Expects the stack to be up with the seed loaded (make up && make seed); `make smoke`
# does both, and then this. It reads the service database through the db container.
#
# Usage: scripts/smoke.sh [base_url]   (default http://localhost:8000)
set -euo pipefail

base_url="${1:-http://localhost:8000}"
expected_balances="3674875 170075 1405050"

response="$("$(dirname "$0")/sample-request.sh" "$base_url")"
status="$(printf '%s\n' "$response" | head -n 1 | awk '{print $2}')"
if [[ "$status" != "201" ]]; then
  printf '%s\n' "$response" >&2
  echo "smoke: expected HTTP 201, got ${status:-no response}" >&2
  exit 1
fi

balances="$(docker compose exec -T db psql -U firm_payments -d firm_payments -tA \
  -c 'SELECT balance_cents FROM firms ORDER BY id' | tr '\n' ' ' | sed 's/ $//')"
if [[ "$balances" != "$expected_balances" ]]; then
  echo "smoke: balances in cents are '$balances', expected '$expected_balances'" >&2
  exit 1
fi

echo "smoke: 201 and balances $expected_balances"
