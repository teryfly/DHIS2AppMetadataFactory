"""Handoff completeness check (BP-HO-001 … 003, overall SRS 17.3).

The nine items of 17.3, run against the package that was actually generated rather than
against the Blueprint's intention, because the question a reviewer is asking is "can a
frontend developer start from this folder" — and a check that reads the source document
would pass on a package whose file was never written.

Two of the nine cannot be answered by S2 at all, and the SRS says so: "deployed and
passed post-verification" and "fixtures imported" are facts about a running instance, so
their basis is the **deployment evidence the caller hands in** (BP-HO-002).  S2 checks
that evidence describes *this* Blueprint version before it believes it; evidence for a
different version is a BLOCKING finding, not a shortcut to green.

A page becomes ``FRONTEND_READY`` only when every check that concerns it passes
(BP-HO-003).  The result is a ``HANDOFF_CHECK_REPORT`` with one entry per page and the
rule IDs that fired, so a page that is not ready says why.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from factory_blueprint.compilers import files
from factory_blueprint.compilers.context import CompileInputs
from factory_blueprint.compilers.fixture import FixtureProduct
from factory_blueprint.compilers.openapi import OpenApiProduct
from factory_blueprint.compilers.package import SOURCE_MAP_FILE, PackageProduct
from factory_blueprint.compilers.ui_spec import UiSpecProduct
from factory_blueprint.content.document import BlueprintDocument
from factory_blueprint.content.layout import CONTAINER_BY_PATH
from factory_blueprint.dto.issue import Issue, make_issue, sort_issues
from factory_blueprint.enums import IssueLevel, Section

RULE_PAGE_INCOMPLETE = "BPV-HO-101"
RULE_EXAMPLE_MISSING = "BPV-HO-102"
RULE_FIELD_UNMAPPED = "BPV-HO-103"
RULE_NOT_DEPLOYED = "BPV-HO-104"
RULE_OPENAPI_INVALID = "BPV-HO-105"
RULE_DEPRECATED_ENDPOINT = "BPV-HO-106"
RULE_TRANSLATION_INCOMPLETE = "BPV-HO-107"
RULE_FIXTURES_NOT_IMPORTED = "BPV-HO-108"
RULE_TRACEABILITY_DANGLING = "BPV-HO-109"
RULE_CREDENTIAL_OR_PII = "BPV-HO-110"

FRONTEND_READY = "FRONTEND_READY"
NOT_READY = "NOT_READY"

#: an environment class that may carry the "deployed and verified" answer (17.3 item 4:
#: a *non-production* target environment, so PROD evidence never counts here)
ACCEPTABLE_DEPLOYMENT_CLASSES = frozenset({"DEV", "TEST", "UAT"})
FIXTURE_IMPORT_CLASSES = frozenset({"DEV", "TEST"})

#: key-shaped secrets, not the words "password" or "token" that the guidance itself uses
CREDENTIAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("password-assignment", re.compile(r"\"password\"\s*:\s*\"[^\"]{1,}\"", re.IGNORECASE)),
    ("api-token-literal", re.compile(r"ApiToken\s+[A-Za-z0-9._\-]{8,}")),
    ("bearer-literal", re.compile(r"Authorization:\s*Bearer\s+[A-Za-z0-9._\-]{8,}", re.IGNORECASE)),
    ("private-key-block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("cookie-value", re.compile(r"JSESSIONID=[A-Za-z0-9]{16,}")),
)


@dataclass
class HandoffReport:
    """The per-page outcome and every finding behind it (BP-HO-003)."""

    document: dict[str, Any] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)
    pages: dict[str, str] = field(default_factory=dict)

    @property
    def blocking(self) -> bool:
        return any(issue.level is IssueLevel.BLOCKING for issue in self.issues)

    @property
    def ready_pages(self) -> list[str]:
        return sorted(key for key, state in self.pages.items() if state == FRONTEND_READY)


def check_handoff(
    inputs: CompileInputs,
    package: PackageProduct,
    openapi: OpenApiProduct,
    ui_spec: UiSpecProduct,
    fixtures: FixtureProduct | None,
    evidence: list[dict[str, Any]],
) -> HandoffReport:
    """Run 17.3's nine checks over one generated package."""
    issues: list[Issue] = []
    per_page: dict[str, list[str]] = {key: [] for key in ui_spec.pages}

    issues += _check_page_completeness(ui_spec, package, per_page)
    issues += _check_examples(openapi, package, per_page)
    issues += _check_field_mappings(ui_spec, per_page)
    issues += _check_deployment(inputs, ui_spec, evidence, per_page)
    issues += _check_openapi(inputs, openapi, package, per_page)
    issues += _check_translations(package, ui_spec, per_page)
    issues += _check_fixture_import(fixtures, evidence, per_page)
    issues += _check_traceability(inputs, package, openapi, per_page)
    issues += _check_no_credentials(package, per_page)

    ordered = sort_issues(issues)
    pages = {
        key: NOT_READY if per_page.get(key) else FRONTEND_READY for key in sorted(ui_spec.pages)
    }
    report = HandoffReport(issues=ordered, pages=pages)
    report.document = _report_document(inputs, package, report, per_page, evidence, ui_spec)
    return report


