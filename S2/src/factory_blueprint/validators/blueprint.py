"""Blueprint validation (BP-VAL-010, BP-VAL-011).

Runs against a materialised document — a draft at freeze time, an approved version
when asked.  Only reports: nothing here modifies the Blueprint (C-07, BP-BR-12).
"""

from __future__ import annotations

import re
from typing import Any

from factory_blueprint.content.document import BlueprintDocument
from factory_blueprint.content.layout import CONTAINER_BY_PATH
from factory_blueprint.dto.issue import Issue, make_issue, sort_issues
from factory_blueprint.enums import (
    OBSERVED_DISPOSITIONS,
    Disposition,
    IssueLevel,
    Section,
)
from factory_blueprint.impact.stage_map import issue_source_stage
from factory_blueprint.patch import businesskey
from factory_blueprint.schema import validate as schema_validate

RULE_SCHEMA = "BPV-BP-001"
RULE_KEYS = "BPV-BP-002"
RULE_DISPOSITION = "BPV-BP-003"
RULE_UID_FORMAT = "BPV-BP-004"
RULE_CODE_UNIQUE = "BPV-BP-005"
RULE_MAPPING_COVERAGE = "BPV-BP-006"
RULE_EMBEDDED_CONTENT = "BPV-BP-007"
RULE_UNREGISTERED_UID = "BPV-BP-008"

#: a Blueprint must not embed a full metadata object (BP-SCH-004, FR-BP-002)
FORBIDDEN_INLINE_KEYS: frozenset[str] = frozenset(
    {"href", "id", "lastUpdated", "created", "access", "displayUrl"}
)

#: DHIS2 UID shape (BP-REG-007). Matched with ``fullmatch`` on purpose: a reference
#: field carries one identifier, so the question is "is this value a UID", never "does
#: this value contain an 11-character run" — the latter rejected every legitimate
#: operationKey of 11 characters or more, because `listPatients` begins with one.
UID_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9]{10}")
UID_EXACT = re.compile(r"\A[A-Za-z][A-Za-z0-9]{10}\Z")
REFERENCE_KEY_SUFFIXES = ("refs", "key", "target", "uid")


def _issue(
    rule_id: str,
    message: str,
    *,
    section: Section,
    business_key: str | None = None,
    uid: str | None = None,
    dhis2_type: str | None = None,
    location: str | None = None,
    remediation: str | None = None,
) -> Issue:
    return make_issue(
        rule_id,
        IssueLevel.BLOCKING,
        message,
        section=section,
        business_key=business_key,
        uid=uid,
        dhis2_type=dhis2_type,
        location=location,
        source_stage=issue_source_stage(section),
        remediation=remediation,
    )


def validate_blueprint(
    document: dict[str, Any], *, schema_version: int | None = None
) -> list[Issue]:
    """Every Blueprint-level rule, returned in a stable order."""
    view = BlueprintDocument(document)
    version = schema_version if schema_version is not None else view.data.get(
        "blueprintSchemaVersion"
    )
    issues: list[Issue] = []

    for violation in schema_validate.validate_document(view.data, version):
        section = _section_of(violation.path)
        issues.append(
            _issue(
                RULE_SCHEMA,
                f"{violation.section or section.value}: {violation.message}",
                section=section,
                location=violation.path,
            )
        )

    for duplicate in businesskey.duplicate_keys(view.data):
        section = Section(duplicate.container_path.split("/")[1])
        issues.append(
            _issue(
                RULE_KEYS,
                f"{duplicate.container_path} holds {duplicate.count} entries whose "
                f"{duplicate.field} is {duplicate.key!r}",
                section=section,
                business_key=duplicate.key,
                location=duplicate.container_path,
            )
        )
    for unresolved in businesskey.unresolved_references(view.data):
        section = Section(unresolved.from_container.split("/")[1])
        issues.append(
            _issue(
                RULE_KEYS,
                f"{unresolved.field} references unknown {unresolved.target_kind} "
                f"{unresolved.target!r}",
                section=section,
                business_key=unresolved.from_key,
                location=f"{unresolved.from_container}/{unresolved.from_key}",
                remediation="register the referenced object first",
            )
        )

    issues.extend(_validate_registry(view))
    issues.extend(_validate_mapping_coverage(view))
    issues.extend(_validate_registered_references(view))
    issues.extend(_validate_no_embedded_payloads(view))
    return sort_issues(issues)


