"""OpenAPI Compiler (BP-CMP-041, overall SRS 12.1 / 10.2 / FR-API-001…005).

Input is `pages` + `apiIntents` + `registry` and the Capability Profile; output is the
OpenAPI 3.1 document, the per-`operationKey` examples and the API reference Markdown.

Three things this module must get right, each of which is a rule and not a taste:

* **Nothing about the endpoint is known in advance.** An intent names a *logical*
  resource; the Profile's catalogue resolves it to a path, a method and this version's
  parameter names (FR-API-001).  A resource the Profile does not list is a BLOCKING
  problem, not a guessed path — a plausible URL that 404s in production is worse than no
  URL at all.
* **Old Tracker endpoints never appear.** From v2.41 Tracker data goes through
  `/tracker…`; the legacy metadata-shaped ones are forbidden *with no exception*
  (FR-API-002), so they are refused even if a Profile happens to still offer them.
* **A shape shared by several pages is defined once.** Repeated inline copies drift the
  moment one page changes (FR-API-004), so identical request/response shapes collapse
  into one `components.schemas` entry referenced by `$ref`.

Examples are derived from the compiled Registry objects — value types and option sets —
never from a random generator, because a compiler may not reach for randomness
(BP-CMP-001) and a reviewer has to see the same example twice.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from factory_blueprint.compilers import files
from factory_blueprint.compilers.context import CompileInputs
from factory_blueprint.compilers.refs import placeholder
from factory_blueprint.content.document import BlueprintDocument
from factory_blueprint.content.layout import CONTAINER_BY_PATH
from factory_blueprint.dto.issue import Issue, make_issue, sort_issues
from factory_blueprint.enums import IssueLevel, Section
from factory_blueprint.s4.contract import CapabilityProfile

RULE_RESOURCE_UNKNOWN = "BPV-API-001"
RULE_LEGACY_TRACKER = "BPV-API-002"
RULE_REMOVED_ENDPOINT = "BPV-API-003"
RULE_REGISTRY_REF_UNKNOWN = "BPV-API-004"
RULE_PARAM_UNKNOWN = "BPV-API-005"
RULE_OPERATION_KEY_DUPLICATE = "BPV-API-006"
RULE_PAGE_WITHOUT_INTENT = "BPV-API-007"
RULE_ENDPOINT_COLLISION = "BPV-API-008"

OPENAPI_FILE = "api/openapi.yaml"
REFERENCE_FILE = "api/api-reference.md"
EXAMPLES_PREFIX = "api/examples"

#: logical resource families whose data must travel on the new Tracker API
TRACKER_RESOURCE_PREFIX = "tracker."
#: pre-2.41 Tracker endpoints, forbidden by FR-API-002 even if a Profile still lists them
#: ``relationships`` belongs here as much as the other four: FR-API-002 names all of
#: ``/api/trackedEntityInstances``, ``/api/enrollments``, ``/api/events`` and
#: ``/api/relationships``, and the new Tracker sub-resource of the same name is only ever
#: reached under ``tracker/``, which the guard below exempts.
LEGACY_TRACKER_SEGMENTS = frozenset(
    {
        "trackedEntityInstances",
        "eventReports",
        "eventCases",
        "enrollments",
        "events",
        "relationships",
    }
)

#: the API prefix an intent's path is built under when the caller gives none
DEFAULT_SECURITY_SCHEMES = ("apiToken", "basic", "appPlatformSession")

OPENAPI_SPEC_VERSION = "3.1.0"

#: operationKey is camelCase (defs.json), so a component name splits on words, not just
#: on separators — `listPatients` becomes `ListPatientsResult`, not `ListpatientsResult`
_WORD_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|[_\s-]+")

#: DHIS2 value type → JSON Schema.  A type that is not listed keeps its object's own
#: declared type rather than being silently typed as a string.
VALUE_TYPE_SCHEMAS: dict[str, dict[str, Any]] = {
    "TEXT": {"type": "string"},
    "LONG_TEXT": {"type": "string"},
    "EMAIL": {"type": "string", "format": "email"},
    "PHONE_NUMBER": {"type": "string"},
    "URL": {"type": "string", "format": "uri"},
    "USERNAME": {"type": "string"},
    "ADDRESS": {"type": "string"},
    "NUMBER": {"type": "number"},
    "REAL": {"type": "number"},
    "INTEGER": {"type": "integer"},
    "INTEGER_POSITIVE": {"type": "integer", "minimum": 1},
    "INTEGER_NEGATIVE": {"type": "integer", "maximum": -1},
    "INTEGER_ZERO_OR_POSITIVE": {"type": "integer", "minimum": 0},
    "PERCENTAGE": {"type": "number", "minimum": 0, "maximum": 100},
    "BOOLEAN": {"type": "boolean"},
    "YES_ONLY_A_TRUE": {"type": "boolean"},
    "DATE": {"type": "string", "format": "date"},
    "TIME": {"type": "string", "pattern": "^[0-2][0-9]:[0-5][0-9]$"},
    "DATETIME": {"type": "string", "format": "date-time"},
    "AGE": {"type": "integer", "minimum": 0},
    "FILE_RESOURCE": {"type": "string"},
    "IMAGE": {"type": "string"},
    "GEOJSON": {"type": "string"},
    "COORDINATE": {"type": "array", "items": {"type": "number"}},
}

#: deterministic sample values, one per JSON type; never a clock and never random
EXAMPLE_VALUES: dict[str, Any] = {
    "string": "sample",
    "number": 1.5,
    "integer": 1,
    "boolean": True,
    "array": [],
    "object": {},
}

#: a paging block in an intent means the version's paging parameters are appended
_PAGING_PARAMETERS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("page", {"schema": {"type": "integer", "minimum": 1}, "description": "1-based page number"}),
    ("pageSize", {"schema": {"type": "integer", "minimum": 1}, "description": "objects per page"}),
)


@dataclass
class OpenApiProduct:
    """The OpenAPI document plus its companion files and the problems found."""

    document: dict[str, Any] = field(default_factory=dict)
    files: dict[str, bytes] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)
    operations: list[dict[str, Any]] = field(default_factory=list)

    @property
    def blocking(self) -> bool:
        return any(issue.level is IssueLevel.BLOCKING for issue in self.issues)


def compile_openapi(
    inputs: CompileInputs, objects: dict[str, dict[str, Any]]
) -> OpenApiProduct:
    """Compile `pages` + `apiIntents` against the Profile's resource catalogue."""
    intents = _intents(inputs)
    types = _registry_types(inputs)
    issues: list[Issue] = []
    analysed: list[_Analysis] = []
    for intent in intents:
        record, intent_issues = _analyse(inputs, intent, objects, types)
        issues.extend(intent_issues)
        if record is not None:
            analysed.append(record)

    # pass two: a shape two operations share becomes one component (FR-API-004)
    components = _shared_components(analysed)
    paths: dict[str, dict[str, Any]] = {}
    operations: list[dict[str, Any]] = []
    for record in analysed:
        collision = paths.setdefault(record.path, {}).get(record.method.lower())
        if collision is not None:
            # two intents resolving to one endpoint is not a thing to guess around: the
            # document can carry only one operation per path and method, so the second
            # would vanish from a package that looked complete (C-07: report, don't fix)
            issues.append(
                make_issue(
                    RULE_ENDPOINT_COLLISION,
                    IssueLevel.BLOCKING,
                    f"operationKey {record.operation_key!r} resolves to the same endpoint as "
                    f"{collision['operationId']!r} ({record.method} {record.path}); an OpenAPI "
                    "document holds one operation per path and method",
                    business_key=record.operation_key,
                    section=Section.API_INTENTS,
                    remediation="give the intent a distinct resource or parameters, or drop it",
                )
            )
            continue
        operation = _render(record, components)
        operations.append(operation)
        paths[record.path][record.method.lower()] = operation["definition"]

    issues.extend(_page_coverage(inputs, operations))
    document = _envelope(inputs, paths, components)
    product = OpenApiProduct(
        document=document,
        issues=sort_issues(issues),
        operations=sorted(operations, key=lambda item: item["operationKey"]),
    )
    product.files = {
        OPENAPI_FILE: files.yaml_bytes(document),
        REFERENCE_FILE: _reference_markdown(inputs, operations),
        **_example_files(inputs, operations),
    }
    return product