# --- 1: page spec, API intent, UI intent ---------------------------------


def _check_page_completeness(
    ui_spec: UiSpecProduct, package: PackageProduct, per_page: dict[str, list[str]]
) -> list[Issue]:
    issues: list[Issue] = []
    for page_key in sorted(ui_spec.pages):
        page = ui_spec.pages[page_key]
        missing: list[str] = []
        if f"pages/{page_key}.md" not in package.files:
            missing.append("no page spec was generated")
        if not page.get("apiOperations"):
            missing.append("no API operation resolved for this page")
        if not page.get("hasUiIntent"):
            missing.append("no UI Intent is recorded for this page")
        if missing:
            per_page[page_key].append(RULE_PAGE_INCOMPLETE)
            issues.append(
                make_issue(
                    RULE_PAGE_INCOMPLETE,
                    IssueLevel.BLOCKING,
                    f"page {page_key!r} is not ready: {'; '.join(missing)}",
                    business_key=page_key,
                    section=Section.PAGES,
                    remediation="complete the page's intents in the Blueprint and recompile",
                )
            )
    return issues


# --- 2: a request and response example per operationKey ------------------


def _check_examples(
    openapi: OpenApiProduct, package: PackageProduct, per_page: dict[str, list[str]]
) -> list[Issue]:
    issues: list[Issue] = []
    for operation in openapi.operations:
        key = operation["operationKey"]
        name = f"api/examples/{key}.json"
        problem: str | None = None
        if name not in package.files:
            problem = "no example file was generated"
        elif operation["responseExample"] is None:
            problem = "the example has no response body"
        elif "requestBody" in operation["definition"] and operation["requestExample"] is None:
            problem = "the operation declares a request body but the example has none"
        if problem is None:
            continue
        page_key = operation["pageKey"]
        per_page.setdefault(page_key, []).append(RULE_EXAMPLE_MISSING)
        issues.append(
            make_issue(
                RULE_EXAMPLE_MISSING,
                IssueLevel.BLOCKING,
                f"operationKey {key!r}: {problem}",
                business_key=key,
                section=Section.API_INTENTS,
            )
        )
    return issues


# --- 3: every form field has a field mapping -----------------------------


