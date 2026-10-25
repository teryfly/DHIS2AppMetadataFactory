"""Fixture Compiler (BP-CMP-043, overall SRS 20.4 / FR-POL-006…008 / FR-DOM-001).

Input is the `fixtures` section plus the Registry; output is the synthetic Tracker
payload, the demo-user specification and its Markdown description.

Three constraints shape the whole module:

* **Synthetic only, and obviously so.** No value comes from a real person and no value is
  real-shaped: sensitive attributes (FR-DOM-001) carry a fixed `SYNTH-` prefix so nobody
  can mistake a demo record for clinical data, and the payload says so in its header.
  Real data never enters this path at all (FR-POL-007).
* **Deterministic from the build context's seed.** FR-CMP-001 forbids a compiler from
  reading a random source, so the values come from a hash of
  (seed, spec, attribute, position).  That is *more* reproducible than a PRNG stream:
  reordering the specs cannot change a value, and `reproduce()` sees the same bytes.
* **Nothing is invented when the Blueprint is silent.** An unknown value strategy is a
  BLOCKING problem rather than a default, because fixture data that quietly stopped
  following its specification is the kind of defect that only shows up as a wrong demo.

Demo users are a *specification* — username, roles, groups, org scope — and never a
credential: DHIS2 accounts are created by S4 at import time with generated passwords
held in a Secret Manager (FR-COV-008, FR-SEC-008).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypeVar, cast

from factory_foundation.api import structure_hash

from factory_blueprint.compilers import files
from factory_blueprint.compilers.context import CompileInputs
from factory_blueprint.compilers.refs import placeholder
from factory_blueprint.content.document import BlueprintDocument
from factory_blueprint.content.layout import CONTAINER_BY_PATH
from factory_blueprint.dto.issue import Issue, make_issue, sort_issues
from factory_blueprint.enums import IssueLevel, Section

RULE_STRATEGY_UNKNOWN = "BPV-FIX-001"
RULE_TARGET_UNKNOWN = "BPV-FIX-002"
RULE_ORG_UNIT_UNBOUND = "BPV-FIX-003"
RULE_NO_TRACKER_IMPORT = "BPV-FIX-004"
RULE_ATTRIBUTE_UNKNOWN = "BPV-FIX-005"
RULE_SENSITIVE_LITERAL = "BPV-FIX-006"

FIXTURE_DATA_FILE = "fixtures/demo-data.json"
DEMO_USERS_FILE = "fixtures/demo-users.json"
DEMO_USERS_DOC_FILE = "fixtures/demo-users.md"

_T = TypeVar("_T")

#: FR-DOM-001: a synthetic value has to look synthetic at a glance, in the data itself
SENSITIVE_PREFIX = "SYNTH-"

#: a Tracker record's temporary id, shaped like a UID but explicitly marked as a placeholder
TEMP_ID_MARK = "SYNTH"
UID_LENGTH = 11

#: value strategies a fixture specification may name
STRATEGY_SEQUENTIAL = "sequential"
STRATEGY_CONSTANT = "constant"
STRATEGY_RANGE = "range"
STRATEGY_OPTION_WEIGHTS = "optionWeights"
STRATEGY_PREFIX = "prefix"
KNOWN_STRATEGIES = frozenset(
    {STRATEGY_SEQUENTIAL, STRATEGY_CONSTANT, STRATEGY_RANGE, STRATEGY_OPTION_WEIGHTS, STRATEGY_PREFIX}
)

#: sensitivity values that make an attribute's data personal (overall SRS 20.4)
SENSITIVE_MARKS = frozenset({"PHI_PII", "PII", "PHI"})

FIXTURES_CONTAINER = CONTAINER_BY_PATH["/fixtures/specifications"]
DEMO_USERS_CONTAINER = CONTAINER_BY_PATH["/fixtures/demoUsers"]
REGISTRY_CONTAINER = CONTAINER_BY_PATH["/registry/entries"]



@dataclass
class FixtureProduct:
    """The compiled synthetic data, the demo-user spec and the problems found."""

    payload: dict[str, Any] = field(default_factory=dict)
    demo_users: list[dict[str, Any]] = field(default_factory=list)
    files: dict[str, bytes] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)
    entity_count: int = 0

    @property
    def blocking(self) -> bool:
        return any(issue.level is IssueLevel.BLOCKING for issue in self.issues)

    @property
    def requested(self) -> bool:
        """Whether the project asked for fixtures at all (BP-CMP-021 says "if any")."""
        return bool(self.payload) or bool(self.demo_users)


def compile_fixtures(
    inputs: CompileInputs, objects: dict[str, dict[str, Any]]
) -> FixtureProduct:
    """Generate the synthetic dataset one approved version specifies."""
    document = BlueprintDocument(inputs.document)
    specs = sorted(
        document.container_entries(FIXTURES_CONTAINER),
        key=lambda item: str(item.get("businessKey") or ""),
    )
    demo_specs = sorted(
        document.container_entries(DEMO_USERS_CONTAINER),
        key=lambda item: str(item.get("username") or ""),
    )
    if not specs and not demo_specs:
        return FixtureProduct()

    registry = _registry_map(document)
    issues: list[Issue] = []
    entities: list[dict[str, Any]] = []
    enrollments: list[dict[str, Any]] = []

    for spec in specs:
        batch, batch_issues = _generate(inputs, spec, registry, objects)
        issues.extend(batch_issues)
        entities.extend(batch["trackedEntities"])
        enrollments.extend(batch["enrollments"])

    issues.extend(_import_route_check(inputs, specs))
    demo_users, user_issues = _demo_users(inputs, demo_specs, registry)
    issues.extend(user_issues)

    payload: dict[str, Any] = {
        "schemaVersion": 1,
        "kind": "FIXTURE_PACKAGE",
        "projectId": inputs.project_id,
        "blueprintVersion": inputs.blueprint_version,
        "blueprintHash": inputs.blueprint_hash,
        "dhis2Version": inputs.dhis2_version or None,
        "environmentKey": inputs.environment_key,
        "synthetic": True,
        "note": (
            "Every record here is generated from the Blueprint's fixture specifications "
            "and the build context's seed. Sensitive attribute values carry the "
            f"`{SENSITIVE_PREFIX}` prefix (FR-DOM-001); no real person's data is present "
            "and none can be reproduced from a different seed."
        ),
        "data": {"trackedEntities": entities, "enrollments": enrollments},
    }
    product = FixtureProduct(
        payload=payload,
        demo_users=demo_users,
        issues=sort_issues(issues),
        entity_count=len(entities),
    )
    product.files = _fixture_files(inputs, product)
    return product


# --- generation -----------------------------------------------------------


def _generate(
    inputs: CompileInputs,
    spec: dict[str, Any],
    registry: dict[str, dict[str, Any]],
    objects: dict[str, dict[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], list[Issue]]:
    issues: list[Issue] = []
    spec_key = str(spec.get("businessKey") or "")
    target_key = str(spec.get("targetKey") or "")
    target = registry.get(target_key)
    if target is None:
        issues.append(
            make_issue(
                RULE_TARGET_UNKNOWN,
                IssueLevel.BLOCKING,
                f"fixture specification {spec_key!r} targets {target_key!r}, which is not a "
                "Registry entry of this version, so the generated records would name an "
                "object that does not exist",
                business_key=spec_key,
                section=Section.FIXTURES,
                remediation="point the specification at a registered tracked entity type or program",
            )
        )
        return {"trackedEntities": [], "enrollments": []}, issues

    org_units, org_issues = _org_units(inputs, spec, spec_key)
    issues.extend(org_issues)
    entity_type, type_issues = _entity_type(inputs, spec_key, target, target_key, objects)
    issues.extend(type_issues)
    if entity_type is None:
        return {"trackedEntities": [], "enrollments": []}, issues
    count = int(spec.get("count") or 0)
    attributes, attribute_issues = _attribute_plan(inputs, spec, registry, objects, spec_key)
    issues.extend(attribute_issues)

    entities: list[dict[str, Any]] = []
    enrollments: list[dict[str, Any]] = []
    for position in range(count):
        org_unit = org_units[position % len(org_units)] if org_units else None
        entity = _tracked_entity(
            inputs, spec, spec_key, target, target_key, org_unit, attributes, entity_type, position
        )
        entity["trackedEntityInstance"] = _instance_id(inputs, spec_key, position)
        entities.append(entity)
        enrollment = _enrollment(inputs, spec_key, target, target_key, entity, org_unit, position)
        if enrollment is not None:
            enrollments.append(enrollment)

    issues.extend(_boundary_notes(spec, spec_key, count))
    return (
        {"trackedEntities": entities, "enrollments": enrollments},
        issues,
    )


def _tracked_entity(
    inputs: CompileInputs,
    spec: dict[str, Any],
    spec_key: str,
    target: dict[str, Any],
    target_key: str,
    org_unit: str | None,
    attributes: list[dict[str, Any]],
    entity_type: str | None,
    position: int,
) -> dict[str, Any]:
    values: list[dict[str, Any]] = []
    for plan in attributes:
        values.append(
            {
                "attribute": {"id": plan["uid"]},
                "value": plan["value"](position),
                "x-attribute-key": plan["businessKey"],
                "x-strategy": plan["strategy"],
            }
        )
    entity: dict[str, Any] = {
        "orgUnit": org_unit,
        # a program target enrolls in the Tracker under *its* entity type, so the type
        # comes from _entity_type_of and never from the target's own number
        "trackedEntityType": {"id": entity_type},
        "attributes": values,
        "x-fixture-spec": spec_key,
        "x-record": position + 1,
        "x-synthetic": True,
    }
    periods = spec.get("periods")
    if isinstance(periods, dict) and periods:
        entity["x-period"] = _period_of(inputs, periods, position)
    return _pruned(entity)


def _entity_type(
    inputs: CompileInputs,
    spec_key: str,
    target: dict[str, Any],
    target_key: str,
    objects: dict[str, dict[str, Any]],
) -> tuple[str | None, list[Issue]]:
    """The TrackedEntityType the records belong to, resolved rather than assumed.

    A tracked-entity-type target is its own type. A program target is *not*: the Tracker
    payload needs the type the program was configured with, and if the compiled program
    does not say so, inventing one would import records under the wrong entity.
    """
    dhis2_type = str(target.get("dhis2Type") or "")
    if dhis2_type == "trackedEntityType":
        return _ref(target, target_key), []
    if dhis2_type != "program":
        return None, [
            make_issue(
                RULE_TARGET_UNKNOWN,
                IssueLevel.BLOCKING,
                f"fixture specification {spec_key!r} targets {target_key!r}, a "
                f"{dhis2_type or 'unknown type'}; a Tracker record belongs to a tracked "
                "entity type or to a program that declares one",
                business_key=target_key,
                section=Section.FIXTURES,
            )
        ]
    program = objects.get(target_key) or {}
    declared = program.get("trackedEntityType")
    uid = ""
    if isinstance(declared, dict):
        uid = str(declared.get("id") or "")
    elif isinstance(declared, str):
        uid = inputs.bindings.get(declared) or declared
    if not uid:
        return None, [
            make_issue(
                RULE_TARGET_UNKNOWN,
                IssueLevel.BLOCKING,
                f"fixture specification {spec_key!r} enrolls in program {target_key!r}, which "
                "does not declare a TrackedEntityType, so the generated records have no "
                "entity type to be created under",
                business_key=target_key,
                section=Section.FIXTURES,
                remediation="give the program its trackedEntityType in the Blueprint, or "
                "point the specification at that tracked entity type",
            )
        ]
    return uid, []


def _enrollment(
    inputs: CompileInputs,
    spec_key: str,
    target: dict[str, Any],
    target_key: str,
    entity: dict[str, Any],
    org_unit: str | None,
    position: int,
) -> dict[str, Any] | None:
    """Only a tracker program enrolls; a tracked entity type on its own does not."""
    if str(target.get("dhis2Type") or "") != "program":
        return None
    return _pruned(
        {
            "org": org_unit,
            "program": {"id": _ref(target, target_key)},
            "trackedEntity": entity.get("trackedEntityInstance"),
            "enrolledAt": _date_at(inputs, position),
            "status": "ACTIVE",
            "x-fixture-spec": spec_key,
        }
    )


def _attribute_plan(
    inputs: CompileInputs,
    spec: dict[str, Any],
    registry: dict[str, dict[str, Any]],
    objects: dict[str, dict[str, Any]],
    spec_key: str,
) -> tuple[list[dict[str, Any]], list[Issue]]:
    issues: list[Issue] = []
    plan: list[dict[str, Any]] = []
    for item in spec.get("attributeValues") or []:
        if not isinstance(item, dict):
            continue
        attribute_key = str(item.get("attributeKey") or "")
        strategy = str(item.get("strategy") or "")
        entry = registry.get(attribute_key)
        if entry is None:
            issues.append(
                make_issue(
                    RULE_ATTRIBUTE_UNKNOWN,
                    IssueLevel.BLOCKING,
                    f"fixture specification {spec_key!r} fills attribute {attribute_key!r}, "
                    "which this version does not register",
                    business_key=attribute_key,
                    section=Section.FIXTURES,
                )
            )
            continue
        if strategy not in KNOWN_STRATEGIES:
            issues.append(
                make_issue(
                    RULE_STRATEGY_UNKNOWN,
                    IssueLevel.BLOCKING,
                    f"strategy {strategy!r} on attribute {attribute_key!r} is not one S2 can "
                    f"generate; known strategies: {', '.join(sorted(KNOWN_STRATEGIES))}",
                    business_key=attribute_key,
                    section=Section.FIXTURES,
                    remediation="name a supported strategy or leave the attribute out",
                )
            )
            continue
        compiled = objects.get(attribute_key) or {}
        sensitive = _is_sensitive(item, entry, objects, attribute_key)
        plan.append(
            {
                "businessKey": attribute_key,
                "uid": _ref(entry, attribute_key),
                "strategy": strategy,
                "value": _value_of(inputs, spec_key, item, compiled, strategy, sensitive),
            }
        )
    return plan, issues


def _value_of(
    inputs: CompileInputs,
    spec_key: str,
    item: dict[str, Any],
    compiled: dict[str, Any],
    strategy: str,
    sensitive: bool,
) -> Any:
    """A closure so the position is decided when the record is built, not in a loop here."""

    def produce(position: int) -> Any:
        raw = _raw_value(inputs, spec_key, item, compiled, strategy, position)
        # FR-DOM-001: a sensitive value is marked in the data itself, whatever its type,
        # so a demo record cannot be mistaken for a real one by anyone reading the JSON
        if sensitive:
            text = raw if isinstance(raw, str) else str(raw)
            return text if text.startswith(SENSITIVE_PREFIX) else f"{SENSITIVE_PREFIX}{text}"
        return raw

    return produce


def _raw_value(
    inputs: CompileInputs,
    spec_key: str,
    item: dict[str, Any],
    compiled: dict[str, Any],
    strategy: str,
    position: int,
) -> Any:
    if strategy == STRATEGY_CONSTANT:
        # the specification's only literal field is `prefix`; a constant with no value
        # stated is a constant empty string rather than an invented one
        return str(item.get("prefix") or "")
    if strategy == STRATEGY_PREFIX:
        prefix = str(item.get("prefix") or spec_key)
        return f"{prefix}{position + 1}"
    if strategy == STRATEGY_SEQUENTIAL:
        return position + 1
    if strategy == STRATEGY_RANGE:
        bounds = item.get("range")
        if not isinstance(bounds, list) or len(bounds) < 2:
            return position + 1
        low, high = _number(bounds[0]), _number(bounds[1])
        if high <= low:
            return _round_value(low, compiled, bounds)
        # one draw picks both the whole part and the fraction, and the result is clamped:
        # a value outside the declared range is a fixture that contradicts its own spec
        step = _draw(inputs, spec_key, str(item.get("attributeKey")), position)
        offset = (step % 10_000) / 10_000 * (high - low)
        return _round_value(min(low + offset, high), compiled, bounds)
    options = _weighted_options(item, compiled)
    if not options:
        return position + 1
    step = _draw(inputs, spec_key, str(item.get("attributeKey")), position)
    return options[step % len(options)]


def _weighted_options(item: dict[str, Any], compiled: dict[str, Any]) -> list[str]:
    weights = item.get("optionWeights")
    if isinstance(weights, dict) and weights:
        pool: list[str] = []
        for code in sorted(weights):
            pool.extend([str(code)] * max(int(_number(weights[code])), 1))
        return pool
    codes = [
        str(option.get("code") or option.get("name"))
        for option in (compiled.get("options") or [])
        if isinstance(option, dict)
    ]
    return sorted({code for code in codes if code})


def _boundary_notes(spec: dict[str, Any], spec_key: str, count: int) -> list[Issue]:
    """17.3's honest limit: a boundary case named in prose cannot be generated as data."""
    cases = [str(item) for item in (spec.get("boundaryCases") or [])]
    if not cases:
        return []
    return [
        make_issue(
            RULE_SENSITIVE_LITERAL,
            IssueLevel.INFO,
            f"fixture specification {spec_key!r} names {len(cases)} boundary case(s) that S2 "
            f"records but does not generate: {', '.join(sorted(cases))}",
            business_key=spec_key,
            section=Section.FIXTURES,
            remediation="the reviewer confirms the generated set covers them, or the case "
            "is expressed as a strategy and a range",
            details={"count": count},
        )
    ]


