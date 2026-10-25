"""Whole-document contract checks (S5IF-AC01 / AC02 / AC15, S5IF-G01 / G03 / G05 / G11).

One test walks the generated OpenAPI instead of 106 tests asserting on it, because
every one of these rules is a property of the document as a whole: a single untyped
response or one snake_case field is a defect of the same kind, wherever it appears.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from factory_control.rest.openapi import components

MUTATING = ("post", "put", "patch", "delete")

#: The S1 project root — this file checks the document as *published*, not only as generated.
S1_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def document() -> dict[str, Any]:
    """The published document, built the same way ``scripts/export_openapi.py`` builds it.

    Deliberately not built on the database-backed test harness: the document is a
    property of the routing table, and a module-scoped fixture cannot depend on a
    function-scoped one.
    """
    from factory_control.adapters.registry import default_ports
    from factory_control.config import Settings
    from factory_control.events.bus import EventBus
    from factory_control.rest.app import create_app
    from factory_control.runtime import build_runtime

    settings = Settings(provider_mode="fakes", workflow_engine="inprocess", database_url="sqlite://")
    runtime = build_runtime(settings=settings, bus=EventBus(), **default_ports(settings=settings))
    return create_app(runtime).openapi()


def _operations(document: dict[str, Any]):  # type: ignore[no-untyped-def]
    for path, item in document["paths"].items():
        for method, operation in item.items():
            if isinstance(operation, dict):
                yield path, method, operation


def _resolve(document: dict[str, Any], node: Any, seen: frozenset[str] = frozenset()) -> Any:
    """Follow ``$ref`` chains so a component cannot hide an untyped object."""
    if isinstance(node, dict) and set(node) == {"$ref"}:
        name = node["$ref"].split("/")[-1]
        if name in seen:
            return {}
        target = document["components"]["schemas"].get(name, {})
        return _resolve(document, target, seen | {name})
    return node


def _named(schema: Any) -> bool:
    """True when the body points at a component rather than declaring itself inline.

    CP-AC-11 / S5IF-AC01 is a statement about generated code: ``openapi-zod-client`` can only give S5
    a reusable type when the operation references one, so an inline object literal is
    a failure even if its fields happen to be listed.
    """
    if not isinstance(schema, dict) or not schema:
        return False
    if "$ref" in schema:
        return True
    for key in ("items", "additionalProperties"):
        if key in schema and isinstance(schema[key], dict):
            if not _named(schema[key]):
                return False
        elif key in schema and isinstance(schema[key], bool):
            return False
    for key in ("anyOf", "oneOf", "allOf"):
        if key in schema:
            return any(isinstance(v, dict) and "$ref" in v for v in schema[key])
    return bool(schema.get("properties"))


def _open_objects(node: Any, where: str = "") -> list[str]:
    """Every ``additionalProperties: true`` inside a resolved body (AC01)."""
    found: list[str] = []
    if isinstance(node, dict):
        if node.get("additionalProperties") is True:
            found.append(where or "body")
        for key, value in node.items():
            if key in ("description", "title", "example"):
                continue
            found += _open_objects(value, f"{where}.{key}")
    elif isinstance(node, list):
        for index, item in enumerate(node):
            found += _open_objects(item, f"{where}[{index}]")
    return found


#: Fields that legitimately hold a provider's own JSON: the value is whatever the
#: subsystem produced, and S1 declares it as an object rather than inventing fields.
OPEN_FIELDS_OK = ("result", "payload", "details", "content", "value", "before", "after", "extra")


#: Fields that stay open objects, each with the reason it is open (S5IF-AC01 register).
#:
#: AC01 is about a response *body* having no type at all, and every body now is a named
#: component.  What is left here is a single field inside an otherwise typed component, and
#: each one is open because S1 has no schema to offer: the shape belongs to a provider that
#: has not published it, or the ruling says S1 must not interpret it.  The register is
#: exact — an open field added without a reason here fails, and so does a registered one
#: that has since been typed, which is what keeps the list from rotting.
OPEN_FIELD_EXEMPTIONS: dict[str, dict[str, str]] = {
    "POST /api/v1/projects/{project_id}/upgrade-mode": {
        ".properties.invalidation": "S2's invalidation plan: which stages it invalidated is the provider's report (BP-IMP-*)"
    },
    "GET /api/v1/artifacts/{artifact_id}/diff": {
        ".properties.lines.items": "S0's diff rows are line-shaped for text artifacts and path-shaped for JSON (FD-ART-013); one field cannot type both"
    },
    "POST /api/v1/notifications/{notification_id}/read": {
        ".properties.objectRef": "Which object a notification points at depends on its type — stage, question, Gate or Run (CP-NTF-001)"
    },
    "GET /api/v1/gates/{gate_id}": {
        ".properties.blueprintRef.anyOf[0]": "S1 stores whatever the producing step reported (a draft, a version, or a build id) — no single shape",
        ".properties.riskSummaryRef.anyOf[0]": "S2 and S4 report their risk summary under different keys (S5IF-UP02/08)",
    },
    "GET /api/v1/gates/{gate_id}/explanation": {
        ".properties.canChange.items": "S2's decision and impact records, forwarded as the provider produced them (S5IF-BP13)",
        ".properties.afterApproval": "S2's own description of what approval triggers",
    },
    "GET /api/v1/deployment-runs/{run_id}/summary": {
        ".properties.maintenanceActions.items": "S4's maintenance action list; the DX-EXE contract package does not exist yet (S5IF-UP01/02)"
    },
    "GET /api/v1/executions/{execution_id}": {
        ".properties.steps.items": "S4's execution plan steps (DX-EXE-*), published only as an index so far (S5IF-UP01)"
    },
    "POST /api/v1/environments/{environment_key}/delete-objects": {
        # The same S4 step rows as the execution read above, in the answer that reports them
        # first: a refused deletion is exactly the case a caller has to see which admission
        # check (SNAPSHOT / VALIDATE / DRIFT_CHECK / COMMIT) stopped it.
        ".properties.steps.items": "S4's execution plan steps (DX-EXE-*), published only as an index so far (S5IF-UP01)"
    },
    "POST /api/v1/executions/{execution_id}/reconcile": {
        ".properties.evidence": "S4's own account of how it reached the verdict (DX-REC-*)"
    },
    "POST /api/v1/artifacts/{artifact_id}/reproduce": {
        "body": "S2's reproduce answer; S1 passes the provider's document through unchanged (S5IF-BP12 note)"
    },
    "GET /api/v1/environments/{environment_key}/protection": {
        ".properties.dataExportPolicy": "S4's policy object for the environment (DX-CLI-*), format owned by S4"
    },
    "GET /api/v1/environments/{environment_key}/capability-profile": {
        ".properties.content.anyOf[0]": "CAPABILITY_PROFILE content format is unpublished (S5IF-UP02); the index layer is typed"
    },
    "GET /api/v1/projects/{project_id}/blueprint/section-schemas/{section}": {
        "body": "The response *is* a Draft 2020-12 document; BP02 requires it self-contained and unmodified",
        ".properties.definitions.anyOf[0]": "JSON Schema definitions are schema-language, not an S1 model",
    },
    "GET /api/v1/projects/{project_id}/blueprint/entries": {
        ".properties.entries.items": "Section JSON as S2 stores it; BP04 asks S1 to preserve it plus ``_section``/``_path`` verbatim"
    },
    "GET /api/v1/projects/{project_id}/blueprint/draft": {
        ".properties.document.anyOf[0]": "S5IF-BP05 【裁决】: S1 forwards draft metadata only; entry content goes through ``entries?source=DRAFT``"
    },
    "GET /api/v1/projects/{project_id}/inputs/{artifact_id}/column-mapping": {
        ".properties.tablePlans.anyOf[0].items": "S3 has not exported TablePlan yet (S5IF-UP07); declared for when it does"
    },
    "GET /api/v1/workflows/{workflow_id}/agent-runs/{run_id}": {
        ".properties.budgetStatus": "S3's budget rollup, whose ``state`` vocabulary is S3's (AI-BUD-*)",
        ".properties.rejectedArtifacts.items": "Same S3 refusal records as the parse response",
    },
    "GET /api/v1/governance/evaluations/{eval_run_id}": {
        ".properties.summary.anyOf[0]": (
            "S3's own tally of the run (``ai.eval_run.summary``): it declares the field a "
            "mapping because the runner adds whatever the set produced, and S1 forwards it "
            "unchanged rather than guessing keys S3 may stop sending (AI-EVAL-001)"
        )
    },
}


def test_every_json_success_response_is_a_named_component(document: dict[str, Any]) -> None:
    """S5IF-G01 / AC01: no 2xx body may be free-form, inline or ``additionalProperties: true``."""
    untyped: list[str] = []
    undocumented: list[str] = []
    for path, method, operation in _operations(document):
        if method == "options" or not path.startswith("/api/v1"):
            continue
        key = f"{method.upper()} {path}"
        for code, response in (operation.get("responses") or {}).items():
            if not str(code).startswith(("2", "3")):
                continue
            content = (response or {}).get("content") or {}
            for media, body in content.items():
                if media != "application/json":
                    continue
                schema = body.get("schema") or {}
                if not _named(schema):
                    untyped.append(f"{key} -> {code} (inline)")
                    continue
                for where in _open_objects(_resolve(document, schema)):
                    if where.split(".")[-1].split("[")[0] in OPEN_FIELDS_OK:
                        continue
                    reason = OPEN_FIELD_EXEMPTIONS.get(key, {}).get(where)
                    if reason is None:
                        undocumented.append(f"{key} -> {code} at {where}")
    assert not untyped, f"responses still free-form: {untyped}"
    assert not undocumented, (
        f"open response fields with no registered reason: {undocumented} — either give the "
        "field a model or add it to OPEN_FIELD_EXEMPTIONS with why S1 cannot type it"
    )


def test_open_field_register_has_no_stale_entries(document: dict[str, Any]) -> None:
    """A field that has since been typed must leave the register (S5IF-AC01)."""
    live: set[tuple[str, str]] = set()
    for path, method, operation in _operations(document):
        if method == "options" or not path.startswith("/api/v1"):
            continue
        key = f"{method.upper()} {path}"
        for code, response in (operation.get("responses") or {}).items():
            if not str(code).startswith(("2", "3")):
                continue
            for media, body in ((response or {}).get("content") or {}).items():
                if media != "application/json":
                    continue
                schema = body.get("schema") or {}
                if not _named(schema):
                    continue
                for where in _open_objects(_resolve(document, schema)):
                    live.add((key, where))
    registered = {(key, where) for key, fields in OPEN_FIELD_EXEMPTIONS.items() for where in fields}
    assert registered == live, f"register drift: unregistered={sorted(live - registered)} stale={sorted(registered - live)}"


def test_every_json_request_body_is_a_named_component(document: dict[str, Any]) -> None:
    """S5IF-G02: a free dict body means the provider raises, and S5 cannot map the code."""
    free_form: list[str] = []
    for path, method, operation in _operations(document):
        body = (operation.get("requestBody") or {}).get("content", {}).get("application/json")
        if body is None:
            continue
        schema = _resolve(document, body.get("schema") or {})
        if not schema or schema.get("additionalProperties") is True and not schema.get("properties"):
            free_form.append(f"{method.upper()} {path}")
    assert not free_form, f"request bodies still free-form: {free_form}"


def test_no_snake_case_property_anywhere(document: dict[str, Any]) -> None:
    """CP-AC-12 / S5IF-AC02 and AC18: the whole wire surface is camelCase."""

    def walk(node: Any, where: str) -> list[str]:
        found: list[str] = []
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("properties",):
                    for name, child in (value or {}).items():
                        if "_" in name:
                            found.append(f"{where}.{name}")
                        found += walk(child, f"{where}.{name}")
                elif key in ("$defs", "components"):
                    continue
                else:
                    found += walk(value, where)
        elif isinstance(node, list):
            for item in node:
                found += walk(item, where)
        return found

    schemas = document["components"]["schemas"]
    offenders = sorted({o for name, schema in schemas.items() for o in walk(schema, name)})
    assert not offenders, f"snake_case leaked into the wire model: {offenders}"


def test_cross_cutting_headers_are_declared_and_referenced(document: dict[str, Any]) -> None:
    """S5IF-G05: a generated client must know to send X-Operator, not learn it by word of mouth."""
    parameters = document["components"].get("parameters") or {}
    assert {
        "XOperator",
        "XCsrfToken",
        "XIdempotencyKey",
        "XAcceptLanguage",
        "XCorrelationId",
        "XTraceparent",
    } <= set(parameters)
    missing_operator: list[str] = []
    missing_csrf: list[str] = []
    csrf_on_a_read: list[str] = []
    for path, method, operation in _operations(document):
        refs = {
            p.get("$ref", "").split("/")[-1] for p in (operation.get("parameters") or []) if isinstance(p, dict)
        }
        if method not in MUTATING:
            if "XCsrfToken" in refs:
                csrf_on_a_read.append(f"{method.upper()} {path}")
            continue
        if "XOperator" not in refs:
            missing_operator.append(f"{method.upper()} {path}")
        if "XCsrfToken" not in refs:
            missing_csrf.append(f"{method.upper()} {path}")
    assert not missing_operator, f"state-changing operations without a declared X-Operator: {missing_operator}"
    # FR-SEC-005: the middleware demands the value on exactly these calls, so exactly these calls
    # have to say so — and a read must not advertise a header the guard never checks.
    assert not missing_csrf, f"state-changing operations without a declared X-CSRF-Token: {missing_csrf}"
    assert not csrf_on_a_read, f"reads declaring a CSRF header nothing checks: {csrf_on_a_read}"


def test_every_operation_says_how_it_is_authenticated(document: dict[str, Any]) -> None:
    """FR-SEC-001: every API requires authentication, so the contract names the credential.

    Only the paths that *make* the session are allowed to clear it; anything else with an empty
    ``security`` would be an endpoint the guard signs nothing in for — which is how a route ends up
    quietly public.
    """
    from factory_control.rest.middleware import UNAUTHENTICATED_PATHS, SessionMiddleware
    from factory_control.rest.openapi import security_schemes

    public = frozenset(SessionMiddleware.PUBLIC_PATHS) | frozenset(UNAUTHENTICATED_PATHS)
    schemes = document["components"].get("securitySchemes") or {}
    assert schemes == security_schemes(), "the served schemes drifted from the declared ones"
    assert {"sessionCookie", "bearerToken", "csrfCookie"} <= set(schemes)
    assert document.get("security") == [{"sessionCookie": []}, {"bearerToken": []}]

    cleared: list[str] = []
    unnamed: list[str] = []
    for path, method, operation in _operations(document):
        if not path.startswith("/api/v1"):
            continue
        if path in public:
            if operation.get("security") != []:
                unnamed.append(f"{method.upper()} {path} (a sign-in endpoint demands a session)")
            continue
        if operation.get("security") == []:
            cleared.append(f"{method.upper()} {path}")
    assert not cleared, f"operations declaring no credential while the guard is on: {cleared}"
    assert not unnamed, f"public operations that still demand a credential: {unnamed}"


def test_the_csrf_names_match_the_middleware(document: dict[str, Any]) -> None:
    """A document naming a cookie the response never sets is worse than naming none."""
    from factory_control.config import Settings

    settings = Settings(provider_mode="fakes", workflow_engine="inprocess", database_url="sqlite://")
    schemes = document["components"]["securitySchemes"]
    assert schemes["sessionCookie"]["name"] == settings.cookie_name
    assert schemes["csrfCookie"]["name"] == settings.csrf_cookie_name
    assert (
        document["components"]["parameters"]["XCsrfToken"]["name"] == settings.csrf_header
    ), "the header the document names is not the header the middleware checks"


#: SRS 8.1's own ``需求`` column, the rows that name a numbered ``CP-*`` requirement.
#: An endpoint that does not cite the requirement the table says it implements is a
#: traceability hole CP-AC-25 is meant to catch.
_SRS_8_1_ROWS: list[tuple[str, str, tuple[str, ...]]] = [
    ("get", "/api/v1/projects/{project_id}/effective-policy", ("CP-UI-006",)),
    ("get", "/api/v1/projects/{project_id}/target-environments", ("CP-PRJ-002", "CP-PRJ-004")),
    ("post", "/api/v1/projects/{project_id}/upgrade-mode", ("CP-PRJ-005",)),
    ("post", "/api/v1/projects/{project_id}/archive", ("CP-PRJ-006",)),
    ("get", "/api/v1/projects/{project_id}/inputs", ("CP-API-009", "CP-UI-007")),
    ("get", "/api/v1/projects/{project_id}/workflow", ("CP-UI-002", "CP-UI-019")),
    ("get", "/api/v1/workflows/{workflow_id}/allowed-actions", ("CP-UI-019",)),
    ("get", "/api/v1/workflows/{workflow_id}/stages", ("CP-UI-008",)),
    ("post", "/api/v1/stages/{stage_id}/patches", ("CP-UI-018",)),
    ("get", "/api/v1/workflows/{workflow_id}/agent-runs/{run_id}", ("CP-UI-007",)),
    ("get", "/api/v1/deployment-runs", ("CP-UI-011",)),
    ("get", "/api/v1/deployment-runs/{run_id}/summary", ("CP-UI-012",)),
    ("post", "/api/v1/deployment-runs/{run_id}/drift-check", ("CP-UI-013",)),
    ("post", "/api/v1/deployment-runs/{run_id}/commit", ("CP-API-014",)),
    ("get", "/api/v1/deployment-runs/{run_id}/environment-bindings", ("CP-UI-005",)),
    ("get", "/api/v1/questions/{question_id}", ("CP-UI-015",)),
    ("get", "/api/v1/gates", ("CP-UI-010",)),
    ("get", "/api/v1/gates/{gate_id}/explanation", ("CP-UI-014",)),
    ("get", "/api/v1/artifacts/{artifact_id}/lineage", ("CP-UI-016",)),
    ("get", "/api/v1/workflows/{workflow_id}/validation-issues", ("CP-UI-004",)),
    ("get", "/api/v1/projects/{project_id}/blueprint/section-schemas/{section}", ("CP-UI-003",)),
    ("get", "/api/v1/projects/{project_id}/registry", ("CP-UI-002",)),
    ("get", "/api/v1/workflows/{workflow_id}/object-graph", ("CP-UI-020",)),
    ("get", "/api/v1/projects/{project_id}/frontend-package/files", ("CP-UI-021",)),
    ("get", "/api/v1/projects/{project_id}/frontend-package/files/{path}", ("CP-UI-021",)),
    ("get", "/api/v1/projects/{project_id}/package-files", ("CP-UI-002",)),
    ("get", "/api/v1/environments", ("CP-UI-001",)),
    ("get", "/api/v1/governance/policy", ("CP-UI-006",)),
    ("get", "/api/v1/settings", ("CP-UI-017",)),
]

#: The six step-starting endpoints CP-API-012 fixes to 202 with one acceptance envelope.
_ACCEPTANCE_OPERATIONS = [
    ("post", "/api/v1/projects/{project_id}/inputs/{artifact_id}/parse"),
    ("post", "/api/v1/deployment-runs/{run_id}/drift-check"),
    ("post", "/api/v1/governance/evaluations"),
    ("get", "/api/v1/projects/{project_id}/export"),
    ("post", "/api/v1/projects/import"),
    ("post", "/api/v1/deployment-runs/{run_id}/commit"),
    ("post", "/api/v1/stages/{stage_id}/run"),
    ("post", "/api/v1/stages/{stage_id}/rerun"),
]


def test_every_operation_documents_the_shared_failure(document: dict[str, Any]) -> None:
    """CP-API-008: a generated client can type the failure of *any* call.

    The four codes every operation can answer with — 400 without ``X-Operator``, 404, 409
    and 422 — all point at ``components/responses/Error``, so no handler has to remember
    to declare them and no route quietly omits one.
    """
    for path, method, operation in _operations(document):
        if method == "options" or not path.startswith("/api/v1"):
            continue
        responses = operation.get("responses") or {}
        for code in ("400", "404", "409", "422"):
            entry = responses.get(code)
            assert entry == {"$ref": "#/components/responses/Error"}, f"{method.upper()} {path} -> {code}: {entry}"


def test_the_srs_endpoint_table_names_every_operation(document: dict[str, Any]) -> None:
    """CP-API-010 ③ / CP-AC-25: the SRS's own requirement numbers reach the document."""
    for method, path, expected in _SRS_8_1_ROWS:
        operation = document["paths"][path][method]
        tags = set(operation.get("x-requirements") or [])
        missing = [item for item in expected if item not in tags]
        assert not missing, f"{method.upper()} {path} does not cite {missing}: {sorted(tags)}"