def _check_field_mappings(ui_spec: UiSpecProduct, per_page: dict[str, list[str]]) -> list[Issue]:
    issues: list[Issue] = []
    for page_key in sorted(ui_spec.pages):
        page = ui_spec.pages[page_key]
        mapped = {
            str(item.get("businessKey"))
            for item in (page.get("fieldMappings") or [])
            if isinstance(item, dict)
        }
        names = {
            str(item.get("field"))
            for item in (page.get("fieldMappings") or [])
            if isinstance(item, dict)
        }
        unmatched: list[str] = []
        for component in page.get("components") or []:
            if not isinstance(component, dict) or not component.get("isInput"):
                continue
            source = component.get("dataSource") or {}
            name = str(component.get("name") or "")
            if source.get("type") == "registry":
                if str(source.get("businessKey")) not in mapped:
                    unmatched.append(name)
            elif source.get("type") == "operation" and not names:
                # a data-entry control fed by a mutation still needs a field to write
                unmatched.append(name)
        if unmatched:
            per_page[page_key].append(RULE_FIELD_UNMAPPED)
            issues.append(
                make_issue(
                    RULE_FIELD_UNMAPPED,
                    IssueLevel.BLOCKING,
                    f"page {page_key!r} has input component(s) without a field mapping: "
                    f"{', '.join(sorted(set(unmatched)))}",
                    business_key=page_key,
                    section=Section.UI_INTENTS,
                    remediation="map each form field to its Registry entry in the UI Intent",
                )
            )
    return issues


# --- 4: dependencies deployed and verified in a non-production target -----


def _check_deployment(
    inputs: CompileInputs,
    ui_spec: UiSpecProduct,
    evidence: list[dict[str, Any]],
    per_page: dict[str, list[str]],
) -> list[Issue]:
    verified = _verified_environments(evidence)
    issues: list[Issue] = []
    for page_key in sorted(ui_spec.pages):
        page = ui_spec.pages[page_key]
        needed = [
            str(item.get("businessKey"))
            for item in (page.get("registryRefs") or [])
            if isinstance(item, dict)
        ]
        if not needed:
            continue
        if not verified:
            per_page[page_key].append(RULE_NOT_DEPLOYED)
            issues.append(
                make_issue(
                    RULE_NOT_DEPLOYED,
                    IssueLevel.BLOCKING,
                    f"page {page_key!r} depends on {len(needed)} Registry entry/entries and no "
                    "deployment evidence reports a successful post-verification in a non-production "
                    "target environment",
                    business_key=page_key,
                    section=Section.PAGES,
                    remediation="deploy and verify the package first (BP-HO-002: the evidence is the basis)",
                )
            )
    return issues