def _org_units(
    inputs: CompileInputs, spec: dict[str, Any], spec_key: str
) -> tuple[list[str], list[Issue]]:
    """The organisation units records go into, taken from *this* environment's bindings.

    A REFERENCE org unit's number is environment-specific (D-02, FR-ENV-005): the UID
    observed in the design environment is not a fact about this one.  So an entry with
    no binding here is a BLOCKING problem, not a value to fall back to — records placed
    under an id that does not exist in the target would fail on import at best and land
    in someone else's facility at worst.
    """
    keys = [str(item) for item in (spec.get("orgUnitKeys") or [])]
    if not keys:
        return [], []
    resolved: list[str] = []
    issues: list[Issue] = []
    for key in sorted(keys):
        uid = inputs.bindings.get(key)
        if not uid:
            issues.append(
                make_issue(
                    RULE_ORG_UNIT_UNBOUND,
                    IssueLevel.BLOCKING,
                    f"fixture specification {spec_key!r} places records in {key!r}, which has no "
                    f"binding in environment {inputs.environment_key or '?'}, so its UID in this "
                    "environment is not known",
                    business_key=key,
                    section=Section.FIXTURES,
                    remediation="run the environment binding compile, or bind this organisation unit by hand",
                )
            )
            continue
        resolved.append(uid)
    return resolved, issues