# --- the document ---------------------------------------------------------


def _envelope(
    inputs: CompileInputs, paths: dict[str, Any], components: dict[str, Any]
) -> dict[str, Any]:
    """`openapi`, `info`, `servers`, `securitySchemes`: the parts not derived from intents."""
    profile = inputs.profile
    api_prefix = (profile.api_prefix if profile else "api") or "api"
    version = inputs.settings().openapi_version or OPENAPI_SPEC_VERSION
    document: dict[str, Any] = {
        "openapi": version,
        "info": {
            "title": _title(inputs),
            "version": inputs.blueprint_version,
            "x-blueprint-hash": inputs.blueprint_hash,
            "x-dhis2-version": inputs.dhis2_version or None,
            "description": (
                f"Compiled from App Blueprint version {inputs.blueprint_version}. "
                "Do not edit: every path, parameter name and schema here is generated "
                "from apiIntents and the target environment's Capability Profile."
            ),
        },
        "servers": [
            {
                # FR-API-005: the instance is a variable, never a baked-in host.  S2 has
                # no environment base URL and must not invent one; the App Platform
                # supplies it, and an empty default means "the instance serving the app".
                "url": "{baseUrl}/" + api_prefix.strip("/"),
                "description": (
                    "Target DHIS2 instance. `baseUrl` is supplied by the App Platform "
                    "configuration for the environment the app runs in."
                ),
                "variables": {"baseUrl": {"default": "", "description": "instance root URL"}},
            }
        ],
        "security": _security(inputs),
        "paths": paths,
        "components": {
            "securitySchemes": _security_schemes(inputs),
            "schemas": components,
        },
    }
    return dict(_pruned(document, root=True))


