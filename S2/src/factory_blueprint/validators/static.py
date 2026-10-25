"""Static product validation (BP-VAL-020/021/022, overall SRS 13.3).

Identity, references, schema, architecture, scope, languages, expressions and
ownership are checked here against the compiled payload — before anything is sent
to a DHIS2 instance.  Failures are BLOCKING except missing translations, which the
SRS grades as WARNING (FR-COV-010).
"""

from __future__ import annotations

from typing import Any

from factory_blueprint.capability import view_of
from factory_blueprint.capability.language import (
    ADDRESSING_FIELDS,
    DEPRECATED,
    REMOVED,
    SUPPORTED,
)
from factory_blueprint.capability.view import (
    RULE_FIELD_DEPRECATED,
    RULE_FIELD_FORBIDDEN,
    RULE_FIELD_REMOVED,
)
from factory_blueprint.compilers import refs
from factory_blueprint.compilers.context import CompileInputs
from factory_blueprint.compilers.metadata import CompileOutput
from factory_blueprint.content.document import BlueprintDocument
from factory_blueprint.content.layout import CONTAINER_BY_PATH
from factory_blueprint.descriptors import registry as descriptors
from factory_blueprint.dto.issue import Issue, make_issue, sort_issues
from factory_blueprint.enums import Disposition, ImpactLevel, IssueLevel, Section
from factory_blueprint.validators import expressions_check as expressions

RULE_IDENTITY = "BPV-ST-001"
RULE_REFERENCE = "BPV-ST-002"
RULE_SCHEMA = "BPV-ST-003"
RULE_ARCHITECTURE = "BPV-ST-004"
RULE_SCOPE = "BPV-ST-005"
RULE_LANGUAGE = "BPV-ST-006"
RULE_EXPRESSION = "BPV-ST-007"
RULE_UNREGISTERED = "BPV-ST-008"
RULE_PLACEHOLDER = "BPV-ST-009"


def validate_build(
    inputs: CompileInputs,
    output: CompileOutput,
) -> list[Issue]:
    """Full static validation of an environment-bound product."""
    return sort_issues([*_check_common(inputs, output), *_check_environment(inputs, output)])


def environment_independent_checks(
    inputs: CompileInputs, output: CompileOutput
) -> list[Issue]:
    """What a baseline compile can decide without an environment (BP-CMP-010)."""
    return sort_issues(_check_common(inputs, output))


def _check_common(
    inputs: CompileInputs, output: CompileOutput
) -> list[Issue]:
    issues: list[Issue] = []
    # reading the Registry through BlueprintDocument normalizes the whole document, so
    # it is done once here rather than once per checked object (BP-NFR-02)
    view = BlueprintDocument(inputs.document)
    entries = _registry_by_key(view)
    issues.extend(_identity(inputs, entries, output))
    issues.extend(_architecture(inputs, view, entries, output))
    issues.extend(_schema(inputs, output, entries))
    issues.extend(_capability(inputs, output, entries))
    issues.extend(_scope(inputs, output, entries))
    issues.extend(_languages(inputs, output, entries))
    issues.extend(_expressions(inputs, output))
    return issues


def _registry_by_key(view: BlueprintDocument) -> dict[str, dict[str, Any]]:
    container = CONTAINER_BY_PATH["/registry/entries"]
    return {
        str(entry.get("businessKey")): entry for entry in view.container_entries(container)
    }


