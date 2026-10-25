"""Package Compiler (BP-CMP-044, overall SRS 17.1 / 17.2 / FR-FE-001…006).

Everything in the development package is generated from one approved Blueprint version
plus the products the other compilers already made, so a frontend developer receives:

* the OpenAPI document, its reference and one example per `operationKey`;
* the UI Specification and one page spec per page;
* the Registry in the spelling each target environment uses (`registry.json`, FR-FE-002);
* field mappings, option sets, program rules and the traceability chain;
* the synthetic fixtures and the demo-user specification;
* connection and debugging instructions with **no credential anywhere** (FR-FE-005).

Deployment status is not invented here.  S2 does not talk to DHIS2 and does not read a
clock, so the environment, the deployment time, the post-verification result and the
fixture import result all come from the deployment evidence the caller hands in
(BP-CMP-044 【调整】12.1), read through S0 as artifacts and checked for belonging to
*this* Blueprint version (BP-HO-002).  Evidence for another version is reported, not used.

Nothing is a placeholder: a file whose data is absent says which input was missing,
because a package that silently omits the option sets is how a developer ships a form
with an empty dropdown.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from factory_blueprint.compilers import files
from factory_blueprint.compilers.context import CompileInputs
from factory_blueprint.compilers.fixture import FixtureProduct
from factory_blueprint.compilers.openapi import (
    EXAMPLES_PREFIX,
    OPENAPI_FILE,
    OpenApiProduct,
)
from factory_blueprint.compilers.ui_spec import UiSpecProduct
from factory_blueprint.content.document import BlueprintDocument
from factory_blueprint.content.layout import CONTAINER_BY_PATH
from factory_blueprint.dto.issue import Issue, make_issue, sort_issues
from factory_blueprint.dto.package_files import (
    PACKAGE_FILE_KIND_CONTRACT,
    PACKAGE_FILE_KIND_DOCUMENT,
    PACKAGE_FILE_KIND_EXAMPLE,
    PACKAGE_FILE_KIND_FIXTURE,
    PACKAGE_FILE_KIND_I18N,
    PACKAGE_FILE_KIND_REGISTRY,
    PACKAGE_FILE_KIND_TRACEABILITY,
    PACKAGE_FILE_KIND_UI_SPEC,
)
from factory_blueprint.enums import IssueLevel, Section

RULE_EVIDENCE_VERSION_MISMATCH = "BPV-HO-001"
RULE_MISSING_EVIDENCE = "BPV-HO-002"

README_FILE = "README.md"
CONTEXT_FILE = "PROJECT-CONTEXT.md"
ARCHITECTURE_FILE = "ARCHITECTURE.md"
GUIDE_FILE = "DEVELOPMENT-GUIDE.md"
PERMISSIONS_FILE = "PERMISSIONS.md"
ACCEPTANCE_FILE = "ACCEPTANCE-CRITERIA.md"
UI_SPEC_ENTRY = "ui/ui-spec.json"
REGISTRY_FILE = "metadata/registry.json"
FIELD_MAPPING_FILE = "metadata/field-mapping.json"
OPTION_SETS_FILE = "metadata/option-sets.json"
PROGRAM_RULES_FILE = "metadata/program-rules.md"
METADATA_MANIFEST_FILE = "metadata/manifest.json"
ENVIRONMENT_EXAMPLE_FILE = "dhis2/environment.example.json"
DEBUGGING_FILE = "dhis2/debugging.md"
SOURCE_MAP_FILE = "traceability/source-map.json"

#: 17.4 needs the package to describe the App Platform stack without S2 generating code
APP_PLATFORM_DOCS = "https://dhis2.github.io/dhis2-docs/master/en/developer/"

#: paths whose role is not the one their directory would give (BP-HO-004)
_EXAMPLE_PATHS = (ENVIRONMENT_EXAMPLE_FILE,)
_DOCUMENT_METADATA_PATHS = (PROGRAM_RULES_FILE, METADATA_MANIFEST_FILE)


def package_file_kind(path: str) -> str:
    """Which role one development-package path plays (BP-HO-004, overall SRS 17.1).

    The listing groups on this rather than the caller pattern-matching names, so the rule
    follows 17.1's directory structure -- which is what makes it hold for a file a later
    compiler adds under the same directory -- with the few exact paths that read
    differently than their neighbours called out beside them.  An unrecognised path
    answers DOCUMENT: a file this version of S2 has no named role for is still something
    a developer reads, and inventing a category per surprise would make the enumeration
    unimplementable for a caller.
    """
    if path == OPENAPI_FILE:
        return PACKAGE_FILE_KIND_CONTRACT
    if path.startswith(f"{EXAMPLES_PREFIX}/"):
        return PACKAGE_FILE_KIND_EXAMPLE
    if path in _EXAMPLE_PATHS:
        return PACKAGE_FILE_KIND_EXAMPLE
    if path == UI_SPEC_ENTRY or path.startswith(files.PAGES_PREFIX):
        return PACKAGE_FILE_KIND_UI_SPEC
    if path.startswith(files.I18N_PREFIX):
        return PACKAGE_FILE_KIND_I18N
    if path.startswith("fixtures/"):
        return PACKAGE_FILE_KIND_FIXTURE
    if path.startswith("traceability/"):
        return PACKAGE_FILE_KIND_TRACEABILITY
    if path.startswith("metadata/") and path not in _DOCUMENT_METADATA_PATHS:
        return PACKAGE_FILE_KIND_REGISTRY
    return PACKAGE_FILE_KIND_DOCUMENT

REGISTRY_CONTAINER = CONTAINER_BY_PATH["/registry/entries"]
PAGES_CONTAINER = CONTAINER_BY_PATH["/pages/items"]
ACCESS_CONTAINER = CONTAINER_BY_PATH["/accessModel/roles"]
GROUPS_CONTAINER = CONTAINER_BY_PATH["/accessModel/userGroups"]
OPTION_SET_CONTAINER = CONTAINER_BY_PATH["/registry/entries"]


@dataclass
class PackageProduct:
    """The development package's files, the per-environment registry and the problems."""

    files: dict[str, bytes] = field(default_factory=dict)
    registry_by_environment: dict[str, dict[str, Any]] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)
    pages: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def blocking(self) -> bool:
        return any(issue.level is IssueLevel.BLOCKING for issue in self.issues)


