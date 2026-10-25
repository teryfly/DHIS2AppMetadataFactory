"""Blueprint / Registry read forwarding (S5IF-BP).

Three rules shape this module:

* the resource shape is S2's, never S1's — these functions project, they do not
  decide (S1 SRS 8.1 "转发" row);
* a provider read that touches artifact bytes is external I/O, so the caller must
  hold no unit of work (00 doc 6.2); ``get_entries`` and ``diff_versions`` are
  marked ``@external_io`` by S2 itself and the adapter re-asserts that;
* a provider read that needs a session takes one from the adapter, not from S1's
  transaction, so nothing is persisted while crossing a subsystem boundary.
"""

from __future__ import annotations

import logging
from typing import Any

from factory_control import store as S
from factory_control.errors import NotFoundControlError, ValidationFailedError
from factory_control.foundation import ArtifactQuery, FactoryError, query_artifacts, read_content_bytes
from factory_control.ports import dto
from factory_control.runtime import Runtime

#: CP-UI-021: an ``ARTIFACT_TYPE_MISMATCH`` back from S2 means S1 handed the provider an
#: artifact of the wrong type — the caller's own resolution is broken, and the envelope's
#: 500 needs a loud line next to it so the drift is noticed before a user reports it.
_logger = logging.getLogger("factory_control.blueprint_reads")


def section_index(runtime: Runtime) -> dict[str, Any]:
    catalog = runtime.blueprint.section_catalog()
    return catalog.model_dump(by_alias=True)


def known_sections(runtime: Runtime) -> list[str]:
    return [s.section for s in runtime.blueprint.section_catalog().sections]


def section_schema(runtime: Runtime, section: str, schema_version: int | None = None) -> dict[str, Any]:
    """S5IF-BP02 — 404 for an unknown section, 422 for an unsupported schema version."""
    if section not in known_sections(runtime):
        raise NotFoundControlError(
            f"unknown blueprint section {section}",
            details={"section": section, "known": known_sections(runtime)},
        )
    if schema_version is not None:
        available = runtime.blueprint.section_catalog().available_versions
        if schema_version not in available:
            raise ValidationFailedError(
                f"blueprint schema version {schema_version} is not supported",
                details={"schemaVersion": schema_version, "available": list(available)},
            )
    return runtime.blueprint.get_section_schema(section, schema_version)


def list_versions(
    runtime: Runtime, project_id: str, *, page: int = 1, page_size: int = 50
) -> dict[str, Any]:
    return runtime.blueprint.list_versions(project_id, page=page, page_size=page_size).model_dump(
        by_alias=True
    )


def entries(runtime: Runtime, project_id: str, selector: dict[str, Any], version: str | None) -> dict[str, Any]:
    """S5IF-BP04 — no transaction may be open (S2 reads artifact bytes)."""
    chosen = runtime.blueprint.get_entries(project_id, selector, version)
    resolved = chosen.version or _current_version(runtime, project_id)
    return {
        "projectId": chosen.project_id or project_id,
        "version": resolved,
        "source": chosen.source,
        "entries": list(chosen.entries),
        "notFound": list(chosen.not_found),
    }


def resolve_active_draft(runtime: Runtime, project_id: str) -> dict[str, Any] | None:
    """Which draft this project is working in — S5 cannot be left to guess (UI-BR-02).

    Two ways to learn it, in this order.  S2's project semantics carry the draft when the
    provider publishes one (``BlueprintDraftInfo``), and S1's own stage rows remember the
    draft each step opened: ``inputBlueprintRef`` of kind ``DRAFT`` is S1's column, written
    from the id S2 returned.  So the answer does not depend on S5IF-UP03 being granted —
    which is exactly what the ruling asks S1 to solve on its own side.

    The current stage's draft wins; otherwise the most recently opened one.
    """
    from factory_control import store as S

    semantics = runtime.blueprint.get_project_semantics(project_id).draft
    if semantics is not None:
        return {
            "draftId": semantics.draft_id,
            "stage": semantics.stage,
            "stageScope": list(semantics.stage_scope),
            "baseVersion": semantics.base_version,
            "contentHash": semantics.content_hash,
        }

    with runtime.transaction(operator="system", project_id=project_id) as ctx:
        workflow = S.get_workflow_for_project(ctx.session, project_id)
        candidates: list[tuple[Any, str, str | None]] = []
        for stage in S.stages_of(ctx.session, workflow.id):
            ref = dict(stage.input_blueprint_ref or {})
            if str(ref.get("kind") or "") != "DRAFT" or not ref.get("id"):
                continue
            candidates.append((stage, str(ref["id"]), stage.stage_type))
        if not candidates:
            return None
        chosen = next(
            (item for item in candidates if item[0].id == workflow.current_stage_id),
            max(candidates, key=lambda item: item[0].created_at),
        )
    return {
        "draftId": chosen[1],
        "stage": chosen[2],
        "stageScope": [chosen[2]] if chosen[2] else [],
        "baseVersion": None,
        "contentHash": str(dict(chosen[0].input_blueprint_ref or {}).get("hash") or "") or None,
    }