def _section_of(path: str) -> Section:
    first = path.strip("/").split("/")[0]
    try:
        return Section(first)
    except ValueError:
        return Section.REGISTRY


def _registry_entries(view: BlueprintDocument) -> list[dict[str, Any]]:
    return view.container_entries(CONTAINER_BY_PATH["/registry/entries"])


def _validate_registry(view: BlueprintDocument) -> list[Issue]:
    issues: list[Issue] = []
    entries = _registry_entries(view)
    seen_uids: dict[str, str] = {}
    codes: dict[tuple[str, str], list[str]] = {}

    for entry in entries:
        business_key = str(entry.get("businessKey") or "")
        dhis2_type = str(entry.get("dhis2Type") or "")
        disposition = entry.get("disposition")
        uid = entry.get("uid")
        code = entry.get("code")

        if disposition not in {item.value for item in Disposition}:
            issues.append(
                _issue(
                    RULE_DISPOSITION,
                    f"unknown disposition {disposition!r}",
                    section=Section.REGISTRY,
                    business_key=business_key,
                    dhis2_type=dhis2_type,
                )
            )
        elif Disposition(disposition) in OBSERVED_DISPOSITIONS and not entry.get(
            "observedHash"
        ):
            issues.append(
                _issue(
                    RULE_DISPOSITION,
                    f"{disposition} entry has no observation baseline (BP-REG-004)",
                    section=Section.REGISTRY,
                    business_key=business_key,
                    dhis2_type=dhis2_type,
                )
            )

        if isinstance(uid, str) and uid:
            if not _valid_uid(uid):
                issues.append(
                    _issue(
                        RULE_UID_FORMAT,
                        f"UID {uid!r} is not 11 characters starting with a letter (BP-REG-007)",
                        section=Section.REGISTRY,
                        business_key=business_key,
                        uid=uid,
                        dhis2_type=dhis2_type,
                        remediation="let S2 allocate the UID from the project pool",
                    )
                )
            owner = seen_uids.get(uid)
            if owner is not None and owner != business_key:
                issues.append(
                    _issue(
                        RULE_UID_FORMAT,
                        f"UID {uid} is used by both {owner!r} and {business_key!r} (BP-REG-005)",
                        section=Section.REGISTRY,
                        business_key=business_key,
                        uid=uid,
                    )
                )
            seen_uids[uid] = business_key

        if isinstance(code, str) and code:
            codes.setdefault((dhis2_type, code), []).append(business_key)

    for (dhis2_type, code), holders in sorted(codes.items()):
        if len(holders) > 1:
            for business_key in holders:
                issues.append(
                    _issue(
                        RULE_CODE_UNIQUE,
                        f"code {code} is shared by several {dhis2_type} entries: "
                        f"{', '.join(sorted(holders))}",
                        section=Section.REGISTRY,
                        business_key=business_key,
                        dhis2_type=dhis2_type,
                        remediation="adjust generationPolicy.codePrefix or namingConvention",
                    )
                )
    return issues


def _valid_uid(uid: str) -> bool:
    return bool(UID_EXACT.match(uid))


