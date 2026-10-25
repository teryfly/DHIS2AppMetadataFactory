"""``factory_intelligence.testing`` — S3 test doubles and fixtures (AI-API-001).

00 doc 6.1 requires every library subsystem to ship a Fake of its own public API so
callers can develop without it; :class:`FakeIntelligenceApi` is that for S3, and the
harness is what lets S3's own tests run against real S0 and real S2.
"""

from __future__ import annotations

from factory_intelligence.testing.fakes import FakeIntelligenceApi
from factory_intelligence.testing.harness import (
    DEFAULT_TEST_URL,
    IntelligenceHarness,
    harness,
    resolve_test_database_url,
)

__all__ = [
    "IntelligenceHarness",
    "FakeIntelligenceApi",
    "harness",
    "resolve_test_database_url",
    "DEFAULT_TEST_URL",
]
