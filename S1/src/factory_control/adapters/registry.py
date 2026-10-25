"""Real adapters for S2 / S3 / S4, and the resolver that picks them.

Selection rule (``FACTORY_PROVIDER_MODE``):

* ``fakes``  — always use :mod:`factory_control.testing.fakes`;
* ``real``   — require the provider packages and fail if absent;
* ``auto``   — use a real adapter when its package is importable, else the fake.

The auto fallback exists because S2/S3/S4 are being developed in parallel; it is
what lets ``factory-api`` boot today (CP-RT-004, CP-ASM-08).  Which mode produced
the running system is reported by ``/healthz`` so nobody mistakes a fake for a
real integration.

Three transaction regimes live in :class:`~factory_control.adapters.delegating.DelegatingAdapter`
and are declared per method here: ``EXTERNAL_IO_METHODS`` (no unit of work may be
open), ``SESSION_METHODS`` (the provider read is pure but cannot open its own
session), and everything else (the provider manages its own connection).
"""

from __future__ import annotations

from typing import Any

from factory_control.adapters.delegating import DelegatingAdapter
from factory_control.config import Settings
from factory_control.ports import dto
from factory_control.ports.protocols import BlueprintPort, ExecutionPort, IntelligencePort

_MODE_AUTO = "auto"
_MODE_FAKES = "fakes"
_MODE_REAL = "real"


def _evidence_ids(deployment_evidence: dict[str, Any]) -> list[str]:
    """The delivery reports S2 is allowed to believe, in the shape S2 reads.

    ``generate_frontend_package`` takes a *sequence* because BP-HO-002 checks per target
    environment: ``validators/handoff.py`` derives the verified environments from the evidence
    documents themselves and blocks when a target has none.  Forwarding only ``artifactId`` — as
    this did — meant a mode-B project with more than one environment could never satisfy the rule
    through S1, no matter how many Runs it had verified.  ``artifactId`` alone still works, so a
    caller with one environment sends one.
    """
    raw = deployment_evidence.get("artifactIds") or deployment_evidence.get("artifact_ids")
    if isinstance(raw, (list, tuple)):
        return [str(item) for item in raw if str(item).strip()]
    single = str(deployment_evidence.get("artifactId") or "").strip()
    return [single] if single else []