def _import_route_check(inputs: CompileInputs, specs: list[dict[str, Any]]) -> list[Issue]:
    """The payload is Tracker data, so the target version must actually offer the route."""
    if not specs or inputs.profile is None:
        return []
    if any(inputs.profile.resource(name) for name in _TRACKER_IMPORT_RESOURCES):
        return []
    return [
        make_issue(
            RULE_NO_TRACKER_IMPORT,
            IssueLevel.BLOCKING,
            f"version {inputs.dhis2_version} offers no Tracker import resource, so the "
            f"{len(specs)} fixture specification(s) cannot be delivered as Tracker data",
            section=Section.FIXTURES,
            remediation="point the project at a version with the Tracker API, or drop the fixtures",
        )
    ]


_TRACKER_IMPORT_RESOURCES = (
    "tracker.trackedEntities",
    "tracker.enrollments",
    "tracker.events",
    "tracker.import",
)


# --- demo users -----------------------------------------------------------


def _demo_users(
    inputs: CompileInputs,
    specs: list[dict[str, Any]],
    registry: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[Issue]]:
    """A specification only: username, roles, groups, scope. Never a password (FR-COV-008)."""
    out: list[dict[str, Any]] = []
    for spec in specs:
        username = str(spec.get("username") or "")
        out.append(
            _pruned(
                {
                    "businessKey": str(spec.get("businessKey") or ""),
                    "username": username,
                    "displayName": str(spec.get("displayName") or ""),
                    "roles": [
                        {"businessKey": key, "uid": _lookup(inputs, registry, key)}
                        for key in sorted({str(item) for item in (spec.get("roleKeys") or [])})
                    ],
                    "userGroups": [
                        {"businessKey": key, "uid": _lookup(inputs, registry, key)}
                        for key in sorted({str(item) for item in (spec.get("userGroupKeys") or [])})
                    ],
                    "orgUnitScope": [
                        {"businessKey": key, "uid": _lookup(inputs, registry, key)}
                        for key in sorted({str(item) for item in (spec.get("orgUnitScope") or [])})
                    ],
                    "credential": "generated by S4 at import and stored in a Secret Manager",
                }
            )
        )
    return out, []


