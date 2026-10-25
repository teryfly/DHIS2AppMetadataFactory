"""Type descriptors: the data that makes a DHIS2 metadata type compilable.

FR-COV-001: adding a new object type means adding a descriptor plus a template and
its rules, never changing the compiler.  Descriptors are versioned data files
shipped inside the package (BP-TD-008), and their version is part of every build
record so a product can be reproduced.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

from factory_blueprint.enums import Tier


class Implicit(str, enum.Enum):
    """Named implicit-dependency rules a compiler must apply (FR-COV-003…006)."""

    DEFAULT_CATEGORY_COMBO = "DEFAULT_CATEGORY_COMBO"
    PROGRAM_TRACKED_ENTITY_OR_EVENT = "PROGRAM_TRACKED_ENTITY_OR_EVENT"
    ATTRIBUTE_MOUNT = "ATTRIBUTE_MOUNT"
    SHARING_FROM_ACCESS_MODEL = "SHARING_FROM_ACCESS_MODEL"
    TRANSLATABLE_FIELDS = "TRANSLATABLE_FIELDS"
    ATTRIBUTE_VALUES = "ATTRIBUTE_VALUES"


@dataclass(frozen=True)
class Dependency:
    """One field-level reference this type holds to another registered object."""

    #: the DHIS2 property carrying the reference, e.g. ``categoryCombo``
    field: str
    #: the dhis2Type of the referenced object
    target_type: str
    required: bool = True
    #: when the payload nests an array of join objects (programStageDataElements),
    #: the property inside each item that holds the reference
    item_field: str | None = None
    #: when the payload nests an array of join objects, that array's property
    wrapper: str | None = None

    @property
    def is_nested(self) -> bool:
        return self.wrapper is not None and self.item_field is not None


@dataclass(frozen=True)
class TypeDescriptor:
    dhis2_type: str
    resource: str
    payload_key: str
    tier: Tier
    #: DHIS2 version range this descriptor was written for, e.g. ["2.41", "2.43"]
    version_range: tuple[str, str]
    required_fields: tuple[str, ...] = ()
    dependencies: tuple[Dependency, ...] = ()
    controlable_fields: tuple[str, ...] = ()
    translatable_fields: tuple[str, ...] = ()
    #: properties the payload must never contain (system-owned, FD/SRS 12.4)
    forbidden_fields: tuple[str, ...] = ()
    implicit: tuple[Implicit, ...] = ()
    template: str = ""
    #: stable ordering hint inside a payload type array
    sort_key: str = "businessKey"
    rules: tuple[str, ...] = field(default_factory=tuple)
    #: true for join objects DHIS2 stores inside their parent rather than as their
    #: own top-level metadata collection
    embedded_in_parent: str | None = None
    descriptor_version: str = "1"

    def accepts_version(self, version: str) -> bool:
        low, high = self.version_range
        return _version_tuple(low) <= _version_tuple(version) < _version_tuple(high)

    def to_json(self) -> dict[str, Any]:
        return {
            "dhis2Type": self.dhis2_type,
            "resource": self.resource,
            "payloadKey": self.payload_key,
            "tier": self.tier.value,
            "versionRange": list(self.version_range),
            "requiredFields": list(self.required_fields),
            "dependencies": [
                {
                    "field": item.field,
                    "targetType": item.target_type,
                    "required": item.required,
                    **({"itemField": item.item_field} if item.item_field else {}),
                    **({"wrapper": item.wrapper} if item.wrapper else {}),
                }
                for item in self.dependencies
            ],
            "controlableFields": list(self.controlable_fields),
            "translatableFields": list(self.translatable_fields),
            "forbiddenFields": list(self.forbidden_fields),
            "implicit": [rule.value for rule in self.implicit],
            "template": self.template,
            "sortKey": self.sort_key,
            "rules": list(self.rules),
            "embeddedInParent": self.embedded_in_parent,
            "descriptorVersion": self.descriptor_version,
        }


def _version_tuple(text: str) -> tuple[int, ...]:
    parts = text.split(".")
    numbers = [int(part) for part in parts if part.isdigit()]
    return tuple(numbers or [0])
