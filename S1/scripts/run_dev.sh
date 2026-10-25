#!/usr/bin/env bash
# Run factory-api locally: migrate, then serve REST + SSE on :8080.
set -euo pipefail
cd "$(dirname "$0")/.."

export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://ubuntu@/factory_s1_dev?host=/var/run/postgresql}"
export FACTORY_MIGRATIONS_MODE="${FACTORY_MIGRATIONS_MODE:-apply}"
export FACTORY_WORKFLOW_ENGINE="${FACTORY_WORKFLOW_ENGINE:-inprocess}"
export FACTORY_PROVIDER_MODE="${FACTORY_PROVIDER_MODE:-auto}"
export OBJECT_STORE_URL="${OBJECT_STORE_URL:-memory://}"
export SECRET_BACKEND="${SECRET_BACKEND:-memory}"
# the interim S4 fake must know these keys before a project may register them
export FACTORY_FAKE_ENVIRONMENTS="${FACTORY_FAKE_ENVIRONMENTS:-dev:TEST,prod:PRODUCTION}"

exec .venv/bin/factory-api "$@"
