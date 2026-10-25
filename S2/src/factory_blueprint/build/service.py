"""Compilation, validation and reproduction services (S2 SRS 7.5).

Every function here is external I/O: it reads artifact content, may call S4
read-only, writes new artifacts and commits its own short transactions to register
products, build records and audit events (00 doc 6.2, BP-CMP-006).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from factory_foundation.api import (
    CancellationToken,
    DataClassification,
    NotFoundError,
    ProgressReporter,
    content_hash,
    external_io,
    idempotency,
    new_id,
    structure_hash,
    unit_of_work,
)
from sqlalchemy import select

from factory_blueprint import artifacts
from factory_blueprint.audit_log import record
from factory_blueprint.build import records as build_records
from factory_blueprint.compilers import baseline as baseline_compiler
from factory_blueprint.compilers import envbound as envbound_compiler
from factory_blueprint.compilers import files as product_files
from factory_blueprint.compilers import fixture as fixture_compiler
from factory_blueprint.compilers import metadata as metadata_compiler
from factory_blueprint.compilers import openapi as openapi_compiler
from factory_blueprint.compilers import package as package_compiler
from factory_blueprint.compilers import ui_spec as ui_spec_compiler
from factory_blueprint.compilers.context import CompileInputs, InputRef
from factory_blueprint.compilers.manifest import top_level_manifest
from factory_blueprint.config import (
    COMPILER_DATASTORE,
    COMPILER_FIXTURE,
    COMPILER_OPENAPI,
    COMPILER_PACKAGE,
    COMPILER_UI_SPEC,
    settings,
)
from factory_blueprint.content.document import BlueprintDocument
from factory_blueprint.content.layout import CONTAINER_BY_PATH
from factory_blueprint.dto.build import (
    BuildContext,
    BuildResult,
    ReproduceResult,
    ValidationSummary,
)
from factory_blueprint.dto.issue import Issue, make_issue, sort_issues
from factory_blueprint.enums import (
    BindingResolution,
    BuildKind,
    BuildStatus,
    Disposition,
    IssueLevel,
    Section,
)
from factory_blueprint.errors import FeatureNotEnabled
from factory_blueprint.graph import queries as graph_queries
from factory_blueprint.models.build import BuildRecordRow, ValidationRunRow
from factory_blueprint.registry import binding as binding_rules
from factory_blueprint.registry import conflicts
from factory_blueprint.registry import scope as managed_scope
from factory_blueprint.s4.contract import CapabilityProfile, DiscoveryResult
from factory_blueprint.validators import cross_artifact, static
from factory_blueprint.validators import handoff as handoff_validator
from factory_blueprint.validators import report as report_module
from factory_blueprint.version import service as version_service

BASELINE_TYPE = "METADATA_BASELINE"
PACKAGE_TYPE = "METADATA_PACKAGE"
MANIFEST_TYPE = "MANIFEST"
VALIDATION_TYPE = "VALIDATION_REPORT"
DIFF_TYPE = "DIFF_REPORT"
CURRENT_DESIGN_ENV_FALLBACK = "DESIGN"


def _report(progress: ProgressReporter | None, step: str, fraction: float | None = None, message: str | None = None) -> None:
    if progress is not None:
        progress.report(step=step, completed_fraction=fraction, message=message)


def _check_cancelled(cancel: CancellationToken | None) -> None:
    if cancel is not None:
        cancel.raise_if_cancelled()


# --- input loading ---------------------------------------------------------


def _load_version(project_id: str, version: str) -> tuple[Any, dict[str, Any]]:
    with unit_of_work() as session:
        row = version_service.get_version(session, project_id, version)
        artifact_id = row.artifact_id
    document, _classification = artifacts.read_json(artifact_id)
    return row, document


def _load_profile(artifact_id: str | None) -> tuple[CapabilityProfile | None, InputRef | None]:
    if artifact_id is None:
        return None, None
    data, _classification = artifacts.read_json(artifact_id)
    info = artifacts.read_metadata(artifact_id)
    return (
        CapabilityProfile.model_validate(data),
        InputRef(
            role="capabilityProfile",
            artifact_id=artifact_id,
            type=info.type,
            artifact_hash=info.content_hash,
        ),
    )


def _load_discovery(artifact_id: str | None) -> tuple[DiscoveryResult | None, InputRef | None]:
    if artifact_id is None:
        return None, None
    data, _classification = artifacts.read_json(artifact_id)
    info = artifacts.read_metadata(artifact_id)
    return (
        DiscoveryResult.model_validate(data),
        InputRef(
            role="discoveryResult",
            artifact_id=artifact_id,
            type=info.type,
            artifact_hash=info.content_hash,
        ),
    )


def _version_bindings(document: dict[str, Any]) -> dict[str, str]:
    """The UIDs a compile may embed without an environment: the objects this project creates.

    REUSE, REFERENCE and UPDATE entries carry the UID *observed in one environment*; a
    baseline product must not leak it, so those resolve per environment instead (D-02).
    """
    container = CONTAINER_BY_PATH["/registry/entries"]
    return {
        str(entry["businessKey"]): str(entry["uid"])
        for entry in BlueprintDocument(document).container_entries(container)
        if entry.get("disposition") == Disposition.CREATE.value
        and isinstance(entry.get("uid"), str)
        and isinstance(entry.get("businessKey"), str)
    }


def _registry_entries(document: dict[str, Any]) -> list[dict[str, Any]]:
    container = CONTAINER_BY_PATH["/registry/entries"]
    return BlueprintDocument(document).container_entries(container)


# --- baseline compile ------------------------------------------------------


def _mode_b_products(
    inputs: CompileInputs, output: metadata_compiler.CompileOutput
) -> tuple[list[Issue], dict[str, Any]]:
    """The OpenAPI and UI Specification artifacts of a baseline compile (BP-CMP-010).

    Mode A ships no app of its own, so it gets neither; a compiler disabled in this
    build is skipped rather than half-run, and `require_compiler` remains the place a
    caller learns that a capability is off.
    """
    if inputs.delivery_mode != "B":
        return [], {}
    issues: list[Issue] = []
    handles: dict[str, Any] = {}
    enabled = settings().enabled_compilers
    project_id = inputs.project_id
    if COMPILER_OPENAPI in enabled:
        openapi_product = openapi_compiler.compile_openapi(inputs, output.objects)
        issues.extend(openapi_product.issues)
        handles[OPENAPI_TYPE] = artifacts.stage_files(
            openapi_product.files, project_id, OPENAPI_TYPE
        )
    if COMPILER_UI_SPEC in enabled:
        ui_product = ui_spec_compiler.compile_ui_spec(
            inputs, [operation["operationKey"] for operation in openapi_product.operations]
        )
        issues.extend(ui_product.issues)
        handles[UI_SPEC_TYPE] = artifacts.stage_files(ui_product.files, project_id, UI_SPEC_TYPE)
    return issues, handles


def _fixture_product(
    inputs: CompileInputs, output: metadata_compiler.CompileOutput
) -> tuple[list[Issue], dict[str, Any], Any]:
    """The FIXTURE_PACKAGE of an environment compile, when the project asks for one."""
    if COMPILER_FIXTURE not in settings().enabled_compilers:
        return [], {}, None
    product = fixture_compiler.compile_fixtures(inputs, output.objects)
    if not product.requested:
        return list(product.issues), {}, None
    return list(product.issues), {
        FIXTURE_TYPE: artifacts.stage_files(product.files, inputs.project_id, FIXTURE_TYPE)
    }, product


@external_io
def compile_baseline(
    project_id: str,
    version: str,
    capability_profile_artifact_id: str | None,
    build_context: BuildContext,
    progress: ProgressReporter | None = None,
    cancel: CancellationToken | None = None,
    *,
    idempotency_key: str,
) -> BuildResult:
    """Compile the environment-independent products of one approved version (BP-CMP-010)."""
    _report(progress, "load inputs", 0.1)
    _check_cancelled(cancel)
    row, document = _load_version(project_id, version)
    profile, profile_ref = _load_profile(capability_profile_artifact_id)
    inputs = CompileInputs(
        project_id=project_id,
        blueprint_version=version,
        blueprint_hash=row.content_hash,
        document=document,
        build_context=build_context,
        blueprint_artifact_id=row.artifact_id,
        profile=profile,
        profile_ref=profile_ref,
        bindings=_version_bindings(document),
        design_environment=_design_environment(document),
    )
    digest = structure_hash(
        {
            "kind": BuildKind.BASELINE.value,
            "version": version,
            "context": build_context.as_record(),
            "profile": profile_ref.artifact_hash if profile_ref else None,
        }
    )
    scope = "bp.compile.baseline"
    begun = idempotency.begin(scope, idempotency_key, digest)
    if begun.already_completed and begun.first_result is not None:
        return BuildResult.model_validate(begun.first_result)

    _report(progress, "compile metadata baseline", 0.4)
    _check_cancelled(cancel)
    product = baseline_compiler.compile_baseline(inputs)
    validation_issues = sort_issues(
        [*product.issues, *static.environment_independent_checks(inputs, product.output)]
    )
    package = product_files.package_hash(product.files)
    handles: dict[str, Any] = {
        BASELINE_TYPE: artifacts.stage_files(product.files, project_id, BASELINE_TYPE),
        MANIFEST_TYPE: artifacts.stage_json(
            baseline_compiler.baseline_manifest(
                inputs,
                build_records.build_record(
                    inputs,
                    kind=BuildKind.BASELINE,
                    output_artifact_ids={},
                    output_hashes={},
                    package_hash=package,
                ),
                product,
            ),
            project_id,
            MANIFEST_TYPE,
        ),
        VALIDATION_TYPE: artifacts.stage_json(
            report_module.document(inputs, validation_issues, kind="baseline"),
            project_id,
            VALIDATION_TYPE,
        ),
    }
    # BP-CMP-010: a mode B project's baseline also carries the OpenAPI document and the
    # UI Specification, because both are fully determinable before any environment exists
    # — an endpoint or a page that is wrong independent of DHIS2 must fail here.
    mode_b_issues, mode_b_handles = _mode_b_products(inputs, product.output)
    if mode_b_issues:
        validation_issues = sort_issues([*validation_issues, *mode_b_issues])
        handles[VALIDATION_TYPE] = artifacts.stage_json(
            report_module.document(inputs, validation_issues, kind="baseline"),
            project_id,
            VALIDATION_TYPE,
        )
    handles.update(mode_b_handles)
    result = _persist(
        inputs,
        kind=BuildKind.BASELINE,
        handles=handles,
        files=product.files,
        issues=validation_issues,
        blocking=product.blocking,
        idempotency_scope=scope,
        idempotency_key=idempotency_key,
        package_hash=package,
        payload_hash=product.payload_hash,
        progress=progress,
    )
    return result


def _design_environment(document: dict[str, Any]) -> str | None:
    constraints = document.get(Section.ENVIRONMENT_CONSTRAINTS.value) or {}
    for item in constraints.get("items") or []:
        if isinstance(item, dict) and item.get("environmentKey"):
            return str(item["environmentKey"])
    return None


# --- environment-bound compile --------------------------------------------


@external_io
def compile_for_environment(
    project_id: str,
    version: str,
    environment_key: str,
    discovery_artifact_id: str,
    capability_profile_artifact_id: str,
    change_request_id: str | None,
    build_context: BuildContext,
    progress: ProgressReporter | None = None,
    cancel: CancellationToken | None = None,
    *,
    idempotency_key: str,
    deletion_candidates: list[dict[str, str]] | None = None,
    design_reference_environment: str | None = None,
) -> BuildResult:
    """Resolve bindings, check conflicts and compile for one environment (BP-CMP-020)."""
    _report(progress, "load inputs", 0.1)
    _check_cancelled(cancel)
    row, document = _load_version(project_id, version)
    profile, profile_ref = _load_profile(capability_profile_artifact_id)
    discovery, discovery_ref = _load_discovery(discovery_artifact_id)
    if profile is None or discovery is None:
        raise NotFoundError(
            "an environment-bound compile needs both a Capability Profile and a Discovery Result",
            code="ARTIFACT_ERROR",
        )
    design_environment = design_reference_environment or str(
        discovery.environment_key or CURRENT_DESIGN_ENV_FALLBACK
    )
    entries = _registry_entries(document)
    policy = dict(document.get(Section.GENERATION_POLICY.value) or {})

    _report(progress, "resolve environment bindings", 0.25)
    with unit_of_work() as session:
        existing = binding_rules.observed(session, project_id, version, environment_key)
    # a binding a human set earlier wins over automatic matching (BP-REG-010.3),
    # so re-compiling after a manual fix succeeds without a new version
    unresolved = [entry for entry in entries if str(entry.get("businessKey")) not in existing]
    outcome = binding_rules.resolve_bindings(
        unresolved,
        discovery,
        environment_key=environment_key,
        design_environment=design_environment,
        policy=policy,
    )
    blocking_issues: list[Issue] = list(outcome.issues)
    # a binding a human set is a claim until this environment's discovery confirms it,
    # which is also where it gets the observation baseline S4 needs (BP-AC-10, BP-AC-13)
    manual = [
        row for row in existing.values() if row.resolution == BindingResolution.MANUAL.value
    ]
    confirmation = binding_rules.confirm_manual(
        manual,
        entries,
        discovery,
        environment_key=environment_key,
        discovery_result_id=discovery_ref.artifact_id if discovery_ref else None,
    )
    blocking_issues.extend(confirmation.issues)
    bindings = dict(_version_bindings(document))
    bindings.update({key: row.uid for key, row in existing.items()})
    bindings.update(outcome.uid_map())

    _report(progress, "UID, code and ownership checks", 0.4)
    # The instance questions go first and outside the session: they are S4 calls, and S0
    # refuses an @external_io function the moment a unit of work is current (00 doc 6.2).
    on_instance = conflicts.check_environment_facing(document, [environment_key])
    with unit_of_work() as session:
        blocking_issues = sort_issues(
            [
                *blocking_issues,
                *on_instance,
                *conflicts.check_database_facing(session, project_id, document, [environment_key]),
            ]
        )
        binding_rules.save_bindings(
            session, project_id, version, environment_key,
            [*outcome.bindings, *confirmation.bindings],
            discovery_result_id=discovery_ref.artifact_id if discovery_ref else None,
        )
        managed = [
            item.model_dump(mode="json")
            for item in managed_scope.managed_scope(session, project_id, version, environment_key)
        ]
        candidates = _deletion_candidates(
            session,
            project_id,
            version,
            entries,
            deletion_candidates or [],
            change_request_id,
            environment_key,
        )

    inputs = CompileInputs(
        project_id=project_id,
        blueprint_version=version,
        blueprint_hash=row.content_hash,
        document=document,
        build_context=build_context,
        blueprint_artifact_id=row.artifact_id,
        profile=profile,
        profile_ref=profile_ref,
        discovery=discovery,
        discovery_ref=discovery_ref,
        environment_key=environment_key,
        bindings=bindings,
        design_environment=design_environment,
        change_request_id=change_request_id,
        deletion_candidates=[str(item["businessKey"]) for item in candidates],
        managed_scope=managed,
    )
    digest = structure_hash(
        {
            "kind": BuildKind.ENVIRONMENT.value,
            "version": version,
            "environmentKey": environment_key,
            "context": build_context.as_record(),
            "discovery": discovery_ref.artifact_hash if discovery_ref else None,
            "profile": profile_ref.artifact_hash if profile_ref else None,
        }
    )
    scope = "bp.compile.environment"
    begun = idempotency.begin(scope, idempotency_key, digest)
    if begun.already_completed and begun.first_result is not None:
        return BuildResult.model_validate(begun.first_result)

    _report(progress, "compile metadata package", 0.6)
    _check_cancelled(cancel)
    product = envbound_compiler.compile_for_environment(inputs)
    # the gate sees one report per build, so the static layer runs here as well as in
    # an explicit validate_build call; both are pure functions of the same inputs
    all_issues = sort_issues(
        [
            *blocking_issues,
            *product.binding_issues,
            *product.output.issues,
            *static.validate_build(inputs, product.output),
        ]
    )
    package = product_files.package_hash(product.files)
    handles: dict[str, Any] = {}
    if not _hard_blocked(all_issues):
        handles = {
            PACKAGE_TYPE: artifacts.stage_files(product.files, project_id, PACKAGE_TYPE),
            MANIFEST_TYPE: artifacts.stage_json(
                envbound_compiler.environment_manifest(
                    inputs,
                    build_records.build_record(
                        inputs,
                        kind=BuildKind.ENVIRONMENT,
                        output_artifact_ids={},
                        output_hashes={},
                        package_hash=package,
                    ),
                    product,
                ),
                project_id,
                MANIFEST_TYPE,
            ),
        }
        if product.diff is not None:
            handles[DIFF_TYPE] = artifacts.stage_json(
                product.diff.model_dump(mode="json"), project_id, DIFF_TYPE
            )
        # BP-CMP-021: the fixture package rides with the environment compile, because
        # its Tracker payload is written for *this* version and references this
        # environment's bound UIDs
        fixture_issues, fixture_handles, _ = _fixture_product(inputs, product.output)
        handles.update(fixture_handles)
        if fixture_issues:
            all_issues = sort_issues([*all_issues, *fixture_issues])
    handles[VALIDATION_TYPE] = artifacts.stage_json(
        report_module.document(inputs, all_issues, kind="environment"), project_id, VALIDATION_TYPE
    )
    result = _persist(
        inputs,
        kind=BuildKind.ENVIRONMENT,
        handles=handles,
        files=product.files,
        issues=all_issues,
        blocking=_hard_blocked(all_issues),
        idempotency_scope=scope,
        idempotency_key=idempotency_key,
        package_hash=package,
        payload_hash=product.payload_hash,
        progress=progress,
        managed_scope=managed,
        s4_probe={"bindings": outcome.uid_map(), "environmentKey": environment_key},
    )
    return result


# --- frontend development package (BP-CMP-044, BP-HO) ----------------------


@external_io
def generate_frontend_package(
    project_id: str,
    version: str,
    environment_keys: list[str],
    deployment_evidence: str | Sequence[str] | None,
    build_context: BuildContext,
    progress: ProgressReporter | None = None,
    cancel: CancellationToken | None = None,
    *,
    idempotency_key: str,
) -> BuildResult:
    """Generate FRONTEND_PACKAGE and run 17.3's checks over the package that was made.

    The per-environment Capability Profile is deliberately not a parameter: it is the
    Profile that environment's own compile consumed, read back from that build record.
    Taking it from the caller again would let a package be assembled against a Profile
    the environment was never compiled with, and the UIDs in it would be a guess.
    """
    require_compiler(COMPILER_PACKAGE)
    _report(progress, "load version", 0.1)
    _check_cancelled(cancel)
    row, document = _load_version(project_id, version)
    evidence_entries = _evidence_documents(_as_id_list(deployment_evidence))

    issues: list[Issue] = []
    environments: list[package_compiler.TargetEnvironment] = []
    openapi_product: openapi_compiler.OpenApiProduct | None = None
    ui_product: ui_spec_compiler.UiSpecProduct | None = None
    fixture_product: fixture_compiler.FixtureProduct | None = None
    package_inputs: CompileInputs | None = None

    for environment_key in sorted(set(environment_keys)):
        _report(progress, f"compile for {environment_key}", 0.3)
        _check_cancelled(cancel)
        compiled, env_issues = _package_environment_inputs(row, document, project_id, version, environment_key)
        issues.extend(env_issues)
        if compiled is None:
            continue
        inputs, output = compiled
        openapi_product = openapi_compiler.compile_openapi(inputs, output.objects)
        ui_product = ui_spec_compiler.compile_ui_spec(
            inputs, [operation["operationKey"] for operation in openapi_product.operations]
        )
        if fixture_product is None:
            fixture_product = fixture_compiler.compile_fixtures(inputs, output.objects)
        issues.extend(openapi_product.issues)
        issues.extend(ui_product.issues)
        issues.extend(fixture_product.issues if fixture_product is not None else [])
        environments.append(
            package_compiler.TargetEnvironment(
                environment_key=environment_key,
                bindings=dict(inputs.bindings),
                manifest=_environment_manifest_body(inputs, output),
                discovery_result_id=inputs.discovery_ref.artifact_id if inputs.discovery_ref else None,
            )
        )
        if package_inputs is None:
            package_inputs = inputs

    if package_inputs is None or openapi_product is None or ui_product is None:
        issues.append(
            make_issue(
                RULE_NO_PACKAGE_SOURCE,
                IssueLevel.BLOCKING,
                f"no target environment of version {version} has a compiled metadata package, "
                "so there is no OpenAPI document, UI specification or bound UID to put in a "
                "development package",
                business_key=version,
                section=Section.PAGES,
                remediation="run compile_for_environment for a target environment first",
            )
        )
        package_inputs = package_inputs or CompileInputs(
            project_id=project_id,
            blueprint_version=version,
            blueprint_hash=row.content_hash,
            document=document,
            build_context=build_context,
            blueprint_artifact_id=row.artifact_id,
            design_environment=_design_environment(document),
        )
        openapi_product = openapi_product or openapi_compiler.OpenApiProduct()
        ui_product = ui_product or ui_spec_compiler.UiSpecProduct()

    package_product = package_compiler.compile_package(
        package_inputs,
        openapi_product,
        ui_product,
        fixture_product,
        environments,
        evidence_entries,
    )
    issues.extend(package_product.issues)

    _report(progress, "handoff completeness check", 0.7)
    _check_cancelled(cancel)
    report = handoff_validator.check_handoff(
        package_inputs,
        package_product,
        openapi_product,
        ui_product,
        fixture_product,
        evidence_entries,
    )
    issues = sort_issues([*issues, *report.issues])

    files_out = dict(package_product.files)
    package_hash = product_files.package_hash(files_out)
    handles: dict[str, Any] = {
        FRONTEND_TYPE: artifacts.stage_files(files_out, project_id, FRONTEND_TYPE),
        HANDOFF_TYPE: artifacts.stage_json(report.document, project_id, HANDOFF_TYPE),
        # BP-VAL-003: a run's findings are a VALIDATION_REPORT artifact, so 17.3's results
        # are queryable through query_issues beside every other report of this project
        VALIDATION_TYPE: artifacts.stage_json(
            report_module.document(package_inputs, issues, kind="handoff"),
            project_id,
            VALIDATION_TYPE,
        ),
        MANIFEST_TYPE: artifacts.stage_json(
            top_level_manifest(
                package_inputs,
                build_records.build_record(
                    package_inputs,
                    kind=BuildKind.HANDOFF,
                    output_artifact_ids={},
                    output_hashes={},
                    package_hash=package_hash,
                ),
                {name: content_hash(data) for name, data in sorted(files_out.items())},
                artifact_kind=FRONTEND_TYPE,
            ),
            project_id,
            MANIFEST_TYPE,
        ),
    }
    digest = structure_hash(
        {
            "kind": BuildKind.HANDOFF.value,
            "version": version,
            "environments": sorted(set(environment_keys)),
            "evidence": sorted(_as_id_list(deployment_evidence)),
            "context": build_context.as_record(),
        }
    )
    scope = "bp.compile.handoff"
    begun = idempotency.begin(scope, idempotency_key, digest)
    if begun.already_completed and begun.first_result is not None:
        return BuildResult.model_validate(begun.first_result)

    result = _persist(
        package_inputs,
        kind=BuildKind.HANDOFF,
        handles=handles,
        files=files_out,
        issues=issues,
        blocking=any(issue.level is IssueLevel.BLOCKING for issue in issues),
        idempotency_scope=scope,
        idempotency_key=idempotency_key,
        package_hash=package_hash,
        payload_hash=package_hash,
        progress=progress,
    )
    # appendix C: the handoff check is an audit event of its own, separate from the
    # build's completion — a reviewer asks "was this page handed off" of any page
    with unit_of_work() as session:
        record(
            "bp.handoff.checked",
            object_type="buildRecord",
            object_id=result.build_id,
            project_id=project_id,
            after={
                "blueprintVersion": version,
                "pages": dict(sorted(report.pages.items())),
                "frontendReady": report.ready_pages,
                "blocking": sum(1 for issue in issues if issue.level is IssueLevel.BLOCKING),
                "evidenceArtifacts": sorted(_as_id_list(deployment_evidence)),
            },
            session=session,
        )
    # BP-HO-003: which pages were handed off is part of the answer, not only of the
    # report artifact — the HANDOFF_GATE decides on it
    return result.model_copy(update={"handoff_pages": dict(sorted(report.pages.items()))})


def _package_environment_inputs(
    row: Any,
    document: dict[str, Any],
    project_id: str,
    version: str,
    environment_key: str,
) -> tuple[tuple[CompileInputs, metadata_compiler.CompileOutput] | None, list[Issue]]:
    """Rebuild one environment's compile inputs from its own build record."""
    with unit_of_work() as session:
        build = session.execute(
            select(BuildRecordRow)
            .where(
                BuildRecordRow.project_id == project_id,
                BuildRecordRow.blueprint_version == version,
                BuildRecordRow.environment_key == environment_key,
                BuildRecordRow.kind == BuildKind.ENVIRONMENT.value,
            )
            .order_by(BuildRecordRow.created_at.desc())
        ).scalars().first()
        if build is None:
            return None, [
                make_issue(
                    RULE_NO_PACKAGE_SOURCE,
                    IssueLevel.BLOCKING,
                    f"environment {environment_key!r} has no compiled metadata package for "
                    f"version {version}, so the development package cannot state its UIDs",
                    business_key=environment_key,
                    section=Section.PAGES,
                )
            ]
        bindings = binding_rules.uid_map(session, project_id, version, environment_key)
        observed_rows = binding_rules.observed(session, project_id, version, environment_key)
        managed = [
            item.model_dump(mode="json")
            for item in managed_scope.managed_scope(session, project_id, version, environment_key)
        ]
        profile_artifact, discovery_artifact = _input_pair(build.inputs)
        context = _context_of_record(build.build_context)
        parameters = _as_view(build).compile_parameters()
    profile, profile_ref = _load_profile(profile_artifact)
    discovery, discovery_ref = _load_discovery(discovery_artifact)
    if profile is None or context is None:
        return None, [
            make_issue(
                RULE_NO_PACKAGE_SOURCE,
                IssueLevel.BLOCKING,
                f"environment {environment_key!r}'s build record names no Capability Profile "
                "the package can be compiled against",
                business_key=environment_key,
                section=Section.PAGES,
            )
        ]
    inputs = CompileInputs(
        project_id=project_id,
        blueprint_version=version,
        blueprint_hash=row.content_hash,
        document=document,
        build_context=context,
        blueprint_artifact_id=row.artifact_id,
        profile=profile,
        profile_ref=profile_ref,
        discovery=discovery,
        discovery_ref=discovery_ref,
        environment_key=environment_key,
        bindings=bindings,
        observed={key: {"uid": item.uid} for key, item in observed_rows.items()},
        design_environment=_design_environment(document),
        change_request_id=parameters.get("changeRequestId"),
        deletion_candidates=[str(item) for item in parameters.get("deletionCandidates") or []],
        managed_scope=managed,
    )
    return (inputs, metadata_compiler.compile_metadata(inputs)), []


