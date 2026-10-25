"""The in-memory Fake API of S2 (BP-API-001, 00 doc 6.1).

S1 and S3 develop against S2's interface, not against its database.  This module
implements every function of :mod:`factory_blueprint.api` in memory, with the same
DTOs and the same error classes, so a caller can be tested without a PostgreSQL
instance and can be shown answers the real S2 would never produce on its own — a
blocked compile, an empty UID pool, a version that cannot be promoted.

Nothing here reads or writes the database.  A Fake that quietly called into the real
services would make S1's tests depend on S2 being healthy, which is the thing this
file exists to prevent.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from factory_foundation.api import ArtifactError, FactoryError, NotFoundError, Page, content_hash

from factory_blueprint.artifact_reads import (
    ARTIFACT_TYPE_MISMATCH,
    REASON_NOT_EXIST,
    REASON_NOT_GENERATED,
    narrow_graph,
    package_media_type,
    validate_package_path,
)
from factory_blueprint.compilers.package import package_file_kind
from factory_blueprint.dto.blueprint import (
    CurrentVersion,
    EntrySelector,
    InitialProjectInfo,
    ProjectSemantics,
    SectionCatalog,
    SelectedEntries,
    VersionInfo,
)
from factory_blueprint.dto.build import BuildContext, BuildResult, ValidationSummary
from factory_blueprint.dto.diff import DiffReport, ObjectDiff
from factory_blueprint.dto.graph import DependencyGraph, GraphFilters, ObjectRelation
from factory_blueprint.dto.issue import Issue, make_issue, sort_issues
from factory_blueprint.dto.manifest import PackageManifest
from factory_blueprint.dto.package_files import (
    PackageFileContent,
    PackageFileEntry,
    PackageFileListing,
)
from factory_blueprint.dto.patch import (
    DraftInfo,
    FreezeResult,
    ImpactSet,
    Patch,
    PatchContext,
    PatchOutcome,
    PromoteResult,
)
from factory_blueprint.dto.portability import (
    ExportResult,
    ImportPreview,
    ImportResult,
    ProjectCleanupResult,
)
from factory_blueprint.dto.registry import (
    BindingInfo,
    DecisionInfo,
    ManagedObject,
    RegistryEntry,
    UidPoolStatus,
)
from factory_blueprint.enums import (
    BindingResolution,
    BuildKind,
    BuildStatus,
    ChangeLevel,
    DeliveryMode,
    DesignStage,
    Disposition,
    DraftStatus,
    IssueLevel,
    PatchStatus,
    ProjectType,
    Section,
    VersionStatus,
)
from factory_blueprint.errors import BlueprintError, UidPoolExhausted
from factory_blueprint.impact.stage_map import STAGE_SCOPE

DEFAULT_BUILD_AT = datetime(2026, 1, 1, tzinfo=UTC)
FAKE_HASH = "sha256:" + "f" * 64


@dataclass
class _Draft:
    id: str
    project_id: str
    base_version: str
    stage: DesignStage
    stage_scope: list[Section] = field(default_factory=list)
    content: dict[str, Any] = field(default_factory=dict)
    status: DraftStatus = DraftStatus.OPEN
    frozen_hash: str | None = None
    patches: list[PatchOutcome] = field(default_factory=list)


@dataclass
class _Project:
    info: InitialProjectInfo
    version: str = "0.1.0"
    change_level: ChangeLevel = ChangeLevel.COSMETIC
    document: dict[str, Any] = field(default_factory=dict)
    history: list[VersionInfo] = field(default_factory=list)
    registry: list[RegistryEntry] = field(default_factory=list)
    relationships: list[ObjectRelation] = field(default_factory=list)
    manifest: PackageManifest | None = None
    decisions: list[DecisionInfo] = field(default_factory=list)
    bindings: list[BindingInfo] = field(default_factory=list)
    pool: list[str] = field(default_factory=list)
    drafts: dict[str, _Draft] = field(default_factory=dict)
    exports: dict[str, dict[str, Any]] = field(default_factory=dict)

    def current(self) -> VersionInfo:
        return self.history[-1] if self.history else _version_info(self)


@dataclass
class _Package:
    """A development package the Fake was told about, and what it holds (BP-HO-004)."""

    files: dict[str, bytes] = field(default_factory=dict)
    project_id: str = "fake"
    blueprint_version: str = "0.1.0"


def _bump_minor(version: str) -> str:
    """`0.1.0` -> `0.2.0`: appendix B makes A -> B a Minor change."""
    major, minor, _patch = (int(part) for part in version.split("."))
    return f"{major}.{minor + 1}.0"


def _section(project: _Project, name: str, container: str) -> list[dict[str, Any]]:
    section = project.document.get(name) or {}
    items = section.get(container) if isinstance(section, dict) else None
    return [item for item in (items or []) if isinstance(item, dict)]


def _version_info(project: _Project) -> VersionInfo:
    return VersionInfo(
        blueprint_id="bpv-fake",
        project_id=project.info.display_name or "fake",
        version=project.version,
        schema_version=1,
        change_level=project.change_level,
        content_hash=FAKE_HASH,
        artifact_id="art-fake",
        status=VersionStatus.CURRENT.value,
        created_by="fake",
        created_at=DEFAULT_BUILD_AT.isoformat(),
    )


class FakeBlueprintApi:
    """Every S2 API function, answered from dictionaries.

    Preset what a call must return by writing into the matching attribute before
    calling it: :attr:`issues` for validation problems, :attr:`build_status` for a
    compile outcome, :attr:`impact` for an impact answer, :attr:`pool_size` for the UID
    pool, :attr:`graphs` for one build's dependency graph and :attr:`packages` for a
    development package's files.  :attr:`calls` records every call so a test can assert
    the interaction.
    """

    def __init__(self) -> None:
        self.projects: dict[str, _Project] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.issues: list[Issue] = []
        #: what each report artifact found, for ``query_issues``
        self.report_issues: dict[str, list[Issue]] = {}
        #: what each draft changed, for ``diff_draft``
        self.draft_changes: dict[str, list[ObjectDiff]] = {}
        #: staged delivery-mode upgrades, by handle
        self.mode_upgrades: dict[str, str] = {}
        self.build_status: BuildStatus = BuildStatus.COMPLETED
        self.impact: ImpactSet | None = None
        self.pool_size: int = 100
        self.fail_compile_with: str | None = None
        #: every build the Fake handed out, by build id, so a product read can tell an
        #: unknown id from a build that produced nothing yet
        self.builds: dict[str, BuildResult] = {}
        #: the graph of one build, preset by build id; absent means "not generated"
        self.graphs: dict[str, DependencyGraph] = {}
        #: the development packages this Fake knows, by FRONTEND_PACKAGE artifact id
        self.packages: dict[str, _Package] = {}
        #: artifacts that exist but are not development packages, by id and actual type
        self.non_package_artifacts: dict[str, str] = {}
        self._sequence = 0

    # --- harness -----------------------------------------------------------
    def reset(self) -> None:
        self.__init__()  # type: ignore[misc]

    def seed(self, project_id: str, info: InitialProjectInfo | None = None) -> _Project:
        """Create a project the Fake already knows about, with one version."""
        recorded = self.projects.get(project_id)
        if recorded is not None:
            return recorded
        project = _Project(info=info or _default_info(), document={"project": {}})
        project.history.append(_version_info(project))
        self.projects[project_id] = project
        return project

    def _note(self, name: str, **args: Any) -> None:
        self.calls.append((name, args))

    def _require(self, project_id: str) -> _Project:
        project = self.projects.get(project_id)
        if project is None:
            raise NotFoundError(
                f"unknown project {project_id}", details={"projectId": project_id}
            )
        return project

    def _next(self, prefix: str) -> str:
        self._sequence += 1
        return f"{prefix}-fake{self._sequence:05d}"

    # --- 7.1 initialization and schema -------------------------------------
    def prepare_initial_blueprint(
        self, project_id: str, project_info: InitialProjectInfo
    ) -> str:
        self._note("prepare_initial_blueprint", project_id=project_id)
        project = self.projects.setdefault(project_id, _Project(info=project_info))
        project.info = project_info
        return f"shd-fake-{project_id}"

    def register_initial_blueprint(self, project_id: str, handle: str) -> VersionInfo:
        self._note("register_initial_blueprint", project_id=project_id, handle=handle)
        project = self.seed(project_id)
        project.history.clear()
        project.history.append(_version_info(project))
        return project.history[-1]

    # --- 7.2 versions and queries -----------------------------------------
    def get_current_version(self, project_id: str) -> CurrentVersion:
        self._note("get_current_version", project_id=project_id)
        project = self._require(project_id)
        return CurrentVersion(
            version=project.version,
            content_hash=FAKE_HASH,
            artifact_id="art-fake",
            change_level=project.change_level,
            schema_version=1,
        )

    def get_version(self, project_id: str, version: str) -> VersionInfo:
        self._note("get_version", project_id=project_id, version=version)
        project = self._require(project_id)
        return _version_info(project).model_copy(update={"version": version})

    def list_versions(self, project_id: str, page: int = 1, page_size: int = 50) -> Page[VersionInfo]:
        self._note("list_versions", project_id=project_id, page=page)
        items = self._require(project_id).history
        start = (page - 1) * page_size
        return Page(items=items[start : start + page_size], total=len(items), limit=page_size, offset=start)

    def get_project_semantics(self, project_id: str, version: str | None = None) -> ProjectSemantics:
        self._note("get_project_semantics", project_id=project_id)
        project = self._require(project_id)
        info = project.info
        return ProjectSemantics(
            project_id=project_id,
            version=version or project.version,
            delivery_mode=info.delivery_mode,
            project_type=info.project_type,
            default_language=info.default_language,
            translation_languages=sorted(info.translation_languages),
            target_dhis2_version_constraint=info.target_dhis2_version_constraint,
            has_fixture_specifications=bool((project.document.get("fixtures") or {}).get("specifications")),
            mapping_frozen=project.version != "0.1.0",
        )

    def get_entries(self, project_id: str, selector: EntrySelector) -> SelectedEntries:
        self._note("get_entries", project_id=project_id, selector=selector)
        project = self._require(project_id)
        entries = _select(project.document, selector)
        return SelectedEntries(
            project_id=project_id,
            version=project.version,
            source=selector.source,
            entries=entries,
            not_found=sorted(set(selector.business_keys) - {str(item.get("businessKey")) for item in entries}),
        )

    def get_decisions(self, project_id: str, decision_ids: list[str]) -> list[DecisionInfo]:
        self._note("get_decisions", project_id=project_id, decision_ids=decision_ids)
        project = self._require(project_id)
        wanted = set(decision_ids)
        return [
            item for item in project.decisions if not wanted or item.decision_id in wanted
        ]

    def diff_versions(
        self, project_id: str, from_version: str, to_version: str, sections: list[str] | None = None
    ) -> DiffReport:
        self._note("diff_versions", project_id=project_id)
        return DiffReport(
            project_id=project_id,
            kind="BLUEPRINT_VERSIONS",
            from_version=from_version,
            to_version=to_version,
            objects=[],
            counts={},
        )

    def diff_draft(self, draft_id: str, sections: list[str] | None = None) -> DiffReport:
        self._note("diff_draft", draft_id=draft_id)
        for project in self.projects.values():
            draft = project.drafts.get(draft_id)
            if draft is not None:
                return DiffReport(
                    project_id=draft.project_id,
                    kind="DRAFT_VS_BASE",
                    from_version=draft.base_version,
                    to_version=draft_id,
                    objects=list(self.draft_changes.get(draft_id) or []),
                    counts={},
                )
        raise NotFoundError(f"unknown draft {draft_id}", details={"draftId": draft_id})

    def register_draft_changes(self, draft_id: str, changes: list[ObjectDiff]) -> None:
        """Say what the draft changed, so a caller's review view has something to show."""
        self.draft_changes[draft_id] = list(changes)

    # --- 7.3 drafts and patches -------------------------------------------
    def open_draft(
        self,
        project_id: str,
        stage: DesignStage | str,
        change_request_id: str | None = None,
        sections: list[Section] | None = None,
    ) -> str:
        self._note("open_draft", project_id=project_id, stage=str(stage))
        project = self._require(project_id)
        design_stage = DesignStage(stage)
        for draft in project.drafts.values():
            if draft.status in (DraftStatus.OPEN, DraftStatus.FROZEN):
                return draft.id
        draft = _Draft(
            id=self._next("dft"),
            project_id=project_id,
            base_version=project.version,
            stage=design_stage,
            stage_scope=sorted(
                STAGE_SCOPE.get(design_stage, frozenset()), key=lambda item: item.value
            ),
            content=dict(project.document),
        )
        project.drafts[draft.id] = draft
        return draft.id

    def prepare_draft_base(self, project_id: str, draft_id: str) -> str:
        self._note("prepare_draft_base", project_id=project_id, draft_id=draft_id)
        draft = self._draft(project_id, draft_id)
        return str(draft.content.get("_hash") or FAKE_HASH)

    def submit_patch(
        self, project_id: str, draft_id: str, patch: Patch, context: PatchContext
    ) -> PatchOutcome:
        self._note("submit_patch", project_id=project_id, draft_id=draft_id, patch=patch.patch_id)
        project = self._require(project_id)
        draft = self._draft(project_id, draft_id)
        blocking = [issue for issue in self.issues if issue.level is IssueLevel.BLOCKING]
        if blocking:
            return PatchOutcome(
                patch_id=patch.patch_id,
                status=PatchStatus.REJECTED,
                draft_id=draft_id,
                new_draft_hash=draft.frozen_hash or FAKE_HASH,
                issues=blocking,
                blocking_count=len(blocking),
            )
        outcome = PatchOutcome(
            patch_id=patch.patch_id,
            status=PatchStatus.PROPOSED,
            draft_id=draft_id,
            new_draft_hash=FAKE_HASH,
            assigned_uids={
                str(entry.get("businessKey")): f"fake{index:07d}"
                for index, entry in enumerate(_new_creates(patch))
            },
            blocking_count=0,
        )
        draft.patches.append(outcome)
        project.document = _apply(preview=False, document=project.document, patch=patch)
        draft.content = dict(project.document)
        return outcome

    def get_draft(self, draft_id: str, project_id: str | None = None) -> DraftInfo:
        self._note("get_draft", draft_id=draft_id)
        for project in self.projects.values():
            draft = project.drafts.get(draft_id)
            if draft is not None:
                return DraftInfo(
                    draft_id=draft.id,
                    project_id=draft.project_id,
                    base_version=draft.base_version,
                    stage=draft.stage,
                    stage_scope=draft.stage_scope,
                    status=draft.status,
                    content_hash=FAKE_HASH,
                    frozen_hash=draft.frozen_hash,
                    open_patch_count=len(draft.patches),
                )
        raise NotFoundError(f"unknown draft {draft_id}", details={"draftId": draft_id})

    def get_active_draft(self, project_id: str) -> DraftInfo | None:
        """The one open or frozen draft, or nothing (BP-DRF-011)."""
        self._note("get_active_draft", project_id=project_id)
        project = self._require(project_id)
        for draft in project.drafts.values():
            if draft.status in (DraftStatus.OPEN, DraftStatus.FROZEN):
                return self.get_draft(draft.id, project_id)
        return None

    def reset_draft(self, draft_id: str, project_id: str | None = None) -> int:
        self._note("reset_draft", draft_id=draft_id)
        for project in self.projects.values():
            draft = project.drafts.get(draft_id)
            if draft is not None:
                discarded = len(draft.patches)
                draft.patches.clear()
                draft.content = dict(project.document)
                return discarded
        raise NotFoundError(f"unknown draft {draft_id}", details={"draftId": draft_id})

    def freeze_draft(self, draft_id: str, target_environments: list[str] | None = None) -> FreezeResult:
        self._note("freeze_draft", draft_id=draft_id, environments=target_environments)
        draft = next(
            (item for project in self.projects.values() for item in project.drafts.values() if item.id == draft_id),
            None,
        )
        if draft is None:
            raise NotFoundError(f"unknown draft {draft_id}", details={"draftId": draft_id})
        blocking = [issue for issue in self.issues if issue.level is IssueLevel.BLOCKING]
        draft.status = DraftStatus.FROZEN
        draft.frozen_hash = FAKE_HASH
        return FreezeResult(
            draft_id=draft_id,
            draft_hash=FAKE_HASH,
            staging_handle=f"shd-fake-{draft_id}",
            promotable=not blocking,
            issues=blocking,
        )

    def promote_draft(
        self, draft_id: str, expected_hash: str, idempotency_key: str
    ) -> PromoteResult:
        self._note("promote_draft", draft_id=draft_id, expected_hash=expected_hash)
        project = next(
            item for item in self.projects.values() if draft_id in item.drafts
        )
        level = ChangeLevel.MAJOR if self.impact else ChangeLevel.MINOR
        project.version = "0.2.0"
        project.change_level = level
        project.history.append(_version_info(project))
        project.drafts[draft_id].status = DraftStatus.PROMOTED
        return PromoteResult(
            version=project.version,
            change_level=level.value,
            artifact_id="art-fake",
            content_hash=FAKE_HASH,
            affected_stages=[DesignStage.MAPPING],
            impact=self.impact or ImpactSet(sections=[Section.REGISTRY], stages=[DesignStage.MAPPING]),
            applied_patch_ids=[item.patch_id for item in project.drafts[draft_id].patches],
        )

    # --- 7.4 impact and registry ------------------------------------------
    def analyze_impact(
        self, project_id: str, business_keys: list[str], description: str | None = None
    ) -> ImpactSet:
        self._note("analyze_impact", project_id=project_id, business_keys=business_keys)
        self._require(project_id)
        if self.impact is not None:
            return self.impact
        return ImpactSet(
            sections=[Section.REGISTRY],
            stages=[DesignStage.MAPPING],
            business_keys=sorted(business_keys),
            artifact_types=["METADATA_PACKAGE", "VALIDATION_REPORT"],
        )

    def get_registry(
        self, project_id: str, version: str, environment_key: str | None = None
    ) -> list[RegistryEntry]:
        self._note("get_registry", project_id=project_id, environment_key=environment_key)
        return list(self._require(project_id).registry)

    def get_object_relationships(
        self,
        project_id: str,
        version: str | None = None,
        dhis2_types: Sequence[str] | None = None,
        edge_kinds: Sequence[str] | None = None,
    ) -> list[ObjectRelation]:
        """The same narrowing as the real read: an edge is kept when *either* end matches.

        A double that narrowed differently would let a caller test a filtered graph that answers
        something else in production (BP-IMP-004).
        """
        self._note("get_object_relationships", project_id=project_id, version=version)
        rows = list(self._require(project_id).relationships)
        if dhis2_types:
            kept = set(dhis2_types)
            rows = [row for row in rows if kept & {row.from_dhis2_type, row.to_dhis2_type}]
        if edge_kinds:
            kinds = set(edge_kinds)
            rows = [row for row in rows if row.edge_kind in kinds]
        return rows

    def get_package_manifest(
        self,
        project_id: str,
        version: str | None = None,
        environment_key: str | None = None,
    ) -> PackageManifest:
        """The files of the last build of that version, or an empty manifest.

        A caller that has never compiled must get no files rather than a plausible layout, which
        is what the real read answers too (BP-CMP-021).
        """
        self._note("get_package_manifest", project_id=project_id, version=version)
        return self._require(project_id).manifest or PackageManifest(
            project_id=project_id,
            blueprint_version=version or "0.1.0",
            build_id="",
            kind="NONE",
            environment_key=environment_key,
        )

    def get_managed_scope(
        self, project_id: str, version: str, environment_key: str
    ) -> list[ManagedObject]:
        self._note("get_managed_scope", project_id=project_id, environment_key=environment_key)
        return [
            ManagedObject(
                business_key=entry.business_key,
                dhis2_type=entry.dhis2_type,
                uid=entry.uid or "",
                disposition=entry.disposition,
                code=entry.code,
                controlled_fields=entry.controlled_fields,
            )
            for entry in self._require(project_id).registry
            if entry.managed
        ]

    def get_bindings(
        self,
        project_id: str,
        version: str,
        environment_keys: Sequence[str] | str | None = None,
    ) -> list[BindingInfo]:
        self._note("get_bindings", project_id=project_id, version=version)
        project = self._require(project_id)
        if isinstance(environment_keys, str):
            environment_keys = [environment_keys]
        if not environment_keys:
            return list(project.bindings)
        wanted = set(environment_keys)
        return [item for item in project.bindings if item.environment_key in wanted]

    #: chapter 7.4's name for the same read
    get_environment_bindings = get_bindings

    def replenish_uid_pool(
        self, project_id: str, environment_key: str, min_available: int | None = None
    ) -> int:
        self._note("replenish_uid_pool", project_id=project_id, environment_key=environment_key)
        project = self._require(project_id)
        wanted = min_available if min_available is not None else self.pool_size
        while len(project.pool) < wanted:
            project.pool.append(self._next("uid"))
        return len(project.pool)

    def get_uid_pool_status(self, project_id: str) -> UidPoolStatus:
        self._note("get_uid_pool_status", project_id=project_id)
        available = len(self._require(project_id).pool) or self.pool_size
        return UidPoolStatus(
            project_id=project_id,
            available=available,
            low_water_mark=50,
            needs_replenishment=available <= 50,
        )

    def set_environment_binding(
        self,
        project_id: str,
        version: str,
        environment_key: str,
        business_key: str,
        uid: str,
        note: str,
        resolved_by: str = "fake",
    ) -> BindingInfo:
        self._note("set_environment_binding", project_id=project_id, business_key=business_key)
        binding = BindingInfo(
            environment_key=environment_key,
            business_key=business_key,
            uid=uid,
            resolution=BindingResolution.MANUAL,
            resolved_by=resolved_by,
            note=note,
            # a hand-set binding is a claim until an environment compile confirms it
            # against that environment's discovery; callers see that in the real one, so
            # the Fake says it too rather than hiding a state S1 has to handle
            pending_reparse=True,
        )
        project = self._require(project_id)
        project.bindings = [
            *(
                item
                for item in project.bindings
                if not (item.environment_key == environment_key and item.business_key == business_key)
            ),
            binding,
        ]
        return binding

    # --- 7.5 compile, validate, reproduce ---------------------------------
    def compile_baseline(
        self, project_id: str, version: str, capability_profile_artifact_id: str | None, idempotency_key: str
    ) -> BuildResult:
        self._note("compile_baseline", project_id=project_id, version=version)
        return self._build(project_id, version, None, BuildKind.BASELINE, idempotency_key)

    def compile_for_environment(
        self,
        project_id: str,
        version: str,
        environment_key: str,
        discovery_artifact_id: str,
        capability_profile_artifact_id: str,
        change_request_id: str | None,
        idempotency_key: str,
    ) -> BuildResult:
        self._note(
            "compile_for_environment",
            project_id=project_id,
            environment_key=environment_key,
            change_request_id=change_request_id,
        )
        return self._build(project_id, version, environment_key, BuildKind.ENVIRONMENT, idempotency_key)

    def _build(
        self,
        project_id: str,
        version: str,
        environment_key: str | None,
        kind: BuildKind,
        idempotency_key: str,
    ) -> BuildResult:
        self._require(project_id)
        if self.fail_compile_with is not None:
            raise UidPoolExhausted(self.fail_compile_with, details={"projectId": project_id})
        blocking = [issue for issue in self.issues if issue.level is IssueLevel.BLOCKING]
        status = BuildStatus.FAILED if blocking or self.build_status is BuildStatus.FAILED else BuildStatus.COMPLETED
        artifacts = {
            "VALIDATION_REPORT": "art-fake-validation",
            "MANIFEST": "art-fake-manifest",
        }
        if kind is BuildKind.BASELINE:
            artifacts["METADATA_BASELINE"] = "art-fake-baseline"
        else:
            artifacts["METADATA_PACKAGE"] = "art-fake-package"
            artifacts["DIFF_REPORT"] = "art-fake-diff"
        result = BuildResult(
            build_id=self._next("bld"),
            kind=kind,
            project_id=project_id,
            version=version,
            environment_key=environment_key,
            status=status,
            artifact_ids=artifacts,
            output_hashes={key: FAKE_HASH for key in artifacts},
            package_hash=FAKE_HASH,
            payload_hash=FAKE_HASH,
            validation=ValidationSummary(
                report_artifact_id=artifacts["VALIDATION_REPORT"],
                blocking_count=len(blocking),
                warning_count=sum(1 for issue in self.issues if issue.level is IssueLevel.WARNING),
                info_count=sum(1 for issue in self.issues if issue.level is IssueLevel.INFO),
                warnings=[issue for issue in self.issues if issue.level is IssueLevel.WARNING],
                issues_digest=FAKE_HASH,
            ),
            binding_issues=[
                issue for issue in self.issues if issue.rule_id.startswith("BPV-BIND")
            ],
        )
        self.builds[result.build_id] = result
        return result

    def validate_build(self, build_id: str, idempotency_key: str) -> BuildResult:
        self._note("validate_build", build_id=build_id)
        return BuildResult(
            build_id=build_id,
            kind=BuildKind.BASELINE,
            project_id="fake",
            version="0.1.0",
            status=BuildStatus.COMPLETED if not self.issues else BuildStatus.FAILED,
            artifact_ids={"VALIDATION_REPORT": "art-fake-validation"},
            output_hashes={"VALIDATION_REPORT": FAKE_HASH},
            validation=ValidationSummary(
                report_artifact_id="art-fake-validation",
                blocking_count=sum(1 for issue in self.issues if issue.level is IssueLevel.BLOCKING),
                warning_count=sum(1 for issue in self.issues if issue.level is IssueLevel.WARNING),
                info_count=len(self.issues),
                warnings=[issue for issue in self.issues if issue.level is IssueLevel.WARNING],
                issues_digest=FAKE_HASH,
            ),
        )

    def reproduce(self, artifact_id: str) -> Any:
        from factory_blueprint.dto.build import ReproduceResult

        self._note("reproduce", artifact_id=artifact_id)
        return ReproduceResult(
            build_id="bld-fake",
            consistent=True,
            expected_hashes={"payload": FAKE_HASH},
            actual_hashes={"payload": FAKE_HASH},
            mismatched=[],
        )

    def query_issues(
        self,
        report_artifact_ids: list[str],
        *,
        level: str | None = None,
        source_stage: str | None = None,
        rule_id: str | None = None,
        business_key: str | None = None,
        sort_by: str = "level",
        sort_dir: str = "ASC",
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """The same read over the preset report issues, filtered and paged (BP-VAL-005)."""
        self._note("query_issues", report_ids=list(report_artifact_ids))
        rows: list[Issue] = []
        for report_id in report_artifact_ids:
            rows.extend(self.report_issues.get(report_id) or self.issues)
        matched = [
            issue
            for issue in rows
            if (level is None or issue.level.value == level.upper())
            and (source_stage is None or issue.source_stage is DesignStage(source_stage.upper()))
            and (rule_id is None or issue.rule_id == rule_id)
            and (business_key is None or issue.business_key == business_key)
        ]
        ordered = sort_issues(matched)
        return {
            "items": [issue.summary() for issue in ordered[offset : offset + limit]],
            "total": len(ordered),
            "limit": limit,
            "offset": offset,
        }

    def register_report_issues(self, report_artifact_id: str, issues: list[Issue]) -> None:
        """Say what one report found, so the cross-report read has something to return."""
        self.report_issues[report_artifact_id] = list(issues)

    def prepare_mode_upgrade(self, project_id: str) -> str:
        """Stage an A -> B upgrade in memory (SRS 7.3)."""
        self._note("prepare_mode_upgrade", project_id=project_id)
        project = self._require(project_id)
        if project.info.delivery_mode is not DeliveryMode.A:
            raise BlueprintError(
                f"project {project_id} is already delivery mode "
                f"{project.info.delivery_mode.value}; only A -> B is an upgrade",
                code="CONFLICT",
                details={"deliveryMode": project.info.delivery_mode.value},
            )
        handle = self._next("stg")
        self.mode_upgrades[handle] = project_id
        return handle

    def commit_mode_upgrade(
        self, project_id: str, handle: str, idempotency_key: str
    ) -> PromoteResult:
        self._note("commit_mode_upgrade", project_id=project_id, handle=handle)
        if handle not in self.mode_upgrades:
            raise NotFoundError(f"unknown mode upgrade {handle}", details={"handle": handle})
        project = self._require(project_id)
        object.__setattr__(project.info, "delivery_mode", DeliveryMode.B)
        project.version = _bump_minor(project.version)
        project.change_level = ChangeLevel.MINOR
        project.history.append(_version_info(project))
        del self.mode_upgrades[handle]
        return PromoteResult(
            version=project.version,
            change_level=ChangeLevel.MINOR.value,
            artifact_id="art-fake",
            content_hash=FAKE_HASH,
            affected_stages=[DesignStage.PAGE_DESIGN],
            impact=ImpactSet(
                sections=[Section.PAGES, Section.API_INTENTS, Section.UI_INTENTS],
                stages=[DesignStage.PAGE_DESIGN],
                artifact_types=["FRONTEND_PACKAGE", "OPENAPI", "UI_SPECIFICATION"],
            ),
        )

    # --- 7.5 frontend package and handoff ---------------------------------
    def generate_frontend_package(
        self,
        project_id: str,
        version: str,
        environment_keys: list[str],
        deployment_evidence: Any,
        build_context: BuildContext,
        idempotency_key: str,
    ) -> BuildResult:
        """A handoff build whose pages are ready unless the caller preset findings."""
        self._note(
            "generate_frontend_package",
            project_id=project_id,
            version=version,
            environment_keys=list(environment_keys),
        )
        project = self._require(project_id)
        pages = sorted({str(item.get("pageKey")) for item in _section(project, "pages", "items")})
        blocked = [issue for issue in self.issues if issue.level is IssueLevel.BLOCKING]
        ready = {} if blocked else {key: "FRONTEND_READY" for key in pages}
        package_id = self._next("art")
        result = BuildResult(
            build_id=self._next("bld"),
            kind=BuildKind.HANDOFF,
            project_id=project_id,
            version=version,
            environment_key=environment_keys[0] if environment_keys else None,
            status=BuildStatus.FAILED if blocked else self.build_status,
            artifact_ids={
                "FRONTEND_PACKAGE": package_id,
                "HANDOFF_CHECK_REPORT": self._next("art"),
            },
            output_hashes={"FRONTEND_PACKAGE": FAKE_HASH},
            package_hash=FAKE_HASH,
            handoff_pages=ready,
        )
        self.builds[result.build_id] = result
        # the package exists now, and holds whatever the test puts in it: an empty
        # listing is the honest answer for a package nobody described, not a made-up
        # 17.1 layout
        self.packages[package_id] = _Package(
            project_id=project_id, blueprint_version=version
        )
        return result

    # --- 7.5 product reads (BP-TD-009, BP-HO-004) -------------------------
    def _require_package(self, artifact_id: str) -> _Package:
        """The same three answers the real read gives (BP-HO-004).

        Unknown is ``NOT_EXIST``; an id preset in :attr:`non_package_artifacts` is
        ``ARTIFACT_TYPE_MISMATCH``, which is what lets S1 test its mapping of that code
        without a real artifact of the wrong type.  Whether a project has a package at all
        is S1's judgement, so the Fake never claims ``NOT_GENERATED`` here.
        """
        actual_type = self.non_package_artifacts.get(artifact_id)
        if actual_type is not None:
            raise FactoryError(
                f"artifact {artifact_id} is a {actual_type}, not a FRONTEND_PACKAGE",
                code=ARTIFACT_TYPE_MISMATCH,
                details={
                    "artifactId": artifact_id,
                    "expectedType": "FRONTEND_PACKAGE",
                    "actualType": actual_type,
                },
            )
        package = self.packages.get(artifact_id)
        if package is None:
            raise NotFoundError(
                f"unknown artifact {artifact_id}",
                details={"artifactId": artifact_id, "reason": REASON_NOT_EXIST},
            )
        return package

    def get_dependency_graph(
        self, build_id: str, filters: GraphFilters | None = None
    ) -> DependencyGraph:
        """The graph preset for one build the Fake handed out, narrowed like the real read.

        A build nobody gave a graph answers ``NOT_GENERATED`` rather than an empty one, which is
        what lets S1 exercise the "compile it first" empty state with no database at all
        (BP-AC-17).  The narrowing is production's ``narrow_graph``, so a filtered view cannot
        answer one thing here and another in S2.
        """
        self._note("get_dependency_graph", build_id=build_id, filters=filters)
        build = self.builds.get(build_id)
        if build is None:
            raise NotFoundError(
                f"unknown build {build_id}",
                details={"buildId": build_id, "reason": REASON_NOT_EXIST},
            )
        graph = self.graphs.get(build_id)
        if graph is None:
            raise NotFoundError(
                f"build {build_id} has produced no dependency graph",
                details={
                    "buildId": build_id,
                    "buildKind": build.kind.value,
                    "buildStatus": build.status.value,
                    "reason": REASON_NOT_GENERATED,
                },
            )
        return narrow_graph(graph, filters)

    def list_package_files(self, artifact_id: str) -> PackageFileListing:
        """The files of a package the Fake knows, classified and hashed by the real rules.

        ``package_file_kind`` and ``content_hash`` come from production, so a listing here
        cannot describe a layout the real package would not have (BP-AC-18).
        """
        self._note("list_package_files", artifact_id=artifact_id)
        package = self._require_package(artifact_id)
        return PackageFileListing(
            artifact_id=artifact_id,
            project_id=package.project_id,
            blueprint_version=package.blueprint_version,
            files=[
                PackageFileEntry(
                    path=path,
                    kind=package_file_kind(path),
                    size_bytes=len(blob),
                    content_hash=content_hash(blob),
                )
                for path, blob in sorted(package.files.items())
            ],
        )

    def read_package_file(self, artifact_id: str, path: str) -> PackageFileContent:
        """One preset file, with the real path guard and the listing's own hash."""
        self._note("read_package_file", artifact_id=artifact_id, path=path)
        package = self._require_package(artifact_id)
        validate_package_path(path, artifact_id)
        blob = package.files.get(path)
        if blob is None:
            raise NotFoundError(
                f"package {artifact_id} holds no file {path!r}",
                details={"artifactId": artifact_id, "path": path, "reason": REASON_NOT_EXIST},
            )
        try:
            content = blob.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ArtifactError(
                f"package file {path!r} is not UTF-8 text",
                details={"artifactId": artifact_id, "path": path},
            ) from error
        return PackageFileContent(
            path=path,
            kind=package_file_kind(path),
            content=content,
            media_type=package_media_type(path),
            size_bytes=len(blob),
            content_hash=content_hash(blob),
        )

    # --- 7.6 export, import, cleanup --------------------------------------
    def export_blueprint(self, project_id: str) -> ExportResult:
        self._note("export_blueprint", project_id=project_id)
        project = self._require(project_id)
        artifact_id = self._next("art")
        project.exports[artifact_id] = {
            "projectId": project_id,
            "version": project.version,
            "registry": [entry.model_dump(mode="json") for entry in project.registry],
            "bindings": [item.model_dump(mode="json") for item in project.bindings],
        }
        return ExportResult(
            project_id=project_id,
            artifact_id=artifact_id,
            content_hash=FAKE_HASH,
            files={"export_manifest.json": FAKE_HASH},
            versions=[item.version for item in project.history],
            current_version=project.version,
            referenced_artifact_ids=["art-fake"],
        )

    def prepare_import(self, export_artifact_id: str, id_map: dict[str, str]) -> ImportPreview:
        self._note("prepare_import", export_artifact_id=export_artifact_id)
        project_id = next(
            (
                key
                for key, item in self.projects.items()
                if export_artifact_id in item.exports
            ),
            "fake",
        )
        return ImportPreview(
            batch_id=self._next("imp"),
            project_id=project_id,
            source_project_id=project_id,
            versions=[self.projects[project_id].version] if project_id in self.projects else [],
            unmapped_artifact_ids=sorted(set(["art-fake"]) - set(id_map)),
        )

    def commit_import(self, project_id: str, batch_id: str, idempotency_key: str) -> ImportResult:
        self._note("commit_import", project_id=project_id, batch_id=batch_id)
        project = self.seed(project_id)
        return ImportResult(
            project_id=project_id,
            batch_id=batch_id,
            versions=[project.version],
            current_version=project.version,
            counts={"uidAllocation": len(project.pool)},
            pending_reparse=len(project.bindings),
        )

    def delete_project_data(self, project_id: str) -> ProjectCleanupResult:
        self._note("delete_project_data", project_id=project_id)
        project = self._require(project_id)
        retired = len(project.registry)
        project.registry = []
        project.bindings = []
        project.drafts.clear()
        project.history.clear()
        project.document = {}
        return ProjectCleanupResult(
            project_id=project_id,
            deleted={"blueprint_version": 1, "registry_entry": retired},
            retired_uids=retired,
            retained_uids=retired,
        )

    # --- schema data and provider injection (pure, so the Fake answers them too) --
    def get_section_schema(self, section: Section | str, schema_version: int | None = None) -> dict[str, Any]:
        """Section schemas are package data with no database behind them."""
        from factory_blueprint.schema.store import section_schema

        self._note("get_section_schema", section=str(section))
        return section_schema(section, schema_version)

    #: the SRS table calls this one get_section_schema; both names answer
    section_schema = get_section_schema

    def list_sections(self, schema_version: int | None = None) -> SectionCatalog:
        """The section catalog is package data too, so the Fake gives the real answer."""
        from factory_blueprint.schema.catalog import list_sections as real

        self._note("list_sections", schema_version=schema_version)
        return real(schema_version)

    def list_object_types(self) -> list[dict[str, Any]]:
        """Type descriptors are package data as well; a Fake that invented them would let
        a caller scope discovery against types the real S2 cannot compile."""
        from factory_blueprint.descriptors.registry import catalog as real

        self._note("list_object_types")
        return real()

    def document_schema(self, schema_version: int | None = None) -> dict[str, Any]:
        from factory_blueprint.schema.store import document_schema as real

        return real(schema_version)

    def current_schema_version(self) -> int:
        from factory_blueprint.schema.store import current_schema_version

        return current_schema_version()

    def available_versions(self) -> tuple[int, ...]:
        from factory_blueprint.schema.store import available_versions

        return available_versions()

    def migrate_document(self, document: dict[str, Any], frm: int, to: int) -> Any:
        from factory_blueprint.schema.migrations import migrate_document as real

        self._note("migrate_document", frm=frm, to=to)
        return real(document, frm, to)

    def has_migration_path(self, from_version: int, to_version: int) -> bool:
        from factory_blueprint.schema.migrations import has_migration_path

        return has_migration_path(from_version, to_version)

    def locate_entries(
        self, project_id: str, selector: EntrySelector, session: Any = None
    ) -> list[tuple[str, str, str]]:
        """The Fake answers from its own document, in the same ``(section, key, path)`` shape."""
        self._note("locate_entries", project_id=project_id)
        project = self._require(project_id)
        return sorted(
            (
                str(entry.get("_section") or ""),
                str(entry.get("businessKey") or ""),
                f"/{entry.get('_section')}/entries/{entry.get('businessKey')}",
            )
            for entry in _select(project.document, selector)
        )

    def set_dhis2(self, implementation: Any) -> None:
        """The Fake holds its own read-only DHIS2 double, never the process-wide one."""
        self._note("set_dhis2", implementation=type(implementation).__name__)
        self.dhis2 = implementation

    def get_dhis2(self) -> Any:
        from factory_blueprint.s4.stub import StubDhis2

        return getattr(self, "dhis2", None) or StubDhis2()

    def reset_providers(self) -> None:
        self.dhis2 = None

    def _draft(self, project_id: str, draft_id: str) -> _Draft:
        project = self._require(project_id)
        draft = project.drafts.get(draft_id)
        if draft is None:
            raise NotFoundError(f"unknown draft {draft_id}", details={"draftId": draft_id})
        return draft


def _default_info() -> InitialProjectInfo:
    return InitialProjectInfo(
        delivery_mode=DeliveryMode.B,
        project_type=ProjectType.BROWNFIELD,
        target_dhis2_version_constraint=">=2.41 <2.43",
        default_language="en",
        translation_languages=[],
    )


def _new_creates(patch: Patch) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for operation in patch.operations:
        value = operation.get("value")
        if (
            isinstance(value, dict)
            and str(operation.get("path", "")).startswith("/registry/entries")
            and value.get("disposition") == Disposition.CREATE.value
        ):
            out.append(value)
    return out


def _select(document: dict[str, Any], selector: EntrySelector) -> list[dict[str, Any]]:
    sections = {section.value for section in selector.sections}
    wanted = set(selector.business_keys)
    out: list[dict[str, Any]] = []
    for name, body in sorted(document.items()):
        if name in ("blueprintSchemaVersion",):
            continue
        if sections and name not in sections:
            continue
        for value in body.values() if isinstance(body, dict) else []:
            if not isinstance(value, list):
                continue
            for entry in value:
                if isinstance(entry, dict) and (not wanted or entry.get("businessKey") in wanted):
                    out.append({**entry, "_section": name})
    return out


def _apply(*, preview: bool, document: dict[str, Any], patch: Patch) -> dict[str, Any]:
    """The Fake's only content work: record where a patch wrote, nothing more.

    A faithful JSON Patch implementation belongs to S2, not to a test double; S1 and
    S3 only need to know that a submitted patch is visible in what they read back.
    """
    del preview
    out = dict(document)
    written = dict(out.get("_patched") or {})
    for operation in patch.operations:
        written[str(operation.get("path"))] = operation.get("op")
    out["_patched"] = written
    return out


def fake_issue(rule_id: str, message: str, *, level: IssueLevel = IssueLevel.BLOCKING) -> Issue:
    """A ready-made issue, so a caller can preset a validation answer in one line."""
    return make_issue(rule_id, level, message, section=Section.REGISTRY)
