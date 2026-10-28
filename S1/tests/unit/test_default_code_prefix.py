"""The project-scoped default ``generationPolicy.codePrefix`` (BP-REG-009, BPV-ENV-002)."""

from __future__ import annotations

import pytest

from factory_control.adapters.registry import _default_code_prefix
from factory_control.rest.schemas.requests import _CODE_PREFIX


@pytest.mark.parametrize(
    ("display_name", "project_id", "expected"),
    [
        ("ANC 2030", "prj-01M5MD8DWKED857A33PJKSZCTJ", "ANC2030"),
        ("child-health", "prj-01M5MD8DWKED857A33PJKSZCTJ", "CHILDHEALTH"),
        ("2030 Plan", "prj-01M5MD8DWKED857A33PJKSZCTJ", "P2030PLAN"),
        # no ASCII at all: the globally unique id tail keeps the prefix project-scoped
        ("儿童保健项目", "prj-01M5MD8DWKED857A33PJKSZCTJ", "PRJKSZCTJ"),
        ("", "prj-01M5MD8DWKED857A33PJKSZCTJ", "PRJKSZCTJ"),
    ],
)
def test_default_code_prefix_is_project_scoped(
    display_name: str, project_id: str, expected: str
) -> None:
    assert _default_code_prefix(display_name, project_id) == expected


def test_derived_prefix_satisfies_the_create_body_pattern() -> None:
    """Whatever the derivation makes, S2 must accept it where a typed prefix is accepted."""
    for name in ("ANC 2030", "儿童保健", "1999 pilot", None, ""):
        prefix = _default_code_prefix(name, "prj-01M5MD8DWKED857A33PJKSZCTJ")
        assert _CODE_PREFIX.fullmatch(prefix), prefix