@dataclass
class TargetEnvironment:
    """What one environment's compile produced, as the package needs to show it."""

    environment_key: str
    bindings: dict[str, str] = field(default_factory=dict)
    manifest: dict[str, Any] = field(default_factory=dict)
    discovery_result_id: str | None = None


def compile_package(
    inputs: CompileInputs,
    openapi: OpenApiProduct,
    ui_spec: UiSpecProduct,
    fixtures: FixtureProduct | None,
    environments: list[TargetEnvironment],
    evidence: list[dict[str, Any]],
) -> PackageProduct:
    """Generate the whole 17.1 tree for one version against its target environments."""
    registry_by_environment = {
        environment.environment_key: _environment_registry(inputs, environment)
        for environment in environments
    }
    issues = _evidence_issues(inputs, environments, evidence)
    product = PackageProduct(
        registry_by_environment=registry_by_environment,
        issues=sort_issues(issues),
        pages=ui_spec.pages,
    )
    out: dict[str, bytes] = {}
    out.update(openapi.files)
    out.update(ui_spec.files)
    if fixtures is not None:
        out.update(fixtures.files)
    out.update(_documents(inputs, ui_spec, environments, evidence, registry_by_environment))
    out[REGISTRY_FILE] = files.json_bytes(
        _registry_document(inputs, registry_by_environment, environments)
    )
    out[FIELD_MAPPING_FILE] = files.json_bytes(_field_mapping(ui_spec))
    out[OPTION_SETS_FILE] = files.json_bytes(_option_sets(inputs))
    out[PROGRAM_RULES_FILE] = _program_rules(inputs, ui_spec)
    out[METADATA_MANIFEST_FILE] = files.json_bytes(
        _deployment_manifest(inputs, environments, evidence)
    )
    out[ENVIRONMENT_EXAMPLE_FILE] = files.json_bytes(_environment_example(inputs, environments))
    out[SOURCE_MAP_FILE] = files.json_bytes(_source_map(inputs, ui_spec, openapi))
    out.update(_translations(inputs, ui_spec))
    product.files = out
    return product


def _translations(inputs: CompileInputs, ui_spec: UiSpecProduct) -> dict[str, bytes]:
    """`i18n/<locale>.json` for every language the project declared (17.1, FR-COV-010).

    The keys come from the approved UI Intents. A value only exists where the Blueprint
    recorded one, so a locale file lists what is missing rather than inventing a
    translation — which is exactly the gap 17.3's sixth check reports.
    """
    keys = sorted(
        {
            str(key)
            for page in ui_spec.pages.values()
            for key in (page.get("translationKeys") or [])
        }
    )
    labels = _translation_values(inputs)
    out: dict[str, bytes] = {}
    for locale in sorted({inputs.default_language, *inputs.translation_languages}):
        out[f"{files.I18N_PREFIX}{locale}.json"] = files.json_bytes(
            {
                "schemaVersion": 1,
                "locale": locale,
                "default": locale == inputs.default_language,
                "keys": {key: labels.get(key, {}).get(locale) for key in keys},
                "missing": sorted(key for key in keys if not labels.get(key, {}).get(locale)),
                "note": (
                    "Generated from the Blueprint's translation keys. A null value is a"
                    " translation the project still owes, not a compiler omission."
                ),
            }
        )
    return out


