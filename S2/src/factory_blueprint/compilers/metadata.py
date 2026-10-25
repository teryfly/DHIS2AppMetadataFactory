"""Metadata Compiler (BP-CMP-021 … 028, BP-CMP-040).

Turns the approved Registry plus the mapping fields into DHIS2 metadata objects:
one logical import payload, a dependency graph, a change summary, the compiled
expressions and the deletion candidates.  It is a pure function of
:class:`CompileInputs` — same inputs, byte-identical output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from factory_foundation.api import FactoryError

from factory_blueprint.capability import (
    ADDRESSING_FIELDS,
    BASELINE_SHARING_FORMAT,
    BASELINE_TRANSLATION_FORMAT,
    ProfileView,
    view_of,
)
from factory_blueprint.compilers import refs
from factory_blueprint.compilers.context import CompileInputs
from factory_blueprint.content.document import BlueprintDocument
from factory_blueprint.content.layout import CONTAINER_BY_PATH
from factory_blueprint.descriptors import registry as descriptors
from factory_blueprint.descriptors.model import Implicit, TypeDescriptor
from factory_blueprint.dto.issue import Issue, make_issue, sort_issues
from factory_blueprint.enums import Disposition, IssueLevel, Section
from factory_blueprint.graph import model as graph_model
from factory_blueprint.registry import scope as managed_scope

RULE_TYPE_UNREGISTERED = "BPV-CMP-001"
RULE_DEPENDENCY_UNRESOLVED = "BPV-CMP-002"
RULE_PROGRAM_KIND = "BPV-CMP-003"
RULE_ATTRIBUTE_MOUNT = "BPV-CMP-004"
RULE_NO_UPDATE_BASE = "BPV-CMP-005"

#: distinguishes "this field is absent on both sides" from "absent on one side" when a
#: controlled field is compared against the environment's current content
_MISSING = object()

#: the dispositions whose object already exists somewhere, so a compile for one
#: environment owes S4 the baseline it observed *there* (BP-CMP-027a); a CREATE object
#: has no observation to carry
OBSERVED_DISPOSITIONS: tuple[str, ...] = (
    Disposition.REUSE.value,
    Disposition.REFERENCE.value,
    Disposition.UPDATE.value,
)

EXPRESSIVE_SECTIONS: tuple[tuple[str, str], ...] = (
    ("/rules/validationRules", "leftExpression"),
    ("/rules/validationRules", "rightExpression"),
    ("/rules/programRules", "expression"),
    ("/rules/programIndicators", "expression"),
)

PAYLOAD_IDENTITY_FIELDS: tuple[str, ...] = ("id", "code")

#: The accessModel's share level -> the ACL string a DHIS2 2.41 instance round-trips verbatim
#: (measured on 2.41.10: `--------`, `r-------`, `rw------`).
PUBLIC_ACCESS_LEVELS: dict[str, str] = {
    "": "--------",
    "none": "--------",
    "read": "r-------",
    "view": "r-------",
    "write": "rw------",
    "edit": "rw------",
    "readwrite": "rw------",
    "create": "rw------",
}
DEFAULT_PUBLIC_ACL = "--------"
#: a share granted to a group in the accessModel is read-only unless the level says otherwise
ROLE_READ_ACL = "r-------"


@dataclass
class CompileOutput:
    payload: dict[str, Any] = field(default_factory=dict)
    objects: dict[str, dict[str, Any]] = field(default_factory=dict)
    object_states: dict[str, str] = field(default_factory=dict)
    dependency_graph: dict[str, Any] = field(default_factory=dict)
    change_summary: dict[str, Any] = field(default_factory=dict)
    expressions: list[dict[str, Any]] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    counts: dict[str, dict[str, int]] = field(default_factory=dict)
    unmanaged_references: list[dict[str, Any]] = field(default_factory=list)
    environment_dependent: list[str] = field(default_factory=list)
    managed_scope: list[dict[str, Any]] = field(default_factory=list)
    uid_map: list[dict[str, Any]] = field(default_factory=list)
    controlled_fields: dict[str, list[str]] = field(default_factory=dict)
    observation_baselines: list[dict[str, Any]] = field(default_factory=list)
    deletion_candidates: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: where this environment's Profile differs from the shipped descriptor set
    profile_differences: list[dict[str, Any]] = field(default_factory=list)

    @property
    def blocking(self) -> bool:
        return any(issue.level is IssueLevel.BLOCKING for issue in self.issues)


@dataclass(frozen=True)
class _Lookups:
    """Document-wide maps, built once per compile instead of once per object.

    Reading them through :class:`BlueprintDocument` normalizes the whole document, so
    doing it inside the entry loop makes the compile quadratic (BP-NFR-01).
    """

    types: dict[str, str]
    role_names: dict[str, Any]


def _entries(inputs: CompileInputs) -> list[dict[str, Any]]:
    view = BlueprintDocument(inputs.document)
    container = CONTAINER_BY_PATH["/registry/entries"]
    return sorted(
        view.container_entries(container),
        key=lambda entry: (str(entry.get("dhis2Type") or ""), str(entry.get("businessKey") or "")),
    )


def _mapping_fields(inputs: CompileInputs) -> dict[str, dict[str, Any]]:
    view = BlueprintDocument(inputs.document)
    container = CONTAINER_BY_PATH["/mapping/entries"]
    result: dict[str, dict[str, Any]] = {}
    for entry in view.container_entries(container):
        key = entry.get("businessKey")
        if isinstance(key, str):
            result[key] = entry
    return result


def _role_names(document: dict[str, Any]) -> dict[str, Any]:
    """businessKey -> role name, resolved once instead of per compiled object."""
    container = CONTAINER_BY_PATH["/accessModel/roles"]
    return {
        str(role.get("businessKey")): role.get("name")
        for role in BlueprintDocument(document).container_entries(container)
    }


#: the keys of one ``managedScope`` manifest row, in the manifest's own naming
MANAGED_SCOPE_KEYS = (
    "businessKey",
    "dhis2Type",
    "uid",
    "code",
    "disposition",
    "controlledFields",
    "observedLastUpdated",
    "observedHash",
)

#: a manifest row is camelCase, ``ManagedObject.model_dump`` is not
_MANAGED_SCOPE_ALIASES = {
    "businessKey": "business_key",
    "dhis2Type": "dhis2_type",
    "controlledFields": "controlled_fields",
    "observedLastUpdated": "observed_last_updated",
    "observedHash": "observed_hash",
}


def _managed_scope_row(row: dict[str, Any]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key in MANAGED_SCOPE_KEYS:
        value = row.get(key, row.get(_MANAGED_SCOPE_ALIASES.get(key, key)))
        if value is None:
            continue
        document[key] = sorted(value) if isinstance(value, list) else value
    return document


def _managed_scope_rows(inputs: CompileInputs) -> list[dict[str, Any]]:
    """The environment's Managed Scope as the manifest states it (BP-CMP-027a).

    S4 never calls S2: the ``Managed Scope`` a Snapshot records and a drift check walks
    can only come from ``metadata_manifest.json``, so an empty section here silently
    shrinks every Snapshot to the payload objects alone.
    """
    return sorted(
        (_managed_scope_row(row) for row in inputs.managed_scope),
        key=lambda row: (str(row.get("dhis2Type") or ""), str(row.get("businessKey") or "")),
    )


def compile_metadata(inputs: CompileInputs) -> CompileOutput:
    """Compile the whole metadata payload of one version and one environment."""
    output = CompileOutput()
    output.managed_scope = _managed_scope_rows(inputs)
    mappings = _mapping_fields(inputs)
    # the document is normalized once per view, so these lookups are built here rather
    # than inside the per-entry loop (BP-NFR-01: 1,000 objects compile in seconds)
    types = graph_model.registry_types(inputs.document)
    role_names = _role_names(inputs.document)
    lookups = _Lookups(types=types, role_names=role_names)
    profile = view_of(inputs.profile)
    # keyed so two entries of one type report the same Profile difference once
    differences: dict[tuple[str, str, str], dict[str, Any]] = {}
    bindings = dict(inputs.bindings)
    payload: dict[str, list[dict[str, Any]]] = {}

    for entry in _entries(inputs):
        business_key = str(entry.get("businessKey") or "")
        dhis2_type = str(entry.get("dhis2Type") or "")
        disposition = str(entry.get("disposition") or "")
        uid = bindings.get(business_key) or (
            entry.get("uid") if isinstance(entry.get("uid"), str) else None
        )

        observed_hash, observed_last_updated = _observation(inputs, dhis2_type, uid, entry)
        if disposition in OBSERVED_DISPOSITIONS:
            # S4 does not call S2: the drift check, the Snapshot and the post-commit
            # readback all work off this section, so it has to carry the baseline of the
            # object *as this environment holds it* (BP-CMP-027a, AC-ENV-01)
            output.observation_baselines.append(
                {
                    "businessKey": business_key,
                    "dhis2Type": dhis2_type,
                    "uid": uid,
                    "observedLastUpdated": observed_last_updated,
                    "observedHash": observed_hash,
                }
            )

        if disposition in (Disposition.REUSE.value, Disposition.REFERENCE.value):
            output.unmanaged_references.append(
                {
                    "businessKey": business_key,
                    "dhis2Type": dhis2_type,
                    "uid": uid,
                    "disposition": disposition,
                    "observedHash": observed_hash,
                    "observedLastUpdated": observed_last_updated,
                }
            )
            output.object_states[business_key] = "REFERENCE_ONLY"
            if uid:
                bindings.setdefault(business_key, uid)
            continue
        if disposition == Disposition.EXTERNAL.value:
            output.object_states[business_key] = "EXTERNAL"
            continue
        if disposition == Disposition.DEPRECATED.value:
            output.object_states[business_key] = "DEPRECATED"
            if business_key in inputs.deletion_candidates:
                output.deletion_candidates.append(
                    {
                        "businessKey": business_key,
                        "dhis2Type": dhis2_type,
                        "uid": uid,
                        "reason": "listed in the approved change request",
                    }
                )
            continue

        descriptor = descriptors.descriptor(dhis2_type)
        if descriptor is None:
            output.issues.append(
                make_issue(
                    RULE_TYPE_UNREGISTERED,
                    IssueLevel.BLOCKING,
                    f"object type {dhis2_type!r} of {business_key!r} has no enabled type "
                    "descriptor; the compile stops instead of skipping it (FR-COV-002)",
                    business_key=business_key,
                    dhis2_type=dhis2_type,
                    section=Section.REGISTRY,
                )
            )
            continue

        mapping = mappings.get(business_key) or {}
        if profile is not None:
            output.issues.extend(profile.type_issues(descriptor))
            if not profile.supports(descriptor):
                # the target version does not have this type, so there is nothing honest
                # to emit for it; the BLOCKING issue above says what to do instead
                output.object_states[business_key] = "UNSUPPORTED_BY_PROFILE"
                output.notes.append(
                    f"{descriptor.dhis2_type} {business_key}: not compiled, "
                    f"{profile.profile_version} does not cover it"
                )
                continue
            for item in profile.differences(descriptor):
                differences[_difference_key(item)] = item

        object_state, resolved_uid = _compile_object(
            inputs, output, descriptor, entry, mapping, business_key, uid, bindings, lookups,
            profile,
        )
        if object_state is None:
            continue
        state, payload_object = object_state
        output.object_states[business_key] = state
        output.objects[business_key] = payload_object
        if resolved_uid:
            bindings[business_key] = resolved_uid
        bucket = payload.setdefault(descriptor.payload_key, [])
        if descriptor.embedded_in_parent is None:
            bucket.append(payload_object)
        output_counts = output.counts.setdefault(descriptor.dhis2_type, {})
        output_counts[state.lower()] = output_counts.get(state.lower(), 0) + 1

    ordered_payload = {
        key: sorted(value, key=_identity_sort_key) for key, value in sorted(payload.items())
    }
    output.payload = ordered_payload
    output.uid_map = [
        {
            "businessKey": str(entry.get("businessKey")),
            "uid": bindings.get(str(entry.get("businessKey"))) or entry.get("uid"),
            "dhis2Type": entry.get("dhis2Type"),
            "code": entry.get("code"),
            "disposition": entry.get("disposition"),
            "variesByEnvironment": _varies_by_environment(str(entry.get("disposition") or "")),
        }
        for entry in _entries(inputs)
    ]
    output.dependency_graph = build_graph(inputs, bindings, output)
    # an object varies by environment when its disposition says so, and also when the
    # compile left one of its references as a placeholder waiting for a binding
    dependent = sorted(
        set(output.environment_dependent) | set(output_environment_dependent(inputs))
    )
    output.environment_dependent = dependent
    output.change_summary = {
        "environmentKey": inputs.environment_key,
        "blueprintVersion": inputs.blueprint_version,
        "byType": {key: dict(sorted(values.items())) for key, values in sorted(output.counts.items())},
        "referencesOutsideManagedScope": sorted(
            {
                (str(item["businessKey"]), str(item["uid"])): item
                for item in output.unmanaged_references
            }.values(),
            key=lambda item: (str(item["businessKey"]), str(item["uid"])),
        ),
        "environmentDependentObjects": dependent,
    }
    output.expressions = compile_expressions(inputs, bindings)
    output.controlled_fields = {
        str(entry.get("businessKey")): list(entry.get("controlledFields") or [])
        for entry in _entries(inputs)
        if entry.get("disposition") == Disposition.UPDATE.value
    }
    if profile is not None:
        output.issues.extend(profile.constraint_issues())
        for item in profile.format_differences():
            differences[_difference_key(item)] = item
    output.profile_differences = [differences[key] for key in sorted(differences)]
    output.issues = sort_issues(output.issues)
    return output


def _difference_key(item: dict[str, Any]) -> tuple[str, str, str]:
    return (str(item["effect"]), str(item["dhis2_type"]), str(item["name"]))


def _observation(
    inputs: CompileInputs, dhis2_type: str, uid: str | None, entry: dict[str, Any]
) -> tuple[Any, Any]:
    """What the environment being compiled holds for one object, as (hash, lastUpdated).

    A Registry entry records what the *design* environment showed at freeze time.  A
    package for TEST or UAT has to hand S4 that instance's own baseline, or the drift
    check compares two different objects and reports a change nobody made (BP-CMP-027a,
    AC-ENV-01).  With no Discovery Result there is nothing newer to say, so the entry
    stands.
    """
    if inputs.discovery is not None and uid:
        found = inputs.discovery.find_by_uid(dhis2_type, uid)
        if found is not None and (found.content_hash or found.last_updated):
            return found.content_hash, found.last_updated
    return entry.get("observedHash"), entry.get("observedLastUpdated")


def _identity_sort_key(item: dict[str, Any]) -> tuple[str, str]:
    return (str(item.get("code") or ""), str(item.get("id") or ""))


def output_environment_dependent(inputs: CompileInputs) -> list[str]:
    """Entries whose UID differs per environment: REUSE and REFERENCE (FR-ENV-005)."""
    view = BlueprintDocument(inputs.document)
    container = CONTAINER_BY_PATH["/registry/entries"]
    return [
        str(entry.get("businessKey"))
        for entry in view.container_entries(container)
        if str(entry.get("disposition"))
        in (Disposition.REUSE.value, Disposition.REFERENCE.value, Disposition.UPDATE.value)
    ]


def _varies_by_environment(disposition: str) -> bool:
    return disposition in (
        Disposition.REUSE.value,
        Disposition.REFERENCE.value,
        Disposition.UPDATE.value,
    )


def _compile_object(
    inputs: CompileInputs,
    output: CompileOutput,
    descriptor: TypeDescriptor,
    entry: dict[str, Any],
    mapping: dict[str, Any],
    business_key: str,
    uid: str | None,
    bindings: dict[str, str],
    lookups: _Lookups,
    profile: ProfileView | None,
) -> tuple[tuple[str, dict[str, Any]] | None, str | None]:
    """Build one payload object; None when the object cannot be compiled."""
    disposition = str(entry.get("disposition"))
    types = lookups.types
    fields = dict(mapping.get("fields") or {})
    structure = dict(mapping.get("structure") or {})
    #: only an UPDATE has a base to depart from and controlled fields to compare
    base: dict[str, Any] = {}
    controlled: list[str] = []

    if disposition == Disposition.UPDATE.value:
        if not inputs.is_environment_bound:
            # the merge needs this environment's current content, which a baseline
            # compile does not have: the object is defined here and merged there
            output.notes.append(
                f"{descriptor.dhis2_type} {business_key}: an UPDATE object is merged over "
                "the environment's content, so it appears in the environment package only"
            )
            output.environment_dependent.append(business_key)
            return None, uid
        current = _update_base(inputs, output, business_key, uid)
        if current is None:
            return None, uid
        base = current
        controlled = [str(name) for name in entry.get("controlledFields") or []]
        merged = {
            **base,
            **{key: value for key, value in fields.items() if key in controlled},
        }
        merged.setdefault("id", uid)
        object_state = "UPDATED"
        candidate = merged
    else:
        candidate = dict(fields)
        object_state = "CREATED"
    if descriptor.template:
        candidate = {
            **_template_literals(inputs, descriptor),
            **candidate,
        }
    candidate.setdefault("id", uid)
    if entry.get("code"):
        candidate.setdefault("code", entry["code"])
    candidate = _resolve_translatable(candidate, inputs, descriptor)
    candidate, missing = _resolve_dependencies(
        inputs, output, descriptor, candidate, structure, bindings, business_key, types
    )
    if missing:
        return None, uid
    candidate = _apply_sharing(inputs, descriptor, candidate, business_key, lookups, output)
    _check_implicit(inputs, descriptor, candidate, output, business_key)
    clean = _strip_forbidden(candidate, descriptor)
    if uid:
        # A tier-1 descriptor lists `id` among the fields DHIS2 maintains, and FR-CMP-006's
        # own examples are `_comment` / `_import_order` / `workflowId` — not the object's
        # identifier.  This Factory allocates UIDs from its pool (BP-REG-008) and S4 commits
        # with `identifier=ASSIGNED` for exactly that reason, so putting the promised UID back
        # is what makes the Registry's `uid` true: stripped, DHIS2 minted a fresh one, and
        # POST_VERIFY then reported the object this Run created as MISSING_OBJECT (DX-EXE-040).
        # Assigning rather than defaulting also drops any `id` an author put in the mapping.
        clean["id"] = uid
        clean = dict(sorted(clean.items()))
    if profile is not None:
        clean = _apply_profile_fields(
            profile, descriptor, business_key, entry, object_state, clean, output
        )
    if object_state == "UPDATED" and not _controlled_delta(base, clean, controlled):
        # FR-CMP-008 counts 新建 / 更新 / 不变 per type.  An UPDATE whose controlled fields
        # already hold those exact values in this environment is compiled and shipped —
        # DHIS2 still decides the import — but it changes nothing, and a reviewer reading
        # "3 updates" who is told "0 objects will actually differ" is being misled.
        object_state = "UNCHANGED"
    return (object_state, clean), uid


def _controlled_delta(
    base: dict[str, Any], compiled: dict[str, Any], controlled: list[str]
) -> list[str]:
    """The controlled fields whose compiled value differs from the environment's own."""
    return [
        name
        for name in controlled
        if compiled.get(name, _MISSING) != base.get(name, _MISSING)
    ]


