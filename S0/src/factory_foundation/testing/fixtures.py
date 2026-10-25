"""Reusable pytest fixtures (FD-TST-001).

Import these in a subsystem's ``conftest.py``::

    from factory_foundation.testing import foundation

The ``foundation`` fixture yields a :class:`FoundationHarness` on a freshly
created ``core`` schema with a memory object store, fixed clock and memory
secret backend already installed.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from factory_foundation.testing.harness import FoundationHarness, resolve_test_database_url


@pytest.fixture
def foundation() -> Iterator[FoundationHarness]:
    url = resolve_test_database_url()
    harness = FoundationHarness(url=url)
    harness.setup()
    try:
        yield harness
    finally:
        harness.teardown()
