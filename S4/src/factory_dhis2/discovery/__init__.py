"""元数据发现（DX-DSC）。"""

from __future__ import annotations

from factory_dhis2.discovery.canonical import changed_fields, normalize_object, object_hash
from factory_dhis2.discovery.defaults import read_defaults
from factory_dhis2.discovery.orgunits import read_org_units
from factory_dhis2.discovery.scope import resolve as resolve_scope
from factory_dhis2.discovery.service import (
    discover,
    discovery_document,
    latest_discovery,
)

__all__ = [
    "changed_fields",
    "discover",
    "discovery_document",
    "latest_discovery",
    "normalize_object",
    "object_hash",
    "read_defaults",
    "read_org_units",
    "resolve_scope",
]