def _title(inputs: CompileInputs) -> str:
    project = inputs.document.get("project") or {}
    return str(project.get("displayName") or f"{inputs.project_id} API")


def _security_schemes(inputs: CompileInputs) -> dict[str, Any]:
    """The three ways FR-API-005 names, minus any the target version does not have."""
    removed = inputs.profile
    schemes: dict[str, Any] = {
        "apiToken": {
            "type": "apiKey",
            "in": "header",
            "name": "Authorization",
            "description": "Personal access token: `Authorization: ApiToken <token>`.",
        },
        "basic": {"type": "http", "scheme": "basic", "description": "HTTP Basic authentication."},
        "appPlatformSession": {
            "type": "apiKey",
            "in": "cookie",
            "name": "JSESSIONID",
            "description": (
                "Session cookie set by the DHIS2 App Platform when the app is hosted; "
                "the browser carries it, the app never handles a credential."
            ),
        },
    }
    if removed is None:
        return {name: schemes[name] for name in sorted(schemes)}
    supported = {
        name: scheme
        for name, scheme in schemes.items()
        if not _removed_by_profile(inputs.profile, "auth", name)
    }
    return {name: supported[name] for name in sorted(supported)}


def _security(inputs: CompileInputs) -> list[dict[str, list[str]]]:
    return [{name: []} for name in sorted(_security_schemes(inputs))]


@dataclass
class _Analysis:
    """One intent with its endpoint resolved and its raw schemas, before sharing."""

    operation_key: str
    page_key: str
    path: str
    method: str
    resource: str
    purpose: str
    parameters: list[dict[str, Any]]
    request_schema: dict[str, Any] | None
    response_schema: dict[str, Any]
    registry_refs: list[dict[str, Any]]
    notes: dict[str, Any]