class BlueprintAdapter(DelegatingAdapter):
    """``factory_blueprint.api`` (S2 SRS 7).

    Every method S1 calls on S2 goes through one of two doors here:

    * :attr:`METHOD_MAP` for the calls whose arguments and result already match,
      which is most of the read side;
    * an explicit method for the calls where S1's port and S2's public module
      disagree on *shape* — an object where S2 wants a typed model, a positional
      argument in a different place, a field named differently.  Those
      translations live here and nowhere else, so S1\u2019s services, ports and
      mirrors keep one vocabulary and a rename inside S2 touches exactly one file.

    Nothing here fills in a value S1 was not given.  Where S2 requires something
    S1 does not have, the call raises: inventing a DHIS2 version range or a build
    seed would silently change what gets compiled (FR-CMP-001).
    """

    PROVIDER_PACKAGE = "factory_blueprint"
    PROVIDER_LABEL = "S2"
    METHOD_MAP = {
        "get_project_semantics": ("get_project_semantics", dto.ProjectSemantics),
        "get_current_version": ("get_current_version", dto.BlueprintVersionInfo),
        "get_draft": ("get_draft", dto.BlueprintDraftInfo),
        "freeze_draft": ("freeze_draft", dto.FreezeResult),
        "prepare_mode_upgrade": ("prepare_mode_upgrade", None),
        "commit_mode_upgrade": ("commit_mode_upgrade", None),
        "registry_object_refs": ("get_registry", None),
        "list_object_types": ("list_object_types", None),
        "replenish_uid_pool": ("replenish_uid_pool", None),
        "uid_pool_status": ("get_uid_pool_status", None),
        "get_section_schema": ("get_section_schema", None),
        "list_versions": ("list_versions", dto.VersionPage),
        "get_registry_entries": ("get_registry", dto.RegistryEntryInfo),
        "get_object_relationships": ("get_object_relationships", dto.ObjectRelation),
        "get_package_manifest": ("get_package_manifest", dto.PackageManifest),
        "get_managed_scope": ("get_managed_scope", dto.ManagedObjectInfo),
        "get_bindings": ("get_bindings", dto.BindingInfo),
        "get_decisions": ("get_decisions", dto.DecisionInfo),
        "diff_objects": ("diff_versions", None),  # explicit: see diff_objects below
        "query_issues": ("query_issues", None),
        "reproduce": ("reproduce", None),
        "delete_project_data": ("delete_project_data", None),
    }
    EXTERNAL_IO_METHODS = frozenset(
        {
            "prepare_initial_blueprint",
            "freeze_draft",
            "prepare_mode_upgrade",
            "replenish_uid_pool",
            "compile_baseline",
            "compile_for_environment",
            "validate_build",
            "generate_frontend_package",
            "export_blueprint",
            "prepare_import",
            "prepare_draft_base",
            # S2 marks these ``@external_io``: they read artifact bytes, so S0
            # refuses them inside a unit of work.
            "get_entries",
            "diff_objects",
            # A report is an artifact: S2 reads its bytes to answer, so no unit of work
            # may be open (S5IF-BP14).
            "query_issues",
            # ``reproduce`` recompiles from a build record and hashes the products, so it
            # reads artifacts too.  The assembly check (FR-DEP-005) caught that this was
            # missing: a caller that had a unit of work open would have gotten a
            # TransactionMisuseError out of what looks like a plain read.
            "reproduce",
            # S2 SRS 7.5 marks the dependency graph and package-file reads external I/O
            # (both read artifact bytes); the S0 guard applies the moment the functions ship.
            "get_dependency_graph",
            "list_package_files",
            "read_package_file",
        }
    )
    #: ``bp`` reads that are transaction-safe but take a keyword ``session``.
    SESSION_METHODS = frozenset(
        {
            "get_project_semantics",
            "get_current_version",
            "registry_object_refs",
            "uid_pool_status",
            "get_decisions",
            "list_versions",
            "get_registry_entries",
            "get_object_relationships",
            "get_package_manifest",
            "get_managed_scope",
            "get_bindings",
            "delete_project_data",
            "open_draft",
            "promote_draft",
            "submit_patch",
            "commit_import",
            "register_initial_blueprint",
        }
    )

    # --- the calls whose shape differs --------------------------------------

    def prepare_initial_blueprint(self, project_id: str, semantics: dict[str, Any]) -> str:
        """S1\u2019s semantics mapping onto S2\u2019s ``InitialProjectInfo``."""
        import importlib

        from factory_control.errors import ValidationFailedError

        api = importlib.import_module("factory_blueprint.api")
        languages = [str(x) for x in (semantics.get("languages") or [])]
        constraint = str(semantics.get("versionConstraints") or "")
        if not constraint:
            # S2 requires it (InitialProjectInfo) and CP-PRJ-001 lists it as a
            # creation input; a default here would pick a DHIS2 version range for
            # the project, which is the user\u2019s decision.
            raise ValidationFailedError(
                "versionConstraints is required to create a project",
                details={"fields": [{"path": "versionConstraints", "message": "required to create a project"}]},
            )
        info = api.InitialProjectInfo(
            delivery_mode=api.DeliveryMode(str(semantics.get("deliveryMode") or "A")),
            # S1 spells the project type lower-case on its own wire ("greenfield"),
            # S2's enum is GREENFIELD/BROWNFIELD; the mapping belongs here.
            project_type=str(semantics.get("projectType") or "greenfield").upper(),
            target_dhis2_version_constraint=constraint,
            default_language=(languages[0] if languages else "zh"),
            translation_languages=languages[1:],
            display_name=semantics.get("displayName"),
            summary=semantics.get("summary"),
            # S2 derives every managed object's code from this (BP-REG-009) and replaces an
            # author-supplied code, so the prefix is the only run-scoped identity a shared
            # target instance can be cleaned up by (FR-TST-002).
            code_prefix=(str(semantics.get("codePrefix") or "").strip() or None),
        )
        handle = self._function("prepare_initial_blueprint")(project_id, info)
        return _handle_id(handle)

    def register_initial_blueprint(
        self, project_id: str, handle: str, *, session: Any = None
    ) -> dto.PromotionResult:
        # S2 answers a VersionInfo, whose field names already match the mirror.
        body = _dump(
            self._call(
                "register_initial_blueprint", "register_initial_blueprint", None,
                (project_id, handle), {"session": session},
            )
        )
        return dto.PromotionResult.model_validate(body)

    def registry_object_refs(
        self, project_id: str, version: str, environment_key: str | None = None, *, session: Any = None
    ) -> list[dict[str, Any]]:
        """S2's Registry rows as the plain mappings S4's discovery scope reads (DX-DSC-002).

        ``get_registry`` answers ``RegistryEntry`` *models*, while the port promises
        ``list[dict]`` and both callers index the result with ``.get(...)``.  Every fake
        returned dicts, so this only ever surfaced on the real path: BIND_COMPILE died in
        S4 with ``DiscoveryScopeInvalidError: an explicit discovery reference must be an
        object with dhis2Type and uid/code``.
        """
        rows = self._call(
            "registry_object_refs", "get_registry", None,
            (project_id, version, environment_key), {"session": session},
        )
        refs: list[dict[str, Any]] = []
        for row in rows or ():
            entry = dto.RegistryEntryInfo.model_validate(row)
            if entry.uid is None and entry.code is None:
                # An entry that has been given neither cannot be pointed at in the target
                # environment, and S4 refuses such a reference outright (DX-DSC-002).
                continue
            refs.append(
                {
                    "businessKey": entry.business_key,
                    "dhis2Type": entry.dhis2_type,
                    "uid": entry.uid,
                    "code": entry.code,
                    "disposition": entry.disposition,
                    "status": entry.status,
                }
            )
        return refs

    def open_draft(
        self,
        project_id: str,
        stage_scope: str,
        change_request_id: str | None = None,
        *,
        session: Any = None,
    ) -> str:
        draft_id = str(
            self._call(
                "open_draft", "open_draft", None, (project_id, stage_scope),
                {"change_request_id": change_request_id, "session": session},
            )
        )
        return draft_id

    def prepare_draft_base(self, project_id: str, draft_id: str) -> str:
        """Materialise a draft's base content, the second half of S2's "open a draft".

        S2 reserves a draft and fills it from the current version in two steps, and
        until the second one ``submit_patch``/``freeze_draft`` answer
        ``BP_DRAFT_NOT_MATERIALIZED``.  That step reads and writes artifact bytes, so
        it is external I/O and the caller must hold no unit of work — which is why it
        is a port method the services call explicitly rather than something tucked
        inside ``open_draft``.
        """
        self._assert_outside_transaction("prepare_draft_base")
        return str(self._function("prepare_draft_base")(project_id, draft_id))

    # --- product reads whose arguments S1's mirror must translate ----------------
    #
    # S2's ``get_dependency_graph`` takes its own frozen ``GraphFilters`` model, and S1's
    # port speaks in its mirror (00 doc 6.1: nobody imports the provider's types outside
    # the adapter); ``_coerce``'s dict-keyword path cannot convert that argument, so the
    # three reads are explicit methods like ``prepare_draft_base`` rather than METHOD_MAP
    # rows.  All three resolve the real ``factory_blueprint.api`` functions (BP-TD-009,
    # BP-HO-004 shipped 2026-10); answers come back as S2's models and are dumped onto
    # S1's mirrors, which the provider mirror test compares field by field (CP-RT-005).

    def get_dependency_graph(
        self, build_id: str, filters: dto.GraphFilters | None = None
    ) -> dto.DependencyGraph:
        self._assert_outside_transaction("get_dependency_graph")
        provider_filters = (
            None
            if filters is None
            else self._function("GraphFilters")(
                dhis2_types=filters.dhis2_types, business_keys=filters.business_keys
            )
        )
        answer = self._function("get_dependency_graph")(build_id, provider_filters)
        return dto.DependencyGraph.model_validate(_dump(answer))

    def list_package_files(self, artifact_id: str) -> dto.PackageFileListing:
        self._assert_outside_transaction("list_package_files")
        answer = self._function("list_package_files")(artifact_id)
        return dto.PackageFileListing.model_validate(_dump(answer))

    def read_package_file(self, artifact_id: str, path: str) -> dto.PackageFileContent:
        self._assert_outside_transaction("read_package_file")
        answer = self._function("read_package_file")(artifact_id, path)
        return dto.PackageFileContent.model_validate(_dump(answer))

    def submit_patch(
        self,
        project_id: str,
        draft_id: str,
        patch: dict[str, Any],
        *,
        stage: str,
        idempotency_key: str,
        change_request_id: str | None = None,
        session: Any = None,
    ) -> dto.PatchResult:
        """Build S2\u2019s ``Patch`` + ``PatchContext`` from S1\u2019s submitted body.

        ``patch_id`` / ``base_version`` / ``author`` identify the *submission*, which
        S1 owns, so the adapter fills them from what the caller already gave S1.  The
        operations themselves are S2\u2019s RFC 6902 vocabulary and are passed through
        untouched — S1 does not interpret them.
        """
        import importlib

        from factory_control.errors import ValidationFailedError
        from factory_control.foundation import structure_hash

        api = importlib.import_module("factory_blueprint.api")
        operations = patch.get("operations")
        if not isinstance(operations, list) or not operations:
            raise ValidationFailedError(
                "a manual patch must carry RFC 6902 operations",
                details={
                    "fields": [{"path": "operations", "message": "must be a non-empty list of RFC 6902 operations"}],
                    "received": type(operations).__name__,
                },
            )
        base_version = str(patch.get("baseVersion") or patch.get("base_version") or "")
        if not base_version:
            current = self.get_current_version(project_id)
            base_version = current.version if current is not None else ""
        if not base_version:
            raise ValidationFailedError(
                "the project has no Blueprint version to patch against yet",
                details={
                    "fields": [{"path": "baseVersion", "message": "no Blueprint version to patch against yet"}],
                    "projectId": project_id,
                },
            )
        body: dict[str, Any] = {
            # Content-derived, the way S3 names its patches (`runtime/patching.py::_patch_id`).
            # Slicing the idempotency key here used to be enough, but the manual-patch key now
            # carries its digest past character 24 (services/steps.py::_manual_patch_key), so the
            # slice is constant per stage and every hand-authored patch of one attempt would land
            # as the same S2 row id uniquified with `#1`, `#2` — which throws away the identity of
            # the submission, and the submission identity is exactly what S1 owns.
            "patch_id": str(
                patch.get("patchId")
                or patch.get("patch_id")
                or f"pat-{structure_hash({'stage': stage, 'ops': operations})[7:31]}"
            ),
            "base_version": base_version,
            "author": {"kind": api.AuthorKind.USER, "ref": str(patch.get("author") or "s1")},
            "operations": list(operations),
            "decision_ids": [str(d) for d in (patch.get("decisionIds") or [])],
        }
        rationale = patch.get("rationale")
        if rationale:
            body["rationale"] = str(rationale)
        built = api.Patch.model_validate(body)
        context = api.PatchContext(stage=api.DesignStage(stage), change_request_id=change_request_id)
        outcome = self._call(
            "submit_patch", "submit_patch", None,
            (project_id, draft_id, built, context),
            {"idempotency_key": idempotency_key, "session": session},
        )
        return dto.PatchResult.model_validate(_dump(outcome))

    def promote_draft(
        self,
        project_id: str,
        draft_id: str,
        expected_hash: str,
        idempotency_key: str,
        *,
        session: Any = None,
    ) -> dto.PromotionResult:
        """S2 keys a promotion off the draft alone; ``project_id`` stays in S1\u2019s port."""
        raw = self._call(
            "promote_draft", "promote_draft", dto.PromotionResult,
            (draft_id, expected_hash), {"idempotency_key": idempotency_key, "session": session},
        )
        body = _dump(raw)
        impact = body.get("impact") or {}
        return dto.PromotionResult(
            version=str(body["version"]),
            change_level=str(body.get("change_level") or "Minor"),
            affected_stages=[str(s) for s in (body.get("affected_stages") or [])],
            artifact_id=body.get("artifact_id"),
            content_hash=body.get("content_hash"),
            impact=dict(impact),
            applied_patch_ids=[str(x) for x in (body.get("applied_patch_ids") or [])],
        )

    def analyze_impact(self, project_id: str, business_keys: list[str]) -> dto.ImpactAnalysis:
        """Rename S2\u2019s ``ImpactSet`` fields into S1\u2019s ``ImpactAnalysis``."""
        raw = self._function("analyze_impact")(project_id, business_keys)
        body = _dump(raw)
        return dto.ImpactAnalysis(
            affected_stages=[str(s) for s in (body.get("stages") or [])],
            affected_object_keys=[str(s) for s in (body.get("business_keys") or [])],
            affected_pages=[str(s) for s in (body.get("page_keys") or [])],
            affected_artifact_types=[str(s) for s in (body.get("artifact_types") or [])],
            sections=[str(s) for s in (body.get("sections") or [])],
            operation_keys=[str(s) for s in (body.get("operation_keys") or [])],
        )

    def compile_baseline(
        self,
        project_id: str,
        version: str,
        capability_profile_artifact_id: str | None,
        build_context: dto.BuildContext,
        *,
        progress: Any = None,
        cancel: Any = None,
        idempotency_key: str,
    ) -> dto.CompileResult:
        return self._compile(
            "compile_baseline", (project_id, version, capability_profile_artifact_id),
            progress, cancel, idempotency_key, build_context,
        )

    def compile_for_environment(
        self,
        project_id: str,
        version: str,
        environment_key: str,
        discovery_result_artifact_id: str | None,
        capability_profile_artifact_id: str | None,
        change_request_id: str | None,
        build_context: dto.BuildContext,
        *,
        progress: Any = None,
        cancel: Any = None,
        idempotency_key: str,
        deletion_candidates: list[dict[str, str]] | None = None,
    ) -> dto.CompileResult:
        return self._compile(
            "compile_for_environment",
            (
                project_id,
                version,
                environment_key,
                discovery_result_artifact_id or "",
                capability_profile_artifact_id or "",
                change_request_id,
            ),
            progress, cancel, idempotency_key, build_context,
            deletion_candidates=deletion_candidates,
        )

    def generate_frontend_package(
        self,
        project_id: str,
        version: str,
        environment_keys: list[str],
        deployment_evidence: dict[str, Any],
        build_context: dto.BuildContext,
        *,
        progress: Any = None,
        cancel: Any = None,
        idempotency_key: str,
    ) -> dto.CompileResult:
        evidence = _evidence_ids(deployment_evidence)
        return self._compile(
            "generate_frontend_package",
            (project_id, version, list(environment_keys), evidence),
            progress, cancel, idempotency_key, build_context,
        )

    def _compile(
        self,
        method: str,
        positional: tuple[Any, ...],
        progress: Any,
        cancel: Any,
        idempotency_key: str,
        build_context: dto.BuildContext,
        deletion_candidates: list[dict[str, str]] | None = None,
    ) -> dto.CompileResult:
        """One translation for the three compile entries: S2 answers ``BuildResult``."""
        self._assert_outside_transaction(method)
        keywords: dict[str, Any] = {"idempotency_key": idempotency_key}
        if deletion_candidates:
            # Only an environment compile takes a candidate set; a baseline has no environment to
            # delete from, and BP-CMP-026 admits a deletion only through this argument.
            keywords["deletion_candidates"] = deletion_candidates
        result = self._function(method)(
            *positional, _build_context(build_context), progress, cancel, **keywords
        )
        body = _dump(result)
        validation = body.get("validation") or {}
        return dto.CompileResult(
            build_id=body.get("build_id"),
            artifact_ids=dict(body.get("artifact_ids") or {}),
            issues=[dto.Issue.model_validate(i) for i in (validation.get("issues") or [])],
            payload_hash=body.get("package_hash"),
            status=str(body.get("status") or ""),
            version=body.get("version"),
            environment_key=body.get("environment_key"),
            output_hashes=dict(body.get("output_hashes") or {}),
        )

    def validate_build(
        self,
        build_id: str,
        *,
        progress: Any = None,
        cancel: Any = None,
        idempotency_key: str,
    ) -> dto.ValidationOutcome:
        self._assert_outside_transaction("validate_build")
        body = _dump(
            self._function("validate_build")(build_id, progress, cancel, idempotency_key=idempotency_key)
        )
        validation = body.get("validation") or {}
        issues = [dto.Issue.model_validate(i) for i in (validation.get("issues") or [])]
        artifacts = dict(body.get("artifact_ids") or {})
        return dto.ValidationOutcome(
            validation_report_artifact_id=artifacts.get("VALIDATION_REPORT"),
            blocking_count=int(validation.get("blocking_count") or sum(1 for i in issues if i.is_blocking)),
            issues=issues,
            artifact_ids=artifacts,
            build_id=body.get("build_id"),
            status=str(body.get("status") or ""),
            warning_count=int(validation.get("warning_count") or 0),
            passed=str(validation.get("status") or body.get("status") or "").upper()
            not in ("FAILED", "BLOCKED"),
        )

    def export_blueprint(self, project_id: str) -> dto.ExportBundle:
        body = _dump(self._function("export_blueprint")(project_id))
        referenced = body.get("referenced_artifact_ids") or body.get("artifact_ids") or []
        return dto.ExportBundle(
            artifact_ids=[str(x) for x in referenced],
            export_artifact_id=body.get("artifact_id"),
            manifest={"projectId": body.get("project_id"), "files": dict(body.get("files") or {})},
            content_hash=body.get("content_hash"),
            versions=[str(x) for x in (body.get("versions") or [])],
            current_version=body.get("current_version"),
            referenced_artifact_ids=[str(x) for x in referenced],
        )

    def prepare_import(self, manifest: dict[str, Any], id_map: dict[str, str]) -> str:
        """S2 previews an import and hands back a ``batch_id``; S1 calls that a handle."""
        preview = self._function("prepare_import")(
            str(manifest.get("exportArtifactId") or manifest.get("artifactId") or ""),
            dict(id_map),
            project_id=manifest.get("projectId"),
        )
        return str(_dump(preview).get("batch_id") or "")

    def commit_import(
        self, project_id: str, handle: str, idempotency_key: str, *, session: Any = None
    ) -> dto.ImportResult:
        body = _dump(
            self._call(
                "commit_import", "commit_import", None, (project_id, handle),
                {"idempotency_key": idempotency_key, "session": session},
            )
        )
        return dto.ImportResult(
            restored_versions=[str(v) for v in (body.get("versions") or [])],
            pending_environment_bindings=[str(v) for v in (body.get("pending_reparse") or [])]
            if isinstance(body.get("pending_reparse"), list)
            else [str(body.get("pending_reparse", ""))] * int(body.get("pending_reparse") or 0),
            project_id=body.get("project_id"),
            batch_id=body.get("batch_id"),
            current_version=body.get("current_version"),
            counts=dict(body.get("counts") or {}),
        )

    def diff_objects(self, project_id: str, from_version: str, to_version: str) -> dict[str, Any]:
        """S2\u2019s ``DiffReport`` as the mapping S1\u2019s read service projects.

        External I/O: the report compares document content, so S0 forbids it inside a
        unit of work and S1 must not hold a transaction open across it.
        """
        self._assert_outside_transaction("diff_objects")
        body = _dump(self._function("diff_versions")(project_id, from_version, to_version))
        return {
            **body,
            "objects": [dict(o) for o in (body.get("objects") or [])],
            "counts": dict(body.get("counts") or {}),
        }

    def get_project_semantics(
        self, project_id: str, version: str | None = None
    ) -> dto.ProjectSemantics:
        """Compose S2\u2019s semantics with the current version.

        S2\u2019s ``ProjectSemantics`` carries no draft and no version pointer, so the
        version comes from ``get_current_version``.  The open draft stays ``None``:
        S2 keeps ``active_draft_id`` module-private (S5IF-UP03), and S1 must not
        reconstruct it by guessing an id.
        """
        raw = self._call("get_project_semantics", "get_project_semantics", None, (project_id, version), {})
        body = _dump(raw)
        current = self.get_current_version(project_id)
        return dto.ProjectSemantics(
            delivery_mode=str(body.get("delivery_mode") or "A"),
            project_type=str(body.get("project_type") or ""),
            has_fixture_spec=bool(body.get("has_fixture_specifications")),
            mapping_frozen=bool(body.get("mapping_frozen")),
            draft=None,
            current_version=current,
        )

    def section_catalog(self) -> dto.SectionCatalog:
        """S5IF-BP01 \u2014 S2 owns the section layout; S1 only serialises it.

        There is no "list every section" function on ``factory_blueprint.api``, but the
        data is right there in its ``Section`` enum and ``content/layout`` table, so the
        adapter reads both rather than keeping a copy in S1.
        """
        import importlib

        layout = importlib.import_module("factory_blueprint.content.layout")
        api = importlib.import_module("factory_blueprint.api")
        containers = {c.section.value: c for c in layout.ENTRY_CONTAINERS}
        return dto.SectionCatalog(
            current_schema_version=api.current_schema_version(),
            available_versions=list(api.available_versions()),
            sections=[
                dto.SectionLayout(
                    section=section.value,
                    entry_container=containers[section.value].path if section.value in containers else None,
                    key_field=containers[section.value].key_field if section.value in containers else None,
                )
                for section in api.Section
            ],
        )

    def get_entries(
        self, project_id: str, selector: dict[str, Any], version: str | None = None
    ) -> dto.SelectedEntries:
        """S5IF-BP04 \u2014 translate S1\u2019s plain selector onto ``EntrySelector``.

        S2\u2019s DTO has no camelCase aliases, so the keyword names below are
        deliberately its own snake_case spellings.
        """
        import importlib

        api = importlib.import_module("factory_blueprint.api")
        chosen = api.EntrySelector(
            business_keys=list(selector.get("businessKeys") or []),
            decision_ids=list(selector.get("decisionIds") or []),
            page_keys=list(selector.get("pageKeys") or []),
            sections=[api.Section(s) for s in (selector.get("sections") or []) if s],
            source=str(selector.get("source") or "APPROVED"),
        )
        return dto.SelectedEntries.model_validate(api.get_entries(project_id, chosen, version).model_dump())

    def set_environment_binding(
        self,
        project_id: str,
        version: str,
        environment_key: str,
        business_key: str,
        uid: str,
        note: str | None = None,
        *,
        session: Any = None,
        resolved_by: str | None = None,
    ) -> dict[str, Any]:
        """S5IF-BP10 \u2014 S2 takes the session first and everything else by keyword."""
        from contextlib import ExitStack

        from factory_control.foundation import unit_of_work

        stack = ExitStack()
        owned = session is None
        if owned:
            session = stack.enter_context(unit_of_work())
        try:
            row = self._function("set_environment_binding")(
                session,
                project_id=project_id,
                blueprint_version=version,
                environment_key=environment_key,
                business_key=business_key,
                uid=uid,
                note=note or "",
                resolved_by=resolved_by or "system",
            )
        finally:
            stack.close()
        return dto.BindingInfo.model_validate(_dump(row)).model_dump(by_alias=True)