def _translation_values(inputs: CompileInputs) -> dict[str, dict[str, str]]:
    """Localized texts the Blueprint already carries, indexed by the key that asks for them.

    A `localizedText` is ``{"default": ..., "translations": {locale: text}}``, so a value
    exists for a locale only when the project actually recorded it — a missing translation
    is reported by the completeness check, never filled in with a guess (C-07).  A plain
    string (``pages[].name`` is one) can only ever fill the default locale.
    """
    document = BlueprintDocument(inputs.document)
    locales = {inputs.default_language, *inputs.translation_languages}
    out: dict[str, dict[str, str]] = {}

    def record(key: str, value: Any) -> None:
        if isinstance(value, str) and value:
            out.setdefault(key, {}).setdefault(inputs.default_language, value)
            return
        if not isinstance(value, dict):
            return
        filled = {
            locale: str(text)
            for locale, text in (value.get("translations") or {}).items()
            if locale in locales and text
        }
        if value.get("default"):
            filled[inputs.default_language] = str(value["default"])
        if filled:
            out.setdefault(key, {}).update(filled)

    for entry in document.container_entries(CONTAINER_BY_PATH["/pages/items"]):
        page_key = str(entry.get("pageKey") or "")
        record(f"{page_key}.name", entry.get("name"))
    for entry in document.container_entries(CONTAINER_BY_PATH["/uiIntents/items"]):
        page_key = str(entry.get("pageKey") or "")
        for mapping in entry.get("fieldMappings") or []:
            if isinstance(mapping, dict) and mapping.get("label"):
                record(f"{page_key}.{mapping.get('field')}", mapping["label"])
    return out


# --- the documents 17.1 names --------------------------------------------


def _documents(
    inputs: CompileInputs,
    ui_spec: UiSpecProduct,
    environments: list[TargetEnvironment],
    evidence: list[dict[str, Any]],
    registry_by_environment: dict[str, dict[str, Any]],
) -> dict[str, bytes]:
    return {
        README_FILE: _readme(inputs, ui_spec, environments, evidence, registry_by_environment),
        CONTEXT_FILE: _project_context(inputs),
        ARCHITECTURE_FILE: _architecture(inputs),
        GUIDE_FILE: _development_guide(inputs, len(ui_spec.pages)),
        PERMISSIONS_FILE: _permissions(inputs),
        ACCEPTANCE_FILE: _acceptance(inputs, ui_spec),
        DEBUGGING_FILE: _debugging(inputs, environments),
    }