def _analyse(
    inputs: CompileInputs,
    intent: dict[str, Any],
    objects: dict[str, dict[str, Any]],
    types: dict[str, str],
) -> tuple[_Analysis | None, list[Issue]]:
    issues: list[Issue] = []
    operation_key = str(intent.get("operationKey") or "")
    logical = str(intent.get("resource") or "")
    entry = inputs.profile.resource(logical) if inputs.profile else None
    if entry is None:
        issues.append(
            make_issue(
                RULE_RESOURCE_UNKNOWN,
                IssueLevel.BLOCKING,
                f"the Capability Profile of {inputs.dhis2_version or 'this target'} does not "
                f"offer resource {logical!r}, so operationKey {operation_key!r} cannot be "
                "given a path or parameter names",
                business_key=operation_key,
                section=Section.API_INTENTS,
                location=f"/apiIntents/items/{operation_key}",
                remediation="pick a resource the version really has, or add it to the Profile",
            )
        )
        return None, issues

    path = "/" + str(entry.path).lstrip("/")
    issues.extend(_endpoint_rules(inputs, intent, entry, path, operation_key))

    parameters, parameter_issues = _parameters(inputs, intent, entry, operation_key)
    issues.extend(parameter_issues)
    request_body = intent.get("requestBodyShape") or []
    request_schema = (
        _shape_schema(inputs, request_body, objects, types)
        if isinstance(request_body, list) and request_body
        else None
    )
    response_schema = _response_schema(inputs, intent, objects, types)
    registry_refs, ref_issues = _registry_refs(inputs, intent, types, operation_key)
    issues.extend(ref_issues)

    return (
        _Analysis(
            operation_key=operation_key,
            page_key=str(intent.get("pageKey") or ""),
            path=path,
            method=str(intent.get("method") or entry.method or "GET").upper(),
            resource=logical,
            purpose=str(intent.get("purpose") or ""),
            parameters=parameters,
            request_schema=request_schema,
            response_schema=response_schema,
            registry_refs=registry_refs,
            notes=_pruned(
                {
                    "preconditions": _text_list(intent.get("preconditions")),
                    "postconditions": _text_list(intent.get("postconditions")),
                    "errorHandling": _text_list(intent.get("errorHandling")),
                    "permissions": _text_list(intent.get("permissions")),
                }
            ),
        ),
        issues,
    )


def _endpoint_rules(
    inputs: CompileInputs, intent: dict[str, Any], entry: Any, path: str, operation_key: str
) -> list[Issue]:
    """FR-API-002 and FR-CMP-010's deprecated/removed check (BP-VAL-020 OpenAPI 维度)."""
    issues: list[Issue] = []
    first_segment = path.strip("/").split("/")[0]
    if first_segment in LEGACY_TRACKER_SEGMENTS and not str(entry.path).startswith("tracker/"):
        issues.append(
            make_issue(
                RULE_LEGACY_TRACKER,
                IssueLevel.BLOCKING,
                f"operationKey {operation_key!r} resolves to {entry.logical_name!r} at {path!r}, "
                "a pre-2.41 Tracker endpoint; Tracker data goes through the new Tracker API "
                "and nothing else",
                business_key=operation_key,
                section=Section.API_INTENTS,
                remediation="reference the tracker.* logical resource instead",
            )
        )
    if _removed_by_profile(inputs.profile, "endpoint", path) or _removed_by_profile(
        inputs.profile, "resource", entry.logical_name
    ):
        issues.append(
            make_issue(
                RULE_REMOVED_ENDPOINT,
                IssueLevel.BLOCKING,
                f"{entry.logical_name!r} resolves to {path!r}, which the target version "
                f"{inputs.dhis2_version} reports as removed",
                business_key=operation_key,
                section=Section.API_INTENTS,
                remediation="resolve the intent onto a resource this version still serves",
            )
        )
    if (
        entry.logical_name.startswith(TRACKER_RESOURCE_PREFIX)
        and not entry.tracker_api
        and not _removed_by_profile(inputs.profile, "resource", entry.logical_name)
    ):
        issues.append(
            make_issue(
                RULE_LEGACY_TRACKER,
                IssueLevel.BLOCKING,
                f"{entry.logical_name!r} is offered at {path!r} without the Tracker API flag, "
                f"which is the legacy route on version {inputs.dhis2_version}",
                business_key=operation_key,
                section=Section.API_INTENTS,
            )
        )
    return issues