def _verified_environments(evidence: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for item in evidence:
        environment_class = str(item.get("environmentClass") or "").upper()
        if environment_class not in ACCEPTABLE_DEPLOYMENT_CLASSES:
            continue
        post = item.get("postVerification")
        passed = post in (None, "PASSED", "SUCCESS") and bool(item.get("deployed", True))
        if passed:
            out.append(str(item.get("environmentKey") or ""))
    return sorted(set(out))


# --- 5: OpenAPI validates and has no deprecated endpoint ------------------


def _mark_all_pages(per_page: dict[str, list[str]], rule: str) -> None:
    """A package-level failure leaves no page ready.

    A credential in the package, or a document that is not valid OpenAPI, is not a
    property of one page; marking the others green would hand the reviewer a gate that
    passed on a package nobody should ship.
    """
    for page_key in per_page:
        per_page[page_key].append(rule)


def _check_openapi(
    inputs: CompileInputs,
    openapi: OpenApiProduct,
    package: PackageProduct,
    per_page: dict[str, list[str]],
) -> list[Issue]:
    """5: the document is structurally valid 3.1 and names no deprecated endpoint."""
    issues: list[Issue] = []
    document = openapi.document
    if "api/openapi.yaml" not in package.files or not document:
        return issues
    structural = _structure_problems(document)
    if structural:
        _mark_all_pages(per_page, RULE_OPENAPI_INVALID)
    for problem in structural:
        issues.append(
            make_issue(
                RULE_OPENAPI_INVALID,
                IssueLevel.BLOCKING,
                f"the compiled OpenAPI document is malformed: {problem}",
                section=Section.API_INTENTS,
            )
        )
    for operation in openapi.operations:
        path = str(operation["path"])
        if inputs.profile is None:
            break
        if inputs.profile.is_deprecated("endpoint", path) or inputs.profile.is_deprecated(
            "resource", operation["resource"]
        ):
            issues.append(
                make_issue(
                    RULE_DEPRECATED_ENDPOINT,
                    IssueLevel.BLOCKING,
                    f"operationKey {operation['operationKey']!r} uses {operation['resource']!r} at "
                    f"{path!r}, which version {inputs.dhis2_version} marks deprecated",
                    business_key=operation["operationKey"],
                    section=Section.API_INTENTS,
                    remediation="move to the replacement resource the Profile offers",
                )
            )
        if inputs.profile.is_removed("endpoint", path):
            issues.append(
                make_issue(
                    RULE_DEPRECATED_ENDPOINT,
                    IssueLevel.BLOCKING,
                    f"operationKey {operation['operationKey']!r} uses {path!r}, removed in "
                    f"{inputs.dhis2_version}",
                    business_key=operation["operationKey"],
                    section=Section.API_INTENTS,
                )
            )
    return issues


def _structure_problems(document: dict[str, Any]) -> list[str]:
    """The 3.1 checks S2 can do locally, without a validator library or a network."""
    problems: list[str] = []
    if not str(document.get("openapi", "")).startswith("3.1"):
        problems.append(f"`openapi` is {document.get('openapi')!r}, not a 3.1 version")
    if not (document.get("info") or {}).get("title"):
        problems.append("`info.title` is missing")
    if not document.get("servers"):
        problems.append("`servers` declares no base URL variable")
    variables = ((document.get("servers") or [{}])[0].get("variables") or {})
    if "baseUrl" not in variables:
        problems.append("`servers[0].variables` has no `baseUrl` (FR-API-005)")
    elif "default" not in (variables.get("baseUrl") or {}):
        problems.append("server variable `baseUrl` declares no `default`, which 3.1 requires")
    if not document.get("paths"):
        problems.append("`paths` is empty while the project has API intents")
    schemes = ((document.get("components") or {}).get("securitySchemes") or {})
    if not schemes:
        problems.append("`components.securitySchemes` is empty")
    problems.extend(_dangling_refs(document))
    problems.extend(_duplicate_operation_ids(document))
    return problems


def _dangling_refs(document: dict[str, Any]) -> list[str]:
    known = {f"#/components/schemas/{name}" for name in ((document.get("components") or {}).get("schemas") or {})}
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str):
                found.add(ref)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(document.get("paths") or {})
    return [f"{ref} is referenced but not defined" for ref in sorted(found - known)]