def _architecture(
    inputs: CompileInputs,
    view: BlueprintDocument,
    entries: dict[str, dict[str, Any]],
    output: CompileOutput,
) -> list[Issue]:
    """The 架构 dimension (BP-VAL-020, overall SRS 13.3).

    Three things must hold: the bytes compiled are the approved version's, nothing ships
    that no mapping decision asked for, and a Program's Tracker or Event shape is the one
    the mapping says.
    """
    issues: list[Issue] = []
    if view.content_hash != inputs.blueprint_hash:
        issues.append(
            _issue(
                RULE_ARCHITECTURE,
                "the document compiled is not the approved version this build claims: "
                f"{view.content_hash} != {inputs.blueprint_hash}",
                dhis2_type="",
            )
        )
    mapped = {
        str(entry.get("businessKey"))
        for entry in view.container_entries(CONTAINER_BY_PATH["/mapping/entries"])
    }
    for business_key in sorted(set(output.objects) - mapped):
        issues.append(
            _issue(
                RULE_ARCHITECTURE,
                f"{business_key!r} is in the payload but no mapping entry produced it "
                "(BP-VAL-020 架构: 不存在未经映射决策的对象)",
                business_key=business_key,
            )
        )
    for business_key, payload in sorted(output.objects.items()):
        if _type_of(entries, business_key) != "program":
            continue
        registered = str(payload.get("programType") or "") == "WITH_REGISTRATION"
        if registered != isinstance(payload.get("trackedEntity"), dict):
            issues.append(
                _issue(
                    RULE_ARCHITECTURE,
                    f"program {business_key!r} is a "
                    f"{'Tracker' if registered else 'Event'} program but its "
                    f"{'trackedEntity is missing' if registered else 'trackedEntity is set'}",
                    business_key=business_key,
                    dhis2_type="program",
                )
            )
    return issues


def _identity(
    inputs: CompileInputs, entries: dict[str, dict[str, Any]], output: CompileOutput
) -> list[Issue]:
    """Every payload object maps back to a Registry entry, and vice versa (BP-VAL-020 身份)."""
    issues: list[Issue] = []
    compiled_keys = set(output.objects)
    expected = {
        str(entry.get("businessKey"))
        for entry in entries.values()
        if str(entry.get("disposition")) in (Disposition.CREATE.value, Disposition.UPDATE.value)
    }
    if not inputs.is_environment_bound:
        # A baseline compile has no observed content to merge an UPDATE over, so the
        # compiler defers those entries to the environment package (BP-CMP-010).
        expected -= {
            key
            for key, entry in entries.items()
            if str(entry.get("disposition")) == Disposition.UPDATE.value
        }
    for key in sorted(expected - compiled_keys):
        issues.append(
            _issue(
                RULE_IDENTITY,
                f"Registry entry {key!r} is {entries[key].get('disposition')} but produced "
                "no payload object",
                business_key=key,
                dhis2_type=str(entries[key].get("dhis2Type") or ""),
            )
        )
    for key in sorted(compiled_keys - expected):
        issues.append(
            _issue(
                RULE_IDENTITY,
                f"payload contains {key!r}, which is not a CREATE or UPDATE entry",
                business_key=key,
            )
        )
    return issues


