"""Patch validation (BP-DRF-005).

The five rules are checked in a fixed order and every failure is reported, so an
agent receives the complete picture in one round trip.  A rejected patch produces
no document.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from factory_blueprint.content.document import entries_of, entry_of
from factory_blueprint.content.pointer import PointerError, locate
from factory_blueprint.dto.issue import Issue, make_issue
from factory_blueprint.enums import (
    MAPPING_FROZEN_SECTIONS,
    DesignStage,
    Disposition,
    IssueLevel,
    Section,
)
from factory_blueprint.patch import businesskey, uid_guard
from factory_blueprint.patch.rfc6902 import (
    LAST_ARRAY_ELEMENT,
    Operation,
    PatchMalformed,
    apply_operations,
    parse_operations,
)
from factory_blueprint.schema import validate as schema_validate

RULE_STAGE_SCOPE = "BPV-PCH-001"
RULE_SECTION_SCHEMA = "BPV-PCH-002"
RULE_KEYS = "BPV-PCH-003"
RULE_UID = "BPV-PCH-004"
RULE_FROZEN_MAPPING = "BPV-PCH-005"
RULE_MALFORMED = "BPV-PCH-000"


@dataclass
class PatchValidation:
    """Outcome of validating one patch against a draft's current content."""

    accepted: bool
    issues: list[Issue] = field(default_factory=list)
    document: dict[str, Any] | None = None
    operations: list[Operation] = field(default_factory=list)
    #: entries the patch created or replaced, as (container path, key)
    touched_entries: list[tuple[str, str]] = field(default_factory=list)
    #: registry entries that need a fresh UID from the pool (CREATE without uid)
    new_create_keys: list[str] = field(default_factory=list)

    def blocking(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.level is IssueLevel.BLOCKING]


def _issue(
    rule_id: str,
    message: str,
    *,
    path: str | None = None,
    section: Section | None = None,
    business_key: str | None = None,
    stage: DesignStage | None = None,
    remediation: str | None = None,
) -> Issue:
    return make_issue(
        rule_id,
        IssueLevel.BLOCKING,
        message,
        location=path,
        section=section,
        business_key=business_key,
        source_stage=stage,
        remediation=remediation,
    )