def _apply_profile_fields(
    profile: ProfileView,
    descriptor: TypeDescriptor,
    business_key: str,
    entry: dict[str, Any],
    object_state: str,
    candidate: dict[str, Any],
    output: CompileOutput,
) -> dict[str, Any]:
    """Drop and report what this version will not take (BP-CMP-030, AC-CAP-01).

    An UPDATE re-states only its controlled fields, and every other value came back
    from the instance itself — so only the managed fields can be a capability conflict
    here.  A CREATE sends the whole object, so each property it carries is checked.
    """
    if object_state == "UPDATED":
        names = [str(name) for name in entry.get("controlledFields") or []]
    else:
        names = [str(name) for name in candidate if name not in ADDRESSING_FIELDS]
    dropped: set[str] = set()
    for name in sorted(set(names)):
        output.issues.extend(profile.field_issues(descriptor, business_key, name))
        if not profile.field(descriptor.dhis2_type, name).writable:
            dropped.add(name)
    return {key: value for key, value in candidate.items() if key not in dropped}


def _template_literals(inputs: CompileInputs, descriptor: TypeDescriptor) -> dict[str, Any]:
    """Only the template's static values: anything awaiting substitution is dropped.

    A template's ``{"id": "${categoryCombo}"}`` is a hole the dependency step fills,
    so copying it in verbatim would make the compiler look up a placeholder as if it
    were a businessKey.
    """
    try:
        template = descriptors.template(descriptor.descriptor_version, descriptor.template)
    except FactoryError:
        # a descriptor that names no template, or one the set does not carry, is
        # reported where the descriptor set is loaded, not per compiled object
        return {}
    literals = {
        key: value
        for key, value in template.items()
        if not _has_placeholder(value)
    }
    del inputs
    return literals