def _context_of_record(record: dict[str, Any] | None) -> BuildContext | None:
    """Rebuild the caller's build context from a build record.

    The record stores the camelCase form S0 serialises it in, so it is read field by
    field here rather than trusting a model to guess the aliases.
    """
    if not record:
        return None
    built_at = record.get("builtAt") or record.get("built_at")
    if built_at is None:
        return None
    return BuildContext(
        built_at=built_at if isinstance(built_at, datetime) else datetime.fromisoformat(str(built_at)),
        seed=int(record.get("seed") or 0),
        requester=str(record.get("requester") or ""),
    )


def _input_pair(inputs: list[Any]) -> tuple[str | None, str | None]:
    """The profile and discovery artifact IDs a build record names, by role.

    ``bp.build_record.inputs`` holds ``BuildInput`` dumps, which are snake_case — an
    ``artifactId`` lookup here would silently return None and compile a package with no
    OpenAPI or UI specification in it, which is why both spellings are read.
    """
    profile = discovery = None
    for item in inputs:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "")
        artifact_id = str(item.get("artifact_id") or item.get("artifactId") or "") or None
        if role == "capabilityProfile":
            profile = artifact_id
        elif role == "discoveryResult":
            discovery = artifact_id
    return profile, discovery


def _environment_manifest_body(
    inputs: CompileInputs, output: metadata_compiler.CompileOutput
) -> dict[str, Any]:
    """The parts of the metadata manifest the package's deployment section shows."""
    return {
        "managedScope": list(output.managed_scope),
        "environmentKey": inputs.environment_key,
        "blueprintVersion": inputs.blueprint_version,
    }