def _render(record: _Analysis, components: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Turn one analysis into the OpenAPI operation object and its examples."""
    definition: dict[str, Any] = {
        "operationId": record.operation_key,
        "summary": record.purpose or record.operation_key,
        "tags": [record.page_key or "shared"],
        "x-registry-refs": record.registry_refs,
        "responses": {
            "200": {
                "description": record.purpose or record.operation_key,
                "schema": _reference_for(record.response_schema, components),
            }
        },
    }
    if record.parameters:
        definition["parameters"] = record.parameters
    if record.request_schema is not None:
        definition["requestBody"] = {
            "required": True,
            "content": {
                "application/json": {
                    "schema": _reference_for(record.request_schema, components)
                }
            },
        }
    if record.notes:
        definition["x-intent-notes"] = record.notes
    return {
        "operationKey": record.operation_key,
        "pageKey": record.page_key,
        "path": record.path,
        "method": record.method,
        "resource": record.resource,
        "purpose": record.purpose,
        "registryRefs": record.registry_refs,
        "definition": definition,
        "requestExample": _example_of(record.request_schema)
        if record.request_schema is not None
        else None,
        "responseExample": _example_of(record.response_schema),
    }


# --- parameters -----------------------------------------------------------


def _parameters(
    inputs: CompileInputs, intent: dict[str, Any], entry: Any, operation_key: str
) -> tuple[list[dict[str, Any]], list[Issue]]:
    """Path and query parameters, named the way *this* version names them (FR-API-003)."""
    issues: list[Issue] = []
    parameters: list[dict[str, Any]] = []
    catalogue = dict(entry.parameters or {})

    for location, source in (("path", intent.get("pathParams")), ("query", intent.get("queryParams"))):
        for name in sorted((source or {}) if isinstance(source, dict) else {}):
            spec = (source or {})[name] or {}
            actual = _resolve_name(name, catalogue)
            if actual is None:
                issues.append(
                    make_issue(
                        RULE_PARAM_UNKNOWN,
                        IssueLevel.BLOCKING,
                        f"operationKey {operation_key!r} asks for parameter {name!r}, which "
                        f"{entry.logical_name} does not accept on version {inputs.dhis2_version}",
                        business_key=operation_key,
                        section=Section.API_INTENTS,
                        details={"offered": sorted(catalogue)},
                        remediation="use a parameter this version exposes, or drop the filter",
                    )
                )
                continue
            parameter: dict[str, Any] = {
                "name": actual,
                "in": location,
                "required": location == "path" or bool(spec.get("required")),
                "schema": {"type": "array" if spec.get("multiple") else "string", "items": {"type": "string"}}
                if spec.get("multiple")
                else {"type": "string"},
            }
            if spec.get("multiple"):
                # multi-UID access is one comma-separated value, not repeated parameters
                parameter["style"] = "form"
                parameter["explode"] = False
            origin = str(spec.get("from") or "")
            if origin:
                parameter["x-source"] = origin
            if isinstance(spec.get("businessKey"), str):
                parameter["x-registry-ref"] = _ref_pair(inputs, spec["businessKey"])
            if "literal" in spec:
                parameter["x-literal"] = spec["literal"]
            parameters.append(parameter)

    if isinstance(intent.get("paging"), dict) and intent["paging"]:
        for name, schema in _PAGING_PARAMETERS:
            parameters.append({"name": name, "in": "query", "required": False, **schema})
    return parameters, issues


def _resolve_name(name: str, catalogue: dict[str, str]) -> str | None:
    """The version's spelling of a parameter, or None if this version has no such thing."""
    if name in catalogue:
        return catalogue[name]
    if name in set(catalogue.values()):
        return name
    if not catalogue:
        # a Profile that documents no parameters gives S2 nothing to contradict
        return name
    return None


# --- schemas, shared by shape (FR-API-004) --------------------------------


def _response_schema(
    inputs: CompileInputs,
    intent: dict[str, Any],
    objects: dict[str, dict[str, Any]],
    types: dict[str, str],
) -> dict[str, Any]:
    fields = [str(item) for item in (intent.get("fields") or [])]
    if fields:
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                name: _field_schema(name, intent, objects, types) for name in sorted(fields)
            },
            "required": sorted(fields),
        }
    else:
        schema = {"type": "object", "additionalProperties": True}
    if isinstance(intent.get("paging"), dict) and intent["paging"]:
        schema = {
            "type": "object",
            "properties": {
                "pager": {"type": "object", "additionalProperties": True},
                "objects": {"type": "array", "items": schema},
            },
            "required": ["pager", "objects"],
        }
    return schema


