"""版本能力 Profile 的数据与装载（DX-CAP-001）。"""

from __future__ import annotations

from factory_dhis2.profiles.base import (
    TYPE_RESOURCE_PREFIX,
    AssociatedDataCheck,
    DeprecatedItem,
    ObjectSchemaDef,
    ResourceEntry,
    VersionProfile,
    load_profile,
)
from factory_dhis2.profiles.registry import (
    available_series,
    load_builtin_profiles,
    profile_for_series,
    profile_for_version,
    register_profile,
)

__all__ = [
    "AssociatedDataCheck",
    "DeprecatedItem",
    "ObjectSchemaDef",
    "ResourceEntry",
    "TYPE_RESOURCE_PREFIX",
    "VersionProfile",
    "available_series",
    "load_builtin_profiles",
    "load_profile",
    "profile_for_series",
    "profile_for_version",
    "register_profile",
]