def test_step_endpoints_are_accepted_with_one_envelope(document: dict[str, Any]) -> None:
    """CP-API-012: 202, the same named component, and no engine or synchronous result."""
    for method, path in _ACCEPTANCE_OPERATIONS:
        operation = document["paths"][path][method]
        key = f"{method.upper()} {path}"
        assert "202" in operation["responses"], f"{key} is not a 202: {sorted(operation['responses'])}"
        assert "CP-API-012" in (operation.get("x-requirements") or []), key
        schema = operation["responses"]["202"]["content"]["application/json"]["schema"]
        ref = schema.get("$ref", "")
        assert ref.split("/")[-1] == "AcceptanceView", f"{key} answers with {ref or schema}"
        resolved = _resolve(document, schema)
        properties = set(resolved["properties"])
        assert properties == {"stageId", "stepRunId", "accepted"}, f"{key}: {sorted(properties)}"
        assert "engine" not in properties


def test_group_requirement_register_names_live_paths(document: dict[str, Any]) -> None:
    """A template that matches no route is a register entry that stopped meaning anything."""
    from factory_control.rest.openapi import GROUP_REQUIREMENTS

    live = {path for path in document["paths"]}
    dead: list[str] = []
    for template, _ids in GROUP_REQUIREMENTS:
        base = template[:-2] if template.endswith("/*") else template
        if not any(path == base or path.startswith(template[:-1]) for path in live):
            dead.append(template)
    assert not dead, f"GROUP_REQUIREMENTS templates with no route: {dead}"