def _readme(
    inputs: CompileInputs,
    ui_spec: UiSpecProduct,
    environments: list[TargetEnvironment],
    evidence: list[dict[str, Any]],
    registry_by_environment: dict[str, dict[str, Any]],
) -> bytes:
    """FR-FE-003's list, in its order, with no invented value in any line."""
    project = inputs.document.get("project") or {}
    pages = sorted(ui_spec.pages)
    deployed = ", ".join(
        f"`{item.get('environmentKey')}` at {item.get('deployedAt') or 'time not reported'}"
        for item in evidence
    )
    sections: list[tuple[str, list[str]]] = [
        (
            "Frontend development package",
            [
                f"Project `{inputs.project_id}` — {project.get('displayName') or 'no display name set'}.",
                "",
                f"- Blueprint version: `{inputs.blueprint_version}` (`{inputs.blueprint_hash}`)",
                f"- Target DHIS2 version: {inputs.dhis2_version or 'not bound to one environment'}",
                f"- Delivery mode: {inputs.delivery_mode}",
                f"- Pages: {len(pages)} — {', '.join(f'`{key}`' for key in pages) or 'none'}",
                "- Generated by the S2 Package Compiler from the approved Blueprint. Every UID, field and"
                " parameter here traces to a Registry entry of that version (FR-FE-001); nothing in this"
                " tree is hand-written, so recompile instead of editing.",
            ],
        ),
        (
            "What is in here",
            [
                "- `api/openapi.yaml` — the 3.1 document for this version, plus `api-reference.md`"
                " and one request/response example per `operationKey` under `api/examples/`.",
                "- `ui/ui-spec.json` and `pages/<pageKey>.md` — layout, components, page states,"
                " interactions, field mappings, program rules, translation keys, test scenarios.",
                "- `metadata/registry.json` — businessKey ↔ UID ↔ code **per environment**, with the"
                " entries that change between environments marked.",
                "- `metadata/field-mapping.json`, `option-sets.json`, `program-rules.md` — what a form"
                " field writes, which options exist, which behaviour comes from a rule.",
                "- `metadata/manifest.json` — the build record and the deployment status of each"
                " environment, from the deployment evidence S1 supplied.",
                "- `fixtures/` — synthetic demo data and the demo-user specification.",
                "- `dhis2/environment.example.json`, `dhis2/debugging.md` — how to connect and how to"
                " troubleshoot. No credential is anywhere in this package (FR-FE-005).",
                "- `traceability/source-map.json` — page → use case → Blueprint section → Registry →"
                " metadata object.",
            ],
        ),
        (
            "Quick start",
            [
                "1. Read `PROJECT-CONTEXT.md` for what the app is for and `ARCHITECTURE.md` for how the"
                " business concepts map onto DHIS2 metadata.",
                "2. Copy `dhis2/environment.example.json` to a local file and fill in the instance you"
                " were given access to. Follow `dhis2/debugging.md` first if the browser cannot reach it.",
                "3. Start from `DEVELOPMENT-GUIDE.md`: it is written against the DHIS2 App Platform,"
                " App Runtime data access and `@dhis2/ui`.",
                "4. The metadata this app needs is already deployed in the environments listed below if"
                " the manifest says so; otherwise ask for the deployment of `metadata/manifest.json`'s"
                " package before wiring against live data.",
                "5. Load `fixtures/demo-data.json` if you need demo records, and use the accounts"
                " described in `fixtures/demo-users.md` (their passwords were given to you separately).",
            ],
        ),
        (
            "Deployment status",
            [
                deployed or "- No deployment evidence was supplied, so no environment is claimed here.",
                "",
                f"- Environments this package was generated against: "
                f"{', '.join(f'`{environment.environment_key}`' for environment in environments) or 'none'}",
                "- The UIDs above resolve per environment; an entry marked environment-dependent changes"
                " between them and must not be hard-coded.",
            ],
        ),
        (
            "Known limitations",
            [
                "- The package describes the app; it is not a runnable project. Generating the App"
                " Platform starter project is a later milestone (overall SRS 17.4), and the data here"
                " is shaped so that generator needs no new inputs.",
                "- Server-side behaviour that DHIS2 computes (program rule effects after save,"
                " validation on write) is described in `metadata/program-rules.md`, not reproduced.",
                "- Page state lists come from the approved UI Intent. A state a page does not declare"
                " is listed in its spec as **not declared** rather than invented.",
            ],
        ),
    ]
    return files.md_lines(sections)


def _project_context(inputs: CompileInputs) -> bytes:
    document = BlueprintDocument(inputs.document)
    use_cases = document.container_entries(CONTAINER_BY_PATH["/useCases/items"])
    actors = document.container_entries(CONTAINER_BY_PATH["/actors/items"])
    requirements = document.container_entries(CONTAINER_BY_PATH["/requirements/items"])
    lines = [
        f"- Requirement items recorded: {len(requirements)}",
        "",
        "## Actors",
        "",
    ]
    for actor in sorted(actors, key=lambda item: str(item.get("businessKey") or "")):
        lines.append(f"- `{actor.get('businessKey')}` — {actor.get('name') or actor.get('displayName') or ''}")
    lines.extend(["", "## Use cases", ""])
    for case in sorted(use_cases, key=lambda item: str(item.get("businessKey") or "")):
        lines.append(f"- `{case.get('businessKey')}` — {case.get('name') or ''}: {case.get('purpose') or ''}")
    return files.md_lines([("Project context", lines)])


def _architecture(inputs: CompileInputs) -> bytes:
    """How the business concepts map onto DHIS2 metadata (12.1's mapping explanation)."""
    document = BlueprintDocument(inputs.document)
    registry = document.container_entries(REGISTRY_CONTAINER)
    by_type: dict[str, list[str]] = {}
    for entry in registry:
        if not isinstance(entry.get("businessKey"), str):
            continue
        by_type.setdefault(str(entry.get("dhis2Type") or "unknown"), []).append(entry["businessKey"])
    lines = [
        "This is the DHIS2 shape the approved mapping decisions produced. The Registry is the",
        "index of it; `metadata/registry.json` gives the numbers per environment.",
        "",
        "## Objects by DHIS2 type",
        "",
        "| type | entries | business keys |",
        "| --- | --- | --- |",
    ]
    for dhis2_type in sorted(by_type):
        keys = sorted(by_type[dhis2_type])
        shown = ", ".join(f"`{key}`" for key in keys[:12])
        if len(keys) > 12:
            shown += f", … (+{len(keys) - 12} more)"
        lines.append(f"| {dhis2_type} | {len(keys)} | {shown} |")
    mapping = document.container_entries(CONTAINER_BY_PATH["/mapping/entries"])
    unmapped = [
        str(item.get("businessKey"))
        for item in mapping
        if item.get("notMapped")
    ]
    lines.extend(["", "## Explicitly not mapped", ""])
    lines.extend(f"- `{key}`" for key in sorted(unmapped))
    if not unmapped:
        lines.append("- Every domain concept was mapped or is a DHIS2-side detail.")
    return files.md_lines([("Architecture and DHIS2 mapping", lines)])


