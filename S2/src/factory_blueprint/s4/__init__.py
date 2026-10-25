"""S2's boundary to S4: protocol, content contract, adapter, and the test stub."""

from factory_blueprint.s4.adapter import S4Adapter
from factory_blueprint.s4.contract import (
    CapabilityProfile,
    DiscoveredObject,
    DiscoveryResult,
    ObjectSchema,
    ResourceEntry,
    discovery_from_json,
    profile_from_json,
)
from factory_blueprint.s4.protocol import CodeOccupancy, ReadonlyDhis2, UidOccupancy

__all__ = [
    "CapabilityProfile",
    "CodeOccupancy",
    "DiscoveredObject",
    "DiscoveryResult",
    "ObjectSchema",
    "ReadonlyDhis2",
    "ResourceEntry",
    "S4Adapter",
    "UidOccupancy",
    "discovery_from_json",
    "profile_from_json",
]