def _schema(
    inputs: CompileInputs, output: CompileOutput, entries: dict[str, dict[str, Any]]
) -> list[Issue]:
    """Required, forbidden and reference fields, per descriptor and Profile schema."""
    issues: list[Issue] = []
    for business_key, object_payload in sorted(output.objects.items()):
        dhis2_type = _type_of(entries, business_key)
        descriptor = descriptors.descriptor(dhis2_type)
        if descriptor is None:
            issues.append(
                _issue(
                    RULE_UNREGISTERED,
                    f"{business_key!r} has object type {dhis2_type!r} with no enabled descriptor",
                    business_key=business_key,
                    dhis2_type=dhis2_type,
                )
            )
            continue
        profile_schema = (
            inputs.profile.object_schema(dhis2_type) if inputs.profile is not None else None
        )
        required = set(descriptor.required_fields) | set(
            profile_schema.required if profile_schema else []
        )
        for field_name in sorted(required):
            if field_name not in object_payload or object_payload[field_name] in (None, ""):
                issues.append(
                    _issue(
                        RULE_SCHEMA,
                        f"{dhis2_type} {business_key!r} is missing required field {field_name!r}",
                        business_key=business_key,
                        dhis2_type=dhis2_type,
                        location=field_name,
                    )
                )
        forbidden = set(descriptor.forbidden_fields)
        # `id` is the one field the payload has to carry.  S2 allocated this UID from the pool
        # (BP-REG-008) and S4 imports with `identifier=ASSIGNED`, whose instance model answers a
        # row without an identifier as E1096 — a descriptor that groups `id` with
        # `href`/`created`/`lastUpdated` is right about those and wrong about the UID this
        # Factory promised.  The blanket exclusion is replaced by the exact check below, so a
        # payload `id` that is not the allocated UID is still an FR-CMP-006 violation.
        forbidden.discard("id")
        for field_name in sorted(forbidden & set(object_payload)):
            issues.append(
                _issue(
                    RULE_SCHEMA,
                    f"{dhis2_type} {business_key!r} carries the system field {field_name!r} "
                    "(FR-CMP-006)",
                    business_key=business_key,
                    dhis2_type=dhis2_type,
                    location=field_name,
                )
            )
        allocated = str((entries.get(business_key) or {}).get("uid") or "")
        shipped = str(object_payload.get("id") or "")
        if shipped and shipped != allocated:
            issues.append(
                _issue(
                    RULE_SCHEMA,
                    f"{dhis2_type} {business_key!r} ships id {shipped!r}, which is not the UID "
                    f"the Registry allocated ({allocated or 'none'}), so the environment would "
                    "be written under an identity nothing here claims",
                    business_key=business_key,
                    dhis2_type=dhis2_type,
                    location="id",
                )
            )
    return issues


def _capability(
    inputs: CompileInputs, output: CompileOutput, entries: dict[str, dict[str, Any]]
) -> list[Issue]:
    """The target version's own answer about the bytes that will ship (BP-CMP-030).

    The compiler asks the Profile these questions while it builds; asking them again of
    the product is what makes ``validate_build`` of a stored package mean the same thing
    a year later, when a different Profile may be the one the record names.
    """
    profile = view_of(inputs.profile)
    if profile is None:
        # a baseline compile has no target version to be wrong about (D-01)
        return []
    issues = list(profile.constraint_issues())
    for business_key, payload_object in sorted(output.objects.items()):
        dhis2_type = _type_of(entries, business_key)
        descriptor = descriptors.descriptor(dhis2_type)
        if descriptor is None:
            continue
        issues.extend(profile.type_issues(descriptor))
        for field_name in sorted(
            key
            for key in payload_object
            if key not in ADDRESSING_FIELDS
        ):
            verdict = profile.field(dhis2_type, field_name)
            if verdict.verdict == SUPPORTED:
                continue
            if verdict.verdict == DEPRECATED:
                issues.append(
                    make_issue(
                        RULE_FIELD_DEPRECATED,
                        IssueLevel.WARNING,
                        f"{dhis2_type} {business_key!r} sends {field_name!r}, which "
                        f"{verdict.detail}; the instance still accepts it today",
                        business_key=business_key,
                        dhis2_type=dhis2_type,
                        location=field_name,
                        section=Section.MAPPING,
                        remediation="move the value to the replacement property this "
                        "Profile offers",
                    )
                )
                continue
            rule = RULE_FIELD_REMOVED if verdict.verdict == REMOVED else RULE_FIELD_FORBIDDEN
            issues.append(
                make_issue(
                    rule,
                    IssueLevel.BLOCKING,
                    f"{dhis2_type} {business_key!r} carries {field_name!r}, which "
                    f"{verdict.detail}",
                    business_key=business_key,
                    dhis2_type=dhis2_type,
                    location=field_name,
                    section=Section.MAPPING,
                    remediation="compile against a Profile whose version has this property, "
                    "or drop it from the mapping",
                )
            )
    return issues


def _type_of(entries: dict[str, dict[str, Any]], business_key: str) -> str:
    entry = entries.get(business_key) or {}
    return str(entry.get("dhis2Type") or "")


