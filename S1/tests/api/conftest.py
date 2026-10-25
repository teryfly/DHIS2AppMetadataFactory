"""Fixtures the identity tests share, so "a secure session" has exactly one definition.

``secure`` and ``enforced`` were written for ``test_identity_m4.py``.  They belong here because a
second file now tests the same session behaviour (lock-out), and an https test secure that two
files build differently is a fixture that can disagree with itself: ``FR-SEC-005`` makes the session
cookie ``Secure``, so any test that expects a cookie to travel has to be over https, and M4
enforcement needs the settings, the config accessor *and* the policy switched together.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from factory_control import iam
from factory_control.config import Settings
from factory_control.identity.policy import RolePermissionPolicy


@pytest.fixture
def secure(harness: Any) -> Iterator[TestClient]:
    """A secure over https: FR-SEC-005 makes the session cookie ``Secure``, and httpx will not
    send such a cookie to an http origin, so the MVP's plain-http test secure cannot carry one."""
    from factory_control.rest.app import create_app

    with TestClient(create_app(harness.runtime), base_url="https://testserver") as test_client:
        yield test_client


@pytest.fixture
def enforced(secure: TestClient, harness: Any, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Turn M4 on for one test: the runtime's settings, the config accessor, and the policy."""
    settings: Settings = harness.runtime.settings
    monkeypatch.setattr(settings, "auth_mode", "local")
    monkeypatch.setattr(settings, "https_only", True)
    monkeypatch.setattr("factory_control.config.get_settings", lambda: settings)
    previous = iam.get_permission_policy()
    iam.set_permission_policy(RolePermissionPolicy())
    try:
        yield settings
    finally:
        iam.set_permission_policy(previous)