def _development_guide(inputs: CompileInputs, page_count: int) -> bytes:
    """FR-FE-004: written against App Platform, App Runtime and `@dhis2/ui`."""
    lines = [
        "This guide assumes the DHIS2 App Platform, not a bespoke webpack setup, because that is",
        "what the target instance serves and what `@dhis2/ui` components expect.",
        "",
        "## Stack",
        "",
        "- **App Platform** — project scaffold, development server, build and publication to the",
        "  instance's App Hub.",
        "- **App Runtime (`@dhis2/app-runtime`)** — data access and authentication. Use its",
        "  `useQuery`/`useDataQuery` hooks against the operations in `api/openapi.yaml`; the runtime",
        "  attaches the session, so the app never handles a token or a password.",
        "- **`@dhis2/ui`** — the component library. `ui/ui-spec.json` marks each component as a",
        "  design-system name or as custom; a custom name is a component you write, and its `use`",
        "  and data source are already specified.",
        "",
        "## Develop",
        "",
        "1. Create the app with the Platform's project initialiser and set the instance in the",
        "   local configuration (see `dhis2/environment.example.json`).",
        "2. Start the development server. The Platform proxies `/api`, so the OpenAPI document's",
        "   `{baseUrl}` resolves without a hard-coded host.",
        "3. Build each page from its `pages/<pageKey>.md`: route, layout, components, the ten page",
        "   states, interactions, field mappings. Every field names the Registry entry it writes.",
        f"4. {page_count} page(s) are specified in this package.",
        "",
        "## Test",
        "",
        "- Unit-test the field mappings against `metadata/field-mapping.json`: value type, option",
        "  set and required flag are the contract the metadata enforces server-side.",
        "- Exercise the page states listed in the spec against the instance with",
        "  `fixtures/demo-data.json` loaded; the `EMPTY`, `VALIDATION_ERROR` and `PERMISSION_DENIED`",
        "  states are the ones most often left unwired.",
        "",
        "## Build and publish",
        "",
        "- Build with the Platform and install through the instance's App Management; the Platform",
        "  pins the DHIS2 version this package was compiled for.",
        "",
        f"Reference: {APP_PLATFORM_DOCS}",
    ]
    return files.md_lines([("Development guide", lines)])


def _permissions(inputs: CompileInputs) -> bytes:
    document = BlueprintDocument(inputs.document)
    roles = document.container_entries(ACCESS_CONTAINER)
    groups = document.container_entries(GROUPS_CONTAINER)
    lines = ["## Roles", "", "| business key | name | UID (this environment) |", "| --- | --- | --- |"]
    for role in sorted(roles, key=lambda item: str(item.get("businessKey") or "")):
        key = str(role.get("businessKey"))
        lines.append(f"| `{key}` | {role.get('name') or role.get('displayName') or ''} | {_uid_for(inputs, key)} |")
    lines.extend(
        ["", "## User groups", "", "| business key | name | UID (this environment) |", "| --- | --- | --- |"]
    )
    for group in sorted(groups, key=lambda item: str(item.get("businessKey") or "")):
        key = str(group.get("businessKey"))
        lines.append(f"| `{key}` | {group.get('name') or ''} | {_uid_for(inputs, key)} |")
    pages = document.container_entries(PAGES_CONTAINER)
    lines.extend(["", "## Per page", "", "A page's actors are the roles that may open it; the app must",
                  "still expect `PERMISSION_DENIED`, because the instance is the authority.", ""])
    for page in sorted(pages, key=lambda item: str(item.get("pageKey") or "")):
        actors = ", ".join(f"`{item}`" for item in sorted(page.get("actors") or []))
        lines.append(f"- `{page.get('pageKey')}` — actors: {actors or 'none declared'}")
    return files.md_lines([("Permissions", lines)])


