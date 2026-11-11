.DEFAULT_GOAL := help
SHELL := /bin/bash

COMPOSE := docker compose
PSQL := $(COMPOSE) exec -T db psql -U firm_payments -v ON_ERROR_STOP=1 --quiet
APP_PORT ?= 8000
POSTGRES_PORT ?= 5432
DATABASE_URL ?= postgresql://firm_payments:firm_payments@localhost:$(POSTGRES_PORT)/firm_payments
TEST_DATABASE_URL ?= postgresql://firm_payments:firm_payments@localhost:$(POSTGRES_PORT)/firm_payments_test
export DATABASE_URL TEST_DATABASE_URL

##@ Setup

.PHONY: install
install: ## Install Python dependencies into .venv (uv)
	uv sync

.PHONY: hooks
hooks: ## Use the tracked git hooks (.githooks/pre-push)
	git config core.hooksPath .githooks

##@ Run

.PHONY: up
up: ## Build and start the stack: Postgres, app replicas, load balancer on :8000 (APP_PORT)
	$(COMPOSE) up -d --build --wait

.PHONY: down
down: ## Stop the stack; keeps the data volume
	$(COMPOSE) down

.PHONY: logs
logs: ## Follow logs from every container
	$(COMPOSE) logs -f

.PHONY: db
db: ## Start Postgres only
	$(COMPOSE) up -d --wait db

.PHONY: run
run: db ## Run one app instance locally with reload on :8000 (APP_PORT) (stop the stack's app first)
	uv run uvicorn app.main:app --reload --port $(APP_PORT) --no-access-log

##@ Database

.PHONY: seed
seed: db ## Empty every table and load the three firms from the brief again
	cat migrations/reset.sql migrations/seed.sql | $(PSQL) -d firm_payments --single-transaction --output=/dev/null
	@$(MAKE) --no-print-directory balances

.PHONY: reset-db
reset-db: ## DELETE the data volume and recreate the database from schema.sql + seed.sql
	$(COMPOSE) down -v
	$(COMPOSE) up -d --wait db

.PHONY: balances
balances: db ## Print every firm's balance
	@$(PSQL) -d firm_payments -c \
	  "SELECT id, name, to_char(balance_cents / 100.0, 'FM\$$999,999,999,990.00') AS balance, uuid FROM firms ORDER BY id"

.PHONY: psql
psql: db ## Open psql on the service database
	$(COMPOSE) exec db psql -U firm_payments -d firm_payments

.PHONY: test-db
test-db: db
	@$(PSQL) -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = 'firm_payments_test'" | grep -q 1 \
	  || $(PSQL) -d postgres -c "CREATE DATABASE firm_payments_test"

##@ Verify

.PHONY: lint
lint: ## Lint and check formatting (ruff)
	uv run ruff check .
	uv run ruff format --check .

.PHONY: fmt
fmt: ## Fix lint findings and format the code (ruff)
	uv run ruff check --fix .
	uv run ruff format .

.PHONY: typecheck
typecheck: ## Type-check (mypy, strict)
	uv run mypy

.PHONY: test
test: test-db ## Run the tests against Postgres
	uv run pytest

.PHONY: verify
verify: lint typecheck test ## Lint, types and tests: the gate before every push

.PHONY: sample
sample: ## Send the brief's sample request to :8000 (APP_PORT), then print the balances
	scripts/sample-request.sh "http://localhost:$(APP_PORT)"
	@echo
	@$(MAKE) --no-print-directory balances

##@ Help

.PHONY: help
help: ## List the targets
	@awk 'BEGIN {FS = ":.*##"} \
	  /^##@/ {printf "\n\033[1m%s\033[0m\n", substr($$0, 5)} \
	  /^[a-zA-Z_-]+:.*##/ {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
