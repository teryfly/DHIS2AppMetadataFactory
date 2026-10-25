"""Object-level Diff (BP-DIFF-001, overall SRS 15.5).

Three comparisons belong to S2: Blueprint version N vs N+1, Registry N vs N+1 (a
projection of the first), and the environment's current state vs this compile.
All of them report per object: added, updated (field level), unchanged,
reference-only, removed.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from factory_blueprint.capability import ProfileView
from factory_blueprint.content.document import BlueprintDocument
from factory_blueprint.content.layout import ENTRY_CONTAINERS, EntryContainer
from factory_blueprint.dto.diff import DiffReport, FieldChange, ObjectDiff, ProfileDifference

ADDED = "ADDED"
UPDATED = "UPDATED"
UNCHANGED = "UNCHANGED"
REMOVED = "REMOVED"
REFERENCE_ONLY = "REFERENCE_ONLY"

#: fields that never appear in a field-level diff of an object
IGNORED_FIELDS: frozenset[str] = frozenset({"source", "notes", "lastUpdated", "href"})

#: the columns that say *which* object a row is, never what it holds.  An environment's
#: discovery carries them beside the content, and the ObjectDiff already shows them as
#: ``uid`` and ``dhis2_type``, so comparing them would report a deploy deleting its own
#: join keys.
IDENTITY_COLUMNS: frozenset[str] = frozenset({"uid", "dhis2Type"})


def _entries(document: dict[str, Any], container: EntryContainer) -> dict[str, dict[str, Any]]:
    view = BlueprintDocument(document)
    return {
        str(entry.get(container.key_field)): entry
        for entry in view.container_entries(container)
        if entry.get(container.key_field) is not None
    }


def field_changes(
    before: dict[str, Any], after: dict[str, Any], *, excluded: frozenset[str] = frozenset()
) -> list[FieldChange]:
    """Leaf-level differences, ordered by field path.

    ``excluded`` names the properties that say nothing about a managed change — a
    version's volatile or forbidden fields, which S4 leaves out of its own field-level
    Diff for the same reason (DX-DSC-007).
    """
    changes: list[FieldChange] = []

    def walk(prefix: str, left: Any, right: Any) -> None:
        if isinstance(left, dict) and isinstance(right, dict):
            for key in sorted(set(left) | set(right)):
                if str(key) in excluded:
                    continue
                path = f"{prefix}/{key}" if prefix else key
                if key not in left:
                    changes.append(FieldChange(field=path, before=None, after=right[key]))
                elif key not in right:
                    changes.append(FieldChange(field=path, before=left[key], after=None))
                else:
                    walk(path, left[key], right[key])
            return
        if isinstance(left, list) and isinstance(right, list):
            if left != right:
                changes.append(FieldChange(field=prefix or "value", before=left, after=right))
            return
        if left != right:
            changes.append(FieldChange(field=prefix or "value", before=left, after=right))

    walk("", before, after)
    return changes


def compare_documents(
    project_id: str,
    from_version: str,
    to_version: str,
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    sections: list[str] | None = None,
    kind: str = "BLUEPRINT_VERSIONS",
) -> DiffReport:
    """Object-by-object comparison of two Blueprint documents of the same shape.

    ``kind`` says what was compared: two approved versions (the default, BP-DIFF-001) or
    a working draft against its base (BP-DIFF-003).  The comparison itself is the same.
    """
    objects: list[ObjectDiff] = []
    for container in ENTRY_CONTAINERS:
        if container.section.value == "architecture":
            continue
        if sections and container.section.value not in sections:
            continue
        left, right = _entries(before, container), _entries(after, container)
        for key in sorted(set(left) | set(right)):
            if key in left and key not in right:
                objects.append(
                    ObjectDiff(
                        business_key=key,
                        dhis2_type=_type(left[key]),
                        uid=_uid(left[key]),
                        state=REMOVED,
                        disposition=_disposition(left[key]),
                        section=container.section.value,
                    )
                )
            elif key not in left:
                objects.append(
                    ObjectDiff(
                        business_key=key,
                        dhis2_type=_type(right[key]),
                        uid=_uid(right[key]),
                        state=ADDED,
                        disposition=_disposition(right[key]),
                        section=container.section.value,
                    )
                )
            elif left[key] != right[key]:
                changes = [
                    change
                    for change in field_changes(left[key], right[key])
                    if change.field.split("/")[0] not in IGNORED_FIELDS
                ]
                objects.append(
                    ObjectDiff(
                        business_key=key,
                        dhis2_type=_type(right[key]),
                        uid=_uid(right[key]),
                        state=UPDATED if changes else UNCHANGED,
                        disposition=_disposition(right[key]),
                        changes=changes,
                        section=container.section.value,
                    )
                )
            else:
                objects.append(
                    ObjectDiff(
                        business_key=key,
                        dhis2_type=_type(right[key]),
                        uid=_uid(right[key]),
                        state=UNCHANGED,
                        disposition=_disposition(right[key]),
                        section=container.section.value,
                    )
                )
    return _report(project_id, kind, objects, from_version, to_version)


def compare_registry(
    project_id: str,
    from_version: str,
    to_version: str,
    before: dict[str, Any],
    after: dict[str, Any],
) -> DiffReport:
    """Registry N vs N+1: the same comparison restricted to the registry section."""
    return compare_documents(
        project_id,
        from_version,
        to_version,
        before,
        after,
        sections=["registry"],
    )


def environment_diff(
    project_id: str,
    blueprint_version: str,
    environment_key: str,
    discovered: dict[str, dict[str, Any]],
    compiled: dict[str, dict[str, Any]],
    *,
    reference_only: set[str] | None = None,
    profile: ProfileView | None = None,
    profile_hash: str | None = None,
    differences: Sequence[dict[str, Any]] = (),
) -> DiffReport:
    """The environment's current objects vs what this compile produced (BP-CMP-029).

    The Profile is part of this comparison, not a note beside it: which fields count as
    a change is a property of the version being deployed against, and what it changed is
    what a reviewer of one environment's Diff needs to see (BP-CMP-030, AC-CAP-01).
    """
    reference_only = reference_only or set()
    objects: list[ObjectDiff] = []
    for key in sorted(set(discovered) | set(compiled)):
        left, right = discovered.get(key), compiled.get(key)
        if left is None and right is not None:
            state = REFERENCE_ONLY if key in reference_only else ADDED
            objects.append(
                ObjectDiff(
                    business_key=key,
                    dhis2_type=_type(right),
                    uid=_uid(right),
                    state=state,
                    disposition=_disposition(right),
                )
            )
        elif right is None and left is not None:
            state = REFERENCE_ONLY if key in reference_only else REMOVED
            objects.append(
                ObjectDiff(
                    business_key=key,
                    dhis2_type=_type(left),
                    uid=_uid(left),
                    state=state,
                )
            )
        elif left is not None and right is not None:
            excluded = (
                IGNORED_FIELDS
                | IDENTITY_COLUMNS
                | (profile.uncomparable_fields(str(_type(right) or "")) if profile else frozenset())
            )
            changes = field_changes(left, right, excluded=excluded)
            objects.append(
                ObjectDiff(
                    business_key=key,
                    dhis2_type=_type(right),
                    uid=_uid(right),
                    state=UPDATED if changes else UNCHANGED,
                    disposition=_disposition(right),
                    changes=changes,
                )
            )
    return _report(
        project_id,
        "ENVIRONMENT_VS_COMPILE",
        objects,
        None,
        blueprint_version,
        environment_key,
        profile=profile,
        profile_hash=profile_hash,
        differences=differences,
    )


def _report(
    project_id: str,
    kind: str,
    objects: list[ObjectDiff],
    from_version: str | None,
    to_version: str | None,
    environment_key: str | None = None,
    *,
    profile: ProfileView | None = None,
    profile_hash: str | None = None,
    differences: Sequence[dict[str, Any]] = (),
) -> DiffReport:
    counts: dict[str, int] = {}
    for item in objects:
        counts[item.state] = counts.get(item.state, 0) + 1
    return DiffReport(
        project_id=project_id,
        kind=kind,
        from_version=from_version,
        to_version=to_version,
        environment_key=environment_key,
        objects=objects,
        counts=dict(sorted(counts.items())),
        profile_version=profile.profile_version if profile else None,
        profile_hash=profile_hash,
        dhis2_version=profile.dhis2_version if profile else None,
        profile_differences=[ProfileDifference(**item) for item in differences],
    )


def _type(entry: dict[str, Any]) -> str | None:
    value = entry.get("dhis2Type") or entry.get("programType")
    return str(value) if value else None


def _uid(entry: dict[str, Any]) -> str | None:
    value = entry.get("uid")
    return str(value) if value else None


def _disposition(entry: dict[str, Any]) -> str | None:
    value = entry.get("disposition")
    return str(value) if value else None