def _acceptance(inputs: CompileInputs, ui_spec: UiSpecProduct) -> bytes:
    lines: list[str] = []
    for page_key in sorted(ui_spec.pages):
        page = ui_spec.pages[page_key]
        criteria = page.get("acceptanceCriteria") or []
        scenarios = page.get("testScenarios") or []
        lines.append(f"### `{page_key}`")
        lines.append("")
        lines.extend(f"- [ ] {item}" for item in criteria)
        lines.extend(f"- scenario: {item}" for item in scenarios)
        if not criteria and not scenarios:
            lines.append("- No page-level criteria or scenario is recorded in the approved Blueprint.")
        lines.append("")
    if not lines:
        lines.append("No page is in this version.")
    return files.md_lines([("Acceptance criteria", lines)])


def _debugging(inputs: CompileInputs, environments: list[TargetEnvironment]) -> bytes:
    """FR-FE-006: instance-side configuration, supported authentication, common failures."""
    keys = ", ".join(f"`{environment.environment_key}`" for environment in environments) or "none"
    lines = [
        "## Connecting a local development server to a remote instance",
        "",
        "The Platform proxies `/api`, but the instance still has to accept the browser origin.",
        "",
        "1. Add your local development origin (for example `http://localhost:3000`) to the",
        "   instance's **cross-origin whitelist** — Settings → Access → CORS allow list, or the",
        "   `cors.response.allow.origin` configuration the instance uses. Without it the browser",
        "   blocks every request and the app shows an empty screen rather than an error.",
        "2. Confirm the instance's version matches `metadata/manifest.json`; the OpenAPI paths in",
        "   this package are what *that* version serves.",
        "3. Sign in through the platform hosting the app. The session cookie the platform sets is",
        "   the credential the app uses; there is no token to configure locally.",
        "",
        "## Authentication modes this package supports",
        "",
        "- App Platform session cookie (the normal case for a hosted app).",
        "- Personal access token: `Authorization: ApiToken <token>`.",
        "- HTTP Basic, for tooling that is not a browser.",
        "",
        "## Common failures",
        "",
        "| symptom | what it usually is |",
        "| --- | --- |",
        "| every request fails, console shows a CORS error | the origin is not on the instance's allow list |",
        "| a query returns 403 | the account lacks the metadata's authorisation; see `PERMISSIONS.md` |",
        "| a page renders but shows no data | the UID you used belongs to another environment; read the entry's per-environment value in `metadata/registry.json` |",
        "| a write fails with a reference error | the metadata was never deployed to this environment — check `metadata/manifest.json` |",
        "| a dropdown is empty | the OptionSet is `REFERENCE` and was not created here; look it up in the environment |",
        f"| the app shows a version warning | it was built for {inputs.dhis2_version or 'another version'} |",
        "",
        f"Environments described by this package: {keys}.",
    ]
    return files.md_lines([("Debugging and integration", lines)])


# --- registry, mappings, options ------------------------------------------


def _registry_document(
    inputs: CompileInputs,
    registry_by_environment: dict[str, dict[str, Any]],
    environments: list[TargetEnvironment],
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "projectId": inputs.project_id,
        "blueprintVersion": inputs.blueprint_version,
        "blueprintHash": inputs.blueprint_hash,
        "defaultLanguage": inputs.default_language,
        "translationLanguages": inputs.translation_languages,
        "environmentKeys": sorted(registry_by_environment),
        "entries": [
            {
                "businessKey": key,
                "dhis2Type": registry_by_environment[environments[0].environment_key].get(key, {}).get("dhis2Type")
                if environments
                else None,
                "environmentDependent": _is_environment_dependent(registry_by_environment, key),
                "uid": {
                    environment.environment_key: registry_by_environment[environment.environment_key].get(key, {}).get("uid")
                    for environment in environments
                },
            }
            for key in sorted(registry_by_environment[environments[0].environment_key])
        ]
        if environments
        else [],
    }


def _environment_registry(inputs: CompileInputs, environment: TargetEnvironment) -> dict[str, Any]:
    document = BlueprintDocument(inputs.document)
    out: dict[str, Any] = {}
    for entry in document.container_entries(REGISTRY_CONTAINER):
        key = entry.get("businessKey")
        if not isinstance(key, str):
            continue
        uid = environment.bindings.get(key)
        out[key] = {
            "dhis2Type": entry.get("dhis2Type"),
            "code": entry.get("code"),
            "disposition": entry.get("disposition"),
            "managed": bool(entry.get("managed")),
            "uid": uid,
            # an entry whose number differs from the design environment's is one the
            # frontend must never bake in (FR-FE-002)
            "environmentDependent": bool(uid) and uid != (entry.get("uid") or uid),
        }
    return out


def _is_environment_dependent(registry_by_environment: dict[str, dict[str, Any]], key: str) -> bool:
    uids = {
        str(entry.get(key, {}).get("uid"))
        for entry in registry_by_environment.values()
        if entry.get(key, {}).get("uid")
    }
    return len(uids) > 1


