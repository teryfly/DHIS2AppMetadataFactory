"""版本与能力 Profile（DX-CAP）。"""

from __future__ import annotations

from factory_dhis2.capability.probe import ProbeResult, probe_instance
from factory_dhis2.capability.service import (
    CORE_SCHEMA_TYPES,
    current_document,
    default_schema_types,
    freshness,
    mark_previous_stale,
    probe,
    publish,
    require_current_profile,
)

__all__ = [
    "CORE_SCHEMA_TYPES",
    "ProbeResult",
    "current_document",
    "default_schema_types",
    "freshness",
    "mark_previous_stale",
    "probe",
    "probe_instance",
    "publish",
    "require_current_profile",
]