# --- emission -------------------------------------------------------------


def _fixture_files(inputs: CompileInputs, product: FixtureProduct) -> dict[str, bytes]:
    files_out: dict[str, bytes] = {FIXTURE_DATA_FILE: files.json_bytes(product.payload)}
    if product.demo_users:
        files_out[DEMO_USERS_FILE] = files.json_bytes(
            {
                "schemaVersion": 1,
                "kind": "DEMO_USER_SPECIFICATION",
                "blueprintVersion": inputs.blueprint_version,
                "users": product.demo_users,
                "note": (
                    "No credential appears here by design (FR-SEC-008). S4 creates the "
                    "accounts during import and places each password in a Secret Manager."
                ),
            }
        )
        files_out[DEMO_USERS_DOC_FILE] = _demo_users_markdown(inputs, product.demo_users)
    return files_out


def _demo_users_markdown(inputs: CompileInputs, users: list[dict[str, Any]]) -> bytes:
    lines = [
        "| username | display name | roles | user groups | organisation scope |",
        "| --- | --- | --- | --- | --- |",
    ]
    for user in users:
        lines.append(
            f"| `{user.get('username')}` | {user.get('displayName') or ''} "
            f"| {_join_refs(user.get('roles'))} | {_join_refs(user.get('userGroups'))} "
            f"| {_join_refs(user.get('orgUnitScope'))} |"
        )
    lines.extend(
        [
            "",
            "These accounts are a **specification**, not a set of credentials. S4 creates",
            "them when the fixtures are imported and stores each generated password in a",
            "Secret Manager, so no password is ever written into an artifact, a prompt or",
            "an audit record (FR-COV-008, FR-SEC-008).",
        ]
    )
    return files.md_lines([("Demo users", lines)])


