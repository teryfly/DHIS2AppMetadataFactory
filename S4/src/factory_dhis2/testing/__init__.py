"""factory_dhis2.testing — S4 的测试替身与夹具（DX-API-001）。"""

from __future__ import annotations

from factory_dhis2.testing.fakes import DEFAULT_VERSION, FakeDhis2Api, fake_issue
from factory_dhis2.testing.harness import (
    DEFAULT_TEST_URL,
    Dhis2Harness,
    harness,
    resolve_test_database_url,
)
from factory_dhis2.testing.instance import FakeDhis2Instance
from factory_dhis2.testing.state import record_connection_test
from factory_dhis2.testing.transport import FakeDhis2Transport

__all__ = [
    "DEFAULT_TEST_URL",
    "DEFAULT_VERSION",
    "Dhis2Harness",
    "FakeDhis2Api",
    "FakeDhis2Instance",
    "FakeDhis2Transport",
    "fake_issue",
    "harness",
    "record_connection_test",
    "resolve_test_database_url",
]