def _scope(
    inputs: CompileInputs, output: CompileOutput, entries: dict[str, dict[str, Any]]
) -> list[Issue]:
    """UPDATE must only change controlled fields; nothing outside the scope (BP-VAL-020 范围)."""
    issues: list[Issue] = []
    for business_key, payload in sorted(output.objects.items()):
        entry = entries.get(business_key) or {}
        if str(entry.get("disposition")) != Disposition.UPDATE.value:
            continue
        controlled = {str(name) for name in entry.get("controlledFields") or []}
        base = inputs.observed.get(str(entry.get("uid") or "")) or {}
        for field_name in sorted(set(base) & set(payload)):
            if field_name in controlled or field_name in ("id", "code"):
                continue
            if base[field_name] != payload[field_name]:
                issues.append(
                    _issue(
                        RULE_SCOPE,
                        f"UPDATE of {business_key!r} changes {field_name!r}, which is not a "
                        "controlled field (FR-REG-001, FR-CMP-007)",
                        business_key=business_key,
                        dhis2_type=str(entry.get("dhis2Type") or ""),
                        location=field_name,
                    )
                )
    issues.extend(_objects_outside_managed_scope(inputs, output))
    return issues


def _objects_outside_managed_scope(
    inputs: CompileInputs, output: CompileOutput
) -> list[Issue]:
    """13.3 范围: the payload must not touch an object this project does not manage.

    Checked only when the compile was given this environment's Managed Scope, which is
    what an environment-bound build is: a baseline compile has no environment to be
    scoped against, and a caller that supplies no scope has asserted nothing to violate.
    """
    if not inputs.is_environment_bound or not inputs.managed_scope:
        return []
    managed = {
        str(row.get("businessKey") or row.get("business_key") or "")
        for row in output.managed_scope
    }
    issues: list[Issue] = []
    for business_key, payload in sorted(output.objects.items()):
        if business_key in managed:
            continue
        entry = payload if isinstance(payload, dict) else {}
        issues.append(
            _issue(
                RULE_SCOPE,
                f"the payload writes {business_key!r}, which is not in this environment's "
                "Managed Scope (FR-COV: a project may only create, update or deprecate the "
                "objects its Registry declares for this environment)",
                business_key=business_key,
                uid=str(entry.get("id") or "") or None,
            )
        )
    return issues


def _languages(
    inputs: CompileInputs, output: CompileOutput, entries: dict[str, dict[str, Any]]
) -> list[Issue]:
    """Missing translations are a WARNING, never a blocker (FR-COV-010)."""
    wanted = inputs.translation_languages
    if not wanted:
        return []
    issues: list[Issue] = []
    for business_key, payload in sorted(output.objects.items()):
        dhis2_type = _type_of(entries, business_key)
        descriptor = descriptors.descriptor(dhis2_type)
        if descriptor is None:
            continue
        present = {
            (str(item.get("field")), str(item.get("locale")))
            for item in payload.get("translations") or []
            if isinstance(item, dict)
        }
        for locale in wanted:
            if locale == inputs.default_language:
                continue
            untranslated = [
                field_name
                for field_name in descriptor.translatable_fields
                if field_name in payload and (field_name, locale) not in present
            ]
            if not untranslated:
                continue
            issues.append(
                make_issue(
                    RULE_LANGUAGE,
                    IssueLevel.WARNING,
                    f"{business_key!r} has no {locale!r} translation for "
                    f"{', '.join(sorted(untranslated))}",
                        business_key=business_key,
                        dhis2_type=dhis2_type,
                        section=Section.REGISTRY,
                        remediation="add the translation in the mapping fields and recompile",
                    )
                )
    return issues