def _has_placeholder(value: Any) -> bool:
    if isinstance(value, str):
        return value.startswith("${") or value.endswith("}") and value.startswith("${")
    if isinstance(value, dict):
        return any(_has_placeholder(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_placeholder(item) for item in value)
    return False


def _update_base(
    inputs: CompileInputs, output: CompileOutput, business_key: str, uid: str | None
) -> dict[str, Any] | None:
    """The environment's current object content, which UPDATE must preserve."""
    observed = inputs.observed.get(uid or "")
    if isinstance(observed, dict) and observed:
        return dict(observed)
    output.issues.append(
        make_issue(
            RULE_NO_UPDATE_BASE,
            IssueLevel.BLOCKING,
            f"no discovered content for UPDATE entry {business_key!r}; the merge would "
            "overwrite fields this project does not own (FR-CMP-007)",
            business_key=business_key,
            uid=uid,
            section=Section.REGISTRY,
        )
    )
    return None


def _resolve_translatable(
    candidate: dict[str, Any], inputs: CompileInputs, descriptor: TypeDescriptor
) -> dict[str, Any]:
    """localizedText values become the target version's name plus translations."""
    result = dict(candidate)
    translations: list[dict[str, Any]] = []
    for name in descriptor.translatable_fields:
        value = result.get(name)
        if isinstance(value, dict) and "default" in value:
            default = str(value["default"])
            result[name] = default
            for locale, text in sorted((value.get("translations") or {}).items()):
                translations.append(
                    {"field": name, "locale": str(locale), "text": str(text)}
                )
    if descriptor.translatable_fields and translations:
        profile = view_of(inputs.profile)
        key = profile.translation_property if profile else BASELINE_TRANSLATION_FORMAT
        result[key] = sorted(translations, key=lambda item: (item["field"], item["locale"]))
    return result


def _resolve_dependencies(
    inputs: CompileInputs,
    output: CompileOutput,
    descriptor: TypeDescriptor,
    candidate: dict[str, Any],
    structure: dict[str, Any],
    bindings: dict[str, str],
    business_key: str,
    types: dict[str, str],
) -> tuple[dict[str, Any], list[str]]:
    """Replace businessKey references with ``{"id": uid}`` and note the defaults."""
    result = dict(candidate)
    missing: list[str] = []
    for dependency in descriptor.dependencies:
        raw = result.get(dependency.field, structure.get(dependency.field))
        if dependency.wrapper:
            items = structure.get(dependency.wrapper)
            if isinstance(items, list):
                result[dependency.wrapper] = sorted(
                    [
                        _reference_item(item, dependency, bindings, inputs)
                        for item in items
                        if isinstance(item, dict)
                    ],
                    key=lambda item: str(item.get(dependency.item_field)),
                )
            continue
        if raw is None:
            if dependency.required and not _implicit_covers(descriptor, dependency.field):
                missing.append(dependency.field)
                output.issues.append(
                    make_issue(
                        RULE_DEPENDENCY_UNRESOLVED,
                        IssueLevel.BLOCKING,
                        f"{descriptor.dhis2_type} {business_key!r} has no {dependency.field}, "
                        "and no implicit rule supplies it (FR-COV-003…005)",
                        business_key=business_key,
                        dhis2_type=descriptor.dhis2_type,
                        section=Section.REGISTRY,
                    )
                )
            continue
        key = raw if isinstance(raw, str) else str(_key_of(raw))
        target_uid = bindings.get(key)
        if target_uid is None:
            if not inputs.is_environment_bound:
                target_uid = refs.placeholder(key)
            else:
                missing.append(dependency.field)
                output.issues.append(
                    make_issue(
                        RULE_DEPENDENCY_UNRESOLVED,
                        IssueLevel.BLOCKING,
                        f"reference {key!r} of {business_key!r} resolves to no object in "
                        f"environment {inputs.environment_key}",
                        business_key=business_key,
                        dhis2_type=descriptor.dhis2_type,
                        section=Section.REGISTRY,
                    )
                )
                continue
            output.environment_dependent.append(key)
        result[dependency.field] = {"id": target_uid}
        if key not in types:
            output.unmanaged_references.append(
                {"businessKey": key, "dhis2Type": dependency.target_type, "uid": target_uid}
            )
    return result, missing


def _implicit_covers(descriptor: TypeDescriptor, field_name: str) -> bool:
    if field_name == "categoryCombo":
        return Implicit.DEFAULT_CATEGORY_COMBO in descriptor.implicit
    if field_name in ("trackedEntity", "programType"):
        return Implicit.PROGRAM_TRACKED_ENTITY_OR_EVENT in descriptor.implicit
    if field_name in ("access", "sharing"):
        return Implicit.SHARING_FROM_ACCESS_MODEL in descriptor.implicit
    if field_name in ("attributeValues",):
        return Implicit.ATTRIBUTE_VALUES in descriptor.implicit
    return False


def _reference_item(
    item: dict[str, Any], dependency: Any, bindings: dict[str, str], inputs: CompileInputs
) -> dict[str, Any]:
    out = dict(item)
    key = out.get(dependency.item_field or "")
    if isinstance(key, str):
        uid = bindings.get(key) or (
            refs.placeholder(key) if not inputs.is_environment_bound else key
        )
        out[dependency.item_field or ""] = {"id": uid}
    return out


def _key_of(raw: Any) -> Any:
    if isinstance(raw, dict):
        return raw.get("id") or raw.get("businessKey") or raw.get("code")
    return raw


def _apply_sharing(
    inputs: CompileInputs,
    descriptor: TypeDescriptor,
    candidate: dict[str, Any],
    business_key: str,
    lookups: _Lookups,
    output: CompileOutput,
) -> dict[str, Any]:
    """FR-COV-006: sharing always comes from the approved accessModel."""
    if Implicit.SHARING_FROM_ACCESS_MODEL not in descriptor.implicit:
        return candidate
    access = inputs.document.get(Section.ACCESS_MODEL.value) or {}
    overrides = {
        str(item.get("businessKey")): item.get("sharing")
        for item in access.get("sharingOverrides") or []
        if isinstance(item, dict)
    }
    sharing = overrides.get(business_key) or access.get("sharingDefaults")
    if isinstance(sharing, dict):
        profile = view_of(inputs.profile)
        # the property holding a share is itself versioned: a Profile that renamed it
        # must be written that way or the instance ignores the whole block (FR-CAP-003)
        key = profile.sharing_property if profile else BASELINE_SHARING_FORMAT
        candidate[key] = _resolve_sharing(sharing, lookups.role_names, output, business_key)
    return candidate


def _resolve_sharing(
    sharing: dict[str, Any],
    role_names: dict[str, Any],
    output: CompileOutput,
    business_key: str,
) -> dict[str, Any]:
    """The approved share, in the document a DHIS2 2.41 metadata import actually reads.

    ``sharingDefaults`` speaks the accessModel's vocabulary (`accessLevel`, `publicAccess`),
    while 2.41 expects ``sharing: {public, users, userGroups}`` with an ACL string.  Sending the
    first shape meant the instance ignored the block, kept its own `rw------`, and POST_VERIFY
    then compared a payload field the environment never had (DX-EXE-040, FR-COV-006).  The ACL
    strings below are the ones 2.41.10 round-trips verbatim.
    """
    public = PUBLIC_ACCESS_LEVELS.get(
        str(sharing.get("publicAccess") or sharing.get("accessLevel") or "").strip().lower(),
    )
    document: dict[str, Any] = {
        "public": public if public is not None else DEFAULT_PUBLIC_ACL,
        "users": dict(sharing.get("users") or {}),
        "userGroups": dict(sharing.get("userGroups") or {}),
    }
    if public is None and (sharing.get("publicAccess") or sharing.get("accessLevel")):
        output.issues.append(
            make_issue(
                "FR-COV-006",
                IssueLevel.WARNING,
                f"{business_key}: the approved share {sharing.get('publicAccess') or sharing.get('accessLevel')!r} "
                "is not a level this Factory can name in a DHIS2 ACL, so the object ships the "
                "closed default rather than a share the instance would silently ignore",
                business_key=business_key,
            )
        )
    # A share granted to a *role* cannot be expressed here: the ACL is keyed by the target
    # environment's userGroup UIDs, which a compile does not have.  Dropping it quietly would
    # hand the operator an object whose sharing is not what the Gate approved.
    for key in ("ownerRoles", "externalRoles"):
        values = sharing.get(key)
        if isinstance(values, list) and values:
            document["userGroups"].update(
                {str(role_names.get(name, name)): ROLE_READ_ACL for name in values}
            )
            output.notes.append(
                f"{business_key}: {key} {sorted(str(v) for v in values)} were named by role and "
                "are shipped under those names — DHIS2 resolves a share by userGroup UID, so the "
                "import report is where an unmatched name shows up (FR-COV-006)"
            )
    return document


def _check_implicit(
    inputs: CompileInputs,
    descriptor: TypeDescriptor,
    candidate: dict[str, Any],
    output: CompileOutput,
    business_key: str,
) -> None:
    """The type-specific implicit rules that must hold in the emitted object."""
    if Implicit.DEFAULT_CATEGORY_COMBO in descriptor.implicit and "categoryCombo" not in candidate:
        default_uid = inputs.discovery.default_category_combo_uid() if inputs.discovery else None
        candidate["categoryCombo"] = {"id": default_uid or refs.placeholder("default.categoryCombo")}
        output.notes.append(
            f"{descriptor.dhis2_type} {business_key}: default CategoryCombo of the target "
            "environment was used (FR-COV-003)"
        )
        if default_uid:
            # this row claims the disposition REFERENCE, so BP-CMP-027a owes it the same
            # thing it owes a declared reference: the environment's own baseline, or S4
            # reads the object it addressed and has nothing to compare it against
            observed_hash, observed_last_updated = _observation(
                inputs, "categoryCombo", default_uid, {}
            )
            output.unmanaged_references.append(
                {
                    "businessKey": "default.categoryCombo",
                    "dhis2Type": "categoryCombo",
                    "uid": default_uid,
                    "disposition": Disposition.REFERENCE.value,
                    "observedHash": observed_hash,
                    "observedLastUpdated": observed_last_updated,
                }
            )
    if Implicit.PROGRAM_TRACKED_ENTITY_OR_EVENT in descriptor.implicit:
        program_type = candidate.get("programType")
        has_entity = isinstance(candidate.get("trackedEntity"), dict)
        if not program_type or (not has_entity and program_type != "WITHOUT_REGISTRATION"):
            output.issues.append(
                make_issue(
                    RULE_PROGRAM_KIND,
                    IssueLevel.BLOCKING,
                    f"program {business_key!r} declares neither a TrackedEntityType nor an "
                    "event program type (FR-COV-004)",
                    business_key=business_key,
                    dhis2_type=descriptor.dhis2_type,
                    section=Section.REGISTRY,
                )
            )
    if Implicit.ATTRIBUTE_MOUNT in descriptor.implicit and not candidate.get("mount"):
        output.issues.append(
            make_issue(
                RULE_ATTRIBUTE_MOUNT,
                IssueLevel.BLOCKING,
                f"trackedEntityAttribute {business_key!r} does not say whether it is mounted "
                "on a TrackedEntityType or on a Program (FR-COV-005)",
                business_key=business_key,
                dhis2_type=descriptor.dhis2_type,
                section=Section.REGISTRY,
            )
        )


def _strip_forbidden(candidate: dict[str, Any], descriptor: TypeDescriptor) -> dict[str, Any]:
    """FR-CMP-006: no system control field may ever reach the payload.

    A descriptor lists the properties DHIS2 maintains itself, and the same list is what
    the static layer checks (BPV-ST-003), so the compiler and the validator cannot
    disagree about what may not ship.
    """
    forbidden = set(descriptor.forbidden_fields) | {"workflowId", "_comment", "_import_order"}
    return {
        key: value
        for key, value in sorted(candidate.items())
        if key not in forbidden and not key.startswith("_")
    }


def compile_expressions(inputs: CompileInputs, bindings: dict[str, str]) -> list[dict[str, Any]]:
    """businessKey references in expressions become UIDs; each is classified (D-05).

    What S4 needs is not only "what did this reference become" but "who has to check
    it": an expression whose every object already exists in the target environment can
    be verified by DHIS2 itself, while anything else waits for the post-commit read
    (BP-CMP-028).  Whether the expression parses is the validator's answer, not the
    compiler's, so no parsing verdict is claimed here.
    """
    view = BlueprintDocument(inputs.document)
    present = set(bindings) | {
        str(entry.get("businessKey")) for entry in _entries(inputs)
    }
    compiled: list[dict[str, Any]] = []
    for path, field_name in EXPRESSIVE_SECTIONS:
        container = CONTAINER_BY_PATH[path]
        for entry in view.container_entries(container):
            raw = entry.get(field_name)
            if not isinstance(raw, str) or not raw:
                continue
            substitution = refs.substitute_references(raw, bindings)
            verdict = refs.classify_expression(substitution.references, present)
            compiled.append(
                {
                    "businessKey": entry.get(container.key_field),
                    "field": field_name,
                    "original": raw,
                    "compiled": substitution.text,
                    "references": sorted(set(substitution.references)),
                    "unresolved": sorted(set(substitution.unresolved)),
                    "validation": verdict,
                    "needsServerCheck": verdict == "POST_VERIFY",
                }
            )
    return sorted(compiled, key=lambda item: (str(item["businessKey"]), str(item["field"])))


#: the graph document's own format version (BP-TD-009).  A file written before the
#: read existed carries no ``isolatedNodes`` or ``unresolvedReferences`` at all, and a
#: reader must tell "nobody computed it" apart from "it was computed and empty".
GRAPH_SCHEMA_VERSION = 1


def build_graph(
    inputs: CompileInputs, bindings: dict[str, str], output: CompileOutput
) -> dict[str, Any]:
    """dependency-graph.json: the metadata object graph of this compile (6.5, BP-TD-009).

    The degrees, ``orphans`` and ``unresolvedRegistryRefs`` are computed here, on the whole
    graph, so that every caller of ``get_dependency_graph`` sees one answer for one build --
    a filtered view reports the same numbers an unfiltered one does (BP-AC-16).  Nodes are
    Registry entries and nothing else (BP-TD-006): the references between pages, intents and
    decisions are provenance, not this graph.
    """
    nodes_by_key: dict[str, dict[str, Any]] = {}
    for entry in _entries(inputs):
        business_key = str(entry.get("businessKey"))
        nodes_by_key[business_key] = {
            "businessKey": business_key,
            "dhis2Type": entry.get("dhis2Type"),
            "uid": bindings.get(business_key) or entry.get("uid"),
            "disposition": entry.get("disposition"),
            "managed": managed_scope.is_managed(str(entry.get("disposition") or "")),
            "inPayload": business_key in output.objects,
            "inDegree": 0,
            "outDegree": 0,
        }
    edges = [edge.as_row() for edge in graph_model.build_edges(inputs.document)]
    for row in edges:
        # both ends are Registry entries by construction: build_edges only emits an edge
        # between keys the Registry holds, and _entries walks that same container
        nodes_by_key[str(row["from_business_key"])]["outDegree"] += 1
        nodes_by_key[str(row["to_business_key"])]["inDegree"] += 1
    return {
        "schemaVersion": GRAPH_SCHEMA_VERSION,
        "nodes": sorted(nodes_by_key.values(), key=lambda node: str(node["businessKey"])),
        "edges": sorted(edges, key=lambda row: (row["from_business_key"], row["field"])),
        "orphans": sorted(
            business_key
            for business_key, node in nodes_by_key.items()
            if node["inDegree"] == 0 and node["outDegree"] == 0
        ),
        "unresolvedRegistryRefs": [
            {
                "from_business_key": item.from_business_key,
                "field": item.field,
                "target": item.target,
            }
            for item in graph_model.dropped_references(inputs.document)
        ],
        "note": "the graph explains references; it never decides import order (FR-COV-012)",
    }