def _join_refs(items: Any) -> str:
    if not items:
        return ""
    return ", ".join(f"`{item['businessKey']}`" for item in items if isinstance(item, dict))


# --- deterministic value helpers -----------------------------------------


def _draw(inputs: CompileInputs, *parts: object) -> int:
    """A stable non-negative integer from the seed and the value's address."""
    digest = structure_hash({"seed": inputs.build_context.seed, "parts": [str(part) for part in parts]})
    return int(digest.split(":", 1)[1][:12], 16)


def _round_value(value: float, compiled: dict[str, Any], bounds: list[Any]) -> Any:
    if _is_integer_type(compiled, bounds):
        return int(round(value))
    return round(value, 3)


def _is_integer_type(compiled: dict[str, Any], bounds: list[Any]) -> bool:
    declared = str(compiled.get("valueType") or "")
    if declared.startswith("INTEGER") or declared in ("AGE", "NUMBER"):
        return declared.startswith("INTEGER") or declared == "AGE"
    return all(isinstance(item, int) for item in bounds)


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _is_sensitive(
    item: dict[str, Any],
    entry: dict[str, Any],
    objects: dict[str, dict[str, Any]],
    attribute_key: str,
) -> bool:
    marks = {str(item.get("sensitivity") or ""), str(entry.get("sensitivity") or "")}
    compiled = objects.get(attribute_key) or {}
    marks.add(str(compiled.get("sensitivity") or ""))
    return bool(marks & SENSITIVE_MARKS)