def _validate_mapping_coverage(view: BlueprintDocument) -> list[Issue]:
    """BP-VAL-010: every domain element is mapped or explicitly not mapped."""
    issues: list[Issue] = []
    entities = view.container_entries(CONTAINER_BY_PATH["/domain/entities"])
    relationships = view.container_entries(CONTAINER_BY_PATH["/domain/relationships"])
    mapped = {
        str(entry.get("businessKey"))
        for entry in view.container_entries(CONTAINER_BY_PATH["/mapping/entries"])
    }
    for entry in [*entities, *relationships]:
        business_key = entry.get("businessKey")
        if isinstance(business_key, str) and business_key not in mapped:
            issues.append(
                _issue(
                    RULE_MAPPING_COVERAGE,
                    f"domain element {business_key!r} has no mapping entry and is not "
                    "marked as unmapped",
                    section=Section.MAPPING,
                    business_key=business_key,
                    remediation="add a mapping entry with dhis2Type, or set notMapped",
                )
            )
    return issues


def _validate_registered_references(view: BlueprintDocument) -> list[Issue]:
    """BP-VAL-011 / FR-PG-002: no unregistered UID may appear in pages, intents or rules."""
    issues: list[Issue] = []
    registered_uids = {
        str(entry["uid"])
        for entry in _registry_entries(view)
        if isinstance(entry.get("uid"), str)
    }
    for path in ("/pages/items", "/apiIntents/items", "/uiIntents/items", "/rules/programRules"):
        container = CONTAINER_BY_PATH[path]
        for entry in view.container_entries(container):
            for found in _uid_literals(entry):
                issues.append(
                    _issue(
                        RULE_UNREGISTERED_UID,
                        f"{path} carries UID literal {found!r} in "
                        f"{entry.get(container.key_field)}",
                        section=container.section,
                        business_key=str(entry.get(container.key_field) or ""),
                        uid=found if found in registered_uids else None,
                        location=f"{path}/{entry.get(container.key_field)}",
                        remediation="reference the object by businessKey and let the compiler resolve it",
                    )
                )
    return issues


def _uid_literals(entry: dict[str, Any]) -> list[str]:
    """UID-shaped strings under reference-ish keys, excluding S2's own uid field."""
    found: set[str] = set()

    def walk(node: Any, key: str | None) -> None:
        if isinstance(node, dict):
            for name, value in node.items():
                if name == "uid":
                    continue
                walk(value, name)
        elif isinstance(node, list):
            for item in node:
                walk(item, key)
        elif (
            isinstance(node, str)
            and key
            and key.lower().endswith(REFERENCE_KEY_SUFFIXES)
        ):
            candidate = node if UID_PATTERN.fullmatch(node) else None
            # a lowercase businessKey of the same length is not a UID claim
            if candidate and not candidate.islower():
                found.add(candidate)

    walk(entry, None)
    return sorted(found)


def _validate_no_embedded_payloads(view: BlueprintDocument) -> list[Issue]:
    """BP-SCH-004: raw documents and complete metadata JSON are forbidden inline."""
    issues: list[Issue] = []
    for path in ("/mapping/entries", "/registry/entries"):
        container = CONTAINER_BY_PATH[path]
        for entry in view.container_entries(container):
            for key in _forbidden_keys(entry):
                issues.append(
                    _issue(
                        RULE_EMBEDDED_CONTENT,
                        f"{path} entry {entry.get(container.key_field)!r} embeds the "
                        f"forbidden metadata key {key!r}",
                        section=container.section,
                        business_key=str(entry.get(container.key_field) or ""),
                        remediation="reference the source artifact instead of inlining it",
                    )
                )
    return issues


def _forbidden_keys(node: Any, depth: int = 0) -> list[str]:
    found: list[str] = []
    if depth > 12:
        return found
    if isinstance(node, dict):
        for key, value in node.items():
            if key in FORBIDDEN_INLINE_KEYS and key != "key":
                found.append(key)
            found.extend(_forbidden_keys(value, depth + 1))
    elif isinstance(node, list):
        for item in node:
            found.extend(_forbidden_keys(item, depth + 1))
    return sorted(set(found))