def test_every_operation_points_back_at_a_requirement(document: dict[str, Any]) -> None:
    """S5IF-AC15 / CP-AC-25: ``x-requirements`` carries both catalogues, in one generic field."""
    missing: list[str] = []
    no_cp: list[str] = []
    for path, method, operation in _operations(document):
        if method == "options" or not path.startswith("/api/v1"):
            continue
        tags = operation.get("x-requirements") or []
        if "x-s5if" in operation:
            missing.append(f"{method.upper()} {path} (still tagged x-s5if)")
        elif not tags:
            missing.append(f"{method.upper()} {path}")
        elif not any(str(tag).startswith("CP-") for tag in tags):
            no_cp.append(f"{method.upper()} {path}")
    assert not missing, f"operations with no x-requirements tag: {missing}"
    assert not no_cp, f"operations that cite no S1 requirement: {no_cp}"


def test_response_headers_are_declared(document: dict[str, Any]) -> None:
    """S5IF-G05: the replay headers the middleware sets have to be on the operation too."""
    headers = document["components"].get("headers") or {}
    assert {"XCorrelationId", "XIdempotentReplay", "XIdempotencyStored"} <= set(headers)

    from factory_control.rest.openapi import _honours_idempotency

    missing: list[str] = []
    replaying: set[str] = set()
    for path, method, operation in _operations(document):
        if method == "options" or not path.startswith("/api/v1"):
            continue
        if not _honours_idempotency(operation):
            continue
        key = f"{method.upper()} {path}"
        replaying.add(key)
        for code, response in (operation.get("responses") or {}).items():
            if not str(code).startswith("2") or not (response or {}).get("content"):
                continue
            declared = set((response or {}).get("headers") or {})
            if "X-Idempotent-Replay" not in declared:
                missing.append(f"{key} -> {code}")
    assert not missing, f"2xx responses with no declared replay header: {missing}"
    # The 202 endpoints (CP-API-012) are create/execute requests too, so they must sit in
    # that group or a client would replay one that never advertised a replay.
    expected = {
        "POST /api/v1/deployment-runs/{run_id}/commit",
        "POST /api/v1/deployment-runs/{run_id}/drift-check",
        "POST /api/v1/projects/{project_id}/inputs/{artifact_id}/parse",
        "POST /api/v1/projects/import",
        "POST /api/v1/governance/evaluations",
    }
    absent = sorted(expected - replaying)
    assert not absent, f"step endpoints the document does not mark idempotent: {absent}"


