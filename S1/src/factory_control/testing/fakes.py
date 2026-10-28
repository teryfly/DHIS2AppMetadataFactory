"""Interim Fakes for S2 / S3 / S4 (CP-ASM-08).

Those subsystems are not published yet, so S1 ships the substitutes it needs to
be developed and tested alone.  They implement the port Protocols against pure
in-memory state, with presettable outcomes for everything the orchestrator
branches on: change levels, affected stages, blocking issues, ``WAITING_INPUT``
runs, policy denials, budget exhaustion, drift, unconfirmable COMMIT.

When the real packages arrive, :mod:`factory_control.adapters` takes over and
these stay only as test doubles.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from factory_control.foundation import FactoryError
from factory_control.ports import dto
from factory_control.ports.dto import (
    AgentRunRequest,
    AgentRunResult,
    Answer,
    BlueprintDraftInfo,
    BlueprintVersionInfo,
    CapabilityFreshness,
    CompileResult,
    ConnectionCheck,
    CredentialMirror,
    DecisionInfo,
    Dhis2ValidationOutcome,
    DiscoveryOutcome,
    DiscoveryResultInfo,
    DriftResult,
    EnvironmentInfo,
    EnvironmentProtection,
    ExecutionOutcome,
    ExportBundle,
    FreezeResult,
    ImpactAnalysis,
    ImportResult,
    Issue,
    ParsedInput,
    PatchResult,
    PostVerifyOutcome,
    ProjectSemantics,
    PromotionResult,
    QuestionDraft,
    ReconcileResult,
    ValidationOutcome,
)
from factory_control.ports.protocols import BlueprintPort, ExecutionPort, IntelligencePort  # noqa: F401


def _hash(seed: str) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _in_days(days: int) -> datetime:
    return datetime.now(UTC) + timedelta(days=days)


def register_fake_artifact(project_id: str, artifact_type: str, seed: str) -> str:
    """Create a real S0 artifact so S1 can resolve, hash and bind it.

    A provider fake that hands out ids which do not exist in the artifact store
    would let S1 pass tests it should fail: Gate binding re-checks, the three-way
    payload hash at COMMIT and the export set all resolve these ids (CP-GATE-004,
    CP-GATE-017, CP-PRJ-008).
    """
    import io
    import json

    from factory_control.foundation import (
        ArtifactCreate,
        ArtifactStatus,
        DataClassification,
        register_artifact,
        stage_content,
        unit_of_work,
    )

    # Content staging is external I/O and runs with no transaction open; the
    # metadata registration then joins a short unit of work, exactly as S0 requires
    # (FD-ART-005).
    handle = stage_content(
        io.BytesIO(json.dumps({"seed": seed, "type": artifact_type}, sort_keys=True).encode("utf-8")),
        project_id=project_id,
        artifact_type=artifact_type,
        media_type="application/json",
    )
    with unit_of_work() as session:
        artifact = register_artifact(
            handle,
            ArtifactCreate(
                project_id=project_id,
                type=artifact_type,
                media_type="application/json",
                data_classification=DataClassification.INTERNAL,
                creator_subsystem=_owner_for(artifact_type),
                status=ArtifactStatus.APPROVED,
                build_record={"fake": True, "seed": seed},
            ),
            session=session,
        )
    return str(artifact.id)


#: Which subsystem may create which artifact type (S0 FD-ART-002 catalog).
TYPE_OWNER: dict[str, str] = {
    "BUSINESS_INPUT": "S1",
    "CHANGE_IMPACT_REPORT": "S1",
    "PROJECT_EXPORT": "S1",
    "NRR": "S3",
    "AGENT_OUTPUT": "S3",
    "BLUEPRINT": "S2",
    "METADATA_PACKAGE": "S2",
    "FIXTURE_PACKAGE": "S2",
    "MANIFEST": "S2",
    "OPENAPI": "S2",
    "UI_SPECIFICATION": "S2",
    "VALIDATION_REPORT": "S2",
    "FRONTEND_PACKAGE": "S2",
    "HANDOFF_CHECK_REPORT": "S2",
    "DIFF_REPORT": "S2",
    "CAPABILITY_PROFILE": "S4",
    "DISCOVERY_RESULT": "S4",
    "EXECUTION_PLAN": "S4",
    "SNAPSHOT": "S4",
    "DHIS2_IMPORT_REPORT": "S4",
    "EXECUTION_REPORT": "S4",
    "POST_VERIFICATION_REPORT": "S4",
}


def _owner_for(artifact_type: str) -> str:
    return TYPE_OWNER.get(artifact_type, "S2")


@dataclass
class FakeBlueprint:
    """S2 stand-in: drafts, freeze/promote, compile, validate, impact, export."""

    projects: dict[str, dict[str, Any]] = field(default_factory=dict)
    drafts: dict[str, BlueprintDraftInfo] = field(default_factory=dict)
    versions: dict[str, list[BlueprintVersionInfo]] = field(default_factory=dict)
    next_version: dict[str, list[str]] = field(default_factory=dict)
    affected_stages: dict[str, list[str]] = field(default_factory=dict)
    compile_issues: list[Issue] = field(default_factory=list)
    #: What S1 handed each environment compile as its authorized deletion candidates (CP-CR-005).
    compile_deletion_candidates: dict[str, list[str]] = field(default_factory=dict)
    validation_issues: list[Issue] = field(default_factory=list)
    freeze_issues: list[Issue] = field(default_factory=list)
    patch_questions: list[QuestionDraft] = field(default_factory=list)
    patch_issues: list[Issue] = field(default_factory=list)
    #: CP-AC-05: refuse the promotion, so the test can watch the approval roll back with it.
    promote_fail: bool = False
    decisions: dict[str, DecisionInfo] = field(default_factory=dict)
    registry_refs: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    package_manifests: dict[str, dto.PackageManifest] = field(default_factory=dict)
    # --- the port doubles for S2's product reads (BP-TD-009, BP-HO-004).
    # S2 ships these functions and its own fake; these containers keep the CP-ASM-08
    # double presettable so S1's suite can serve graph and package answers without
    # S2's build database.  Their refusal mirrors the provider: no preset answer is
    # NOT_GENERATED, never an empty stand-in (CP-UI-022).
    dependency_graphs: dict[str, dto.DependencyGraph] = field(default_factory=dict)
    package_file_listings: dict[str, dto.PackageFileListing] = field(default_factory=dict)
    package_file_contents: dict[tuple[str, str], dto.PackageFileContent] = field(
        default_factory=dict
    )
    report_issues: dict[str, list[Issue]] = field(default_factory=dict)
    uid_pool: dict[str, int] = field(default_factory=dict)
    uid_pool_source: str = "dev"
    calls: list[str] = field(default_factory=list)
    _default_project: str = "prj-fake-default"
    EXTERNAL_IO_METHODS: frozenset[str] = frozenset(
        {
            "prepare_initial_blueprint",
            "prepare_draft_base",
            "freeze_draft",
            "prepare_mode_upgrade",
            "replenish_uid_pool",
            "compile_baseline",
            "compile_for_environment",
            "validate_build",
            "export_blueprint",
            "prepare_import",
            "generate_frontend_package",
            "get_dependency_graph",
            "list_package_files",
            "read_package_file",
        }
    )

    def set_project(
        self,
        project_id: str,
        *,
        delivery_mode: str = "A",
        has_fixture: bool = True,
        version_queue: list[str] | None = None,
    ) -> None:
        self.projects[project_id] = {
            "delivery_mode": delivery_mode,
            "has_fixture_spec": has_fixture,
            "draft_hash": _hash(f"{project_id}:draft"),
            "draft_id": None,
        }
        if version_queue:
            self.next_version[project_id] = list(version_queue)

    def prepare_initial_blueprint(self, project_id: str, semantics: dict[str, Any]) -> str:
        if not str(semantics.get("versionConstraints") or ""):
            from factory_control.errors import ValidationFailedError

            raise ValidationFailedError(
                "versionConstraints is required to create a project",
                details={"fields": [{"path": "versionConstraints", "message": "required to create a project"}]},
            )
        self.calls.append("prepare_initial_blueprint")
        self.projects.setdefault(
            project_id,
            {
                "delivery_mode": semantics.get("deliveryMode", "A"),
                "has_fixture_spec": True,
                "draft_hash": _hash(f"{project_id}:draft"),
                "draft_id": None,
            },
        )
        return f"sth-{project_id}"

    def register_initial_blueprint(
        self, project_id: str, handle: str, *, session: Session | None = None
    ) -> PromotionResult:
        self.calls.append("register_initial_blueprint")
        info = self.projects.setdefault(project_id, {"delivery_mode": "A", "has_fixture_spec": True})
        version = BlueprintVersionInfo(version="0.1.0", content_hash=_hash(f"{project_id}:0.1.0"))
        self.versions.setdefault(project_id, []).append(version)
        info["current_version"] = version.version
        return PromotionResult(
            version=version.version,
            change_level="Minor",
            affected_stages=[],
            artifact_id=f"art-{project_id}-0-1-0",
            content_hash=version.content_hash,
        )

    def get_project_semantics(
        self, project_id: str, version: str | None = None
    ) -> ProjectSemantics:
        info = self.projects.get(project_id, {})
        versions = self.versions.get(project_id, [])
        return ProjectSemantics(
            delivery_mode=str(info.get("delivery_mode", "A")),
            has_fixture_spec=bool(info.get("has_fixture_spec", True)),
            draft=self.drafts.get(info.get("draft_id") or ""),
            current_version=versions[-1] if versions and version is None else None,
            mapping_frozen=bool(info.get("mapping_frozen", False)),
        )

    def get_current_version(self, project_id: str) -> BlueprintVersionInfo | None:
        versions = self.versions.get(project_id, [])
        return versions[-1] if versions else None

    def open_draft(
        self,
        project_id: str,
        stage_scope: str,
        change_request_id: str | None = None,
        *,
        session: Session | None = None,
    ) -> str:
        info = self.projects.setdefault(project_id, {"delivery_mode": "A", "has_fixture_spec": True})
        draft_id = info.get("draft_id") or f"dra-{project_id}-{stage_scope}"
        info["draft_id"] = draft_id
        self.drafts.setdefault(
            draft_id,
            BlueprintDraftInfo(
                draft_id=draft_id,
                content_hash=str(info.get("draft_hash") or _hash(f"{project_id}:draft")),
                # S2 records which stage opened the draft as well as what it may touch.
                stage=stage_scope,
                stage_scope=[stage_scope],
            ),
        )
        return draft_id

    def get_draft(self, draft_id: str) -> BlueprintDraftInfo | None:
        return self.drafts.get(draft_id)

    def prepare_draft_base(self, project_id: str, draft_id: str) -> str:
        """The fake has nothing to materialise, but the step must still be legal."""
        self.calls.append(f"prepare_draft_base:{draft_id}")
        self.drafts.setdefault(draft_id, BlueprintDraftInfo(draft_id=draft_id, content_hash=_hash(f"{project_id}:draft")))
        return draft_id

    def list_versions(
        self, project_id: str, *, page: int = 1, page_size: int = 50
    ) -> dto.VersionPage:
        rows = list(reversed(self.versions.get(project_id, [])))
        start = (page - 1) * page_size
        return dto.VersionPage(
            items=[
                dto.VersionDetail(version=v.version, content_hash=v.content_hash, change_level=v.change_level)
                for v in rows[start : start + page_size]
            ],
            total=len(rows),
            limit=page_size,
            offset=start,
        )

    def get_entries(
        self, project_id: str, selector: dict[str, Any], version: str | None = None
    ) -> dto.SelectedEntries:
        """A stand-in document per selected section, carrying S2's locator keys."""
        sections = [str(s) for s in (selector.get("sections") or [])]
        keys = [str(k) for k in (selector.get("businessKeys") or [])]
        entries: list[dict[str, Any]] = []
        for section in sections:
            for key in keys or ["bk-1"]:
                entries.append(
                    {
                        "businessKey": key,
                        "statement": f"fake {section} entry for {key}",
                        "_section": section,
                        "_path": f"/{section}/items/{key}",
                    }
                )
        current = self.versions.get(project_id)
        return dto.SelectedEntries(
            project_id=project_id,
            version=version or (current[-1].version if current else None),
            source=str(selector.get("source") or "APPROVED"),
            entries=entries,
            not_found=[],
        )

    def get_registry_entries(
        self, project_id: str, version: str, environment_key: str | None = None
    ) -> list[dto.RegistryEntryInfo]:
        return [
            dto.RegistryEntryInfo(
                business_key=str(ref.get("businessKey") or f"{project_id}:obj"),
                dhis2_type=str(ref.get("dhis2Type") or "dataElement"),
                disposition=str(ref.get("disposition") or "CREATE"),
                status="APPROVED",
                uid=str(ref.get("uid") or "A1"),
                code=str(ref.get("code") or "code-1"),
                managed=True,
                controlled_fields=["name"],
                observed=dto.ObservationBaseline(
                    source_environment=environment_key,
                    observed_last_updated="2026-01-01T00:00:00Z",
                    observed_hash=_hash(f"{project_id}:{environment_key}"),
                    discovery_result_id=f"bart-fake-disc-{environment_key}" if environment_key else None,
                ),
                environment_bindings={environment_key: "A1"} if environment_key else {},
            )
            for ref in self.registry_refs.get(project_id, [{"dhis2Type": "dataElement", "uid": "A1"}])
        ]

    def get_object_relationships(
        self,
        project_id: str,
        version: str | None = None,
        dhis2_types: list[str] | None = None,
    ) -> list[dto.ObjectRelation]:
        """A chain over the fake's Registry, with one object deliberately left unconnected.

        UI-REV-002 asks the graph to show orphan nodes, so a fake whose every object is wired to
        another could never prove that.  The narrowing follows S2's rule: an edge is kept when
        *either* end is one of the requested types.
        """
        rows = self.registry_refs.get(project_id, [{"dhis2Type": "dataElement", "uid": "A1"}])
        keys = [str(row.get("businessKey") or f"{project_id}:obj{i}") for i, row in enumerate(rows)]
        types = {
            (row.get("businessKey") or f"{project_id}:obj{i}"): str(row.get("dhis2Type") or "dataElement")
            for i, row in enumerate(rows)
        }
        pairs = [(keys[i], keys[i + 1]) for i in range(len(keys) - 1)]
        if len(keys) >= 3:
            # one object stays unconnected, so there is an orphan for the graph to find
            pairs = pairs[:-1]
        edges = [
            dto.ObjectRelation(
                from_business_key=source,
                to_business_key=target,
                field="dataElements",
                from_dhis2_type=types.get(source),
                to_dhis2_type=types.get(target),
            )
            for source, target in pairs
        ]
        if dhis2_types:
            kept = set(dhis2_types)
            edges = [e for e in edges if kept & {e.from_dhis2_type, e.to_dhis2_type}]
        return edges

    def get_package_manifest(
        self,
        project_id: str,
        version: str | None = None,
        environment_key: str | None = None,
    ) -> dto.PackageManifest:
        """The preset package, or S2's answer for a version that was never built.

        A caller that has not preset one must get an empty file list rather than a plausible
        layout, so the by-file screen's "nothing compiled yet" path is reachable from a test.
        """
        preset = self.package_manifests.get(project_id)
        if preset is not None:
            return preset
        return dto.PackageManifest(
            project_id=project_id,
            blueprint_version=version or "0.1.0",
            build_id="",
            kind="NONE",
            environment_key=environment_key,
        )

    def get_dependency_graph(
        self, build_id: str, filters: dto.GraphFilters | None = None
    ) -> dto.DependencyGraph:
        """The preset graph for this build, narrowed by S2's own filter rule.

        No preset is S2's answer for a build that produced no graph: 404 NOT_FOUND
        ``NOT_GENERATED`` (BP-TD-009, CP-UI-022) — the fake refuses like the provider,
        it never invents a picture the test did not stage.
        """
        self.calls.append("get_dependency_graph")
        graph = self.dependency_graphs.get(build_id)
        if graph is None:
            from factory_control.errors import NotFoundControlError

            raise NotFoundControlError(
                f"build {build_id} produced no dependency graph",
                details={"buildId": build_id},
                reason="NOT_GENERATED",
            )
        types = set(filters.dhis2_types or ()) if filters else set()
        keys = set(filters.business_keys or ()) if filters else set()
        if not types and not keys:
            return graph

        # S2's narrow_graph rule (BP-TD-009): dimensions combine as AND, members within
        # one match any; an edge survives when either end does, so its far end may lie
        # outside the view.  Degrees, orphans and dangling references are the staged
        # graph's facts — narrowing never recomputes them.
        def keep(node: dto.GraphNode) -> bool:
            if types and node.dhis2_type not in types:
                return False
            return not keys or node.business_key in keys

        nodes = [node for node in graph.nodes if keep(node)]
        survivors = {node.business_key for node in nodes}
        edges = [
            edge
            for edge in graph.edges
            if edge.from_business_key in survivors or edge.to_business_key in survivors
        ]
        return graph.model_copy(update={"nodes": nodes, "edges": edges})

    def list_package_files(self, artifact_id: str) -> dto.PackageFileListing:
        """The staged listing of a FRONTEND_PACKAGE artifact.

        Nothing staged is BP-HO-004's "artifact unknown": ``NOT_EXIST`` — the answer a
        wrong ``artifact_id`` gets in production too, so a test cannot read a missing
        preset as an empty package.  (S2 additionally answers ``ARTIFACT_TYPE_MISMATCH``
        for an artifact of the wrong type; S1 maps that to a 500 of its own, CP-UI-021.)
        """
        self.calls.append("list_package_files")
        listing = self.package_file_listings.get(artifact_id)
        if listing is None:
            from factory_control.errors import NotFoundControlError

            raise NotFoundControlError(
                f"no development package {artifact_id}",
                details={"artifactId": artifact_id},
                reason="NOT_EXIST",
            )
        return listing

    def read_package_file(self, artifact_id: str, path: str) -> dto.PackageFileContent:
        """The staged content of one package file; unstaged paths answer NOT_EXIST like S2.

        A path the listing does not carry is indistinguishable from one S2 refuses for
        form (``..``, leading ``/``, backslash, ``#``, NUL, empty): BP-HO-004 keeps the
        package's internal structure unleaked, and S1 forwards the answer untouched.
        """
        self.calls.append("read_package_file")
        listing = self.list_package_files(artifact_id)
        staged = self.package_file_contents.get((artifact_id, path))
        if staged is None or path not in {item.path for item in listing.files}:
            from factory_control.errors import NotFoundControlError

            raise NotFoundControlError(
                f"package {artifact_id} holds no file {path!r}",
                details={"artifactId": artifact_id, "path": path},
                reason="NOT_EXIST",
            )
        return staged

    def get_managed_scope(
        self, project_id: str, version: str, environment_key: str
    ) -> list[dto.ManagedObjectInfo]:
        return [
            dto.ManagedObjectInfo(
                business_key=e.business_key,
                dhis2_type=e.dhis2_type,
                uid=e.uid,
                disposition=e.disposition,
                code=e.code,
                controlled_fields=list(e.controlled_fields),
                observed_last_updated=e.observed.observed_last_updated,
                observed_hash=e.observed.observed_hash,
            )
            for e in self.get_registry_entries(project_id, version, environment_key)
            if e.managed
        ]

    def get_bindings(
        self, project_id: str, version: str, environment_key: str | None = None
    ) -> list[dto.BindingInfo]:
        return [
            dto.BindingInfo(
                environment_key=environment_key,
                business_key=e.business_key,
                uid=e.uid,
                resolution="AUTO",
                resolved_by=None,
                note=None,
                pending_reparse=False,
            )
            for e in self.get_registry_entries(project_id, version, environment_key)
        ]

    def section_catalog(self) -> dto.SectionCatalog:
        """The 17 sections and their key fields, mirroring S2's layout table."""
        whole = {"project", "generationPolicy"}
        key_fields = {
            "architecture": "decisionId",
            "pages": "pageKey",
            "apiIntents": "intentKey",
            "uiIntents": "intentKey",
        }
        sections = [
            "project",
            "requirements",
            "domain",
            "useCases",
            "actors",
            "organizationModel",
            "accessModel",
            "architecture",
            "mapping",
            "registry",
            "pages",
            "apiIntents",
            "uiIntents",
            "rules",
            "fixtures",
            "environmentConstraints",
            "generationPolicy",
        ]
        return dto.SectionCatalog(
            current_schema_version=1,
            available_versions=[1],
            sections=[
                dto.SectionLayout(
                    section=name,
                    entry_container=None if name in whole else f"/{name}/items",
                    key_field=None if name in whole else key_fields.get(name, "businessKey"),
                )
                for name in sections
            ],
        )

    def submit_patch(
        self,
        project_id: str,
        draft_id: str,
        patch: dict[str, Any],
        *,
        stage: str,
        idempotency_key: str = "patch",
        change_request_id: str | None = None,
        session: Session | None = None,
    ) -> PatchResult:
        self.calls.append("submit_patch")
        draft = self.drafts.get(draft_id)
        new_hash = _hash(f"{draft_id}:{len(self.calls)}:{patch.get('id', 'x')}")
        if draft is not None:
            draft.content_hash = new_hash
            self.projects.get(project_id, {})["draft_hash"] = new_hash
        return PatchResult(
            status="PROPOSED",
            draft_hash=new_hash,
            issues=list(self.patch_issues),
            questions=list(self.patch_questions),
        )

    def freeze_draft(
        self,
        draft_id: str,
        target_environment_keys: list[str],
        *,
        progress: Any = None,
        cancel: Any = None,
    ) -> FreezeResult:
        self.calls.append("freeze_draft")
        draft = self.drafts.get(draft_id)
        return FreezeResult(
            draft_hash=draft.content_hash if draft else _hash(draft_id),
            staging_handle=f"sth-{draft_id}",
            promotable=not any(i.is_blocking for i in self.freeze_issues),
            issues=list(self.freeze_issues),
        )

    def promote_draft(
        self,
        project_id: str,
        draft_id: str,
        expected_hash: str,
        idempotency_key: str,
        *,
        session: Session | None = None,
    ) -> PromotionResult:
        self.calls.append("promote_draft")
        if self.promote_fail:
            raise FactoryError(
                "the draft cannot be promoted yet", code="BP_NOT_PROMOTABLE", details={"draftId": draft_id}
            )
        draft = self.drafts.get(draft_id)
        if draft is not None and draft.content_hash != expected_hash:
            raise AssertionError(
                f"freeze hash {expected_hash} no longer matches draft hash {draft.content_hash}"
            )
        queue = self.next_version.get(project_id) or []
        version = queue.pop(0) if queue else f"0.{len(self.versions.get(project_id, [])) + 1}.0"
        info = BlueprintVersionInfo(version=version, content_hash=_hash(f"{project_id}:{version}"))
        self.versions.setdefault(project_id, []).append(info)
        return PromotionResult(
            version=version,
            change_level=self.projects.get(project_id, {}).get("change_level", "Minor"),
            affected_stages=list(self.affected_stages.get(project_id, [])),
            artifact_id=f"art-{project_id}-{version}",
            content_hash=info.content_hash,
        )

    def prepare_mode_upgrade(self, project_id: str) -> str:
        self.calls.append("prepare_mode_upgrade")
        return f"sth-{project_id}-mode"

    def commit_mode_upgrade(
        self,
        project_id: str,
        handle: str,
        idempotency_key: str,
        *,
        session: Session | None = None,
    ) -> PromotionResult:
        info = self.projects.setdefault(project_id, {})
        info["delivery_mode"] = "B"
        return PromotionResult(version="0.2.0", change_level="Minor", affected_stages=[])

    def analyze_impact(self, project_id: str, business_keys: list[str]) -> ImpactAnalysis:
        stages = self.affected_stages.get(project_id, ["MAPPING"])
        # Real S2's ``ImpactSet`` carries no deletion field, so neither does this double: a fake that
        # invented one would let S1 ship a path that answers empty on the provider (see
        # ``services/change_requests.analyze``, where the invented rule used to overwrite the
        # operator's own declaration).
        return ImpactAnalysis(
            affected_stages=list(stages),
            affected_object_keys=list(business_keys),
            affected_pages=["page-1"] if "PAGE_DESIGN" in stages else [],
            affected_artifact_types=["METADATA_PACKAGE"],
        )

    def registry_object_refs(
        self, project_id: str, version: str, environment_key: str | None = None
    ) -> list[dict[str, Any]]:
        return list(self.registry_refs.get(project_id, [{"dhis2Type": "dataElement", "uid": "A1"}]))

    def list_object_types(self) -> list[dict[str, Any]]:
        """Tier 1 only: what S2 can compile in this build is what discovery should read."""
        return [
            {"dhis2Type": "dataElement", "tier": "1"},
            {"dhis2Type": "optionSet", "tier": "1"},
            {"dhis2Type": "program", "tier": "1"},
        ]

    def replenish_uid_pool(self, project_id: str, environment_key: str) -> int:
        self.uid_pool[environment_key or project_id] = 50
        return 50

    def uid_pool_status(self, project_id: str) -> dict[str, Any]:
        available = sum(self.uid_pool.values())
        low_water = 10
        return {
            "projectId": project_id,
            "available": available,
            "lowWaterMark": low_water,
            "sourceEnvironment": self.uid_pool_source or None,
            "needsReplenishment": available < low_water,
        }

    def set_environment_binding(
        self,
        project_id: str,
        version: str,
        environment_key: str,
        business_key: str,
        uid: str,
        note: str | None = None,
        *,
        session: Session | None = None,
    ) -> dict[str, Any]:
        return {"businessKey": business_key, "uid": uid, "environmentKey": environment_key}

    def get_section_schema(self, section: str, schema_version: int | None = None) -> dict[str, Any]:
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": f"https://damf.invalid/bp/{schema_version or 1}/sections/{section}.json",
            "title": section,
            "type": "object",
            "definitions": {},
        }

    def compile_baseline(
        self,
        project_id: str,
        version: str,
        capability_profile_artifact_id: str | None,
        build_context: Any,
        *,
        progress: Any = None,
        cancel: Any = None,
        idempotency_key: str,
    ) -> CompileResult:
        self.calls.append("compile_baseline")
        artifacts = {
            name: register_fake_artifact(project_id, name, f"{project_id}-baseline-{version}-{name}")
            for name in ("OPENAPI", "UI_SPECIFICATION", "VALIDATION_REPORT")
        }
        return CompileResult(
            build_id=f"bld-{project_id}-baseline",
            artifact_ids=artifacts,
            issues=list(self.compile_issues),
            payload_hash=_hash(f"{project_id}:baseline:{version}"),
        )

    def compile_for_environment(
        self,
        project_id: str,
        version: str,
        environment_key: str,
        discovery_result_artifact_id: str | None,
        capability_profile_artifact_id: str | None,
        change_request_id: str | None,
        build_context: Any,
        *,
        progress: Any = None,
        cancel: Any = None,
        idempotency_key: str,
        deletion_candidates: list[dict[str, str]] | None = None,
    ) -> CompileResult:
        self.calls.append(f"compile_for_environment:{environment_key}")
        self.compile_deletion_candidates[environment_key] = [
            str(item.get("businessKey")) for item in deletion_candidates or []
        ]
        artifacts = {
            name: register_fake_artifact(
                project_id, name, f"{project_id}-{environment_key}-{version}-{name}"
            )
            for name in ("METADATA_PACKAGE", "FIXTURE_PACKAGE", "MANIFEST", "DIFF_REPORT")
        }
        return CompileResult(
            build_id=f"bld-{project_id}-{environment_key}",
            artifact_ids=artifacts,
            issues=list(self.compile_issues),
            payload_hash=_hash(f"{project_id}:{environment_key}:{version}"),
        )

    def validate_build(
        self,
        build_id: str,
        *,
        progress: Any = None,
        cancel: Any = None,
        idempotency_key: str,
    ) -> ValidationOutcome:
        self.calls.append(f"validate_build:{build_id}")
        issues = list(self.validation_issues)
        project_id = self._project_of_build(build_id)
        report_id = register_fake_artifact(project_id, "VALIDATION_REPORT", build_id)
        return ValidationOutcome(
            validation_report_artifact_id=report_id,
            blocking_count=sum(1 for i in issues if i.is_blocking),
            issues=issues,
            artifact_ids={"VALIDATION_REPORT": report_id},
        )

    def _project_of_build(self, build_id: str) -> str:
        for project_id in self.projects:
            if build_id.endswith(project_id) or project_id in build_id:
                return project_id
        return self._default_project

    def get_decisions(self, project_id: str, decision_ids: list[str]) -> list[DecisionInfo]:
        return [
            self.decisions.get(d, DecisionInfo(decision_id=d, rationale=f"reason for {d}"))
            for d in decision_ids
        ]

    def diff_objects(self, project_id: str, artifact_id: str, against: str) -> dict[str, Any]:
        return {"changed": artifact_id != against, "objects": []}

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
        """S5IF-BP14 — the cross-report Issue read, in S2's own structure.

        Issues are held per report so a test can put a finding where the console will look
        for it; the filtering and paging happen here rather than in the caller, because the
        point of the S2 read is that a client never loads a whole report to filter it.
        """
        self.calls.append("query_issues")
        rows: list[Issue] = []
        for report_id in report_artifact_ids:
            rows.extend(self.report_issues.get(report_id) or self.validation_issues)
        matched = [
            issue
            for issue in rows
            if (level is None or issue.level.upper() == level.upper())
            and (source_stage is None or issue.source_stage == source_stage)
            and (rule_id is None or issue.rule_id == rule_id)
            and (business_key is None or issue.business_key == business_key)
        ]
        order = {"level": lambda i: i.level, "ruleId": lambda i: i.rule_id or "",
                 "sourceStage": lambda i: i.source_stage or "", "businessKey": lambda i: i.business_key or "",
                 "objectKey": lambda i: i.object_key or ""}.get(sort_by)
        if order is not None:
            matched = sorted(matched, key=order, reverse=sort_dir == "DESC")
        return {
            "items": [issue.model_dump(by_alias=True) for issue in matched[offset : offset + limit]],
            "total": len(matched),
            "limit": limit,
            "offset": offset,
        }

    def register_report_issues(self, report_artifact_id: str, issues: list[Issue]) -> None:
        """Say what one report found, so the forwarded read has something to return."""
        self.report_issues[report_artifact_id] = list(issues)

    def reproduce(self, artifact_id: str) -> dict[str, Any]:
        return {"matches": True, "hash": _hash(artifact_id)}

    def generate_frontend_package(
        self,
        project_id: str,
        version: str,
        environment_keys: list[str],
        deployment_evidence: dict[str, Any],
        build_context: Any,
        *,
        progress: Any = None,
        cancel: Any = None,
        idempotency_key: str,
    ) -> CompileResult:
        return CompileResult(
            build_id=f"bld-{project_id}-handoff",
            artifact_ids={
                name: register_fake_artifact(project_id, name, f"{project_id}-{name}")
                for name in ("FRONTEND_PACKAGE", "HANDOFF_CHECK_REPORT")
            },
        )

    def export_blueprint(self, project_id: str) -> ExportBundle:
        ids = [
            register_fake_artifact(project_id, "BLUEPRINT", f"{project_id}-{v.version}")
            for v in self.versions.get(project_id, [])
        ]
        return ExportBundle(
            artifact_ids=ids,
            export_artifact_id=register_fake_artifact(project_id, "MANIFEST", f"{project_id}-bpexport"),
            manifest={"projectId": project_id, "versions": ids},
        )

    def prepare_import(self, manifest: dict[str, Any], id_map: dict[str, str]) -> str:
        return "sth-import"

    def commit_import(
        self,
        project_id: str,
        handle: str,
        idempotency_key: str,
        *,
        session: Session | None = None,
    ) -> ImportResult:
        return ImportResult(restored_versions=["0.1.0"], pending_environment_bindings=[])

    def delete_project_data(self, project_id: str, *, session: Session | None = None) -> None:
        self.projects.pop(project_id, None)
        self.versions.pop(project_id, None)
        self.calls.append(f"delete_project_data:{project_id}")

    def mark_draft_changed(self, project_id: str, seed: str) -> str:
        """Test helper — simulate a patch or new artifact moving the draft hash."""
        info = self.projects.get(project_id)
        if not info or not info.get("draft_id"):
            return ""
        draft = self.drafts[info["draft_id"]]
        draft.content_hash = _hash(seed)
        info["draft_hash"] = draft.content_hash
        return draft.content_hash


