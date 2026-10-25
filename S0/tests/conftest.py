"""Pytest configuration for the foundation test suite.

Points the ``foundation`` fixture at a real PostgreSQL database.  Uses
``FACTORY_TEST_DATABASE_URL`` when set (a live/managed instance), otherwise a
local cluster, otherwise a testcontainers instance via the harness.
"""

from __future__ import annotations

import os

# Default local development cluster used throughout this project's setup.
os.environ.setdefault(
    "FACTORY_TEST_DATABASE_URL",
    "postgresql+psycopg://factory:factory@localhost:5432/factory_test",
)

pytest_plugins = ["factory_foundation.testing.fixtures"]

import pytest  # noqa: E402

from factory_foundation.common import config as _config  # noqa: E402
from factory_foundation.common.context import reset_operation_context  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_context():
    # reset operation context and any per-process subsystem settings schema so
    # tests don't leak global registration state into one another
    reset_operation_context()
    _config._declared_keys.clear()  # noqa: SLF001
    yield
    reset_operation_context()
    _config._declared_keys.clear()  # noqa: SLF001