def _evidence_documents(ids: list[str]) -> list[dict[str, Any]]:
    """Read S4's reports as the package sees them: environment, version, outcome."""
    out: list[dict[str, Any]] = []
    for artifact_id in ids:
        document, _classification = artifacts.read_json(artifact_id)
        if not isinstance(document, dict):
            continue
        payload: dict[str, Any] = document.get("payload") or {}
        steps: list[Any] = document.get("steps") or []
        payload = payload if isinstance(payload, dict) else {}
        steps = steps if isinstance(steps, list) else []
        out.append(
            {
                "artifactId": artifact_id,
                "kind": document.get("kind"),
                "environmentKey": document.get("environmentKey"),
                "environmentClass": document.get("environmentClass"),
                "blueprintVersion": document.get("blueprintVersion") or payload.get("blueprintVersion"),
                "deployed": str(document.get("status") or "").upper() in ("SUCCESS", "COMPLETED", "OK"),
                "deployedAt": document.get("completedAt") or document.get("generatedAt"),
                "postVerification": _step_status(steps, ("post_verify", "postverify", "post-verify")),
                "fixtureImport": _step_status(steps, ("fixture", "import_fixtures")),
            }
        )
    return out


def _step_status(steps: list[Any], names: tuple[str, ...]) -> str | None:
    for step in steps:
        if not isinstance(step, dict):
            continue
        label = str(step.get("step") or step.get("name") or step.get("kind") or "").lower()
        if any(name in label for name in names):
            return str(step.get("status") or "").upper() or None
    return None