def _handle_id(handle: Any) -> str:
    """S2 stages content and hands back a ``StagingHandle``; S1 carries its id.

    S2 accepts the bare id on the committing call, so a string keeps S1\u2019s port
    signature (`prepare \u2192 str`, `commit \u2190 str`) and stays storable in a JSON
    column across the two transactions S1 uses (CP-PRJ-001).
    """
    return str(getattr(handle, "handle", None) or handle)


def _dump(value: Any) -> dict[str, Any]:
    """A provider model, dict or ``str``-keyed mapping as a plain dict."""
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        return dict(dumped) if isinstance(dumped, dict) else {}
    if isinstance(value, dict):
        return dict(value)
    return {}


def _build_context(context: dto.BuildContext | dict[str, Any]) -> Any:
    """S1\u2019s deterministic build context as S2\u2019s ``BuildContext``.

    ``seed`` and ``built_at`` must be the values S1 used on the first attempt, because
    byte-level reproduction depends on sending them again (FR-CMP-001).  That is why
    the port carries the context instead of letting the provider invent it.
    """
    import importlib

    api = importlib.import_module("factory_blueprint.api")
    if isinstance(context, dto.BuildContext):
        return api.BuildContext(
            built_at=context.built_at,
            seed=context.seed,
            requester=context.requester,
            correlation_id=context.correlation_id,
        )
    return api.BuildContext.model_validate(dict(context or {}))