def test_requirement_register_has_no_stale_entries(document: dict[str, Any]) -> None:
    """A per-operation register rots: every key must still name a live operation."""
    from factory_control.rest.openapi import LEGACY_REQUIREMENTS

    live = {f"{method.upper()} {path}" for path, method, _ in _operations(document)}
    stale = sorted(set(LEGACY_REQUIREMENTS) - live)
    assert not stale, f"LEGACY_REQUIREMENTS entries with no operation: {stale}"


def test_error_envelope_is_one_component(document: dict[str, Any]) -> None:
    responses = document["components"].get("responses") or {}
    assert "Error" in responses
    schema = responses["Error"]["content"]["application/json"]["schema"]
    assert set(schema["required"]) >= {"errorCode", "message", "retryable", "correlationId", "details"}
    # S5IF-G07.3: the per-code shape of ``details`` is part of the contract, so it is
    # a component with named fields rather than an open object.
    details = _resolve(document, schema["properties"]["details"])
    assert "$ref" in schema["properties"]["details"], "details must be a named component"
    names = set(details["properties"])
    assert {"gateId", "mismatches", "environmentKey", "lockReason", "windows"} <= names
    assert {"maxBytes", "allowedTypes", "requiredEvidence", "environmentTimezone"} <= names
    # SRS 8.3's other rows: the state shape, the lock timestamp, and a typed field entry.
    assert {"currentState", "requiredStates", "targetId", "lockChangedAt", "scope"} <= names
    mismatch = _resolve(document, _resolve(document, details["properties"]["mismatches"])["items"])
    assert set(mismatch["required"]) == {"refType", "refId", "expectedHash", "actualHash"}
    field = _resolve(document, _resolve(document, details["properties"]["fields"])["items"])
    assert set(field["required"]) == {"path", "message"}