def _as_id_list(value: str | Sequence[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def _hard_blocked(issues: list[Issue]) -> bool:
    """What stops an environment compile from producing a package at all.

    The families here are the ones about the compile itself: a binding that does not
    resolve, an object another project owns, a payload the compiler could not build, or a
    target version that does not have what is being sent.  Validation findings (BPV-ST,
    BPV-EXR) are reported and decided at the Gate instead, so a package stays inspectable
    even when its findings are bad (BP-VAL-003).
    """
    compile_side = ("BPV-BIND", "BPV-ENV", "BPV-CMP", "BPV-CAP")
    return any(
        issue.level is IssueLevel.BLOCKING and issue.rule_id.startswith(compile_side)
        for issue in issues
    )


def _deletion_candidates(
    session: Any,
    project_id: str,
    version: str,
    entries: list[dict[str, Any]],
    requested: list[dict[str, str]],
    change_request_id: str | None,
    environment_key: str,
) -> list[dict[str, Any]]:
    """BP-CMP-026: only DEPRECATED entries named by an approved CR, and only when safe."""
    from factory_blueprint.deps import get_dhis2

    if not requested or not change_request_id:
        return []
    by_key = {str(entry.get("businessKey")): entry for entry in entries}
    accepted: list[dict[str, Any]] = []
    for item in sorted(requested, key=lambda entry: str(entry.get("businessKey"))):
        key = str(item.get("businessKey"))
        entry = by_key.get(key)
        if entry is None or str(entry.get("disposition")) != Disposition.DEPRECATED.value:
            continue
        if graph_queries.is_referenced(session, project_id, version, key):
            continue
        uid = entry.get("uid") or item.get("uid")
        dhis2_type = str(entry.get("dhis2Type") or "")
        if uid and get_dhis2().has_associated_data(environment_key, dhis2_type, str(uid)):
            continue
        accepted.append(
            {
                "businessKey": key,
                "dhis2Type": dhis2_type,
                "uid": uid,
                "changeRequestId": change_request_id,
            }
        )
    return accepted


def _persist(
    inputs: CompileInputs,
    *,
    kind: BuildKind,
    handles: dict[str, Any],
    files: dict[str, bytes],
    issues: list[Issue],
    blocking: bool,
    idempotency_scope: str,
    idempotency_key: str,
    package_hash: str | None,
    payload_hash: str | None,
    progress: ProgressReporter | None,
    managed_scope: list[dict[str, Any]] | None = None,
    s4_probe: dict[str, Any] | None = None,
) -> BuildResult:
    """Register products, the build record and the validation run in one transaction."""
    _report(progress, "register products", 0.85)
    classification = _product_classification(inputs)
    status = BuildStatus.FAILED if blocking else BuildStatus.COMPLETED
    artifact_ids: dict[str, str] = {}
    output_hashes: dict[str, str] = {}
    with unit_of_work() as session:
        for role, handle in sorted(handles.items()):
            info = artifacts.register(
                handle,
                project_id=inputs.project_id,
                artifact_type=role,
                data_classification=classification,
                source_artifact_ids=_input_ids(inputs),
                session=session,
                build_record=build_records.build_record(
                    inputs,
                    kind=kind,
                    output_artifact_ids={},
                    output_hashes={},
                    package_hash=package_hash,
                ),
            )
            artifact_ids[role] = info.id
            output_hashes[role] = info.content_hash
        from factory_foundation.api import content_hash

        # the prefix ``reproduce()`` looks under.  A baseline's products are not the environment's
        # metadata package: filed under that prefix the compare in reproduce() found no keys to
        # check and passed, so a baseline package could change byte-for-byte unnoticed.
        prefix = f"{BASELINE_TYPE if kind is BuildKind.BASELINE else PACKAGE_TYPE}/"
        for name, data in sorted(files.items()):
            output_hashes[f"{prefix}{name}"] = content_hash(data)
        record_row = build_records.save(
            session,
            inputs=inputs,
            kind=kind,
            record=build_records.build_record(
                inputs,
                kind=kind,
                output_artifact_ids=artifact_ids,
                output_hashes=output_hashes,
                package_hash=package_hash,
                status=status,
                s4_probe=s4_probe,
            ),
            output_artifact_ids=artifact_ids,
            output_hashes=output_hashes,
            package_hash=package_hash,
            status=status,
            s4_probe=s4_probe,
        )
        validation_artifact = artifact_ids.get(VALIDATION_TYPE)
        run = ValidationRunRow(
            id=new_id("vrun"),
            build_id=record_row.id,
            report_artifact_id=validation_artifact,
            blocking_count=sum(1 for issue in issues if issue.level is IssueLevel.BLOCKING),
            warning_count=sum(1 for issue in issues if issue.level is IssueLevel.WARNING),
            info_count=sum(1 for issue in issues if issue.level is IssueLevel.INFO),
            issues_digest=structure_hash([issue.issue_id for issue in issues]),
        )
        session.add(run)
        session.flush()
        record(
            "bp.build.completed" if status is BuildStatus.COMPLETED else "bp.build.failed",
            object_type="buildRecord",
            object_id=record_row.id,
            project_id=inputs.project_id,
            after={
                "kind": kind.value,
                "blueprintVersion": inputs.blueprint_version,
                "environmentKey": inputs.environment_key,
                "artifacts": artifact_ids,
                "packageHash": package_hash,
                "blocking": run.blocking_count,
                "managedScopeSize": len(managed_scope or []),
            },
            session=session,
        )
        result = BuildResult(
            build_id=record_row.id,
            kind=kind,
            project_id=inputs.project_id,
            version=inputs.blueprint_version,
            environment_key=inputs.environment_key,
            status=status,
            artifact_ids=artifact_ids,
            output_hashes=output_hashes,
            package_hash=package_hash,
            payload_hash=payload_hash,
            validation=ValidationSummary(
                report_artifact_id=validation_artifact,
                blocking_count=run.blocking_count,
                warning_count=run.warning_count,
                info_count=run.info_count,
                warnings=[issue for issue in issues if issue.level is IssueLevel.WARNING],
                issues_digest=run.issues_digest,
            ),
            binding_issues=[
                issue for issue in issues if issue.rule_id.startswith("BPV-BIND")
            ],
        )
        idempotency.complete(
            idempotency_scope, idempotency_key, result.model_dump(mode="json"), session=session
        )
    return result


def _input_ids(inputs: CompileInputs) -> list[str]:
    return sorted({ref.artifact_id for ref in inputs.input_refs()})


def _product_classification(inputs: CompileInputs) -> DataClassification:
    """BP-CMP-007: a product inherits the highest level of its sources."""
    levels = [DataClassification.INTERNAL]
    for ref in inputs.input_refs():
        try:
            levels.append(artifacts.read_metadata(ref.artifact_id).data_classification)
        except NotFoundError:
            continue
    return artifacts.highest_classification(levels)


# --- validation and reproduction -------------------------------------------


@external_io
def validate_build(
    build_id: str,
    progress: ProgressReporter | None = None,
    cancel: CancellationToken | None = None,
    *,
    idempotency_key: str,
) -> BuildResult:
    """Run the static and cross-product validation of one build (BP-VAL-020 … 031)."""
    _report(progress, "load build", 0.1)
    _check_cancelled(cancel)
    with unit_of_work() as session:
        build = session.get(BuildRecordRow, build_id)
        if build is None:
            raise NotFoundError(f"unknown build {build_id}", code="NOT_FOUND")
        build_view = _as_view(build)
        project_id, version = build.project_id, build.blueprint_version
        environment_key = build.environment_key
        profile_artifact = build_view.input_id("capabilityProfile")
        discovery_artifact = build_view.input_id("discoveryResult")
        context = build_view.build_context()
        parameters = build_view.compile_parameters()
    inputs_row, document = _load_version(project_id, version)
    profile, profile_ref = _load_profile(profile_artifact)
    discovery, discovery_ref = _load_discovery(discovery_artifact)
    with unit_of_work() as session:
        bindings = binding_rules.uid_map(session, project_id, version, environment_key or "")
        managed = [
            item.model_dump(mode="json")
            for item in managed_scope.managed_scope(session, project_id, version, environment_key or "")
        ] if environment_key else []
    inputs = CompileInputs(
        project_id=project_id,
        blueprint_version=version,
        blueprint_hash=inputs_row.content_hash,
        document=document,
        build_context=context,
        blueprint_artifact_id=inputs_row.artifact_id,
        profile=profile,
        profile_ref=profile_ref,
        discovery=discovery,
        discovery_ref=discovery_ref,
        environment_key=environment_key,
        bindings=bindings,
        managed_scope=managed,
        design_environment=parameters.get("designEnvironment"),
        change_request_id=parameters.get("changeRequestId"),
        deletion_candidates=[str(item) for item in parameters.get("deletionCandidates") or []],
    )
    _report(progress, "static validation", 0.4)
    _check_cancelled(cancel)
    compiled = (
        metadata_compiler.compile_metadata(inputs)
        if environment_key is None
        else envbound_compiler.compile_for_environment(inputs).output
    )
    issues = sort_issues(
        [
            *static.validate_build(inputs, compiled),
            *cross_artifact.validate(inputs.document, compiled, inputs),
        ]
    )
    _report(progress, "write report", 0.8)
    handle = artifacts.stage_json(
        report_module.document(inputs, issues, kind="validation"), project_id, VALIDATION_TYPE
    )
    with unit_of_work() as session:
        info = artifacts.register(
            handle,
            project_id=project_id,
            artifact_type=VALIDATION_TYPE,
            data_classification=_product_classification(inputs),
            source_artifact_ids=_input_ids(inputs),
            session=session,
            build_record={
                "buildId": build_id,
                "blueprintVersion": version,
                "environmentKey": environment_key,
            },
        )
        run = ValidationRunRow(
            id=new_id("vrun"),
            build_id=build_id,
            report_artifact_id=info.id,
            blocking_count=sum(1 for issue in issues if issue.level is IssueLevel.BLOCKING),
            warning_count=sum(1 for issue in issues if issue.level is IssueLevel.WARNING),
            info_count=sum(1 for issue in issues if issue.level is IssueLevel.INFO),
            issues_digest=structure_hash([issue.issue_id for issue in issues]),
        )
        session.add(run)
        session.flush()
        record(
            "bp.validation.completed",
            object_type="validationRun",
            object_id=run.id,
            project_id=project_id,
            after={
                "buildId": build_id,
                "reportArtifactId": info.id,
                "blocking": run.blocking_count,
                "warnings": run.warning_count,
            },
            session=session,
        )
    return BuildResult(
        build_id=build_id,
        kind=BuildKind(build_view.kind),
        project_id=project_id,
        version=version,
        environment_key=environment_key,
        status=BuildStatus.FAILED if run.blocking_count else BuildStatus.COMPLETED,
        artifact_ids={VALIDATION_TYPE: info.id},
        output_hashes={VALIDATION_TYPE: info.content_hash},
        validation=ValidationSummary(
            report_artifact_id=info.id,
            blocking_count=run.blocking_count,
            warning_count=run.warning_count,
            info_count=run.info_count,
            warnings=[issue for issue in issues if issue.level is IssueLevel.WARNING],
            issues_digest=run.issues_digest,
        ),
    )


@external_io
def reproduce(artifact_id: str) -> ReproduceResult:
    """Recompile from a build record and compare hashes (BP-CMP-004, AC-REG-01)."""
    with unit_of_work() as session:
        build = build_records.find_by_artifact(session, artifact_id)
        if build is None:
            raise NotFoundError(
                f"no build record produced artifact {artifact_id}",
                code="NOT_FOUND",
                details={"artifactId": artifact_id},
            )
        build_view = _as_view(build)
        expected = dict(build.output_hashes)
        expected_package = build.package_hash
        project_id = build.project_id
    row, document = _load_version(project_id, build_view.blueprint_version)
    profile, profile_ref = _load_profile(build_view.input_id("capabilityProfile"))
    discovery, discovery_ref = _load_discovery(build_view.input_id("discoveryResult"))
    parameters = build_view.compile_parameters()
    with unit_of_work() as session:
        bindings = (
            binding_rules.uid_map(session, project_id, build_view.blueprint_version, build_view.environment_key or "")
            if build_view.environment_key
            else _version_bindings(document)
        )
        replay_managed = [
            item.model_dump(mode="json")
            for item in managed_scope.managed_scope(
                session, project_id, build_view.blueprint_version, build_view.environment_key or ""
            )
        ] if build_view.environment_key else []
    inputs = CompileInputs(
        project_id=project_id,
        blueprint_version=build_view.blueprint_version,
        blueprint_hash=row.content_hash,
        document=document,
        build_context=build_view.build_context(),
        blueprint_artifact_id=row.artifact_id,
        profile=profile,
        profile_ref=profile_ref,
        discovery=discovery,
        discovery_ref=discovery_ref,
        environment_key=build_view.environment_key,
        bindings=bindings,
        design_environment=parameters.get("designEnvironment"),
        change_request_id=parameters.get("changeRequestId"),
        deletion_candidates=[str(item) for item in parameters.get("deletionCandidates") or []],
        managed_scope=replay_managed,
    )
    if build_view.kind == BuildKind.BASELINE.value:
        product: Any = baseline_compiler.compile_baseline(inputs)
        prefix = f"{BASELINE_TYPE}/"
    else:
        product = envbound_compiler.compile_for_environment(inputs)
        prefix = f"{PACKAGE_TYPE}/"
    from factory_foundation.api import content_hash

    actual = {
        f"{prefix}{name}": content_hash(data) for name, data in sorted(product.files.items())
    }
    actual["payload"] = product.payload_hash
    actual["package"] = product_files.package_hash(product.files)
    # only the product files are comparable: the artifact IDs the record also stores
    # are new on every compile, which is exactly what BP-CMP-004 does not promise
    expected_subset = {
        key: value for key, value in expected.items() if key.startswith(prefix)
    }
    if expected_package:
        expected_subset["package"] = expected_package
    mismatched = sorted(
        key for key, value in expected_subset.items() if actual.get(key) != value
    )
    return ReproduceResult(
        build_id=build_view.build_id,
        consistent=not mismatched and bool(expected_subset),
        expected_hashes=expected_subset,
        actual_hashes={key: actual[key] for key in sorted(actual)},
        mismatched=mismatched,
        message=None if not mismatched else "recompilation differs; treat this as a defect",
    )


class _BuildView:
    def __init__(self, row: BuildRecordRow) -> None:
        self._row = row
        self.build_id = row.id
        self.kind = row.kind
        self.blueprint_version = row.blueprint_version
        self.environment_key = row.environment_key

    def input_id(self, role: str) -> str | None:
        for item in self._row.inputs or []:
            if isinstance(item, dict) and item.get("role") == role:
                value = item.get("artifact_id") or item.get("artifactId")
                return str(value) if value else None
        return None

    def build_context(self) -> BuildContext:
        stored = self._row.build_context or {}
        return BuildContext(
            built_at=stored.get("builtAt"),
            seed=int(stored.get("seed", 0)),
            requester=str(stored.get("requester", "reproduction")),
        )

    def compile_parameters(self) -> dict[str, Any]:
        """The parameters this build was compiled with, as recorded (FR-CMP-004).

        A build compiled before this column existed answers with nothing to replay, which
        is exactly what such a build was compiled with: it had no authorized deletions,
        because deletions only arrive through a change request.
        """
        return dict(self._row.compile_parameters or {})


def _as_view(row: BuildRecordRow) -> _BuildView:
    return _BuildView(row)


def require_compiler(name: str) -> None:
    """Refuse a compiler this build of S2 does not deliver (BP-TD-002)."""
    enabled = settings().enabled_compilers
    if name not in enabled:
        raise FeatureNotEnabled(
            f"compiler {name!r} is not enabled in this build of S2",
            details={"enabled": sorted(enabled), "requested": name},
        )


#: compilers a later milestone delivers; requesting one is an explicit error.
#: M3 delivered OpenAPI, UI Spec, Fixture and Package; DataStore and the starter
#: project generation are M5 (BP-TD-002), and they stay refused rather than half-built.
DISABLED_COMPILERS: tuple[str, ...] = (COMPILER_DATASTORE,)

OPENAPI_TYPE = "OPENAPI"
UI_SPEC_TYPE = "UI_SPECIFICATION"
FIXTURE_TYPE = "FIXTURE_PACKAGE"
FRONTEND_TYPE = "FRONTEND_PACKAGE"
HANDOFF_TYPE = "HANDOFF_CHECK_REPORT"

#: a package is assembled from an environment's compile, so without one there is nothing
#: truthful to put in it — reported rather than filled in from the design environment
RULE_NO_PACKAGE_SOURCE = "BPV-HO-112"