class IntelligenceAdapter(DelegatingAdapter):
    """``factory_intelligence.api`` (S3 SRS 7).

    CP-UI-006 item 6 let this be the one place that mapped a typed port method onto S3's
    transitional ``governance(action, payload)`` façade until the end of M2.  S3 has now
    exported every capability as a typed function (AI-GOV-001, AI-GOV-009), so the mapping
    went straight to those functions and **no façade reference remains in S1** -- which is
    what ``test_no_s1_code_reaches_the_governance_facade`` now holds open.
    """

    PROVIDER_PACKAGE = "factory_intelligence"
    PROVIDER_LABEL = "S3"
    METHOD_MAP = {
        "parse_input": ("parse_input", dto.ParsedInput),
        "list_llm_exchanges": ("list_llm_exchanges", dto.LlmExchangePage),
        "get_llm_exchange": ("get_llm_exchange", dto.LlmExchangeDetail),
        "purge_llm_exchanges": ("purge_llm_exchanges", None),
        "start_agent_run": ("start_agent_run", dto.AgentRunResult),
        "resume_agent_run": ("resume_agent_run", dto.AgentRunResult),
        "cancel_agent_run": ("cancel_agent_run", None),
        "get_agent_run": ("get_agent_run", dto.AgentRunResult),
        "explain_issues": ("explain_issues", None),
        "run_evaluation": ("run_evaluation", None),
        # governance, one entry per capability (CP-UI-006 item 6)
        "list_providers": ("list_providers", dto.ProviderView),
        "register_provider": ("register_provider", dto.ProviderView),
        "update_provider": ("update_provider", dto.ProviderView),
        "set_provider_enabled": ("set_provider_enabled", dto.EnableResult),
        "check_provider_health": ("check_provider_health", dto.ProviderHealthView),
        "list_models": ("list_models", dto.ModelView),
        "register_model": ("register_model", dto.ModelView),
        "update_model": ("update_model", dto.ModelView),
        "set_model_enabled": ("set_model_enabled", dto.EnableResult),
        "list_prompts": ("list_prompts", dto.PromptView),
        "get_prompt": ("get_prompt", dto.PromptDetailView),
        "activate_prompt": ("activate_prompt", dto.ActivationView),
        "record_regression": ("record_regression", dto.RegressionView),
        "get_org_policy": ("get_org_policy", dto.OrgPolicyView),
        "relax_org_policy": ("relax_org_policy", dto.RelaxationView),
        "get_effective_policy": ("get_effective_policy", dto.EffectivePolicyView),
        "list_evaluations": ("list_evaluations", dto.EvaluationSummary),
        "get_evaluation": ("get_evaluation", dto.EvaluationDetail),
        "get_budget": ("get_budget", dto.BudgetView),
        "set_budget": ("set_budget", dto.BudgetView),
        "stage_chat": ("stage_chat", dto.ChatReply),
        "get_chat": ("get_chat", None),
        "clear_chat": ("clear_chat", None),
        "usage_rollup": ("usage_rollup", dto.UsageRollup),
        "list_agents": ("list_agents", dto.AgentCatalog),
        # S1 spelled this ``list_prompt_activations``; the provider function is ``list_activations``.
        "list_prompt_activations": ("list_activations", dto.ActivationView),
        "list_regressions": ("list_regressions", dto.RegressionView),
        "delete_project_data": ("delete_project_data", None),
    }
    EXTERNAL_IO_METHODS = frozenset(
        {
            "parse_input",
            "start_agent_run",
            "resume_agent_run",
            "explain_issues",
            "run_evaluation",
            # the probe opens a socket to the provider's address, so it must not run while the
            # caller's transaction is holding a connection open
            "check_provider_health",
            # a dialogue turn calls a model, and its transcript is read from object storage
            "stage_chat",
            "get_chat",
        }
    )

    def get_chat(self, project_id: str, stage: str) -> list[dict[str, Any]]:
        """The one translation S3's transcript needs: the port promises ``list[dict]``.

        ``factory_intelligence.api.get_chat`` answers ``runtime/chat.py Transcript``, a plain
        ``@dataclass``, and :mod:`factory_control.adapters.delegating` coerces only pydantic
        models, dicts and lists — so the generic binding handed the object through and
        ``services/chat.transcript`` iterated something that is not iterable.  That 500 appears
        only with ``FACTORY_PROVIDER_MODE=real``, because the fake answers the port shape.
        """
        self._assert_outside_transaction("get_chat")
        value = self._function("get_chat")(project_id, stage)
        turns = getattr(value, "turns", None)
        if turns is None:
            # the fake's shape, or a provider that already answers the port declaration
            turns = value or []
        return [dict(item) for item in turns]


