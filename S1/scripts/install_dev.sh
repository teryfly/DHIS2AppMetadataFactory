#!/usr/bin/env bash
# Development setup for S1 (CP-RT-001): a venv with the local S0 and this package.
set -euo pipefail
cd "$(dirname "$0")/.."

python3 -m venv .venv 2>/dev/null || true
VIRTUAL_ENV=.venv uv pip install -q -e ../S0 -e ".[dev]"
# The sibling subsystems S1 can run against for real.  Without them the real-provider tests
# (tests/service/test_real_s2.py, test_real_s2_s4.py) skip silently and CP-RT-005's
# compatibility gate compares nothing.  --no-deps because each one declares
# factory-foundation as its own sibling checkout, which uv must not resolve from an index.
VIRTUAL_ENV=.venv uv pip install -q --no-deps -e ../S2 -e ../S3 -e ../S4

: "${FACTORY_TEST_DATABASE_URL:=postgresql+psycopg://ubuntu@/factory_s1_test?host=/var/run/postgresql}"
export FACTORY_TEST_DATABASE_URL
echo "venv ready: .venv/bin/python -m pytest"
echo "test database: $FACTORY_TEST_DATABASE_URL"