@dataclass
class FakeIntelligence:
    """S3 stand-in: parse, agent runs with questions, resume, policy and budget."""

    parse_results: dict[str, ParsedInput] = field(default_factory=dict)
    run_results: list[AgentRunResult] = field(default_factory=list)
    runs: dict[str, AgentRunResult] = field(default_factory=dict)
    #: when set, ``resume_agent_run`` raises it after the answers were committed, which is
    #: how a provider refusal (S2's BP_PATCH_MALFORMED) reaches S1's stage guard.
    resume_error: Exception | None = None
    activations: list[dict[str, Any]] = field(default_factory=list)
    providers: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: provider name → the failure sentence a health probe should report (S5IF-GOV20)
    provider_health: dict[str, str] = field(default_factory=dict)
    models: dict[str, dict[str, Any]] = field(default_factory=dict)
    prompts: list[dict[str, Any]] = field(default_factory=list)
    #: regression results as recorded, so the activation gate can be shown its precondition
    regressions: list[dto.RegressionView] = field(default_factory=list)
    #: ``ai.budget`` as the fake holds it, so a write and a read agree (S5IF-GOV18)
    budget_rows: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: every dialogue message the fake answered, in order (AI-CHAT-001)
    chat_turns: list[tuple[str, str, str]] = field(default_factory=list)
    #: transcript per ``(project, stage)``, as S3's ``get_chat`` would answer it
    chat_history: dict[tuple[str, str], list[dict[str, Any]]] = field(default_factory=dict)
    #: the organisation's own policy matrix, once a relaxation has widened it.  ``None`` means
    #: nobody has: the answer is AI-POL-002's *default*, never the ceiling.
    org_allowed: dict[str, list[str]] | None = None
    #: every relaxation this fake granted, so a read-back can name what was recorded
    relaxations: list[dict[str, Any]] = field(default_factory=list)
    #: transcript rows per ``(project, artifact)``, oldest first (S5IF-INP08)
    llm_exchanges: dict[tuple[str, str], list[dto.LlmExchangeSummary]] = field(default_factory=dict)
    #: full rows the detail endpoint answers, keyed by exchange id
    llm_exchange_details: dict[str, dto.LlmExchangeDetail] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)
    _counter: int = 0
    EXTERNAL_IO_METHODS: frozenset[str] = frozenset(
        {
            "parse_input",
            "start_agent_run",
            "resume_agent_run",
            "explain_issues",
            "stage_chat",
            "get_chat",
        }
    )

    def _provider_error(self, code: str, details: dict[str, Any]) -> FactoryError:
        """A refusal carrying S3's own code, so S1's mapping is what is under test."""
        return FactoryError(f"fake S3 refused the call ({code})", code=code, details=details)

    def queue_run(self, *results: AgentRunResult) -> None:
        self.run_results.extend(results)

    def next_run_result(self, fallback_status: str = "COMPLETED") -> AgentRunResult:
        if self.run_results:
            return self.run_results.pop(0)
        return AgentRunResult(status=fallback_status)

    def parse_input(
        self,
        project_id: str,
        input_artifact_id: str,
        *,
        options: dict[str, Any] | None = None,
        operator: str,
        idempotency_key: str,
        progress: Any = None,
        cancel: Any = None,
    ) -> ParsedInput:
        self.calls.append("parse_input")
        cached = self.parse_results.get(idempotency_key)
        if cached is not None:
            return cached
        from factory_control.foundation import unit_of_work

        with unit_of_work() as session:
            from sqlalchemy import select

            from factory_control.models import Workflow

            owner = session.execute(
                select(Workflow).where(Workflow.id.in_(
                    _workflow_ids_for_input(session, input_artifact_id)
                ))
            ).scalars().first()
        project_for_artifact = owner.project_id if owner is not None else project_id
        result = ParsedInput(
            status="PARSED",
            nrr_artifact_id=register_fake_artifact(project_for_artifact, "NRR", input_artifact_id),
            questions=[],
            parser_version="fake-1",
            used_model=False,
        )
        self.parse_results[idempotency_key] = result
        return result

    def list_llm_exchanges(
        self,
        project_id: str,
        input_artifact_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> dto.LlmExchangePage:
        self.calls.append(f"list_llm_exchanges:{input_artifact_id}")
        rows = self.llm_exchanges.get((project_id, input_artifact_id), [])
        return dto.LlmExchangePage(
            items=rows[offset : offset + limit],
            total=len(rows),
            limit=limit,
            offset=offset,
        )

    def get_llm_exchange(
        self, project_id: str, input_artifact_id: str, exchange_id: str
    ) -> dto.LlmExchangeDetail | None:
        self.calls.append(f"get_llm_exchange:{exchange_id}")
        detail = self.llm_exchange_details.get(exchange_id)
        if detail is None or detail.input_artifact_id != input_artifact_id:
            return None
        return detail

    def purge_llm_exchanges(self) -> int:
        self.calls.append("purge_llm_exchanges")
        return 0

    def start_agent_run(
        self,
        request: AgentRunRequest,
        *,
        progress: Any = None,
        cancel: Any = None,
    ) -> AgentRunResult:
        self.calls.append(f"start_agent_run:{request.stage}")
        if request.idempotency_key in self.runs:
            # Same key: continue rather than redo (CP-WF-008).
            return self.runs[request.idempotency_key]
        result = self.next_run_result()
        self._counter += 1
        run_id = result.run_id or f"agn-{self._counter:04d}"
        result = result.model_copy(update={"run_id": run_id})
        self.runs[request.idempotency_key] = result
        self.runs[run_id] = result
        if progress is not None:
            progress.report(step="started", completed_fraction=0.0, message="fake agent started")
        return result

    def resume_agent_run(
        self,
        run_id: str,
        answers: list[Answer],
        idempotency_key: str,
        *,
        language: str | None = None,
        progress: Any = None,
        cancel: Any = None,
    ) -> AgentRunResult:
        self.calls.append(f"resume_agent_run:{run_id}")
        if self.resume_error is not None:
            raise self.resume_error
        result = self.next_run_result("COMPLETED")
        result = result.model_copy(update={"run_id": run_id})
        self.runs[run_id] = result
        return result

    def cancel_agent_run(self, run_id: str) -> None:
        self.calls.append(f"cancel_agent_run:{run_id}")
        current = self.runs.get(run_id)
        if current is not None:
            self.runs[run_id] = current.model_copy(update={"status": "CANCELLED"})

    def get_agent_run(self, run_id: str) -> AgentRunResult | None:
        return self.runs.get(run_id)

    def explain_issues(
        self,
        project_id: str,
        report_artifact_id: str,
        *,
        environment_policies: dict[str, Any],
        operator: str,
        language: str | None = None,
    ) -> str | None:
        self.calls.append("explain_issues")
        return f"art-{report_artifact_id}-explanation"

    # --- governance, one typed method per capability (CP-UI-006 item 6) -------------
    #
    # The rows are S3's declared result models, with the values S3's
    # ``governance/capabilities.py`` produces for the same call rather than shapes invented
    # here, so a contract test run against the fake sees what S3 answers — that is what lets
    # S5 develop against S1 before S3 is wired in (CP-ASM-08).

    def list_providers(self) -> list[dto.ProviderView]:
        return [dto.ProviderView.model_validate(row) for row in self._provider_rows()]

    def register_provider(self, spec: dict[str, Any], operator: str) -> dto.ProviderView:
        name = str(spec.get("name") or "fake")
        row = dto.ProviderView.model_validate(_provider_view(spec, name))
        self.providers[name] = row.model_dump(mode="json", by_alias=True)
        self.calls.append(f"register_provider:{name}:{operator}")
        return row

    def update_provider(
        self, name: str, patch: dict[str, Any], operator: str
    ) -> dto.ProviderView:
        """Merge, never replace: the whole point of an edit path over re-registration.

        Deliberately mirrors S3's refusals — an empty patch and an unknown name both fail — so a
        contract test cannot pass here and break against the real package.
        """
        if not {k: v for k, v in patch.items() if v is not None}:
            from factory_control.errors import ValidationFailedError

            raise ValidationFailedError(
                "update_provider needs at least one field to change",
                details={"provider": name},
            )
        stored = self.providers.get(name)
        if stored is None:
            from factory_control.errors import NotFoundControlError

            raise NotFoundControlError(
                f"provider {name!r} is not registered", details={"provider": name}
            )
        merged = {
            **stored,
            **{key: value for key, value in patch.items() if value is not None},
        }
        # `id` is carried, not regenerated, and a provider registered without a Key reference
        # keeps reporting none: inventing one here would hide the very failure this path exists
        # to make impossible (a silent wipe of the stored reference).
        row = dto.ProviderView.model_validate(
            {**_provider_view(merged, name), "id": stored.get("id"),
             "apiKeySecretRef": merged.get("apiKeySecretRef")}
        )
        self.providers[name] = row.model_dump(mode="json", by_alias=True)
        self.calls.append(f"update_provider:{name}:{operator}")
        return row

    def set_provider_enabled(
        self, name: str, enabled: bool, operator: str
    ) -> dto.EnableResult:
        if name in self.providers:
            self.providers[name] = {**self.providers[name], "enabled": bool(enabled)}
        self.calls.append(f"set_provider_enabled:{name}={enabled}:{operator}")
        return dto.EnableResult(ref=name, enabled=bool(enabled))

    def check_provider_health(self, name: str) -> dto.ProviderHealthView:
        """A probe answer, never an exception: an unhealthy provider is the case under test."""
        detail = self.provider_health.get(name)
        if name not in self.providers:
            return dto.ProviderHealthView(ref=name, healthy=False, detail=f"provider {name!r} is not registered")
        return dto.ProviderHealthView(ref=name, healthy=detail is None, detail=detail)

    def list_models(
        self, agent_type: str | None = None, *, include_disabled: bool = True
    ) -> list[dto.ModelView]:
        if not self.models:
            ref = "fake-provider/fake-model"
            self.models[ref] = _model_view({"provider": "fake-provider"}, ref)
        rows = [dto.ModelView.model_validate(row) for row in self.models.values()]
        if not include_disabled:
            rows = [row for row in rows if row.enabled]
        if agent_type:
            rows = [row for row in rows if agent_type in row.applicable_agents]
        return rows

    def register_model(self, spec: dict[str, Any], operator: str) -> dto.ModelView:
        ref = f"{spec.get('provider', 'fake-provider')}/{spec.get('model') or 'fake-model'}"
        row = dto.ModelView.model_validate(_model_view(spec, ref))
        self.models[ref] = row.model_dump(mode="json", by_alias=True)
        self.calls.append(f"register_model:{ref}:{operator}")
        return row

    def update_model(self, ref: str, patch: dict[str, Any], operator: str) -> dto.ModelView:
        """Only the named keys change (S5IF-GOV06).

        The stored id and evaluation standing are carried rather than regenerated, and a patch
        that names one capability leaves the other two as they were — the same rules the real
        registry enforces, because a stand-in that half-obeyed them would hide the very bug this
        verb exists to prevent: a re-registration that cleared a stored override.
        """
        stored = self.models.get(ref)
        if stored is None:
            from factory_control.errors import NotFoundControlError

            raise NotFoundControlError(
                f"model {ref!r} is not registered", details={"ref": ref}
            )
        merged = dict(stored)
        for key, value in patch.items():
            if value is None:
                continue
            if key == "capabilities":
                merged["capabilities"] = {**dict(stored.get("capabilities") or {}), **value}
            else:
                merged[key] = value
        row = dto.ModelView.model_validate(merged)
        self.models[ref] = row.model_dump(mode="json", by_alias=True)
        self.calls.append(f"update_model:{ref}:{operator}")
        return row

    def set_model_enabled(self, ref: str, enabled: bool, operator: str) -> dto.EnableResult:
        if ref in self.models:
            self.models[ref] = {**self.models[ref], "enabled": bool(enabled)}
        self.calls.append(f"set_model_enabled:{ref}={enabled}:{operator}")
        return dto.EnableResult(ref=ref, enabled=bool(enabled))

    def list_prompts(self, agent_type: str | None = None) -> list[dto.PromptView]:
        rows = [dto.PromptView.model_validate(row) for row in _fake_prompts(self)]
        if agent_type:
            rows = [row for row in rows if row.agent_type == agent_type]
        return rows

    def get_prompt(self, name: str, version: int) -> dto.PromptDetailView:
        for row in _fake_prompts(self):
            if row["name"] == name and int(row["version"]) == int(version):
                return dto.PromptDetailView.model_validate(row)
        raise FactoryError(
            f"no prompt {name}.v{version}",
            code="AI_PROMPT_NOT_FOUND",
            details={"name": name, "version": version},
        )

    def activate_prompt(
        self,
        agent_type: str,
        prompt_name: str,
        prompt_version: int,
        model_list: list[str],
        operator: str,
    ) -> dto.ActivationView:
        record = {
            "agentType": agent_type,
            "promptVersionId": f"{prompt_name}.v{prompt_version}",
            "name": prompt_name,
            "version": int(prompt_version),
            "modelList": [str(item) for item in model_list],
            "evalRunId": "evr-fake-0001",
            "regressionPassedRef": "reg-fake-0001",
            "activatedBy": operator or "system",
        }
        self.activations = [*[a for a in self.activations if a["agentType"] != agent_type], record]
        self.calls.append(f"activate_prompt:{agent_type}={prompt_name}.v{prompt_version}")
        return dto.ActivationView.model_validate(record)

    def list_prompt_activations(self) -> list[dto.ActivationView]:
        return [dto.ActivationView.model_validate(row) for row in self.activations]

    def record_regression(
        self,
        prompt_name: str,
        prompt_version: int,
        passed: bool,
        model: str | None,
        source: str,
        note: str | None,
        operator: str,
    ) -> dto.RegressionView:
        self.calls.append(f"record_regression:{prompt_name}.v{prompt_version}={passed}:{operator}")
        row = dto.RegressionView(
            id=f"reg-fake-{len(self.regressions) + 1:04d}",
            passed=bool(passed),
            prompt_version_id=f"{prompt_name}.v{prompt_version}",
        )
        self.regressions.append(row)
        return row

    def list_regressions(
        self,
        prompt_name: str | None = None,
        prompt_version: int | None = None,
    ) -> list[dto.RegressionView]:
        self.calls.append(f"list_regressions:{prompt_name}.v{prompt_version}")
        wanted = f"{prompt_name}.v{prompt_version}" if prompt_name and prompt_version else None
        return [
            row for row in self.regressions if wanted is None or row.prompt_version_id == wanted
        ]

    def _effective_matrix(self) -> dict[str, list[str]]:
        return dict(self.org_allowed if self.org_allowed is not None else SYSTEM_DEFAULT_MATRIX)

    def get_org_policy(self) -> dto.OrgPolicyView:
        allowed = self._effective_matrix()
        return dto.OrgPolicyView.model_validate(
            {
                "effective": {"allowed": allowed, "sources": ["org"], "warnings": []},
                # an organisation that has never relaxed anything is still on the default, which
                # is what ``isDefault`` means to S5's policy page
                "isDefault": self.org_allowed is None,
                "updatedBy": None if self.org_allowed is None else (self.relaxations[-1]["operator"] if self.relaxations else None),
                "ceiling": {"allowed": {k: list(v) for k, v in POLICY_MATRIX.items()}, "sources": ["ceiling"], "warnings": []},
            }
        )

    def relax_org_policy(
        self, change: dict[str, Any], reason: str, operator: str, scope: str = "organization"
    ) -> dto.RelaxationView:
        """Widen the matrix the way S3 does, and *keep* the result.

        The fake used to answer 201 with a fixed id and change nothing, so a relaxation was
        invisible: the very bug the Administration pages exist not to have.  It also read its
        effective policy from the ceiling, which left nothing legal to relax at all.  Both halves
        of AI-POL-002 — the default an organisation starts on and the ceiling it may reach — are
        honoured here, including S3's two refusals.
        """
        self.calls.append(f"relax_org_policy:{scope}:{operator}")
        if not (reason or "").strip():
            raise self._provider_error("VALIDATION_ERROR", {"missing": "reason"})
        level = str(change.get("level") or change.get("classification") or "")
        wanted = change.get("addClasses") or change.get("allowedClasses") or change.get("classes") or []
        wanted = [str(item) for item in (wanted if isinstance(wanted, list) else [wanted])]
        if not level:
            raise self._provider_error("VALIDATION_ERROR", {"missing": "change.level"})
        if level not in POLICY_MATRIX:
            raise self._provider_error(
                "VALIDATION_ERROR", {"level": level, "allowed": sorted(POLICY_MATRIX)}
            )
        # AI-POL-005 / AC-POL-02: patient data never leaves, whatever the caller asks for.
        if level == "PHI_PII":
            raise self._provider_error(
                "AI_POLICY_RELAXATION_DENIED", {"level": level, "reason": "PHI_PII is never sendable"}
            )
        ceiling = set(POLICY_MATRIX[level])
        past = [item for item in wanted if item not in ceiling]
        if past:
            raise self._provider_error(
                "AI_POLICY_RELAXATION_DENIED",
                {"level": level, "requested": past, "ceiling": sorted(ceiling)},
            )
        current = self._effective_matrix()
        current[level] = sorted(set(current.get(level, [])) | set(wanted))
        self.org_allowed = current
        self._counter += 1
        from factory_control.foundation import utcnow

        row = {
            "id": f"aplax-fake-{self._counter:04d}",
            "scope": scope or "organization",
            "operator": operator or "system",
            "reason": reason,
            "change": {"level": level, "addClasses": sorted(wanted)},
            "roleChecked": False,
            "roleGate": "operator-name (MVP)",
            # S3 stamps the row it writes; the read-back is the operator's only proof the
            # relaxation is dated from this call rather than from an earlier one
            "createdAt": utcnow(),
        }
        self.relaxations.append(row)
        return dto.RelaxationView.model_validate(row)

    def get_effective_policy(
        self, environment_policies: dict[str, Any] | None = None
    ) -> dto.EffectivePolicyView:
        return dto.EffectivePolicyView.model_validate({"allowed": self._effective_matrix()})

    def list_evaluations(
        self, agent_type: str | None = None, *, limit: int = 50
    ) -> list[dto.EvaluationSummary]:
        del agent_type, limit
        return [dto.EvaluationSummary.model_validate(_evaluation_row())]

    def get_evaluation(self, eval_run_id: str) -> dto.EvaluationDetail:
        return dto.EvaluationDetail.model_validate(
            _evaluation_row(eval_run_id, with_cases=True)
        )

    def get_budget(self, project_id: str) -> dto.BudgetView:
        row = self.budget_rows.get(project_id)
        if row is None:
            return dto.BudgetView.model_validate(_budget_row(project_id))
        return dto.BudgetView.model_validate({**_budget_row(project_id), **row})

    def set_budget(
        self,
        project_id: str,
        limit: float | None,
        thresholds: Sequence[float],
        *,
        currency: str = "USD",
        operator: str,
    ) -> dto.BudgetView:
        """M4's switch, mirrored: stored, echoed, and refused on the same shapes S3 refuses."""
        self.calls.append(f"set_budget:{project_id}:{limit}")
        steps = sorted({float(item) for item in thresholds})
        code = str(currency or "").strip().upper()
        if limit is not None and (not math.isfinite(limit) or limit < 0.0):
            raise self._provider_error("AI_BUDGET_INVALID", {"limit": limit})
        if any(not 0.0 < item < 1.0 for item in steps):
            raise self._provider_error("AI_BUDGET_INVALID", {"thresholds": steps})
        if len(code) != 3 or not code.isalpha():
            raise self._provider_error("AI_BUDGET_INVALID", {"currency": currency})
        spent = float(self.budget_rows.get(project_id, {}).get("spent", 0.0))
        state = "OK" if limit is None or spent <= limit else "EXCEEDED"
        row = {
            "state": state,
            "spent": spent,
            "limit": limit,
            "currency": code,
            "thresholdReached": None,
            "updatedBy": operator,
        }
        self.budget_rows[project_id] = row
        return dto.BudgetView.model_validate({**_budget_row(project_id), **row})

    def stage_chat(
        self,
        project_id: str,
        stage: str,
        draft_id: str | None,
        message: str,
        environment_policies: dict[str, Any],
        operator: str,
        *,
        language: str | None = None,
        idempotency_key: str,
        progress: Any = None,
        cancel: Any = None,
    ) -> dto.ChatReply:
        self.calls.append(f"stage_chat:{project_id}:{stage}:{idempotency_key}")
        self.chat_turns.append((project_id, stage, message))
        return dto.ChatReply(
            answer=f"fake: {(message or '')[:200]}",
            patch_ids=[],
            questions=[],
        )

    def get_chat(self, project_id: str, stage: str) -> list[dict[str, Any]]:
        self.calls.append(f"get_chat:{project_id}:{stage}")
        return self.chat_history.get((project_id, stage), [])

    def clear_chat(self, project_id: str, stage: str) -> None:
        self.calls.append(f"clear_chat:{project_id}:{stage}")
        self.chat_history[(project_id, stage)] = []

    def usage_rollup(
        self,
        project_id: str | None = None,
        *,
        group_by: Sequence[str] = ("stage", "agent", "model"),
    ) -> list[dto.UsageRollup]:
        del group_by
        return [dto.UsageRollup.model_validate(_usage_row(project_id or "prj-fake"))]

    def list_agents(self) -> dto.AgentCatalog:
        return dto.AgentCatalog.model_validate(_agent_rows())

    def run_evaluation(
        self,
        agent_type: str,
        prompt_version: str,
        model: str,
        eval_set_version: str | None = None,
        *,
        idempotency_key: str,
        operator: str,
    ) -> dto.EvalResult:
        self.calls.append(f"run_evaluation:{agent_type}:{prompt_version}")
        name, _, version = prompt_version.rpartition(".v")
        return dto.EvalResult(
            eval_run_id=f"evr-{self._counter + 1:04d}",
            agent_type=agent_type,
            prompt_name=name or prompt_version,
            prompt_version=int(version) if version.isdigit() else 1,
            model=model,
            eval_set_version=eval_set_version or "1",
            score=1.0,
            passed=True,
            cases=[
                dto.EvalCaseOutcome(
                    case_id="c1", passed=True, score=1.0, kind="golden", diff={}
                )
            ],
            injection_cases=0,
            injection_passed=0,
        )

    def _provider_rows(self) -> list[dict[str, Any]]:
        if not self.providers:
            self.providers["fake"] = _provider_view({}, "fake")
        return list(self.providers.values())


    def delete_project_data(self, project_id: str, *, session: Session | None = None) -> None:
        self.calls.append(f"delete_project_data:{project_id}")


def _with_derived_refs(info: EnvironmentInfo) -> EnvironmentInfo:
    """Re-derive ``secret_refs`` from the credential rows, the way S4 does at read time.

    S4 builds both from one list (``factory_dhis2/environment/service.py:213``), so the two fields
    cannot disagree there. A ``PATCH /environments/{key}`` carries only ``credentials``, and a fake
    that kept its previous map handed the console a reference nothing resolves with any more — while
    the delete guard (CP-SEC-003) went on refusing to release the old credential because that stale
    map named it. Measured on a fakes deployment on 2026-10-04.
    """
    refs = {
        str(credential.scope): str(credential.secret_ref)
        for credential in info.credentials
        if str(credential.secret_ref or "")
    }
    return info.model_copy(update={"secret_refs": refs})


@dataclass
class FakeExecution:
    """S4 stand-in: environments, protection, discovery, VALIDATE/COMMIT, verify."""

    environments: dict[str, EnvironmentInfo] = field(default_factory=dict)
    protections: dict[str, EnvironmentProtection] = field(default_factory=dict)
    freshness: dict[str, CapabilityFreshness] = field(default_factory=dict)
    connections: dict[str, ConnectionCheck] = field(default_factory=dict)
    discovery_objects: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    drift: dict[str, DriftResult] = field(default_factory=dict)
    executions: dict[str, ExecutionOutcome] = field(default_factory=dict)
    by_key: dict[str, ExecutionOutcome] = field(default_factory=dict)
    validate_results: list[Dhis2ValidationOutcome] = field(default_factory=list)
    commit_results: list[ExecutionOutcome] = field(default_factory=list)
    expiring: list[ExpiringCredentialSeed] = field(default_factory=list)
    missing_authorities: dict[str, list[str]] = field(default_factory=dict)
    discovery_results: dict[str, list[DiscoveryResultInfo]] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)
    capability_ids: dict[str, str] = field(default_factory=dict)
    default_project: str = "prj-fake-default"
    _counter: int = 0
    EXTERNAL_IO_METHODS: frozenset[str] = frozenset(
        {
            "test_connection",
            "check_capability_freshness",
            "probe_capabilities",
            "discover",
            "prepare_and_validate",
            "check_drift",
            "commit",
            "reconcile",
            "post_verify",
            "import_fixtures",
            # S4's twin is marked ``@external_io`` and submits an import to the instance, so the
            # double has to claim the same transaction regime or S1 codes itself against a lie.
            "delete_objects",
        }
    )

    def add_environment(
        self,
        key: str,
        *,
        environment_class: str = "TEST",
        locked: bool = False,
        within_window: bool = True,
        base_url: str | None = None,
        name: str | None = None,
        auth_mode: str = "PAT",
        llm_data_policy: str = "PRIVATE_NETWORK",
        secret_refs: dict[str, str] | None = None,
        commit_windows: list[dict[str, Any]] | None = None,
        timezone: str = "UTC",
        deletion_policy: str | None = None,
        enabled: bool = True,
    ) -> None:
        refs = dict(secret_refs or {"discovery": f"vault://fake/{key}/discovery"})
        if environment_class == "PRODUCTION" and "import" not in refs:
            refs["import"] = f"vault://fake/{key}/import"
        self.environments[key] = EnvironmentInfo(
            environment_key=key,
            name=name or key,
            base_url=base_url or f"https://{key}.invalid",
            version="2.41.4",
            enabled=enabled,
            # S4 derives one from the other (`enabled = row.status == ACTIVE`); keeping the two
            # fields in step here is what stops a seeded disabled environment reading as live on
            # the row and dead in the action list.
            status="ACTIVE" if enabled else "DISABLED",
            environment_class=environment_class,
            auth_mode=auth_mode,
            llm_data_policy=llm_data_policy,
            secret_refs=refs,
            credentials=[
                CredentialMirror(
                    scope=scope,
                    secret_ref=ref,
                    expires_at=_in_days(120 if scope == "discovery" else 90),
                    label=f"{key} {scope}",
                )
                for scope, ref in sorted(refs.items())
            ],
        )
        self.protections[key] = EnvironmentProtection(
            environment_key=key,
            environment_class=environment_class,
            emergency_locked=locked,
            within_commit_window=within_window,
            allows_fixture_import=environment_class != "PRODUCTION",
            commit_windows=list(commit_windows or []),
            timezone=timezone,
            deletion_policy=deletion_policy or ("FORBID" if environment_class == "PRODUCTION" else "SAFE_ONLY"),
            llm_data_policy=llm_data_policy,
        )

    def get_environment(self, environment_key: str) -> EnvironmentInfo | None:
        return self.environments.get(environment_key)

    def list_environments(self) -> list[EnvironmentInfo]:
        return list(self.environments.values())

    def create_environment(self, spec: dict[str, Any]) -> EnvironmentInfo:
        key = str(spec["environmentKey"])
        refs = {
            str(c.get("scope")): str(c.get("secretRef"))
            for c in (spec.get("credentials") or [])
            if c.get("scope") and c.get("secretRef")
        }
        protection = spec.get("protection") or {}
        self.add_environment(
            key,
            environment_class=str(spec.get("environmentClass", "TEST")),
            base_url=spec.get("baseUrl"),
            name=spec.get("name"),
            auth_mode=str(spec.get("authMode", "PAT")),
            llm_data_policy=str(spec.get("llmDataPolicy", "PRIVATE_NETWORK")),
            secret_refs=refs or None,
            timezone=str(protection.get("timezone", "UTC")),
            commit_windows=list(protection.get("commitWindows") or []),
            # no default here: S4 reads "not given" as the class default, and a hard-coded
            # SAFE_ONLY would hand a PRODUCTION environment the non-production policy
            deletion_policy=protection.get("deletionPolicy"),
        )
        return self.environments[key]

    def update_environment(self, environment_key: str, patch: dict[str, Any]) -> EnvironmentInfo:
        current = self.environments[environment_key]
        merged = {**current.model_dump(), **{k: v for k, v in patch.items() if v is not None}}
        # S4 keeps protection in its own row, and so does the fake: dropping it here would make
        # "amended the commit windows" answer 200 having changed nothing.
        protection = merged.pop("protection", None)
        if protection is not None:
            stored = self.protections.setdefault(
                environment_key, EnvironmentProtection(environment_key=environment_key)
            )
            self.protections[environment_key] = stored.model_copy(
                update={
                    "commit_windows": list(protection.get("commitWindows") or []),
                    "timezone": str(protection.get("timezone") or stored.timezone),
                    "deletion_policy": protection.get("deletionPolicy") or stored.deletion_policy,
                }
            )
        self.environments[environment_key] = _with_derived_refs(
            EnvironmentInfo.model_validate(merged)
        )
        return self.environments[environment_key]

    def disable_environment(self, environment_key: str, reason: str | None = None) -> None:
        environment = self.environments.get(environment_key)
        if environment is not None:
            environment.status = "DISABLED"
            environment.enabled = False

    def get_protection(self, environment_key: str) -> EnvironmentProtection:
        return self.protections.get(
            environment_key, EnvironmentProtection(environment_key=environment_key)
        )

    def set_emergency_lock(
        self, environment_key: str, locked: bool, reason: str | None, operator: str
    ) -> dict[str, Any]:
        from factory_control.foundation import utcnow

        self.calls.append(f"set_emergency_lock:{environment_key}={locked}")
        protection = self.protections.setdefault(
            environment_key, EnvironmentProtection(environment_key=environment_key)
        )
        stamp = utcnow()
        self.protections[environment_key] = protection.model_copy(
            update={
                "emergency_locked": locked,
                "lock_reason": reason if locked else None,
                "lock_changed_at": stamp,
                "lock_changed_by": operator,
            }
        )
        return {
            "environmentKey": environment_key,
            "emergencyLocked": locked,
            "lockReason": reason if locked else None,
            "lockChangedAt": stamp,
            "lockChangedBy": operator,
        }

    def list_expiring_credentials(self, within_days: int) -> list[Any]:
        from factory_control.ports.dto import ExpiringCredential

        out: list[Any] = []
        for row in self.expiring:
            out.append(
                ExpiringCredential(
                    environment_key=row.environment_key,
                    secret_ref=row.secret_ref,
                    expires_at=row.expires_at,
                    scope=row.scope,
                )
            )
        # Credentials the fake knows about through their environment count too, so
        # the UI has something to show before a test seeds ``expiring`` explicitly.
        now = datetime.now(UTC)
        for environment in self.environments.values():
            for credential in environment.credentials:
                if credential.expires_at is None:
                    continue
                remaining = (credential.expires_at - now).days
                if 0 <= remaining <= within_days:
                    out.append(
                        ExpiringCredential(
                            environment_key=environment.environment_key,
                            secret_ref=credential.secret_ref,
                            expires_at=credential.expires_at,
                            scope=credential.scope,
                        )
                    )
        return out

    def test_connection(
        self,
        environment_key: str,
        required_scopes: list[str] | None = None,
        *,
        idempotency_key: str,
    ) -> ConnectionCheck:
        from factory_control.foundation import utcnow

        self.calls.append("test_connection")
        scopes = list(required_scopes or ["discovery", "import"])
        known = environment_key in self.environments
        missing = {s: list(self.missing_authorities.get(s, [])) for s in scopes if self.missing_authorities.get(s)}
        eight = [
            ("addressReachable", known, None if known else "no route to host"),
            ("tlsValid", known, "TLS 1.3"),
            ("authentication", known, None),
            ("versionIdentified", known, "2.41.4 via /api/system/info"),
            ("versionConstraint", known, "matches 2.41.x"),
            ("metadataRead", "discovery" not in missing, "scope=discovery"),
            ("metadataImport", "import" not in missing, "403 on authority M:metadata:add" if "import" in missing else None),
            ("serviceAccountRole", True, "not required"),
        ]
        checks = [
            {"name": name, "state": "PASSED" if ok else "FAILED", "detail": detail}
            for name, ok, detail in eight
        ]
        ok = all(c["state"] == "PASSED" for c in checks)
        record = ConnectionCheck(
            ok=ok,
            result="PASSED" if ok else "FAILED",
            environment_key=environment_key,
            version="2.41.4",
            dhis2_version="2.41.4",
            tested_at=utcnow(),
            required_scopes=scopes,
            missing_authorities=missing,
            checks=checks,
            capability_profile_artifact_id=self.capability_artifact(environment_key),
            message=None if ok else "元数据导入权限缺失",
        )
        self.connections[environment_key] = record
        return record

    def get_last_connection_test(self, environment_key: str) -> ConnectionCheck | None:
        """The last recorded test (S5IF-UP10).

        A pure read of S4's own table: DX-CON-004 makes S4 persist every connection test,
        so answering the console needs no DHIS2 call — which is what makes it safe to use
        inside ``GET /deployment-runs/{id}/summary``.
        """
        self.calls.append("get_last_connection_test")
        return self.connections.get(environment_key)

    def search_discovery_objects(
        self,
        discovery_artifact_id: str,
        *,
        dhis2_type: str | None = None,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """S5IF-BP11 — candidates inside one stored discovery result.

        Reads only what a test registered as the result's content: the real S4 function
        parses the DISCOVERY_RESULT artifact and never opens a session to the instance, and
        a fake that called out would be pretending to be the thing it stands in for.
        """
        self.calls.append(f"search_discovery_objects:{discovery_artifact_id}")
        rows = self.discovery_objects.get(discovery_artifact_id) or []
        needle = (query or "").strip().lower()
        matched = [
            row
            for row in rows
            if (dhis2_type is None or str(row.get("dhis2Type")) == dhis2_type)
            and (not needle or needle in str(row.get("code", "")).lower() or needle in str(row.get("name", "")).lower())
        ]
        return {
            "items": matched[offset : offset + limit],
            "total": len(matched),
            "limit": limit,
            "offset": offset,
            "discoveryArtifactId": discovery_artifact_id,
        }

    def register_discovery_objects(self, discovery_artifact_id: str, objects: list[dict[str, Any]]) -> None:
        """Put objects into a fake discovery result so the candidate read can find them."""
        self.discovery_objects[discovery_artifact_id] = list(objects)

    def check_capability_freshness(self, environment_key: str) -> CapabilityFreshness:
        return self.freshness.get(
            environment_key,
            CapabilityFreshness(
                state="VALID",
                freshness="VALID",
                capability_profile_artifact_id=self.capability_artifact(environment_key),
                artifact_id=self.capability_artifact(environment_key),
                version="2.41.4",
                dhis2_version="2.41.4",
                profile_version="Dhis241Profile",
                stale=False,
            ),
        )

    def list_discovery_results(
        self,
        environment_key: str,
        project_id: str | None = None,
        scope: str | None = None,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[DiscoveryResultInfo]:
        rows = self.discovery_results.get(environment_key, [])
        if project_id:
            rows = [r for r in rows if r.project_id == project_id]
        if scope:
            rows = [r for r in rows if r.scope == scope]
        return rows[offset : offset + limit]

    def add_discovery_result(self, environment_key: str, result: DiscoveryResultInfo) -> None:
        self.discovery_results.setdefault(environment_key, []).append(result)

    def probe_capabilities(self, environment_key: str, *, idempotency_key: str) -> str | None:
        self.calls.append(f"probe_capabilities:{environment_key}")
        return self.capability_artifact(environment_key)

    def capability_artifact(self, environment_key: str) -> str:
        if environment_key not in self.capability_ids:
            self.capability_ids[environment_key] = register_fake_artifact(
                self.default_project, "CAPABILITY_PROFILE", f"{environment_key}-cap"
            )
        return self.capability_ids[environment_key]

    def discover(
        self,
        environment_key: str,
        project_id: str | None,
        scope: str | dict[str, Any],
        explicit_refs: list[dict[str, Any]] | None,
        *,
        idempotency_key: str,
        progress: Any = None,
        cancel: Any = None,
    ) -> DiscoveryOutcome:
        # S4 refuses a scope that selects nothing rather than degrading to a whole-instance
        # pull (FR-DSC-002), and it is right to: a silent empty scope once let DISCOVERY
        # "succeed" here while the real S4 rejected the same call outright, which is the
        # kind of lie a Fake is only allowed to tell on purpose.
        document = scope if isinstance(scope, dict) else {"kind": scope}
        selects = bool(
            document.get("greenfield")
            or document.get("dhis2Types")
            or document.get("codePrefixes")
            or document.get("programs")
            or document.get("orgUnitDepth")
            or (explicit_refs or [])
        )
        if not selects:
            raise FactoryError(
                "the discovery scope selects nothing to read: give dhis2Types, explicit "
                "references, a code prefix, an org unit depth, or mark it greenfield",
                code="DX_DISCOVERY_SCOPE_INVALID",
                details={"scope": document, "environmentKey": environment_key},
            )
        self.calls.append(f"discover:{environment_key}:{document.get('kind')}")
        return DiscoveryOutcome(
            discovery_result_artifact_id=register_fake_artifact(
                project_id or self.default_project, "DISCOVERY_RESULT", f"{environment_key}-disc"
            ),
            capability_profile_artifact_id=self.capability_artifact(environment_key),
            version="2.41.0",
            blocking_issues=[],
        )

    def prepare_and_validate(
        self,
        environment_key: str,
        project_id: str,
        package_artifact_id: str,
        expected_hash: str | None,
        *,
        import_options: dict[str, Any] | None = None,
        maintenance_actions: list[dict[str, Any]] | None = None,
        operator: str,
        idempotency_key: str,
        progress: Any = None,
        cancel: Any = None,
    ) -> Dhis2ValidationOutcome:
        self.calls.append(f"prepare_and_validate:{environment_key}")
        if self.validate_results:
            return self.validate_results.pop(0)
        ids = {
            name: register_fake_artifact(project_id, name, f"{environment_key}-{name}")
            for name in ("EXECUTION_PLAN", "SNAPSHOT", "DHIS2_IMPORT_REPORT")
        }
        return Dhis2ValidationOutcome(
            execution_plan_artifact_id=ids["EXECUTION_PLAN"],
            snapshot_artifact_id=ids["SNAPSHOT"],
            import_report_artifact_id=ids["DHIS2_IMPORT_REPORT"],
            payload_artifact_id=package_artifact_id,
            payload_hash=expected_hash or _hash(package_artifact_id),
            passed=True,
            issues=[],
            import_options={"atomicMode": "all"},
            requires_confirmation_items=[],
        )

    def check_drift(self, environment_key: str, package_artifact_id: str) -> DriftResult:
        self.calls.append(f"check_drift:{environment_key}")
        return self.drift.get(environment_key, DriftResult(drifted=False, objects=[]))

    def commit(
        self,
        environment_key: str,
        project_id: str,
        package_artifact_id: str,
        expected_hash: str,
        *,
        operator: str,
        idempotency_key: str,
        progress: Any = None,
        cancel: Any = None,
    ) -> ExecutionOutcome:
        self.calls.append(f"commit:{environment_key}")
        existing = self.by_key.get(idempotency_key)
        if existing is not None:
            return existing  # CP-ASM-04: same key never double-submits
        if self.commit_results:
            outcome = self.commit_results.pop(0)
        else:
            self._counter += 1
            outcome = ExecutionOutcome(
                execution_id=f"exe-{self._counter:04d}",
                status="COMPLETED",
                report_artifact_id=register_fake_artifact(
                    project_id, "EXECUTION_REPORT", f"exe-{self._counter}"
                ),
                import_report_artifact_id=register_fake_artifact(
                    project_id, "DHIS2_IMPORT_REPORT", f"exe-{self._counter}-imp"
                ),
                applied=True,
                payload_hash=expected_hash,
            )
        self.by_key[idempotency_key] = outcome
        if outcome.execution_id:
            self.executions[outcome.execution_id] = outcome
        return outcome

    def get_execution_by_key(self, idempotency_key: str) -> ExecutionOutcome | None:
        return self.by_key.get(idempotency_key)

    def get_execution(self, execution_id: str) -> ExecutionOutcome | None:
        return self.executions.get(execution_id)

    def reconcile(self, execution_id: str) -> ReconcileResult:
        outcome = self.executions.get(execution_id)
        if outcome is None:
            return ReconcileResult(outcome="UNKNOWN")
        if outcome.applied is True:
            return ReconcileResult(outcome="APPLIED", evidence={"status": outcome.status})
        if outcome.applied is False:
            return ReconcileResult(outcome="NOT_APPLIED", evidence={"status": outcome.status})
        return ReconcileResult(outcome="UNKNOWN", evidence={"status": outcome.status})

    def resolve_manual_check(
        self, execution_id: str, outcome: str, note: str, operator: str
    ) -> dict[str, Any]:
        self.calls.append(f"resolve_manual_check:{execution_id}={outcome}")
        current = self.executions.get(execution_id)
        if current is not None:
            self.executions[execution_id] = current.model_copy(
                update={"status": "COMPLETED" if outcome == "PASSED" else "FAILED", "applied": outcome == "PASSED"}
            )
        return {"executionId": execution_id, "resolved": True, "releasesLock": True}

    def post_verify(
        self,
        environment_key: str,
        project_id: str,
        package_artifact_id: str,
        execution_id: str | None,
        *,
        operator: str,
        idempotency_key: str,
        progress: Any = None,
        cancel: Any = None,
    ) -> PostVerifyOutcome:
        self.calls.append(f"post_verify:{environment_key}")
        return PostVerifyOutcome(
            report_artifact_id=register_fake_artifact(
                project_id, "POST_VERIFICATION_REPORT", f"{environment_key}-pv"
            ),
            consistent=True,
            issues=[],
        )

    def import_fixtures(
        self,
        environment_key: str,
        project_id: str,
        fixture_artifact_id: str,
        *,
        operator: str,
        idempotency_key: str,
        progress: Any = None,
        cancel: Any = None,
    ) -> ExecutionOutcome:
        self.calls.append(f"import_fixtures:{environment_key}")
        self._counter += 1
        return ExecutionOutcome(
            execution_id=f"exe-fix-{self._counter:04d}",
            status="COMPLETED",
            report_artifact_id=register_fake_artifact(
                project_id, "EXECUTION_REPORT", f"exe-fix-{self._counter}"
            ),
            applied=True,
        )

    def delete_objects(
        self,
        environment_key: str,
        project_id: str,
        deletion_artifact_id: str,
        expected_hash: str,
        *,
        operator: str,
        idempotency_key: str,
        progress: Any = None,
        cancel: Any = None,
    ) -> ExecutionOutcome:
        """DX-EXE-038 as S4's published double answers it, and nothing more.

        Like ``factory_dhis2.testing``'s own fake this returns COMPLETED without running the
        deletion's admission checks: an object with associated data, a ``deletionPolicy=FORBID``
        environment and an emergency lock all pass here.  That is a stated limit rather than an
        oversight — those refusals are S4's rules over S4's rows (DX-QRY-004, DX-ENV-007), and a
        double that guessed at them would let an S1 test pass by arguing with itself.  They are
        asserted against the real S4 in ``tests/service/test_real_s4_deletion.py``.

        What the double does keep is the one property a retry can break: a repeated key returns
        the first verdict and deletes nothing a second time (NFR-REL-003), as ``commit`` above.
        """
        self.calls.append(f"delete_objects:{environment_key}:{deletion_artifact_id}")
        existing = self.by_key.get(idempotency_key)
        if existing is not None:
            return existing  # a deletion is never replayed against the instance
        self._counter += 1
        outcome = ExecutionOutcome(
            execution_id=f"exe-del-{self._counter:04d}",
            status="COMPLETED",
            report_artifact_id=register_fake_artifact(
                project_id, "EXECUTION_REPORT", f"exe-del-{self._counter}"
            ),
            applied=True,
            payload_hash=expected_hash,
            environment_key=environment_key,
        )
        self.by_key[idempotency_key] = outcome
        self.executions[outcome.execution_id or ""] = outcome
        return outcome

    def delete_project_data(self, project_id: str, *, session: Session | None = None) -> None:
        self.calls.append(f"delete_project_data:{project_id}")


@dataclass
class ExpiringCredentialSeed:
    environment_key: str
    secret_ref: str
    expires_at: datetime | None = None
    scope: str | None = None


# --- Governance rows: the values S3's ``governance/capabilities.py`` answers with ------
#
# Declared as camelCase rows because that is what the fake stores and what S3's models
# project to.  ``FakeIntelligence`` validates each one into the port's mirror, so a shape
# that drifted from S3 surfaces as a validation error here rather than as a REST body S5
# cannot read (CP-UI-006 item 6, CP-ASM-08).

#: The **ceiling** an organisation may widen toward (AI-POL-002), not the state it starts in.
#: The fake used to answer its effective policy from this table, which left every level already
#: at its limit: no relaxation could be granted, and none could be refused for being too wide.
POLICY_MATRIX = {
    "PUBLIC": ["LOCAL_ONLY", "PRIVATE_NETWORK", "APPROVED_EXTERNAL", "UNRESTRICTED"],
    "INTERNAL": ["LOCAL_ONLY", "PRIVATE_NETWORK", "APPROVED_EXTERNAL"],
    "CONFIDENTIAL": ["LOCAL_ONLY", "PRIVATE_NETWORK"],
    "RESTRICTED": ["LOCAL_ONLY"],
    # D-07 / AC-POL-02: PHI_PII is never permitted to leave, so no relaxation reaches it.
    "PHI_PII": [],
}

#: What AI-POL-002 says an organisation begins with: both external classes denied above
#: INTERNAL.  Everything here is a subset of the ceiling above, which is what makes a
#: widening legal at all — and PUBLIC/INTERNAL are the only levels with room to move.
SYSTEM_DEFAULT_MATRIX = {
    level: (["LOCAL_ONLY"] if level == "RESTRICTED" else ["LOCAL_ONLY", "PRIVATE_NETWORK"])
    for level in ("PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED")
} | {"PHI_PII": []}


def _model_view(payload: dict[str, Any], ref: str) -> dict[str, Any]:
    capabilities = payload.get("capabilities") or {}
    return {
        "id": ref,
        "provider": payload.get("provider", "fake-provider"),
        "model": payload.get("model") or payload.get("name", "fake-model"),
        "ref": ref,
        "providerClass": payload.get("providerClass", "PRIVATE_NETWORK"),
        "capabilities": {
            "structuredOutput": bool(capabilities.get("structuredOutput", True)),
            "vision": bool(capabilities.get("vision", False)),
            "maxContextTokens": int(capabilities.get("maxContextTokens") or 8000),
        },
        "enabled": bool(payload.get("enabled", True)),
        "unitPriceInputPer1k": float(payload.get("unitPriceInputPer1k") or 0.0),
        "unitPriceOutputPer1k": float(payload.get("unitPriceOutputPer1k") or 0.0),
        "applicableAgents": dict(payload.get("applicableAgents") or {}),
        "lastEvalScore": None,
        "lastEvalRunId": None,
    }


def _provider_view(payload: dict[str, Any], name: str) -> dict[str, Any]:
    return {
        "id": payload.get("id") or f"prv-{name}",
        "name": name,
        "providerClass": payload.get("providerClass", "PRIVATE_NETWORK"),
        "endpoint": payload.get("endpoint", "https://invalid.local/v1"),
        "enabled": bool(payload.get("enabled", True)),
        "adapter": payload.get("adapter", "openai_compat"),
        "apiKeySecretRef": payload.get("apiKeySecretRef", "vault://fake/api-key"),
        "vendor": payload.get("vendor") or "custom",
        "suggestedModels": list(payload.get("suggestedModels") or []),
    }


def _prompt_row(name: str, version: int, agent_type: str) -> dict[str, Any]:
    return {
        "id": f"{name}.v{version}",
        "name": name,
        "version": version,
        "qualifiedName": f"{name}.v{version}",
        "agentType": agent_type,
        "systemRules": "fake system rules",
        "body": "fake prompt body",
        "inputSchemaRef": "input.json",
        "outputSchemaRef": "output.json",
        "blueprintSchemaRange": ">=1",
        "dhis2VersionRange": ">=2.40",
        "prohibitions": [],
        "applicableStages": [agent_type],
        "sourceHash": _hash(f"{name}.v{version}"),
        "active": True,
    }


def _fake_prompts(fake: Any) -> list[dict[str, Any]]:
    if not fake.prompts:
        fake.prompts = [_prompt_row("requirements", 1, "REQUIREMENTS")]
    return list(fake.prompts)


def _evaluation_row(eval_run_id: str = "evr-fake-0001", *, with_cases: bool = False) -> dict[str, Any]:
    """One evaluation as AI-GOV-004 requires it: the summary counts and, in the detail,
    the per-case verdicts and the injection outcome."""
    row: dict[str, Any] = {
        "id": eval_run_id,
        "agentType": "REQUIREMENTS",
        "prompt": "requirements.v1",
        "model": "fake-provider/fake-model",
        "evalSetVersion": "1",
        "score": 1.0,
        "passed": True,
        "caseCount": 1,
        "summary": {"golden": 1, "total": 1, "passed": 1},
    }
    if with_cases:
        row["cases"] = [
            {"caseId": "c1", "passed": True, "score": 1.0, "kind": "golden", "diff": {}}
        ]
        row["injectionCases"] = 1
        row["injectionPassed"] = 1
    return row


def _budget_row(project_id: str) -> dict[str, Any]:
    return {
        "projectId": project_id,
        "state": "UNSET",
        "spent": 0.0,
        "limit": None,
        "currency": "USD",
        "thresholdReached": None,
    }


def _usage_row(project_id: str) -> dict[str, Any]:
    return {
        "projectId": project_id,
        "workflowId": "wf-fake",
        "stage": "MAPPING",
        "agentType": "MAPPING",
        "model": "fake-model",
        "provider": "fake-provider",
        "calls": 1,
        "inputTokens": 10,
        "outputTokens": 20,
        "estimatedCost": 0.0003,
        "failedCalls": 0,
    }


def _agent_rows() -> dict[str, Any]:
    return {
        "milestone": "M2",
        "items": [
            {
                "agentType": agent,
                "stage": agent,
                "milestone": "M2",
                "enabled": True,
                "promptName": agent.lower(),
                "activePrompt": f"{agent.lower()}.v1",
                # AI-GOV-008 asks *whether* the agent runs per page, not how many it takes
                "perPage": True,
                "patchMode": "sections",
            }
            for agent in ("REQUIREMENTS", "DOMAIN", "MAPPING", "PAGE_DESIGN")
        ],
    }


# Conformance of these doubles to the port Protocols is asserted by
# ``tests/unit/test_port_conformance.py`` rather than at import time, so running
# under ``python -O`` cannot skip the check and a port change surfaces as a clear
# test failure.

__all__ = [
    "ExpiringCredentialSeed",
    "FakeBlueprint",
    "FakeExecution",
    "FakeIntelligence",
]


def _workflow_ids_for_input(session: Session, input_artifact_id: str) -> list[str]:
    from sqlalchemy import select

    from factory_control.models import Stage

    return [
        row.workflow_id
        for row in session.execute(
            select(Stage).where(Stage.stage_type == "REQUIREMENTS")
        ).scalars()
        if input_artifact_id in list(row.output_artifact_ids or [])
    ][:1]