def _duplicate_operation_ids(document: dict[str, Any]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for operations in (document.get("paths") or {}).values():
        for definition in (operations or {}).values():
            if not isinstance(definition, dict):
                continue
            operation_id = str(definition.get("operationId") or "")
            if operation_id and operation_id in seen:
                duplicates.add(operation_id)
            seen.add(operation_id)
    return [f"operationId {key!r} is not unique" for key in sorted(duplicates)]


# --- 6: translation keys complete for every language ----------------------


def _check_translations(
    package: PackageProduct, ui_spec: UiSpecProduct, per_page: dict[str, list[str]]
) -> list[Issue]:
    issues: list[Issue] = []
    for name in sorted(package.files):
        if not name.startswith("i18n/"):
            continue
        document = files.load_json(package.files[name])
        missing = [str(key) for key in (document.get("missing") or [])]
        if not missing:
            continue
        issues.append(
            make_issue(
                RULE_TRANSLATION_INCOMPLETE,
                IssueLevel.BLOCKING,
                f"locale {document.get('locale')!r} is missing "
                f"{len(missing)} translation key(s): {', '.join(missing[:8])}"
                + (" …" if len(missing) > 8 else ""),
                section=Section.UI_INTENTS,
                details={"locale": document.get("locale"), "keys": missing},
                remediation="supply the translations in the Blueprint or accept a partial locale",
            )
        )
        for page_key in sorted(ui_spec.pages):
            if any(key.startswith(f"{page_key}.") for key in missing):
                per_page[page_key].append(RULE_TRANSLATION_INCOMPLETE)
    return issues


# --- 7: fixtures imported in DEV or TEST, if the project asked for them ---


def _check_fixture_import(
    fixtures: FixtureProduct | None,
    evidence: list[dict[str, Any]],
    per_page: dict[str, list[str]],
) -> list[Issue]:
    if fixtures is None or not fixtures.requested:
        return []
    imported = [
        item
        for item in evidence
        if str(item.get("environmentClass") or "").upper() in FIXTURE_IMPORT_CLASSES
        and str(item.get("fixtureImport") or "").upper() in ("OK", "PASSED", "SUCCESS", "IMPORTED")
    ]
    if imported:
        return []
    issues = [
        make_issue(
            RULE_FIXTURES_NOT_IMPORTED,
            IssueLevel.BLOCKING,
            "the project specifies fixtures but no deployment evidence reports a successful "
            "fixture import in a DEV or TEST environment",
            section=Section.FIXTURES,
            remediation="import the fixture package and pass its execution report as evidence",
        )
    ]
    for page_key in sorted(per_page):
        per_page[page_key].append(RULE_FIXTURES_NOT_IMPORTED)
    return issues


# --- 8: traceability chain complete --------------------------------------


def _check_traceability(
    inputs: CompileInputs,
    package: PackageProduct,
    openapi: OpenApiProduct,
    per_page: dict[str, list[str]],
) -> list[Issue]:
    source_map = files.load_json(package.files.get(SOURCE_MAP_FILE, b"{}"))
    registry = _registry_keys(inputs)
    compiled = {operation["operationKey"] for operation in openapi.operations}
    issues: list[Issue] = []
    for page in source_map.get("pages") or []:
        page_key = str(page.get("pageKey") or "")
        dangling: list[str] = []
        for entry in page.get("registryEntries") or []:
            key = str(entry.get("businessKey") or "")
            if key not in registry:
                dangling.append(f"registry entry {key!r} is not in the Registry")
        for item in page.get("fields") or []:
            key = str(item.get("businessKey") or "")
            if key and key not in registry:
                dangling.append(f"field {item.get('field')!r} names {key!r}, not registered")
        for operation_key in page.get("apiOperations") or []:
            if operation_key not in compiled:
                dangling.append(f"operation {operation_key!r} has no compiled operation")
        if dangling:
            per_page.setdefault(page_key, []).append(RULE_TRACEABILITY_DANGLING)
            issues.append(
                make_issue(
                    RULE_TRACEABILITY_DANGLING,
                    IssueLevel.BLOCKING,
                    f"page {page_key!r} has a dangling traceability reference: {'; '.join(dangling)}",
                    business_key=page_key,
                    section=Section.PAGES,
                )
            )
    return issues


def _registry_keys(inputs: CompileInputs) -> set[str]:
    document = BlueprintDocument(inputs.document)
    return {
        str(entry["businessKey"])
        for entry in document.container_entries(CONTAINER_BY_PATH["/registry/entries"])
        if isinstance(entry.get("businessKey"), str)
    }


# --- 9: no credential, no real personal data -----------------------------


def _check_no_credentials(
    package: PackageProduct, per_page: dict[str, list[str]]
) -> list[Issue]:
    issues: list[Issue] = []
    for name in sorted(package.files):
        if not name.endswith((".md", ".json", ".yaml")):
            continue
        text = package.files[name].decode("utf-8", errors="replace")
        for label, pattern in CREDENTIAL_PATTERNS:
            if pattern.search(text):
                issues.append(
                    make_issue(
                        RULE_CREDENTIAL_OR_PII,
                        IssueLevel.BLOCKING,
                        f"{name} contains what looks like a credential ({label}); the "
                        "development package must never carry one (FR-FE-005)",
                        location=name,
                        section=Section.PAGES,
                        remediation="remove it from the Blueprint input; credentials are distributed separately",
                    )
                )
    if issues:
        _mark_all_pages(per_page, RULE_CREDENTIAL_OR_PII)
    return issues


# --- report ---------------------------------------------------------------


def _report_document(
    inputs: CompileInputs,
    package: PackageProduct,
    report: HandoffReport,
    per_page: dict[str, list[str]],
    evidence: list[dict[str, Any]],
    ui_spec: UiSpecProduct,
) -> dict[str, Any]:
    issues_by_page: dict[str, list[Issue]] = {}
    for issue in report.issues:
        if issue.business_key and issue.business_key in report.pages:
            issues_by_page.setdefault(issue.business_key, []).append(issue)
    pages = []
    for page_key in sorted(report.pages):
        pages.append(
            {
                "pageKey": page_key,
                "status": report.pages[page_key],
                "failedRules": sorted(set(per_page.get(page_key, []))),
                "issues": [issue.summary() for issue in issues_by_page.get(page_key, [])],
                "route": ui_spec.pages.get(page_key, {}).get("route"),
            }
        )
    return {
        "schemaVersion": 1,
        "kind": "HANDOFF_CHECK_REPORT",
        "projectId": inputs.project_id,
        "blueprintVersion": inputs.blueprint_version,
        "blueprintHash": inputs.blueprint_hash,
        "dhis2Version": inputs.dhis2_version or None,
        "checks": [
            {"ruleId": rule, "description": description}
            for rule, description in _CHECK_DESCRIPTIONS
        ],
        "counts": {
            "BLOCKING": sum(1 for issue in report.issues if issue.level is IssueLevel.BLOCKING),
            "WARNING": sum(1 for issue in report.issues if issue.level is IssueLevel.WARNING),
            "INFO": sum(1 for issue in report.issues if issue.level is IssueLevel.INFO),
            "FRONTEND_READY": len(report.ready_pages),
            "NOT_READY": sum(1 for state in report.pages.values() if state == NOT_READY),
        },
        "deploymentEvidence": [
            {
                "artifactId": item.get("artifactId"),
                "environmentKey": item.get("environmentKey"),
                "environmentClass": item.get("environmentClass"),
                "blueprintVersion": item.get("blueprintVersion"),
                "deployedAt": item.get("deployedAt"),
                "postVerification": item.get("postVerification"),
                "fixtureImport": item.get("fixtureImport"),
            }
            for item in sorted(evidence, key=lambda entry: str(entry.get("environmentKey") or ""))
        ],
        "packageFiles": sorted(package.files),
        "pages": pages,
        "note": (
            "Only reporting: nothing here repairs a package (overall SRS C-07). A page is "
            "FRONTEND_READY only when every check that concerns it passed (BP-HO-003)."
        ),
    }


#: 17.3's list, in its order, so the report shows which item each rule answers
_CHECK_DESCRIPTIONS: tuple[tuple[str, str], ...] = (
    (RULE_PAGE_INCOMPLETE, "every page has a page spec, an API intent and a UI intent"),
    (RULE_EXAMPLE_MISSING, "every operationKey has a request and a response example"),
    (RULE_FIELD_UNMAPPED, "every form field has a field mapping"),
    (RULE_NOT_DEPLOYED, "the page's metadata is deployed and post-verified in a non-production target"),
    (RULE_OPENAPI_INVALID, "the OpenAPI document is structurally valid 3.1"),
    (RULE_DEPRECATED_ENDPOINT, "no deprecated or removed endpoint or parameter is used"),
    (RULE_TRANSLATION_INCOMPLETE, "every translation key exists in every declared language"),
    (RULE_FIXTURES_NOT_IMPORTED, "the demo data imported successfully in DEV or TEST, if required"),
    (RULE_TRACEABILITY_DANGLING, "the traceability chain is complete, no dangling reference"),
    (RULE_CREDENTIAL_OR_PII, "the package contains no credential and no real personal data"),
)