def _field_mapping(ui_spec: UiSpecProduct) -> dict[str, Any]:
    """Every form field of every page with the Registry object behind it (FR-FE-001)."""
    pages: list[dict[str, Any]] = []
    for page_key in sorted(ui_spec.pages):
        page = ui_spec.pages[page_key]
        pages.append(
            {
                "pageKey": page_key,
                "route": page.get("route"),
                "fields": page.get("fieldMappings") or [],
            }
        )
    return {"schemaVersion": 1, "blueprintVersion": ui_spec.document.get("blueprintVersion"), "pages": pages}


def _option_sets(inputs: CompileInputs) -> dict[str, Any]:
    document = BlueprintDocument(inputs.document)
    out: list[dict[str, Any]] = []
    for entry in document.container_entries(OPTION_SET_CONTAINER):
        if str(entry.get("dhis2Type") or "") != "optionSet":
            continue
        key = str(entry.get("businessKey") or "")
        out.append(
            {
                "businessKey": key,
                "uid": _uid_for(inputs, key),
                "code": entry.get("code"),
                "disposition": entry.get("disposition"),
                "note": (
                    "Options are the deployed object's; read them from the instance. A REFERENCE"
                    " option set was created by the instance owner, not by this project."
                    if str(entry.get("disposition") or "") in ("REFERENCE", "REUSE")
                    else ""
                ),
            }
        )
    return {"schemaVersion": 1, "optionSets": sorted(out, key=lambda item: item["businessKey"])}


def _program_rules(inputs: CompileInputs, ui_spec: UiSpecProduct) -> bytes:
    document = BlueprintDocument(inputs.document)
    rules = document.container_entries(CONTAINER_BY_PATH["/rules/programRules"])
    lines = [
        "These rules change what a page must show or require. Whether DHIS2 evaluates one at",
        "save time or the app must reproduce it is stated per rule; the app never guesses.",
        "",
    ]
    for rule in sorted(rules, key=lambda item: str(item.get("businessKey") or "")):
        key = str(rule.get("businessKey"))
        lines.append(f"### `{key}`")
        lines.append("")
        lines.append(f"- Condition: `{rule.get('expression') or rule.get('condition') or 'not recorded'}`")
        actions = rule.get("actions") or []
        if actions:
            lines.append("- Effects:")
            lines.extend(f"  - {_describe_action(action)}" for action in actions if isinstance(action, dict))
        else:
            lines.append("- No action recorded on this rule.")
        lines.append(f"- Registry UID: `{_uid_for(inputs, key) or 'unbound'}`")
        lines.append("")
    referenced = {
        str(rule.get("businessKey"))
        for page_key in sorted(ui_spec.pages)
        for rule in (ui_spec.pages[page_key].get("programRules") or [])
        if isinstance(rule, dict)
    }
    if referenced:
        lines.append("## Rules the pages explicitly depend on")
        lines.append("")
        lines.extend(f"- `{key}`" for key in sorted(referenced))
    if not rules:
        lines.append("No ProgramRule is in this version.")
    return files.md_lines([("Program rules", lines)])


def _describe_action(action: dict[str, Any]) -> str:
    kind = str(action.get("programRuleActionType") or action.get("type") or "action")
    data = action.get("data") or action.get("content") or ""
    return f"{kind}: {data}" if data else kind


