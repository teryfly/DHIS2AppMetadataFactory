"""The Registry refs S1 forwards to S4 are plain mappings, on the real S2 path.

``BlueprintPort.registry_object_refs`` promises ``list[dict]`` and S4's discovery scope
rejects anything that is not a ``Mapping`` (DX-DSC-002).  Real S2 answers
``RegistryEntry`` *models*, so the whole Run line — BIND_COMPILE's discovery in particular —
died in S4 with ``DiscoveryScopeInvalidError`` while every fake, which returns dicts, passed.
"""

from __future__ import annotations

from typing import Any

import pytest

from factory_control.adapters.registry import BlueprintAdapter

needs_s2 = pytest.mark.skipif(not __import__("importlib").util.find_spec("factory_blueprint"), reason="S2 not installed")


def _adapter(rows: list[Any]) -> BlueprintAdapter:
    def get_registry(project_id: str, version: str, environment_key: str | None = None, *, session: Any = None):
        del project_id, version, environment_key, session
        return list(rows)

    return BlueprintAdapter(resolver=lambda package, name: get_registry)


@needs_s2
def test_real_registry_models_are_forwarded_as_mappings() -> None:
    from factory_blueprint.dto.registry import RegistryEntry

    rows = [
        RegistryEntry(
            business_key="de-weight",
            dhis2_type="dataElement",
            disposition="CREATE",
            status="APPROVED",
            uid="a8Un39j3e1C",
            code="SB_L2_DE_WEIGHT",
            managed=True,
        ),
        # an entry S2 has not given an identifier yet: the target instance cannot be asked
        # for it, and S4 refuses such a reference outright
        RegistryEntry(
            business_key="de-unallocated",
            dhis2_type="dataElement",
            disposition="CREATE",
            status="PROPOSED",
            managed=True,
        ),
    ]

    refs = _adapter(rows).registry_object_refs("prj-1", "0.5.0", "sbtest", session=object())

    assert refs == [
        {
            "businessKey": "de-weight",
            "dhis2Type": "dataElement",
            "uid": "a8Un39j3e1C",
            "code": "SB_L2_DE_WEIGHT",
            "disposition": "CREATE",
            "status": "APPROVED",
        }
    ]
    # the guard S4 applies, asserted here rather than assumed
    from collections.abc import Mapping

    assert all(isinstance(ref, Mapping) for ref in refs)
    assert refs and not isinstance(rows[0], Mapping), "the fake stopped proving anything"