def _expressions(inputs: CompileInputs, output: CompileOutput) -> list[Issue]:
    """Local syntax and reference validation of every compiled expression (D-05)."""
    grammar = (
        inputs.profile.expression_grammar_version if inputs.profile is not None else None
    )
    issues: list[Issue] = []
    for item in output.expressions:
        text = str(item.get("compiled") or "")
        for error in expressions.check(text, language=_language_of(str(item.get("field") or "")), grammar_version=grammar):
            issues.append(
                _issue(
                    RULE_EXPRESSION,
                    f"{item['businessKey']}.{item['field']}: {error}",
                    business_key=str(item.get("businessKey")),
                    location=str(item.get("field")),
                )
            )
        if inputs.is_environment_bound and item.get("unresolved"):
            issues.append(
                _issue(
                    RULE_REFERENCE,
                    f"{item['businessKey']}.{item['field']} references unresolvable "
                    f"{', '.join(item['unresolved'])}",
                    business_key=str(item.get("businessKey")),
                    location=str(item.get("field")),
                )
            )
    return issues


def _language_of(field: str) -> str:
    if field in ("leftExpression", "rightExpression"):
        return expressions.LANGUAGE_VALIDATION_RULE
    if field == "expression":
        return expressions.LANGUAGE_PROGRAM_INDICATOR_OR_RULE
    return expressions.LANGUAGE_PROGRAM_RULE_CONDITION


def _check_environment(inputs: CompileInputs, output: CompileOutput) -> list[Issue]:
    """An environment-bound product may not contain a single unresolved placeholder."""
    issues: list[Issue] = []
    if not inputs.is_environment_bound:
        return issues
    for business_key, payload in sorted(output.objects.items()):
        for path, value in _strings(payload):
            key = refs.key_of_placeholder(value)
            if key is None and refs.is_placeholder(value):
                key = None
            if key is not None:
                issues.append(
                    _issue(
                        RULE_PLACEHOLDER,
                        f"{business_key!r} still holds the placeholder for {key!r} at {path}; "
                        "the environment binding was not resolved (BP-REG-010)",
                        business_key=business_key,
                        location=path,
                    )
                )
    issues.extend(_observation(inputs, output))
    return issues


def _observation(inputs: CompileInputs, output: CompileOutput) -> list[Issue]:
    """BP-VAL-022: REUSE and REFERENCE targets must exist in the latest discovery."""
    issues: list[Issue] = []
    if inputs.discovery is None:
        return issues
    # The four `default` objects are named in the discovery result's `defaults` map rather than
    # among the objects it read, because an environment-bound compile asks for an explicit list
    # and DX-DSC-002 keeps S4 to that list.  A REFERENCE to `default.categoryCombo` is therefore
    # proved by `defaults`, and treating it as absent would block a compile that already knows
    # the UID it falls back to (DX-DSC-003, FR-COV-007).
    seen = {item.uid for item in inputs.discovery.objects} | set(inputs.discovery.defaults.values())
    for reference in output.unmanaged_references:
        uid = reference.get("uid")
        if isinstance(uid, str) and uid and uid not in seen and not refs.is_placeholder(uid):
            issues.append(
                _issue(
                    RULE_REFERENCE,
                    f"{reference.get('disposition')} target {reference.get('businessKey')!r} "
                    f"(UID {uid}) is absent from this environment's discovery result",
                    business_key=str(reference.get("businessKey") or ""),
                    uid=uid,
                    dhis2_type=str(reference.get("dhis2Type") or ""),
                )
            )
    return issues


def _strings(node: Any, prefix: str = "") -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            found.extend(_strings(value, f"{prefix}/{key}"))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            found.extend(_strings(item, f"{prefix}/{index}"))
    elif isinstance(node, str):
        found.append((prefix, node))
    return found


def _issue(
    rule_id: str,
    message: str,
    *,
    business_key: str | None = None,
    uid: str | None = None,
    dhis2_type: str | None = None,
    location: str | None = None,
) -> Issue:
    return make_issue(
        rule_id,
        IssueLevel.BLOCKING,
        message,
        business_key=business_key,
        uid=uid,
        dhis2_type=dhis2_type,
        location=location,
        section=Section.REGISTRY,
    )


IMPACT_HIGH = ImpactLevel.HIGH.value
