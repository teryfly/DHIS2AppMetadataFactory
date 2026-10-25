"""Deterministic in-memory stand-in for S4's read-only services.

S4 is delivered, so the production path is ``s4.adapter``; this is what a test installs
when it wants to answer S2's four read-only questions itself, and what
:mod:`factory_blueprint.testing`'s standalone Fake uses so S1 and S3 can run without an
instance.  Generated UIDs follow the DHIS2 shape rule and come from a counter, so a run
is repeatable.

Occupants carry a type and code the way S4's answers do, because "occupied" and
"occupied by the very object this UPDATE entry means" have to be distinguishable.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from factory_blueprint.s4.protocol import CodeOccupancy, UidOccupancy

UID_LENGTH = 11


class StubDhis2:
    """Scriptable read-only DHIS2 view for tests and for the standalone Fake API."""

    def __init__(
        self,
        *,
        uid_prefix: str = "stub",
        known_uids: dict[str, Iterable[str]] | None = None,
        known_codes: dict[tuple[str, str], Iterable[str]] | None = None,
        objects_with_data: Iterable[tuple[str, str, str]] | None = None,
    ) -> None:
        self.uid_prefix = uid_prefix
        #: environment -> uid -> who holds it
        self.uids: dict[str, dict[str, UidOccupancy]] = {}
        #: (environment, dhis2Type) -> code -> who holds it
        self.codes: dict[tuple[str, str], dict[str, CodeOccupancy]] = {}
        self.objects_with_data: set[tuple[str, str, str]] = set(objects_with_data or ())
        self._issued: set[str] = set()
        self._counter = 0
        #: every call, for assertions about what S2 asked for
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        for environment_key, uids in (known_uids or {}).items():
            for uid in uids:
                self.add_existing_uid(environment_key, uid)
        for (environment_key, dhis2_type), codes in (known_codes or {}).items():
            for code in codes:
                self.add_existing_code(environment_key, dhis2_type, code)

    def _next_uid(self) -> str:
        taken = {uid for held in self.uids.values() for uid in held}
        while True:
            uid = f"{self.uid_prefix}{self._counter:0{UID_LENGTH - len(self.uid_prefix)}d}"
            self._counter += 1
            if len(uid) != UID_LENGTH:
                raise AssertionError(f"stub produced a {len(uid)} character UID: {uid}")
            if uid in self._issued or uid in taken:
                continue
            self._issued.add(uid)
            return uid

    def generate_uids(self, environment_key: str, count: int) -> list[str]:
        self.calls.append(("generate_uids", (environment_key, count)))
        return [self._next_uid() for _ in range(count)]

    def uid_exists(self, environment_key: str, uids: Sequence[str]) -> dict[str, UidOccupancy]:
        self.calls.append(("uid_exists", (environment_key, tuple(uids))))
        held = self.uids.get(environment_key, {})
        return {uid: held.get(uid, UidOccupancy(exists=False)) for uid in uids}

    def code_exists(
        self, environment_key: str, dhis2_type: str, codes: Sequence[str]
    ) -> dict[str, CodeOccupancy]:
        self.calls.append(("code_exists", (environment_key, dhis2_type, tuple(codes))))
        held = self.codes.get((environment_key, dhis2_type), {})
        return {code: held.get(code, CodeOccupancy(exists=False)) for code in codes}

    def has_associated_data(self, environment_key: str, dhis2_type: str, uid: str) -> bool:
        self.calls.append(("has_associated_data", (environment_key, dhis2_type, uid)))
        return (environment_key, dhis2_type, uid) in self.objects_with_data

    # --- test-side scripting -------------------------------------------------

    def add_existing_uid(
        self,
        environment_key: str,
        uid: str,
        *,
        dhis2_type: str | None = None,
        code: str | None = None,
        name: str | None = None,
    ) -> None:
        """Put an object holding ``uid`` in the environment; the default carries no type,
        which reads as "occupied by something we do not recognize"."""
        self.uids.setdefault(environment_key, {})[uid] = UidOccupancy(
            exists=True, dhis2_type=dhis2_type, code=code, name=name
        )

    def add_existing_code(
        self,
        environment_key: str,
        dhis2_type: str,
        code: str,
        *,
        uid: str | None = None,
        name: str | None = None,
    ) -> None:
        self.codes.setdefault((environment_key, dhis2_type), {})[code] = CodeOccupancy(
            exists=True, uid=uid, name=name
        )

    def mark_has_data(self, environment_key: str, dhis2_type: str, uid: str) -> None:
        self.objects_with_data.add((environment_key, dhis2_type, uid))


#: the shape S4 publishes for one import section: what it defaults to, and what the
#: version will accept at all (DX-EXE-011、DX-CAP-001).  S2 reads the defaults to check its own
#: payload against them, so the stub has to be shaped like the real thing.
_DEFAULT_IMPORT_PARAMETERS: dict[str, Any] = {
    "metadata": {
        "defaults": {
            "importMode": "COMMIT",
            "importStrategy": "CREATE_AND_UPDATE",
            "identifier": "ASSIGNED",
            "atomicMode": "ALL",
            "async": False,
            "preheatMode": "FULL",
        },
        "allowed": {
            "importMode": ["VALIDATE", "COMMIT"],
            "identifier": ["ASSIGNED", "EXISTING", "AUTO"],
            "atomicMode": ["ALL", "NONE"],
        },
        "nonDefaultRequiresConfirmation": ["importStrategy", "identifier", "atomicMode"],
    }
}

#: the two Profile statements that constrain what S2 emits, as S4 states them
_DEFAULT_VALIDATION_RULES: dict[str, Any] = {
    "rules": {
        "metadata.import.requiresUidIdentifier": {
            "severity": "BLOCKING",
            "description": "identifier 必须为 ASSIGNED：S2 已在 UID 池里定好 UID",
        },
        "metadata.import.atomicByDefault": {
            "severity": "WARNING",
            "description": "atomicMode=NONE 会让一次导入部分生效",
        },
    }
}


def profile_document(
    *,
    version: str = "2.41",
    profile_version: str = "Dhis241Profile",
    environment_key: str | None = None,
    expression_grammar_version: str = "dhis2-2.41",
    translation_format: str = "translations",
    sharing_format: str = "sharing",
    resources: list[dict[str, Any]] | None = None,
    schemas: list[dict[str, Any]] | None = None,
    deprecated: list[dict[str, Any]] | None = None,
    removed: list[dict[str, Any]] | None = None,
    descriptor_overrides: list[dict[str, Any]] | None = None,
    import_parameters: dict[str, Any] | None = None,
    validation_rules: dict[str, Any] | None = None,
    volatile_fields: list[str] | None = None,
) -> dict[str, Any]:
    """A Capability Profile document shaped the way S2 reads it.

    Every default here is the value S4's 2.41 Profile publishes, so a test that does not
    care about one dimension still gets a document S2 could have received in production.
    """
    return {
        "version": version,
        "profileVersion": profile_version,
        "expressionGrammarVersion": expression_grammar_version,
        "translationFormat": translation_format,
        "sharingFormat": sharing_format,
        "importParameters": (
            _DEFAULT_IMPORT_PARAMETERS if import_parameters is None else import_parameters
        ),
        "resources": resources if resources is not None else _DEFAULT_RESOURCES,
        "schemas": schemas if schemas is not None else _DEFAULT_SCHEMAS,
        "deprecated": deprecated or [],
        "removed": removed or [],
        "descriptorOverrides": descriptor_overrides or [],
        "validationRules": _DEFAULT_VALIDATION_RULES if validation_rules is None else validation_rules,
        "volatileFields": volatile_fields if volatile_fields is not None else _DEFAULT_VOLATILE_FIELDS,
        **({"environmentKey": environment_key} if environment_key else {}),
    }


#: S4's 2.41 Profile lists these as rewritten by the instance itself (DX-DSC-007)
_DEFAULT_VOLATILE_FIELDS: list[str] = [
    "id",
    "href",
    "created",
    "lastUpdated",
    "createdBy",
    "lastUpdatedBy",
    "access",
    "publicAccess",
    "externalAccess",
    "favourite",
    "favorites",
    "subscriptions",
    "displayOptions",
    "sharedUrl",
    "apiPath",
]


def discovery_document(
    environment_key: str,
    objects: list[dict[str, Any]],
    *,
    defaults: dict[str, str] | None = None,
    discovered_at: str = "2026-01-01T00:00:00Z",
) -> dict[str, Any]:
    return {
        "environmentKey": environment_key,
        "discoveredAt": discovered_at,
        "objects": objects,
        "defaults": defaults or {},
    }


def discovered_object(
    dhis2_type: str,
    uid: str,
    *,
    code: str | None = None,
    name: str | None = None,
    content: dict[str, Any] | None = None,
    last_updated: str = "2026-01-01T00:00:00Z",
    content_hash: str = "sha256:" + "1" * 64,
    references: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "dhis2Type": dhis2_type,
        "uid": uid,
        "lastUpdated": last_updated,
        "contentHash": content_hash,
    }
    if code is not None:
        payload["code"] = code
    if name is not None:
        payload["name"] = name
    if content is not None:
        payload["content"] = content
    if references is not None:
        payload["references"] = references
    return payload


_DEFAULT_RESOURCES: list[dict[str, Any]] = [
    {"logicalName": "metadata.dataElement", "path": "dataElements", "method": "POST"},
    {"logicalName": "metadata.optionSet", "path": "optionSets", "method": "POST"},
    {"logicalName": "metadata.program", "path": "programs", "method": "POST"},
    {"logicalName": "metadata.trackedEntity", "path": "trackedEntityTypes", "method": "POST"},
    {"logicalName": "metadata.categoryCombo", "path": "categoryCombos", "method": "POST"},
    {"logicalName": "system.id", "path": "reserved/id", "method": "GET"},
    {
        "logicalName": "tracker.trackedEntities.get",
        "path": "tracker/trackedEntities",
        "method": "GET",
        "parameters": {"id": "trackedEntity"},
        "paging": "paging",
        "trackerApi": True,
    },
]

_DEFAULT_SCHEMAS: list[dict[str, Any]] = [
    {
        "dhis2Type": "dataElement",
        "required": ["name", "shortName", "valueType", "domainType", "categoryCombo"],
        "controlableFields": ["name", "shortName", "description", "formName", "valueType"],
        "forbidden": ["lastUpdated", "href", "id", "access"],
        "properties": {
            "name": {"type": "string"},
            "shortName": {"type": "string"},
            "valueType": {"type": "string"},
        },
    },
    {
        "dhis2Type": "optionSet",
        "required": ["name"],
        "controlableFields": ["name", "description"],
        "forbidden": ["lastUpdated", "href", "id", "access"],
        "properties": {"name": {"type": "string"}},
    },
    {
        "dhis2Type": "program",
        "required": ["name", "programType"],
        "controlableFields": ["name", "shortName", "description"],
        "forbidden": ["lastUpdated", "href", "id"],
        "properties": {"name": {"type": "string"}},
    },
]