def draft(runtime: Runtime, project_id: str) -> dict[str, Any]:
    """S5IF-BP05 【裁决】: metadata only, and the document is deliberately absent.

    S5 does not need a whole-draft read — entry content goes through ``entries?source=DRAFT``
    — and shipping one at 5,000 objects would waste the bandwidth the review view needs.
    What S1 must supply is the ``draftId``, and ``resolve_active_draft`` gets it even where
    the provider has not exported a draft lookup yet.

    No draft at all is a stage that has not run, so it is CP-UI-022 guidance naming the stage
    that opens the first one, not a dead reference: a "nothing yet" answer must never be faked
    with a 200 and an empty body, and it must not read to the client as a missing object.
    """
    from factory_control.domain.enums import StageType

    resolved = resolve_active_draft(runtime, project_id)
    if resolved is None:
        raise NotFoundControlError(
            f"project {project_id} has no open blueprint draft",
            details={"projectId": project_id},
            reason="NOT_GENERATED",
            required_stage=StageType.REQUIREMENTS.value,
        )
    row = _draft_row(runtime, str(resolved["draftId"]))
    return {
        "draftId": (row.draft_id if row is not None else None) or resolved["draftId"],
        "baseVersion": (row.base_version if row is not None else None) or resolved["baseVersion"],
        "stage": (row.stage if row is not None else None) or resolved["stage"],
        "stageScope": list(row.stage_scope) if row is not None else list(resolved["stageScope"]),
        "status": row.status if row is not None else None,
        "contentHash": (row.content_hash if row is not None else None) or resolved["contentHash"],
        "promotable": row.promotable if row is not None else None,
        "openPatchCount": row.open_patch_count if row is not None else 0,
        "changeRequestId": row.change_request_id if row is not None else None,
        # S5IF-UP03: S2 keeps ``draft_document`` module-private, so there is no
        # whole-document read to forward.  Explicitly null, not absent.
        "document": None,
    }


def _draft_row(runtime: Runtime, draft_id: str) -> Any:
    """S2's draft row when the provider publishes the read, ``None`` when it does not.

    The metadata beyond the id is nice to have; the id is the requirement, so a provider
    that has not exported ``get_draft`` still gets a usable answer.
    """
    from factory_control.ports.protocols import ProviderUnavailableError

    try:
        return runtime.blueprint.get_draft(draft_id)
    except ProviderUnavailableError:
        return None