class ExecutionAdapter(DelegatingAdapter):
    """``factory_dhis2.api`` (S4 SRS 7)."""

    PROVIDER_PACKAGE = "factory_dhis2"
    PROVIDER_LABEL = "S4"
    METHOD_MAP = {
        "get_environment": ("get_environment", dto.EnvironmentInfo),
        "list_environments": ("list_environments", dto.EnvironmentInfo),
        "create_environment": ("create_environment", dto.EnvironmentInfo),
        "update_environment": ("update_environment", dto.EnvironmentInfo),
        "disable_environment": ("disable_environment", None),
        "get_protection": ("get_protection", dto.EnvironmentProtection),
        "set_emergency_lock": ("set_emergency_lock", None),
        "list_expiring_credentials": ("list_expiring_credentials", dto.ExpiringCredential),
        "test_connection": ("test_connection", dto.ConnectionCheck),
        # The name is SRS 7.1's; S1 spelled this read ``last_connection_check`` first,
        # which is why S4 also exports that name as an alias.
        "get_last_connection_test": ("get_last_connection_test", dto.ConnectionCheck),
        "check_capability_freshness": ("check_capability_freshness", dto.CapabilityFreshness),
        "list_discovery_results": ("list_discovery_results", dto.DiscoveryResultInfo),
        "search_discovery_objects": ("search_discovery_objects", None),
        "probe_capabilities": ("probe_capabilities", None),
        "discover": ("discover", dto.DiscoveryOutcome),
        "prepare_and_validate": ("prepare_and_validate", dto.Dhis2ValidationOutcome),
        "check_drift": ("check_drift", dto.DriftResult),
        "commit": ("commit", dto.ExecutionOutcome),
        "get_execution_by_key": ("get_execution_by_key", dto.ExecutionOutcome),
        "get_execution": ("get_execution", dto.ExecutionOutcome),
        "reconcile": ("reconcile", dto.ReconcileResult),
        "resolve_manual_check": ("resolve_manual_check", None),
        "post_verify": ("post_verify", dto.PostVerifyOutcome),
        "import_fixtures": ("import_fixtures", dto.ExecutionOutcome),
        # DX-EXE-038.  The port keeps S4's own argument order and names (``environment_key,
        # project_id, deletion_artifact_id, expected_hash`` then keyword-only), because the
        # delegating adapter forwards positional and keyword arguments untouched.
        "delete_objects": ("delete_objects", dto.ExecutionOutcome),
        "delete_project_data": ("delete_project_data", None),
    }
    EXTERNAL_IO_METHODS = frozenset(
        {
            "test_connection",
            "check_capability_freshness",
            "probe_capabilities",
            "discover",
            "prepare_and_validate",
            "check_drift",
            # Reads the stored DISCOVERY_RESULT only — never the instance (S5IF-BP11) —
            # but it reads artifact bytes, so it is external I/O in S4's own regime.
            "search_discovery_objects",
            "commit",
            "reconcile",
            "post_verify",
            "import_fixtures",
            # S4 marks it ``@external_io`` and it submits an import to the instance, so an open
            # unit of work here would raise inside S4 rather than at the seam (00 doc 6.2).
            "delete_objects",
        }
    )