def _shared_components(analysed: list[_Analysis]) -> dict[str, dict[str, Any]]:
    """Register exactly the shapes more than one operation uses (FR-API-004).

    Counting first matters: registering every schema would give a one-off operation a
    component nobody else points at, and inlining everything would leave two pages with
    two copies of the same patient shape — the copy that gets missed when the concept
    changes.
    """
    candidates: list[tuple[str, dict[str, Any]]] = []
    for record in analysed:
        if record.request_schema is not None:
            candidates.append((f"request:{record.operation_key}", record.request_schema))
        candidates.append((f"response:{record.operation_key}", record.response_schema))

    by_signature: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for hint, schema in candidates:
        by_signature.setdefault(_canonical(schema), []).append((hint, schema))

    components: dict[str, dict[str, Any]] = {}
    for signature in sorted(by_signature):
        group = by_signature[signature]
        if len(group) < 2:
            continue
        name = _component_name(group[0][0], [hint for hint, _ in group])
        components[name] = group[0][1]
    return components


def _reference_for(
    schema: dict[str, Any], components: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """`$ref` when this shape is one of the shared components, inline otherwise."""
    signature = _canonical(schema)
    for name in sorted(components):
        if _canonical(components[name]) == signature:
            return {"$ref": f"#/components/schemas/{name}"}
    return schema


def _component_name(hint: str, group: list[str]) -> str:
    """A component named after the operation that first needed it, stable across runs."""
    kind, _, operation_key = hint.partition(":")
    words = [word for word in _WORD_BOUNDARY.split(operation_key) if word]
    label = "".join(word[:1].upper() + word[1:] for word in words)
    suffix = "Request" if kind == "request" else "Result"
    if len(group) > 1 and label.endswith(suffix):
        label = label[: -len(suffix)]
    return f"{label or 'Shared'}{suffix}"


def _field_schema(
    name: str, intent: dict[str, Any], objects: dict[str, dict[str, Any]], types: dict[str, str]
) -> dict[str, Any]:
    """Type a response field from the Registry object it comes from, when it does."""
    for key in _candidate_keys(name, intent):
        entry = objects.get(key)
        if not entry:
            continue
        value_type = str(entry.get("valueType") or "")
        schema = dict(VALUE_TYPE_SCHEMAS.get(value_type, {"type": "string"}))
        options = _option_codes(entry, objects, types)
        if options:
            schema = {"type": "string", "enum": options}
        return schema
    return {"type": "string"}


def _candidate_keys(name: str, intent: dict[str, Any]) -> list[str]:
    refs = [str(item) for item in (intent.get("registryRefs") or [])]
    head = name.split(".")[0]
    return [key for key in (*refs, head) if key in refs or key == head]


def _shape_schema(
    inputs: CompileInputs,
    shape: list[Any],
    objects: dict[str, dict[str, Any]],
    types: dict[str, str],
) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for item in shape:
        if not isinstance(item, dict):
            continue
        name = str(item.get("field") or "")
        if not name:
            continue
        business_key = item.get("businessKey")
        if isinstance(business_key, str) and business_key:
            properties[name] = _field_schema(name, {"registryRefs": [business_key]}, objects, types)
        else:
            declared = str(item.get("valueType") or "")
            properties[name] = dict(VALUE_TYPE_SCHEMAS.get(declared, {"type": "string"}))
        if item.get("required"):
            required.append(name)
    document: dict[str, Any] = {
        "type": "object",
        "properties": {name: properties[name] for name in sorted(properties)},
    }
    if required:
        document["required"] = sorted(set(required))
    return document


def _canonical(document: Any) -> str:
    import json

    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


# --- registry references --------------------------------------------------


def _registry_refs(
    inputs: CompileInputs, intent: dict[str, Any], types: dict[str, str], operation_key: str
) -> tuple[list[dict[str, Any]], list[Issue]]:
    issues: list[Issue] = []
    refs: list[dict[str, Any]] = []
    for key in sorted({str(item) for item in (intent.get("registryRefs") or [])}):
        if key not in types:
            issues.append(
                make_issue(
                    RULE_REGISTRY_REF_UNKNOWN,
                    IssueLevel.BLOCKING,
                    f"operationKey {operation_key!r} references registry entry {key!r}, "
                    "which this version does not have",
                    business_key=key,
                    section=Section.API_INTENTS,
                    remediation="register the object, or remove the reference",
                )
            )
            continue
        refs.append(_ref_pair(inputs, key))
    return refs, issues


def _ref_pair(inputs: CompileInputs, business_key: str) -> dict[str, Any]:
    """A reference is businessKey plus the UID *this* environment resolves it to.

    In a baseline compile the UID is not known yet, so it is emitted as the same
    `{{ref:…}}` placeholder the metadata baseline uses — a product that cannot be
    deployed by accident, rather than one carrying the design environment's number.
    """
    uid = inputs.bindings.get(business_key)
    return {
        "businessKey": business_key,
        "uid": uid if uid else placeholder(business_key),
        "environmentDependent": not bool(uid),
    }


def _option_codes(
    entry: dict[str, Any], objects: dict[str, dict[str, Any]], types: dict[str, str]
) -> list[str]:
    """The codes of the OptionSet this object points at, when it points at one."""
    option_set = entry.get("optionSet")
    key = None
    if isinstance(option_set, dict):
        key = str(option_set.get("id") or option_set.get("businessKey") or "")
    elif isinstance(option_set, str):
        key = option_set
    if not key:
        return []
    resolved = objects.get(key) or {}
    options = resolved.get("options") or []
    codes = [str(item.get("code") or item.get("name") or "") for item in options if isinstance(item, dict)]
    return sorted({code for code in codes if code})


def _registry_types(inputs: CompileInputs) -> dict[str, str]:
    container = CONTAINER_BY_PATH["/registry/entries"]
    return {
        str(entry["businessKey"]): str(entry.get("dhis2Type") or "")
        for entry in BlueprintDocument(inputs.document).container_entries(container)
        if isinstance(entry.get("businessKey"), str)
    }


def _removed_by_profile(profile: CapabilityProfile | None, kind: str, name: str) -> bool:
    if profile is None:
        return False
    return profile.is_removed(kind, name) or profile.is_removed("path", name)


# --- examples (BP-CMP-041 新增) -------------------------------------------


def _example_of(schema: dict[str, Any]) -> Any:
    """A deterministic sample body built from the schema, i.e. from Registry types.

    No generator and no seed: the same value type always shows the same sample, so an
    example in a review is the example in the package (BP-CMP-001, 17.3).
    """
    if "enum" in schema:
        return schema["enum"][0]
    kind = str(schema.get("type") or "object")
    if kind == "object":
        return {
            name: _example_of(schema["properties"][name])
            for name in sorted(schema.get("properties") or {})
        }
    if kind == "array":
        return [_example_of(schema.get("items") or {"type": "string"})]
    if kind == "string" and schema.get("format") == "date":
        return "2026-01-01"
    if kind == "string" and schema.get("format") == "date-time":
        return "2026-01-01T00:00:00.000Z"
    return EXAMPLE_VALUES.get(kind, "sample")


def _example_files(inputs: CompileInputs, operations: list[dict[str, Any]]) -> dict[str, bytes]:
    """One example file per operationKey, which 17.3 checks for existence."""
    out: dict[str, bytes] = {}
    for operation in operations:
        document = {
            "schemaVersion": 1,
            "operationKey": operation["operationKey"],
            "pageKey": operation["pageKey"],
            "method": operation["method"],
            "path": operation["path"],
            "request": operation["requestExample"],
            "response": operation["responseExample"],
        }
        out[f"{EXAMPLES_PREFIX}/{operation['operationKey']}.json"] = files.json_bytes(_pruned(document))
    return out


# --- API reference Markdown ----------------------------------------------


def _reference_markdown(inputs: CompileInputs, operations: list[dict[str, Any]]) -> bytes:
    pages: dict[str, list[dict[str, Any]]] = {}
    for operation in operations:
        pages.setdefault(operation["pageKey"], []).append(operation)
    sections: list[tuple[str, list[str]]] = []
    for page_key in sorted(pages):
        lines = [
            f"Page `{page_key}`. Target DHIS2 version: "
            f"{inputs.dhis2_version or 'unbound (baseline compile)'}. "
            "Every path and parameter name below is what that version serves."
        ]
        for operation in sorted(pages[page_key], key=lambda item: item["operationKey"]):
            lines.append(f"### `{operation['operationKey']}`")
            lines.append("")
            lines.append(f"- Purpose: {operation['purpose'] or 'n/a'}")
            lines.append(f"- Call: `{operation['method']} {operation['path']}`")
            lines.append(f"- Logical resource: `{operation['resource']}`")
            if operation["definition"].get("parameters"):
                rendered = ", ".join(
                    f"{item['name']} ({item['in']}{', required' if item.get('required') else ''})"
                    for item in operation["definition"]["parameters"]
                )
                lines.append(f"- Parameters: {rendered}")
            if operation["registryRefs"]:
                lines.append(
                    "- Registry entries: "
                    + ", ".join(
                        f"`{item['businessKey']}` → `{item['uid']}`" for item in operation["registryRefs"]
                    )
                )
            lines.append(f"- Example: `{EXAMPLES_PREFIX}/{operation['operationKey']}.json`")
            lines.append("")
        sections.append((f"API for page {page_key}", lines))
    if not sections:
        sections.append(("API", ["No API intent is present in this Blueprint version."]))
    return files.md_lines(sections)


# --- small helpers --------------------------------------------------------


def _intents(inputs: CompileInputs) -> list[dict[str, Any]]:
    container = CONTAINER_BY_PATH["/apiIntents/items"]
    return sorted(
        BlueprintDocument(inputs.document).container_entries(container),
        key=lambda item: str(item.get("operationKey") or ""),
    )


def _pages(inputs: CompileInputs) -> list[dict[str, Any]]:
    container = CONTAINER_BY_PATH["/pages/items"]
    return BlueprintDocument(inputs.document).container_entries(container)


def _page_coverage(inputs: CompileInputs, operations: list[dict[str, Any]]) -> list[Issue]:
    """A page with no API intent has nothing for the frontend to call (FR-PG-001)."""
    covered = {operation["pageKey"] for operation in operations}
    issues: list[Issue] = []
    for page in _pages(inputs):
        key = str(page.get("pageKey") or "")
        if key and key not in covered:
            issues.append(
                make_issue(
                    RULE_PAGE_WITHOUT_INTENT,
                    IssueLevel.BLOCKING,
                    f"page {key!r} has no API intent that resolved, so the development "
                    "package would document a page with no data source",
                    business_key=key,
                    section=Section.PAGES,
                    remediation="add the apiIntent for this page, or drop the page",
                )
            )
    seen: set[str] = set()
    for operation in operations:
        if operation["operationKey"] in seen:
            issues.append(
                make_issue(
                    RULE_OPERATION_KEY_DUPLICATE,
                    IssueLevel.BLOCKING,
                    f"operationKey {operation['operationKey']!r} appears more than once",
                    business_key=operation["operationKey"],
                    section=Section.API_INTENTS,
                )
            )
        seen.add(operation["operationKey"])
    return issues


def _text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        out.append(str(item) if not isinstance(item, dict) else _canonical(item))
    return sorted(out)


#: keys the OpenAPI object must always carry, however empty: 3.1 requires `paths` to
#: be present, and pruning it would emit an invalid document for a project that has no
#: API intent yet
REQUIRED_DOCUMENT_KEYS = frozenset({"openapi", "info", "paths", "components", "default"})


#: keys kept at any depth because the spec needs them present, "" included: an OpenAPI
#: server variable must declare a ``default``, and a schema's ``required`` may be empty
REQUIRED_KEYS = frozenset({"default", "required", "schemaVersion"})


def _pruned(document: Any, *, root: bool = False) -> Any:
    """Drop empty keys, so a product does not carry a field that says nothing.

    ``root`` keeps the members 3.1 requires to exist even when they are empty, because a
    document missing ``paths`` is not a smaller document — it is an invalid one.
    """
    if isinstance(document, dict):
        kept = {key: _pruned(value) for key, value in document.items()}
        always = REQUIRED_KEYS | (REQUIRED_DOCUMENT_KEYS if root else frozenset())
        return {
            key: value
            for key, value in sorted(kept.items())
            if value not in (None, {}, [], "") or key in always
        }
    if isinstance(document, list):
        return [_pruned(item) for item in document]
    return document