def validation_issues(
    runtime: Runtime,
    workflow_id: str,
    *,
    operator: str,
    run_id: str | None = None,
    level: str | None = None,
    source_stage: str | None = None,
    rule_id: str | None = None,
    business_key: str | None = None,
    sort_by: str = "level",
    sort_dir: str = "ASC",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """S5IF-BP14 — issues across this workflow's reports, filtered by the provider.

    S1 names the reports (its own artifact index knows which VALIDATION_REPORT and
    DHIS2_IMPORT_REPORT a step produced) and hands their ids to the S2 read, which owns the
    Issue structure and does the filtering: UI-REV-005's four filters are query parameters
    there, not a client-side pass over a loaded report.  ``limit``/``offset`` travel with
    the call so a large report set is never fetched whole.
    """
    from factory_control import store as S
    from factory_control.foundation import ArtifactQuery, query_artifacts

    with runtime.transaction(operator=operator) as ctx:
        workflow = S.get_workflow(ctx.session, workflow_id)
        produced: set[str] = set()
        for stage in S.stages_of(ctx.session, workflow_id, run_id=run_id):
            produced.update(stage.output_artifact_ids or [])
        reports = [
            report
            for artifact_type in ("VALIDATION_REPORT", "DHIS2_IMPORT_REPORT")
            for report in query_artifacts(
                ArtifactQuery(project_id=workflow.project_id, type=artifact_type, limit=500),
                session=ctx.session,
            ).items
        ]
    # The stage rows, not an artifact column, say which reports belong to this workflow or
    # Run: S1 keeps the reference on the step that produced it (CP-WF-005), and a report
    # registered by a provider carries no workflow of its own.
    report_ids = [str(report.id) for report in reports if str(report.id) in produced]
    return runtime.blueprint.query_issues(
        report_ids,
        level=level,
        source_stage=source_stage,
        rule_id=rule_id,
        business_key=business_key,
        sort_by=sort_by,
        sort_dir=sort_dir,
        limit=limit,
        offset=offset,
    )


def registry(
    runtime: Runtime, project_id: str, version: str | None, environment_key: str | None
) -> dict[str, Any]:
    resolved = version or _current_version(runtime, project_id)
    if not resolved:
        raise NotFoundControlError(
            f"project {project_id} has no blueprint version to read", details={"projectId": project_id}
        )
    rows = runtime.blueprint.get_registry_entries(project_id, resolved, environment_key)
    items = []
    for row in rows:
        view = row.model_dump(by_alias=True)
        observed = dict(view.get("observed") or {})
        # UI-WB-005 wants UID/code/disposition and the baseline on the row itself;
        # S5IF-BP06 permits flattening the ``observed`` sub-object.
        for key in ("observedHash", "observedLastUpdated", "discoveryResultId", "sourceEnvironment"):
            view[key] = observed.get(key)
        items.append(view)
    return {"items": items, "total": len(items)}


def _baseline_build_for_version(ctx: Any, stage: Any, requested: str, current: str) -> str:
    """The newest BASELINE_COMPILE attempt whose recorded build answers ``requested``.

    S1 stamps ``blueprintVersion`` on the StageRun result from the baseline step on; an
    older row without the stamp can only answer for the project's current version — the
    honest limit of S1's own record.  Anything unmatched is "no baseline compile for
    that version", which the caller turns into NOT_GENERATED.
    """
    runs = [run for run in reversed(S.stage_runs(ctx.session, stage.id)) if (run.build_ids or [])]
    for run in runs:
        recorded = str(dict(run.result or {}).get("blueprintVersion") or "")
        if recorded == requested:
            ids = [b for b in (run.build_ids or []) if b]
            if ids:
                return str(ids[-1])
    if requested == current and runs:
        ids = [b for b in (runs[0].build_ids or []) if b]
        if ids:
            return str(ids[-1])
    return ""


def workflow_object_graph(
    runtime: Runtime,
    workflow_id: str,
    *,
    operator: str,
    run_id: str | None = None,
    blueprint_version: str | None = None,
    dhis2_types: list[str] | None = None,
    business_keys: list[str] | None = None,
) -> dict[str, Any]:
    """The dependency graph of one build — S2 is its only source (CP-UI-020, BP-TD-009).

    Three ways to name the build (SRS V1.1.3): ``runId`` takes that Run's
    environment-bound compile, ``blueprintVersion`` takes that version's latest
    baseline compile, and neither takes the current version's.  Both together name two
    possible builds and answer neither, so 422.  Nodes, degrees, ``orphans`` and
    ``unresolvedRegistryRefs`` are S2's facts computed on the complete graph — filters
    only trim what is returned, and S1 keeps no code path that assembles them
    (CP-AC-33 forbids exactly the arithmetic the old project-level view ran).

    A build that has not happened is not an empty graph: 404 NOT_FOUND, reason
    NOT_GENERATED, requiredStage the compile that would produce it (CP-UI-022,
    CP-AC-31).  An id S1 cannot resolve is NOT_EXIST.
    """
    from factory_control.domain.enums import StageType
    from factory_control.models import Project

    if run_id is not None and blueprint_version is not None:
        raise ValidationFailedError(
            "runId and blueprintVersion name different builds; give exactly one",
            details={
                "fields": [
                    {"path": "runId", "message": "mutually exclusive with blueprintVersion"},
                    {"path": "blueprintVersion", "message": "mutually exclusive with runId"},
                ]
            },
        )

    with runtime.transaction(operator=operator) as ctx:
        workflow = S.get_workflow(ctx.session, workflow_id)
        project = ctx.session.get(Project, workflow.project_id)
        current_version = str(project.current_blueprint_version or "") if project else ""
        if run_id is not None:
            run = S.get_run(ctx.session, run_id)
            if run.workflow_id != workflow.id:
                raise NotFoundControlError(
                    f"run {run_id} is not a run of workflow {workflow_id}",
                    details={"workflowId": workflow.id},
                    reason="NOT_EXIST",
                    run_id=run_id,
                )
            stage_type = StageType.BIND_COMPILE
            stage = S.stage_by_type(ctx.session, workflow.id, stage_type, run_id=run.id)
        else:
            stage_type = StageType.BASELINE_COMPILE
            stage = S.stage_by_type(ctx.session, workflow.id, stage_type)
        required_stage = stage_type.value
        build_id = ""
        if stage is not None:
            if blueprint_version is None:
                row = S.latest_stage_run(ctx.session, stage.id)
                ids = [b for b in ((row.build_ids if row else []) or []) if b]
                build_id = str(ids[-1]) if ids else ""
            else:
                build_id = _baseline_build_for_version(ctx, stage, blueprint_version, current_version)

    if not build_id:
        raise NotFoundControlError(
            f"no {required_stage} build has been recorded for this workflow yet",
            details={"workflowId": workflow.id, "blueprintVersion": blueprint_version},
            reason="NOT_GENERATED",
            required_stage=required_stage,
            run_id=run_id,
        )

    filters = (
        dto.GraphFilters(
            dhis2_types=tuple(dhis2_types) if dhis2_types else None,
            business_keys=tuple(business_keys) if business_keys else None,
        )
        if (dhis2_types or business_keys)
        else None
    )
    graph = runtime.blueprint.get_dependency_graph(build_id, filters)
    body = _row(graph)
    body["workflowId"] = workflow.id
    body["runId"] = run_id
    body["filters"] = {
        "dhis2Types": list(dhis2_types or []),
        "businessKeys": list(business_keys or []),
    }
    return body


def managed_scope(runtime: Runtime, project_id: str, version: str | None, environment_key: str) -> dict[str, Any]:
    resolved = version or _current_version(runtime, project_id)
    if not resolved:
        raise NotFoundControlError(
            f"project {project_id} has no blueprint version to read", details={"projectId": project_id}
        )
    rows = runtime.blueprint.get_managed_scope(project_id, resolved, environment_key)
    return {"items": [r.model_dump(by_alias=True) for r in rows], "total": len(rows)}


def package_files(
    runtime: Runtime,
    project_id: str,
    version: str | None = None,
    environment_key: str | None = None,
) -> dict[str, Any]:
    """The files of one build, for UI-REV-003's *by file* view (S5IF-BP17, BP-CMP-021).

    S2 answers this from its own build record, so no package bytes are fetched and no file layout
    is guessed here: an object's identity stays the Registry's answer, and this says only which
    file carries it and with what hash.  A version nobody compiled used to answer 200 with
    ``kind=NONE``; CP-UI-022 retired that marker — the answer is now 404 ``NOT_FOUND`` with
    ``reason=NOT_GENERATED``, which tells the client what to finish instead of what to ignore.
    """
    from factory_control.domain.enums import StageType

    manifest = runtime.blueprint.get_package_manifest(project_id, version, environment_key)
    if not manifest.build_id or manifest.kind == "NONE":
        raise NotFoundControlError(
            f"project {project_id} has no compiled metadata package for this version",
            details={"projectId": project_id, "version": manifest.blueprint_version or version},
            reason="NOT_GENERATED",
            required_stage=StageType.BASELINE_COMPILE.value,
        )
    files = [_row(item) for item in manifest.files]
    return {
        "projectId": manifest.project_id,
        "version": manifest.blueprint_version,
        "buildId": manifest.build_id,
        "kind": manifest.kind,
        "environmentKey": manifest.environment_key,
        "packageHash": manifest.package_hash,
        "files": files,
        "total": len(files),
    }


def _frontend_package_listing(
    runtime: Runtime, project_id: str, operator: str
) -> tuple[dto.PackageFileListing, str, str]:
    """Resolve the project's current-package artifact and forward S2's listing.

    Returns ``(listing, artifactId, dataClassification)``.  Three refusals, three
    meanings (CP-UI-021, BP-HO-004): no FRONTEND_PACKAGE indexed for the current
    Blueprint version is a guided NOT_GENERATED naming HANDOFF; an
    ``ARTIFACT_TYPE_MISMATCH`` from S2 means S1 itself resolved the wrong artifact —
    warn loudly and let the 500 stand, never relabel a caller error as missing data;
    anything else S2 answers passes through.
    """
    from factory_control.models import Project

    with runtime.transaction(operator=operator, project_id=project_id) as ctx:
        project = ctx.session.get(Project, project_id)
        current_version = str(project.current_blueprint_version or "") if project else ""
        items = query_artifacts(
            ArtifactQuery(project_id=project_id, type="FRONTEND_PACKAGE", limit=1),
            session=ctx.session,
        ).items
        artifact_id = ""
        classification = ""
        if items:
            head = items[0]
            artifact_id = str(head.id)
            classification_value = getattr(head, "data_classification", None)
            classification = str(getattr(classification_value, "value", classification_value or ""))
        if not artifact_id:
            raise NotFoundControlError(
                f"project {project_id} has no frontend package yet",
                details={"projectId": project_id},
                reason="NOT_GENERATED",
                required_stage="HANDOFF",
            )

    try:
        listing = runtime.blueprint.list_package_files(artifact_id)
    except FactoryError as error:
        if error.error_code == "ARTIFACT_TYPE_MISMATCH":
            _logger.warning(
                "frontend package resolution passed S2 an artifact that is not a FRONTEND_PACKAGE: %s",
                error.message,
            )
        raise

    # CP-UI-021: the package must answer for the *current* version.  A promoted version
    # without a fresh HANDOFF has no package of its own — that is NOT_GENERATED, not
    # yesterday's listing wearing today's label.
    if listing.blueprint_version and current_version and listing.blueprint_version != current_version:
        raise NotFoundControlError(
            f"the newest frontend package answers for {listing.blueprint_version}, not {current_version}",
            details={"projectId": project_id, "blueprintVersion": current_version},
            reason="NOT_GENERATED",
            required_stage="HANDOFF",
        )
    return listing, artifact_id, classification


def frontend_package_files(runtime: Runtime, project_id: str, *, operator: str) -> tuple[dict[str, Any], str]:
    """The dev-package file listing, forwarded from S2 (CP-UI-021, BP-HO-004).

    Returns the listing body and the artifact's data classification, which the route
    answers with as ``X-Data-Classification`` — the same rule as the artifact content
    endpoint (CP-UI-016).  S2 owns the package layout; S1 forwards its listing.
    """
    listing, _artifact_id, classification = _frontend_package_listing(runtime, project_id, operator)
    return _row(listing), classification


def frontend_package_file(
    runtime: Runtime, project_id: str, path: str, *, operator: str
) -> tuple[dto.PackageFileContent, str, str]:
    """One file of the dev package (CP-UI-021).  ``(content, artifactId, classification)``.

    Membership is decided against S2's own listing; a path it does not carry is
    NOT_EXIST — indistinguishable from a path S2 refuses for form (BP-HO-004), so
    neither answer leaks the package's internal structure, and S1 never unpacks.
    """
    listing, artifact_id, classification = _frontend_package_listing(runtime, project_id, operator)
    if path not in {item.path for item in listing.files}:
        raise NotFoundControlError(
            f"frontend package {artifact_id} carries no file {path}",
            details={"artifactId": artifact_id, "path": path},
            reason="NOT_EXIST",
        )
    content = runtime.blueprint.read_package_file(artifact_id, path)
    return content, artifact_id, classification


def _row(value: Any) -> dict[str, Any]:
    """One port mirror, projected into the camelCase body S5 is promised (00 doc 6.11)."""
    return dict(value.model_dump(mode="json", by_alias=True))


def _run_context(ctx: Any, run_id: str) -> tuple[str, str, str]:
    """``(projectId, blueprintVersion, environmentKey)`` for one Deployment Run."""
    run = S.get_run(ctx.session, run_id)
    workflow = S.get_workflow(ctx.session, run.workflow_id)
    return workflow.project_id, str(run.blueprint_version), str(run.environment_key)


def bindings_for_run(runtime: Runtime, run_id: str, *, operator: str) -> dict[str, Any]:
    """S5IF-BP09 — ``version`` and ``environmentKey`` come from the Run, not the URL."""
    with runtime.transaction(operator=operator) as ctx:
        project_id, version, environment_key = _run_context(ctx, run_id)
    rows = runtime.blueprint.get_bindings(project_id, version, environment_key)
    items = [row.model_dump(by_alias=True) for row in rows]
    return {"items": items, "total": len(items), "environmentKey": environment_key, "blueprintVersion": version}


def set_binding_for_run(
    runtime: Runtime, run_id: str, *, business_key: str, uid: str, note: str, operator: str
) -> dict[str, Any]:
    """S5IF-BP10 — S1 resolves the version and environment the caller must not guess."""
    with runtime.transaction(operator=operator) as ctx:
        project_id, version, environment_key = _run_context(ctx, run_id)
    result = runtime.blueprint.set_environment_binding(
        project_id, version, environment_key, business_key, uid, note
    )
    return {
        **dict(result or {}),
        "environmentKey": environment_key,
        "blueprintVersion": version,
        "businessKey": business_key,
    }


def uid_pool(runtime: Runtime, project_id: str) -> dict[str, Any]:
    return dict(runtime.blueprint.uid_pool_status(project_id))


def replenish_uid_pool(
    runtime: Runtime, project_id: str, *, operator: str, environment_key: str | None = None
) -> dict[str, Any]:
    """Top the project's UID pool up from a real instance (S5IF-BP09).

    ``uid_pool.replenish`` has been in the action table since the forwarding actions were
    written, and S1 SRS 697 names it as something allowed-actions must answer — but nothing
    in S1 ever *called* it except an agent stage run, and there is no other way to reach
    :func:`factory_blueprint.registry.pool.replenish`.  So a project whose design stages are
    authored by hand (``POST /stages/{id}/patches``) hits ``UID_POOL_EXHAUSTED`` on its first
    registry CREATE and has no button to press, while the UI was told the action exists.
    ``UI-MAN-003``'s "the interface only shows waiting" is about that error code arriving
    mid-stage; it never meant "and there is no way out".

    The UIDs come from the target instance's own generate endpoint, which is external I/O, so
    this reads what it needs, closes its own unit of work, and then calls the provider.
    ``environment_key`` defaults to the project's design reference, which is the environment
    every other design-stage pool fill already uses.
    """
    with runtime.transaction(operator=operator, project_id=project_id) as ctx:
        project = S.get_project(ctx.session, project_id)
        if environment_key is None:
            design = S.design_reference_environment(ctx.session, project.id)
            if design is None:
                raise ValidationFailedError(
                    "the project has no design reference environment to draw UIDs from",
                    details={"missing": "DESIGN_REFERENCE", "projectId": project.id},
                )
            environment_key = design.environment_key
        else:
            known = {t.environment_key for t in S.target_environments(ctx.session, project.id)}
            if environment_key not in known:
                raise ValidationFailedError(
                    f"{environment_key} is not a target environment of this project",
                    details={"environmentKey": environment_key, "known": sorted(known)},
                )

    runtime.blueprint.replenish_uid_pool(project.id, environment_key)
    # The answer is the pool as S2 now reports it, not a count S1 calculated: `available` and
    # `needs_replenishment` are the two things the operator pressed the button to learn, and
    # S2 owns the low-water mark that decides them.
    return dict(runtime.blueprint.uid_pool_status(project.id))


def decisions(runtime: Runtime, project_id: str, decision_ids: list[str]) -> dict[str, Any]:
    rows = runtime.blueprint.get_decisions(project_id, decision_ids)
    return {"items": [r.model_dump(by_alias=True) for r in rows], "total": len(rows)}


def diff(
    runtime: Runtime,
    project_id: str,
    *,
    from_version: str,
    to_version: str,
    environment_key: str | None,
    sections: list[str] | None,
) -> dict[str, Any]:
    """S5IF-BP12 — only version-vs-version exists upstream (S5IF-UP04)."""
    raw = runtime.blueprint.diff_objects(project_id, from_version, to_version)
    body = dict(raw or {})
    objects = [dict(o) for o in (body.get("objects") or [])]
    return {
        "projectId": body.get("projectId") or body.get("project_id") or project_id,
        "kind": body.get("kind"),
        "fromVersion": from_version,
        "toVersion": to_version,
        "environmentKey": environment_key,
        "objects": objects,
        "counts": dict(body.get("counts") or {}),
        "sections": list(sections or []),
    }


def nrr_items(
    runtime: Runtime,
    workflow_id: str,
    *,
    operator: str,
    category: str | None = None,
    input_artifact_id: str | None = None,
    min_confidence: float | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """S5IF-BP15 — read the workflow's NRR artifacts and project what they carry.

    ``category`` exists only inside S3's NRR document; where the document does not
    have it the field is answered ``null`` rather than inferred (S5IF-UP06).
    """
    with runtime.transaction(operator=operator) as ctx:
        workflow = S.get_workflow(ctx.session, workflow_id)
        page = query_artifacts(
            ArtifactQuery(project_id=workflow.project_id, type="NRR", limit=200),
            session=ctx.session,
        )
        # S3 registers the NRR from a parse, and older builds wrote no workflow on the artifact.
        # The source-input link still names the workflow, so an NRR whose own column is empty is
        # included when one of its sources is a business input of this workflow. Without this,
        # every parse made before the registration fix reads as "no normalised requirements"
        # while the parsed document sits in the artifact store.
        inputs = query_artifacts(
            ArtifactQuery(
                project_id=workflow.project_id,
                workflow_id=workflow.id,
                type="BUSINESS_INPUT",
                limit=500,
            ),
            session=ctx.session,
        )
        input_ids = {artifact.id for artifact in inputs.items}
        artifact_ids = [
            artifact.id
            for artifact in page.items
            if artifact.workflow_id == workflow.id
            or input_ids.intersection(artifact.source_artifact_ids or [])
        ]
    items: list[dict[str, Any]] = []
    for artifact_id in artifact_ids:
        for record in _nrr_records(artifact_id):
            record["artifactId"] = artifact_id
            items.append(record)
    # CP-UI-003: category, source input and confidence are the three ways UI-REQS-004
    # narrows the list; they filter here so the page it shows is the page it asked for.
    if category:
        items = [r for r in items if str(r.get("category") or "").upper() == category.upper()]
    if input_artifact_id:
        items = [
            r for r in items
            if str((r.get("source") or {}).get("artifactId") or r.get("artifactId")) == input_artifact_id
        ]
    if min_confidence is not None:
        items = [r for r in items if (r.get("confidence") or 0) >= min_confidence]
    total = len(items)
    return {
        "items": items[offset : offset + limit],
        "total": total,
        "limit": limit,
        "offset": offset,
        "workflowId": workflow_id,
    }


def _nrr_records(artifact_id: str) -> list[dict[str, Any]]:
    import json

    try:
        data, _classification, _media = read_content_bytes(artifact_id)
        payload = json.loads(data.decode("utf-8"))
    except Exception:  # noqa: BLE001 - an unreadable artifact is simply no evidence
        return []
    rows = payload.get("items") if isinstance(payload, dict) else payload if isinstance(payload, list) else []
    out: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        out.append(
            {
                "businessKey": row.get("businessKey") or row.get("business_key"),
                "statement": row.get("statement"),
                "category": row.get("category"),
                "source": dict(row.get("source") or {}),
                "rationale": row.get("rationale"),
                "priority": row.get("priority"),
                "status": row.get("status"),
                "confidence": row.get("confidence"),
                "actors": list(row.get("actors") or []),
                "openQuestions": list(row.get("openQuestions") or row.get("open_questions") or []),
            }
        )
    return out


def _current_version(runtime: Runtime, project_id: str) -> str | None:
    row = runtime.blueprint.get_current_version(project_id)
    return row.version if row is not None else None