def _period_of(inputs: CompileInputs, periods: dict[str, Any], position: int) -> Any:
    """A period label from the specification's own list, cycling deterministically."""
    choices = periods.get("values") or periods.get("periods") or []
    if isinstance(choices, list) and choices:
        return str(choices[position % len(choices)])
    return None


def _date_at(inputs: CompileInputs, position: int) -> str:
    """A synthetic enrollment date derived from the build context, never from a clock."""
    from datetime import timedelta

    base = inputs.build_context.built_at
    day = (base - timedelta(days=(position % 365) + 1)).date()
    return day.isoformat()


def _instance_id(inputs: CompileInputs, spec_key: str, position: int) -> str:
    """A deterministic placeholder id for the new entity, so its enrollment can point at it.

    This is *not* a Registry UID and does not go through the allocation ledger (P-04):
    the Tracker assigns the real number when the import runs.  It is 11 characters and
    starts with the synthetic marker so nobody mistakes it for an allocated object id.
    """
    draw = _draw(inputs, "instance", spec_key, position)
    return f"{TEMP_ID_MARK}{draw % 100_000_000:08d}"[:UID_LENGTH]


def _ref(entry: dict[str, Any], business_key: str) -> str:
    return _uid_of(entry) or placeholder(business_key)


def _uid_of(entry: dict[str, Any]) -> str:
    for key in ("uid", "observedUid"):
        value = entry.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _lookup(inputs: CompileInputs, registry: dict[str, dict[str, Any]], business_key: str) -> str:
    return inputs.bindings.get(business_key) or _uid_of(registry.get(business_key) or {})


def _registry_map(document: BlueprintDocument) -> dict[str, dict[str, Any]]:
    return {
        str(entry["businessKey"]): entry
        for entry in document.container_entries(REGISTRY_CONTAINER)
        if isinstance(entry.get("businessKey"), str)
    }


def _pruned(document: _T) -> _T:
    """Drop empty keys, keeping the value's own type so callers stay typed."""
    if isinstance(document, dict):
        kept = {key: _pruned(value) for key, value in document.items()}
        return cast(_T, {key: value for key, value in sorted(kept.items()) if value not in (None, {}, [], "")})
    if isinstance(document, list):
        return cast(_T, [_pruned(item) for item in document])
    return document
