"""Loading the versioned descriptor set (BP-TD-001/002/008).

Descriptors and templates are data files inside the package, so adding an object
type or a DHIS2 version never requires touching the compiler (NFR-MNT-001).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from factory_foundation.api import FactoryError

from factory_blueprint.config import settings
from factory_blueprint.descriptors.model import Dependency, Implicit, TypeDescriptor
from factory_blueprint.enums import Tier

DESCRIPTOR_ROOT = Path(__file__).resolve().parent / "descriptor_files"
TEMPLATE_ROOT = Path(__file__).resolve().parent / "templates"


def _read(path: Path) -> dict[str, Any]:
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return document


def descriptor_from_json(data: dict[str, Any]) -> TypeDescriptor:
    dependencies = tuple(
        Dependency(
            field=str(item["field"]),
            target_type=str(item["targetType"]),
            required=bool(item.get("required", True)),
            item_field=item.get("itemField"),
            wrapper=item.get("wrapper"),
        )
        for item in data.get("dependencies") or []
    )
    version_range = data.get("versionRange") or ["2.41", "2.43"]
    return TypeDescriptor(
        dhis2_type=str(data["dhis2Type"]),
        resource=str(data["resource"]),
        payload_key=str(data["payloadKey"]),
        tier=Tier(str(data["tier"])),
        version_range=(str(version_range[0]), str(version_range[1])),
        required_fields=tuple(str(name) for name in data.get("requiredFields") or []),
        dependencies=dependencies,
        controlable_fields=tuple(str(name) for name in data.get("controlableFields") or []),
        translatable_fields=tuple(str(name) for name in data.get("translatableFields") or []),
        forbidden_fields=tuple(str(name) for name in data.get("forbiddenFields") or []),
        implicit=tuple(Implicit(name) for name in data.get("implicit") or []),
        template=str(data.get("template") or ""),
        sort_key=str(data.get("sortKey") or "businessKey"),
        rules=tuple(str(rule) for rule in data.get("rules") or []),
        embedded_in_parent=data.get("embeddedInParent"),
        descriptor_version=str(data.get("descriptorVersion") or "1"),
    )


@lru_cache(maxsize=8)
def _descriptor_set(version: str) -> dict[str, TypeDescriptor]:
    directory = DESCRIPTOR_ROOT / version
    if not directory.is_dir():
        raise FactoryError(
            f"no type descriptor set {version!r} is shipped with this build",
            code="REGISTRY_ERROR",
        )
    loaded: dict[str, TypeDescriptor] = {}
    for path in sorted(directory.glob("*.json")):
        descriptor = descriptor_from_json(_read(path))
        loaded[descriptor.dhis2_type] = descriptor
    return loaded


def available_versions() -> tuple[str, ...]:
    return tuple(sorted(path.name for path in DESCRIPTOR_ROOT.iterdir() if path.is_dir()))


def descriptor(dhis2_type: str) -> TypeDescriptor | None:
    """The registered descriptor, or None when the type is unknown or disabled."""
    found = _descriptor_set(settings().descriptor_version).get(dhis2_type)
    if found is None:
        return None
    return found if Tier(found.tier) in settings().enabled_tiers else None


def require(dhis2_type: str) -> TypeDescriptor:
    """BP-TD-003: an unregistered or disabled type is an error, never ignored."""
    found = descriptor(dhis2_type)
    if found is None:
        raise FactoryError(
            f"metadata object type {dhis2_type!r} has no enabled type descriptor",
            code="REGISTRY_ERROR",
            details={"dhis2Type": dhis2_type, "enabledTiers": sorted(t.value for t in settings().enabled_tiers)},
        )
    return found


def known_types() -> list[str]:
    return sorted(_descriptor_set(settings().descriptor_version))


def enabled_types() -> list[str]:
    return sorted(name for name in known_types() if descriptor(name) is not None)


def catalog() -> list[dict[str, Any]]:
    """Every enabled object type with its coverage tier (FR-COV-001).

    Published so a caller can ask "what does this Factory know how to build?" without
    reading the descriptor files.  Discovery is the consumer that needs it: a Brownfield
    project maps to these types, so the metadata index it must inspect before mapping
    covers exactly them and nothing else (FR-DSC-001, FR-DSC-002).
    """
    rows: list[dict[str, Any]] = []
    for name in enabled_types():
        found = descriptor(name)
        if found is None:
            continue
        rows.append({"dhis2Type": found.dhis2_type, "tier": str(found.tier)})
    return sorted(rows, key=lambda row: (str(row["tier"]), str(row["dhis2Type"])))


@lru_cache(maxsize=256)
def template(document_version: str, name: str) -> dict[str, Any]:
    path = TEMPLATE_ROOT / document_version / name
    if not path.is_file():
        raise FactoryError(
            f"compile template {name!r} is missing from descriptor set {document_version}",
            code="REGISTRY_ERROR",
        )
    return _read(path)


def for_version(dhis2_version: str) -> list[TypeDescriptor]:
    """Descriptors applicable to a concrete DHIS2 instance version."""
    return [
        item
        for name in sorted(_descriptor_set(settings().descriptor_version))
        if (item := _descriptor_set(settings().descriptor_version)[name]) is not None
        and item.accepts_version(dhis2_version)
        and descriptor(name) is not None
    ]