def validate_patch(
    base_document: dict[str, Any],
    operations_json: list[dict[str, Any]],
    *,
    allowed_sections: list[Section],
    schema_version: int,
    mapping_frozen: bool,
    change_request_id: str | None,
    stage: DesignStage | str,
    delivery_mode_sections: list[Section] | None = None,
) -> PatchValidation:
    """Apply BP-DRF-005.1 … .5 to one patch."""
    design_stage = DesignStage(stage)
    issues: list[Issue] = []

    # 0. the operations must be a patch at all
    try:
        operations = parse_operations(operations_json)
    except PatchMalformed as exc:
        return PatchValidation(
            accepted=False,
            issues=[_issue(RULE_MALFORMED, exc.message, stage=design_stage)],
            operations=[],
        )

    # 1. paths stay inside the draft's stage scope
    allowed = set(allowed_sections)
    for operation in operations:
        try:
            location = locate(operation.path)
        except PointerError as exc:
            issues.append(_issue(RULE_MALFORMED, str(exc), path=operation.path, stage=design_stage))
            continue
        if location.section not in allowed:
            issues.append(
                _issue(
                    RULE_STAGE_SCOPE,
                    f"section {location.section.value!r} is outside this draft's stage scope "
                    f"({design_stage.value})",
                    path=operation.path,
                    section=location.section,
                    stage=design_stage,
                    remediation="submit this change in a draft opened for the owning stage",
                )
            )
        if operation.from_ is not None:
            try:
                source = locate(operation.from_)
            except PointerError as exc:
                issues.append(_issue(RULE_MALFORMED, str(exc), path=operation.from_, stage=design_stage))
                continue
            if source.section not in allowed:
                issues.append(
                    _issue(
                        RULE_STAGE_SCOPE,
                        f"operation source section {source.section.value!r} is outside the stage scope",
                        path=operation.from_,
                        section=source.section,
                        stage=design_stage,
                    )
                )

    # 2. no UID may be written by a patch
    for write in uid_guard.check_operations(operations):
        issues.append(
            _issue(
                RULE_UID,
                f"UIDs are allocated by S2 only; {write.reason} ({write.value})",
                path=write.path,
                stage=design_stage,
                remediation="reference the object by its businessKey and let the compiler resolve the UID",
            )
        )

    # 3. frozen mapping needs an approved change request
    if mapping_frozen:
        touched_frozen = sorted(
            {
                locate(operation.path).section
                for operation in operations
                if _resolves(operation.path)
            }
            & MAPPING_FROZEN_SECTIONS
        )
        if touched_frozen and not change_request_id:
            names = ", ".join(str(section.value) for section in touched_frozen)
            issues.append(
                _issue(
                    RULE_FROZEN_MAPPING,
                    f"the mapping is frozen by ARCHITECTURE_GATE; touching {names} requires an "
                    "approved Change Request ID",
                    stage=design_stage,
                    remediation="raise a Change Request and execute it, instead of editing the mapping",
                )
            )

    if issues:
        return PatchValidation(accepted=False, issues=issues, operations=operations)

    document = apply_operations(base_document, operations)
    touched = _touched_entries(operations, document)

    # 4. touched sections still satisfy their JSON Schema
    for section in sorted({section for _, section in _touched_sections(operations)}, key=str):
        violations = schema_validate.validate_section(section, document.get(section.value), schema_version)
        for violation in violations:
            issues.append(
                _issue(
                    RULE_SECTION_SCHEMA,
                    f"{section.value}: {violation.message}",
                    path=f"/{section.value}{violation.path}",
                    section=section,
                    stage=design_stage,
                )
            )

    # 5. businessKey uniqueness and reference resolvability
    for duplicate in businesskey.duplicate_keys(document):
        issues.append(
            _issue(
                RULE_KEYS,
                f"{duplicate.container_path} contains {duplicate.count} entries whose "
                f"{duplicate.field} is {duplicate.key!r}",
                path=f"{duplicate.container_path}/{duplicate.key}",
                business_key=duplicate.key,
                stage=design_stage,
            )
        )
    for unresolved in businesskey.unresolved_references(document, touched):
        issues.append(
            _issue(
                RULE_KEYS,
                f"{unresolved.field} references unknown {unresolved.target_kind} "
                f"{unresolved.target!r}",
                path=f"{unresolved.from_container}/{unresolved.from_key}/{unresolved.field}",
                business_key=unresolved.from_key,
                stage=design_stage,
                remediation="register the referenced object first",
            )
        )

    if issues:
        return PatchValidation(accepted=False, issues=issues, operations=operations)

    new_create_keys = _new_create_keys(base_document, document)
    return PatchValidation(
        accepted=True,
        issues=[],
        document=document,
        operations=operations,
        touched_entries=touched,
        new_create_keys=new_create_keys,
    )


def _resolves(pointer: str) -> bool:
    try:
        locate(pointer)
    except PointerError:
        return False
    return True


def _touched_sections(operations: list[Operation]) -> set[tuple[str, Section]]:
    result: set[tuple[str, Section]] = set()
    for operation in operations:
        if not _resolves(operation.path):
            continue
        section = locate(operation.path).section
        result.add((operation.path, section))
    return result


def _touched_entries(
    operations: list[Operation], document: dict[str, Any]
) -> list[tuple[str, str]]:
    """(container path, entry key) pairs the patch introduced or replaced."""
    seen: set[tuple[str, str]] = set()
    for operation in operations:
        location = locate(operation.path)
        if location.container is None or location.entry_key is None:
            continue
        key = location.entry_key
        if key == LAST_ARRAY_ELEMENT:
            # "-" addresses the position the entry was appended to; the entry the
            # patch just introduced is the last one in the container
            entries = entries_of(document, location.container)
            if not entries:
                continue
            last = entries[-1].get(location.container.key_field)
            key = last if isinstance(last, str) else key
        if entry_of(document, location.container, key) is not None:
            seen.add((location.container.path, key))
    return sorted(seen)


def _new_create_keys(base: dict[str, Any], document: dict[str, Any]) -> list[str]:
    """Registry entries the patch added with disposition CREATE and no UID yet."""

    def entries(source: dict[str, Any]) -> dict[str, Any]:
        registry = source.get("registry") or {}
        items = registry.get("entries") or []
        return {
            entry["businessKey"]: entry
            for entry in items
            if isinstance(entry, dict) and isinstance(entry.get("businessKey"), str)
        }

    before = entries(base)
    result: list[str] = []
    for key, entry in sorted(entries(document).items()):
        if key in before:
            continue
        if entry.get("disposition") == Disposition.CREATE.value and not entry.get("uid"):
            result.append(key)
    return result