def default_ports(
    *,
    settings: Settings,
    blueprint: BlueprintPort | None = None,
    intelligence: IntelligencePort | None = None,
    execution: ExecutionPort | None = None,
) -> dict[str, Any]:
    """Resolve the three ports, honouring ``FACTORY_PROVIDER_MODE``."""
    from factory_control.testing.fakes import FakeBlueprint, FakeExecution, FakeIntelligence

    mode = settings.provider_mode

    def pick(explicit: Any, adapter: DelegatingAdapter, fake: Any) -> Any:
        if explicit is not None:
            return explicit
        if mode == _MODE_FAKES:
            return fake()
        if adapter.available():
            return adapter
        if mode == _MODE_REAL:
            raise RuntimeError(
                f"FACTORY_PROVIDER_MODE=real but {adapter.PROVIDER_PACKAGE} is not installed"
            )
        return fake()

    ports = {
        "blueprint": pick(blueprint, BlueprintAdapter(), FakeBlueprint),
        "intelligence": pick(intelligence, IntelligenceAdapter(), FakeIntelligence),
        "execution": pick(execution, ExecutionAdapter(), FakeExecution),
    }
    _wire_blueprint_reads(ports)
    # A development process has no real DHIS2 instance to point at, so the target
    # environments it will register on the project must exist on the fake.
    if settings.fake_environments and type(ports["execution"]).__name__ == "FakeExecution":
        for item in settings.fake_environments.split(","):
            key, _, environment_class = item.partition(":")
            if key.strip():
                ports["execution"].add_environment(key.strip(), environment_class=(environment_class or "TEST").strip())
    return ports