def test_document_version_and_servers(document: dict[str, Any]) -> None:
    """S5IF-G11: semver with the breaking changes recorded, and no host pinned."""
    assert document["info"]["version"] == "1.19.0"
    assert document.get("servers") == []
    assert "servers" in document["info"]["description"]


def test_the_published_and_vendored_documents_are_all_the_current_one(document: dict[str, Any]) -> None:
    """Every copy of the contract on disk is the document the app serves right now.

    Re-exporting is a command nobody remembers after editing a route, and both consoles generate
    their types from *their* copy — so the drift surfaces far from its cause, as a generated field
    the server never sends.  Asserting the three files against the live document turns that into a
    failed test at the commit that caused it.
    """
    published = S1_ROOT / "docs" / "openapi.json"
    assert json.loads(published.read_text(encoding="utf-8")) == document, (
        "docs/openapi.json is stale — run `python scripts/export_openapi.py --out docs/openapi.json`"
    )
    for console in ("S5", "S5M"):
        vendored = S1_ROOT.parent / console / "contract" / "openapi.json"
        assert json.loads(vendored.read_text(encoding="utf-8")) == document, (
            f"{console}/contract/openapi.json is stale — copy docs/openapi.json over it and re-run codegen"
        )


def test_components_helper_is_the_single_definition(document: dict[str, Any]) -> None:
    """The served document and ``components()`` must not drift apart."""
    for name in components()["parameters"]:
        assert name in document["components"]["parameters"]
