"""Blueprint schema store (BP-SCH-001, BP-SCH-006).

Schemas are versioned data files under ``schemas/<n>/``.  They are loaded into an
offline referencing registry: a Blueprint schema never resolves a remote ``$ref``,
because a compiler must not touch the network (BP-CMP-001).

``section_schema()`` returns a *self-contained* document — every ``$ref`` is
pulled in under a local ``definitions`` block — because S5's editors and S3's
agent output constraints consume a single section schema at a time.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from factory_blueprint.enums import Section

SCHEMA_ROOT = Path(__file__).resolve().parent.parent / "schemas"
DEFS_BASE = "https://damf.invalid/bp"

SCHEMA_VERSION_FIELD = "blueprintSchemaVersion"


def _read_json(path: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


@lru_cache(maxsize=8)
def available_versions() -> tuple[int, ...]:
    index = _read_json(SCHEMA_ROOT / "index.json")
    versions = [int(v) for v in index["versions"]]
    return tuple(sorted(versions))


@lru_cache(maxsize=8)
def current_schema_version() -> int:
    index = _read_json(SCHEMA_ROOT / "index.json")
    return int(index["current"])


def resolve_version(schema_version: int | None) -> int:
    if schema_version is None:
        return current_schema_version()
    if schema_version not in available_versions():
        raise ValueError(f"unsupported blueprintSchemaVersion: {schema_version}")
    return schema_version


@lru_cache(maxsize=8)
def _version_dir(version: int) -> Path:
    path = SCHEMA_ROOT / str(version)
    if not path.is_dir():
        raise ValueError(f"missing schema directory for version {version}")
    return path


@lru_cache(maxsize=8)
def registry_for(version: int) -> Registry[Any]:
    """Offline registry: the root schema, the shared definitions and one
    self-contained resource per section, addressed by its own ``$id``."""
    documents: list[dict[str, Any]] = [
        _read_json(_version_dir(version) / "defs.json"),
        _read_json(_version_dir(version) / "blueprint.json"),
    ]
    documents.extend(section_schema(section, version) for section in Section)
    registry = Registry()
    for contents in documents:
        resource = Resource.from_contents(contents, default_specification=DRAFT202012)
        resource_id = resource.id()
        if resource_id is not None:
            registry = registry.with_resource(resource_id, resource)
    return registry


@lru_cache(maxsize=64)
def _section_document(version: int, section: str) -> dict[str, Any]:
    sections = _read_json(_version_dir(version) / "sections.json")["sections"]
    schema = sections[section]
    assert isinstance(schema, dict)
    return schema


@lru_cache(maxsize=64)
def _defs_document(version: int) -> dict[str, Any]:
    defs = _read_json(_version_dir(version) / "defs.json")
    definitions = defs["definitions"]
    assert isinstance(definitions, dict)
    return definitions


def _split_ref(ref: str, base_id: str) -> tuple[str, str]:
    """Return (document id, local pointer) for an absolute or same-document ref."""
    if ref.startswith("#"):
        return base_id, ref
    if "#/" in ref or ref.endswith("#"):
        document, _, fragment = ref.partition("#")
        if not document:
            return base_id, "#" + fragment
        return document, "#" + fragment
    return ref, "#"


def _lookup(target_document: str, pointer: str, version: int) -> dict[str, Any]:
    base = f"{DEFS_BASE}/{version}"
    if target_document == f"{base}/defs.json":
        name = pointer.rsplit("/", 1)[-1]
        found = _defs_document(version).get(name)
        if not isinstance(found, dict):
            raise ValueError(f"unknown definition {name!r} in defs.json")
        return found
    if target_document in (f"{base}/sections.json", f"{base}/blueprint.json"):
        node: Any = _read_json(_version_dir(version) / f"{target_document.rsplit('/', 1)[-1]}")
        for token in [t for t in pointer.split("/")[1:] if t]:
            key = token.replace("~1", "/").replace("~0", "~")
            node = node[int(key)] if isinstance(node, list) else node[key]
        assert isinstance(node, dict)
        return node
    raise ValueError(f"cannot resolve $ref to {target_document}{pointer}")


def _collect_refs(
    node: Any,
    base_id: str,
    version: int,
    pulled: dict[str, Any],
    local: dict[str, Any] | None = None,
) -> Any:
    """Rewrite ``$ref`` into ``#/definitions/<name>`` and pull the target in."""
    if isinstance(node, list):
        return [_collect_refs(item, base_id, version, pulled, local) for item in node]
    if not isinstance(node, dict):
        return node
    if set(node) <= {"$ref", "description"} and "$ref" in node:
        ref = str(node["$ref"])
        description = node.get("description")
        name = ref.rsplit("/", 1)[-1]
        if ref.startswith("#/") and local is not None and name in local:
            target = local[name]
            target_document = base_id
        else:
            target_document, pointer = _split_ref(ref, base_id)
            target = _lookup(target_document, pointer, version)
        if name not in pulled:
            pulled[name] = {}
            pulled[name] = _collect_refs(target, target_document, version, pulled, local)
        rewritten: dict[str, Any] = {"$ref": f"#/definitions/{name}"}
        if isinstance(description, str):
            rewritten["description"] = description
        return rewritten
    return {
        key: _collect_refs(value, base_id, version, pulled, local)
        for key, value in node.items()
        if key != "definitions"
    }


def section_schema(
    section: Section | str, schema_version: int | None = None
) -> dict[str, Any]:
    """A standalone, self-contained JSON Schema for one Blueprint section."""
    version = resolve_version(schema_version)
    name = Section(section).value
    base = _section_document(version, name)
    base_id = f"{DEFS_BASE}/{version}/sections.json"
    local = base.get("definitions") if isinstance(base.get("definitions"), dict) else {}
    pulled: dict[str, Any] = {}
    body = _collect_refs(base, base_id, version, pulled, local)
    document: dict[str, Any] = dict(body)
    document["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    document["$id"] = f"{DEFS_BASE}/{version}/sections/{name}.json"
    if pulled:
        document["definitions"] = dict(sorted(pulled.items()))
    return document


def document_schema(schema_version: int | None = None) -> dict[str, Any]:
    """The root Blueprint schema for a schema version."""
    version = resolve_version(schema_version)
    return dict(_read_json(_version_dir(version) / "blueprint.json"))


def validator_for_section(
    section: Section | str, schema_version: int | None = None
) -> Any:
    from jsonschema import Draft202012Validator

    version = resolve_version(schema_version)
    schema = section_schema(section, version)
    return Draft202012Validator(schema, registry=registry_for(version))


def validator_for_document(schema_version: int | None = None) -> Any:
    from jsonschema import Draft202012Validator

    version = resolve_version(schema_version)
    schema = document_schema(version)
    return Draft202012Validator(schema, registry=registry_for(version))