def _deployment_manifest(
    inputs: CompileInputs,
    environments: list[TargetEnvironment],
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    """17.1's `metadata/manifest.json`: build record + each environment's status."""
    return {
        "schemaVersion": 1,
        "kind": "METADATA_MANIFEST",
        "projectId": inputs.project_id,
        "blueprintVersion": inputs.blueprint_version,
        "blueprintHash": inputs.blueprint_hash,
        "dhis2Version": inputs.dhis2_version or None,
        "compiler": inputs.compatibility(),
        "buildContext": inputs.build_context.as_record(),
        "inputs": [ref.model_dump() for ref in inputs.input_refs()],
        "environments": [
            {
                "environmentKey": environment.environment_key,
                "discoveryResultId": environment.discovery_result_id,
                "managedScopeObjects": len(environment.manifest.get("managedScope") or []),
                "deployment": _status_for(evidence, environment.environment_key),
            }
            for environment in sorted(environments, key=lambda item: item.environment_key)
        ],
        "deploymentEvidence": [
            {
                "environmentKey": item.get("environmentKey"),
                "deployedAt": item.get("deployedAt"),
                "blueprintVersion": item.get("blueprintVersion"),
                "postVerification": item.get("postVerification"),
                "fixtureImport": item.get("fixtureImport"),
                "artifactId": item.get("artifactId"),
            }
            for item in sorted(evidence, key=lambda entry: str(entry.get("environmentKey") or ""))
        ],
    }


def _status_for(evidence: list[dict[str, Any]], environment_key: str) -> dict[str, Any]:
    for item in evidence:
        if str(item.get("environmentKey") or "") == environment_key:
            return {
                "deployed": bool(item.get("deployed", True)),
                "deployedAt": item.get("deployedAt"),
                "postVerification": item.get("postVerification"),
                "fixtureImport": item.get("fixtureImport"),
                "evidenceArtifactId": item.get("artifactId"),
            }
    return {"deployed": False, "note": "no deployment evidence was supplied for this environment"}


def _source_map(inputs: CompileInputs, ui_spec: UiSpecProduct, openapi: OpenApiProduct) -> dict[str, Any]:
    """page → use case → Blueprint section → Registry → metadata object (17.3 traceability)."""
    operations_by_page: dict[str, list[str]] = {}
    for operation in openapi.operations:
        operations_by_page.setdefault(operation["pageKey"], []).append(operation["operationKey"])
    pages: list[dict[str, Any]] = []
    for page_key in sorted(ui_spec.pages):
        page = ui_spec.pages[page_key]
        pages.append(
            {
                "pageKey": page_key,
                "useCases": page.get("useCases") or [],
                "actors": page.get("actors") or [],
                "spec": f"{files.PAGES_PREFIX}{page_key}.md",
                "apiOperations": sorted(operations_by_page.get(page_key, [])),
                "registryEntries": [
                    {
                        "businessKey": item["businessKey"],
                        "section": "registry",
                        "uid": item.get("uid"),
                    }
                    for item in (page.get("registryRefs") or [])
                ],
                "fields": [
                    {"field": item.get("field"), "businessKey": item.get("businessKey")}
                    for item in (page.get("fieldMappings") or [])
                ],
            }
        )
    return {
        "schemaVersion": 1,
        "blueprintVersion": inputs.blueprint_version,
        "blueprintHash": inputs.blueprint_hash,
        "pages": pages,
    }


def _environment_example(inputs: CompileInputs, environments: list[TargetEnvironment]) -> dict[str, Any]:
    """FR-FE-005: the shape of a configuration file, with every secret left out."""
    return {
        "schemaVersion": 1,
        "note": (
            "Copy this file, fill in the instance you were given, and never commit the result."
            " No credential belongs in the package or in version control (FR-FE-005, FR-SEC-008)."
        ),
        "instance": {"baseUrl": "https://<the-instance-you-were-given>", "apiPrefix": "api"},
        "authentication": {
            "mode": "appPlatformSession",
            "alternatives": ["apiToken", "basic"],
            "note": "the App Runtime attaches the session; set this only for tooling outside a browser",
        },
        "app": {
            "dataId": f"app_{inputs.project_id}",
            "blueprintVersion": inputs.blueprint_version,
            "dhis2Version": inputs.dhis2_version or None,
        },
        "environmentsKnown": sorted(environment.environment_key for environment in environments),
    }


# --- evidence checks ------------------------------------------------------


def _evidence_issues(
    inputs: CompileInputs, environments: list[TargetEnvironment], evidence: list[dict[str, Any]]
) -> list[Issue]:
    """BP-HO-002: the evidence must describe *this* version, or it proves nothing."""
    issues: list[Issue] = []
    for item in evidence:
        claimed = str(item.get("blueprintVersion") or "")
        if claimed and claimed != inputs.blueprint_version:
            issues.append(
                make_issue(
                    RULE_EVIDENCE_VERSION_MISMATCH,
                    IssueLevel.BLOCKING,
                    f"deployment evidence {item.get('artifactId') or '?'} describes Blueprint version "
                    f"{claimed!r}, not {inputs.blueprint_version!r}, so it cannot certify this package",
                    section=Section.PAGES,
                    details={"evidenceVersion": claimed},
                    remediation="pass the evidence produced for the version being handed off",
                )
            )
    covered = {str(item.get("environmentKey") or "") for item in evidence}
    for environment in environments:
        if environment.environment_key not in covered:
            issues.append(
                make_issue(
                    RULE_MISSING_EVIDENCE,
                    IssueLevel.WARNING,
                    f"no deployment evidence was supplied for environment "
                    f"{environment.environment_key!r}, so its pages cannot be marked ready",
                    business_key=environment.environment_key,
                    section=Section.PAGES,
                )
            )
    return issues


def _uid_for(inputs: CompileInputs, business_key: str) -> str:
    return inputs.bindings.get(business_key, "")