def _wire_blueprint_reads(ports: dict[str, Any]) -> None:
    """Tell a real S2 which DHIS2 its four read-only questions go to.

    S2 asks them through its own provider slot rather than through S1's execution port,
    so whoever resolves the ports has to fill that slot: the real S4 when S1 got one,
    S2's published in-memory double when it did not.  S2 keeps no fallback of its own on
    purpose — a pool of UIDs no instance generated, or "free" answered for a number that
    is taken, is corrupted data rather than a degraded mode (S2 SRS chapter 8).
    """
    blueprint = ports.get("blueprint")
    if blueprint is None or type(blueprint).__name__.startswith("Fake"):
        return

    import importlib

    deps = importlib.import_module("factory_blueprint.deps")
    execution = ports.get("execution")
    if type(execution).__name__.startswith("Fake"):
        stub = importlib.import_module("factory_blueprint.s4.stub")
        deps.set_dhis2(stub.StubDhis2())
    else:
        adapter = importlib.import_module("factory_blueprint.s4.adapter")
        deps.set_dhis2(adapter.S4Adapter())


def provider_modes(ports: dict[str, Any]) -> dict[str, str]:
    """Which implementation backs each port — reported by ``/healthz``."""
    out: dict[str, str] = {}
    for label, port in (("S2", ports.get("blueprint")), ("S3", ports.get("intelligence")), ("S4", ports.get("execution"))):
        name = type(port).__name__
        out[label] = "fake" if name.startswith("Fake") else "real"
    return out


__all__ = [
    "BlueprintAdapter",
    "ExecutionAdapter",
    "IntelligenceAdapter",
    "default_ports",
    "provider_modes",
]
