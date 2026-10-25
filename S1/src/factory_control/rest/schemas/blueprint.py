"""Blueprint, Registry and Schema contracts (S5IF-BP).

S2 owns the *content* of every resource forwarded here.  These models mirror S2's
own DTO field names — they add nothing, and where S2 has not exported something yet
(S5IF-UP03/UP04/UP05/UP06) the field is declared optional and answered ``null``
rather than guessed at.
"""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, Field, model_validator

from factory_control.errors import ValidationFailedError
from factory_control.rest.schemas.common import CamelModel, IssueView, ListEnvelope, UpperSnakeValue

SOURCES = ("APPROVED", "DRAFT")


class SectionLayoutView(CamelModel):
    section: str
    entry_container: str | None = None
    key_field: str | None = None


class SectionSchemaIndexView(CamelModel):
    """S5IF-BP01 — enough to build the section list of a schema-driven form."""

    current_schema_version: int | None = None
    available_versions: list[int] = Field(default_factory=list)
    sections: list[SectionLayoutView] = Field(default_factory=list)


class SectionSchemaView(CamelModel):
    """S5IF-BP02 — the self-contained Draft 2020-12 document, passed through as-is.

    The known keys are declared so a generated client sees them; ``extra="allow"``
    keeps the rest of the JSON Schema vocabulary intact, which is the point of the
    endpoint: S5 hands the document to Monaco and React Hook Form unchanged.
    """

    model_config = ConfigDict(alias_generator=None, populate_by_name=True, extra="allow")

    schema_uri: str | None = Field(default=None, alias="$schema")
    id_uri: str | None = Field(default=None, alias="$id")
    title: str | None = None
    type: str | None = None
    definitions: dict[str, Any] | None = None


class VersionView(CamelModel):
    blueprint_id: str | None = None
    project_id: str | None = None
    version: str
    parent_version: str | None = None
    schema_version: int | None = None
    change_level: UpperSnakeValue | None = None
    content_hash: str | None = None
    artifact_id: str | None = None
    status: str | None = None
    source_artifact_ids: list[str] = Field(default_factory=list)
    decision_ids: list[str] = Field(default_factory=list)
    created_by: str | None = None
    created_at: str | None = None
    promoted_from_stage: str | None = None


class VersionPage(ListEnvelope):
    items: list[VersionView] = Field(default_factory=list)


class EntryPage(CamelModel):
    """S5IF-BP04 — raw section JSON plus S2's own ``_section``/``_path`` locators.

    Those two keys are S2's spelling and S5's checker depends on them (S5IF-BP04),
    so they are kept verbatim instead of being renamed to the surrounding camelCase.
    """

    project_id: str | None = None
    version: str | None = None
    source: str = "APPROVED"
    entries: list[dict[str, Any]] = Field(default_factory=list)
    not_found: list[str] = Field(default_factory=list)


class ObservationBaselineView(CamelModel):
    source_environment: str | None = None
    observed_last_updated: str | None = None
    observed_hash: str | None = None
    discovery_result_id: str | None = None


class RegistryEntryView(CamelModel):
    business_key: str
    dhis2_type: str | None = None
    disposition: str | None = None
    status: str | None = None
    uid: str | None = None
    code: str | None = None
    managed: bool = False
    controlled_fields: list[str] = Field(default_factory=list)
    decision_id: str | None = None
    observed: ObservationBaselineView = Field(default_factory=ObservationBaselineView)
    environment_bindings: dict[str, str] = Field(default_factory=dict)
    # Flattened from ``observed`` for UI-WB-005, which asks for UID/code/Disposition
    # and the observation baseline directly on the row (S5IF-BP06 allows this).
    observed_hash: str | None = None
    observed_last_updated: str | None = None
    discovery_result_id: str | None = None
    source_environment: str | None = None


class RegistryPage(ListEnvelope):
    items: list[RegistryEntryView] = Field(default_factory=list)


class ManagedObjectView(CamelModel):
    business_key: str
    dhis2_type: str | None = None
    uid: str | None = None
    disposition: str | None = None
    code: str | None = None
    controlled_fields: list[str] = Field(default_factory=list)
    observed_last_updated: str | None = None
    observed_hash: str | None = None


class ManagedScopePage(ListEnvelope):
    items: list[ManagedObjectView] = Field(default_factory=list)


class BindingView(CamelModel):
    environment_key: str | None = None
    # S1 resolves both from the Run and echoes them back, so S5 can confirm what it
    # just bound to without guessing (S5IF-BP10).
    blueprint_version: str | None = None
    business_key: str
    uid: str | None = None
    resolution: str | None = None
    resolved_by: str | None = None
    note: str | None = None
    pending_reparse: bool = False
    # S2 declares these but leaves them empty on the public read path
    # (S5IF-UP05); they are ``null`` here rather than absent so S5 sees the concept.
    observed_last_updated: str | None = None
    observed_hash: str | None = None
    discovery_result_id: str | None = None


class BindingPage(ListEnvelope):
    items: list[BindingView] = Field(default_factory=list)


class CandidateView(CamelModel):
    """One object inside a stored discovery result (S5IF-BP11).

    The candidate list exists so UI-DEP-003 can offer a target at 5,000 objects without
    shipping the whole DISCOVERY_RESULT to the browser; ``code`` and ``name`` are what the
    search matches on.
    """

    dhis2_type: str | None = None
    code: str | None = None
    name: str | None = None
    uid: str | None = None
    last_updated: str | None = None
    # The hash the discovery recorded for this object: UI-DEP-003 shows it next to the
    # candidate so the operator can tell an unchanged object from a re-parsed one.
    content_hash: str | None = None
    matching: bool = False


class CandidatePage(ListEnvelope):
    items: list[CandidateView] = Field(default_factory=list)
    discovery_artifact_id: str | None = None
    environment_key: str | None = None
    business_key: str | None = None
    dhis2_type: str | None = None


class ValidationIssuePage(ListEnvelope):
    """S5IF-BP14 — S2's Issue rows, camelCased by S1, filtered server-side."""

    items: list[IssueView] = Field(default_factory=list)


class UidPoolView(CamelModel):
    project_id: str | None = None
    available: int = 0
    low_water_mark: int = 0
    source_environment: str | None = None
    needs_replenishment: bool = False


class UidPoolReplenishRequest(CamelModel):
    """S5IF-BP09 — which instance to draw the UIDs from; default is the design reference."""

    environment_key: str | None = None


class DraftView(CamelModel):
    """S5IF-BP05 — the transition shape; the whole-document read is S5IF-UP03."""

    draft_id: str
    base_version: str | None = None
    stage: str | None = None
    stage_scope: list[str] = Field(default_factory=list)
    status: str | None = None
    content_hash: str | None = None
    promotable: bool | None = None
    open_patch_count: int = 0
    change_request_id: str | None = None
    document: dict[str, Any] | None = None


class DecisionView(CamelModel):
    decision_id: str
    type: str | None = None
    subject: Any = None
    selected: Any = None
    alternatives: list[Any] = Field(default_factory=list)
    rationale: str | None = None
    risks: list[Any] = Field(default_factory=list)
    confidence: float | None = None
    impact_level: str | None = None
    confirmation: Any = None
    affected_artifacts: list[str] = Field(default_factory=list)


class DecisionList(CamelModel):
    items: list[DecisionView] = Field(default_factory=list)


class DiffObjectView(CamelModel):
    business_key: str
    dhis2_type: str | None = None
    uid: str | None = None
    state: str | None = None
    disposition: str | None = None
    section: str | None = None
    changes: list[dict[str, Any]] = Field(default_factory=list)


class BlueprintDiffView(CamelModel):
    project_id: str | None = None
    kind: str | None = None
    from_version: str | None = None
    to_version: str | None = None
    environment_key: str | None = None
    objects: list[DiffObjectView] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)


class NrrItemView(CamelModel):
    business_key: str | None = None
    statement: str | None = None
    # ``category`` lives only in S3's NRR artifact; S2's ``requirements`` section
    # cannot hold it (S5IF-UP06), so it is answered when present and null when not.
    category: str | None = None
    source: dict[str, Any] = Field(default_factory=dict)
    rationale: str | None = None
    priority: str | None = None
    status: str | None = None
    confidence: float | None = None
    actors: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    artifact_id: str | None = None


class NrrPage(ListEnvelope):
    items: list[NrrItemView] = Field(default_factory=list)


class BindingSetRequest(CamelModel):
    uid: str
    note: str

    @model_validator(mode="after")
    def _note_required(self) -> BindingSetRequest:
        # S2 refuses a binding without a note (registry/binding.py:303); S1 checks
        # first so the caller gets S1's validation error rather than a provider one.
        if not (self.note or "").strip():
            raise ValidationFailedError(
                "note is required when setting an environment binding",
                details={"fields": [{"path": "note", "message": "required when setting a binding"}]},
            )
        if not (self.uid or "").strip():
            raise ValidationFailedError("uid is required when setting an environment binding", details={"field": "uid"})
        return self


class EntriesQuery(CamelModel):
    sections: list[str] = Field(default_factory=list)
    business_keys: list[str] = Field(default_factory=list)
    decision_ids: list[str] = Field(default_factory=list)
    page_keys: list[str] = Field(default_factory=list)
    source: str = "APPROVED"
    version: str | None = None

    @model_validator(mode="after")
    def _check_source(self) -> EntriesQuery:
        if self.source.upper() not in SOURCES:
            raise ValidationFailedError(
                "source must be APPROVED or DRAFT", details={"source": self.source, "allowed": list(SOURCES)}
            )
        return self

    def to_selector(self) -> dict[str, Any]:
        return {
            "sections": list(self.sections),
            "businessKeys": list(self.business_keys),
            "decisionIds": list(self.decision_ids),
            "pageKeys": list(self.page_keys),
            "source": self.source.upper(),
        }


class GraphNodeView(CamelModel):
    """One Registry entry as S2's build recorded it (CP-UI-020, BP-TD-009).

    The degrees are the complete graph's: filtering to one type cannot turn a
    referenced object into an orphan (BP-AC-16), so S1 forwards S2's numbers instead
    of recomputing anything.  Nodes are Registry entries only — pages, intents and
    decisions belong to other views (BP-TD-006).
    """

    business_key: str
    dhis2_type: str | None = None
    uid: str | None = None
    disposition: str | None = None
    managed: bool = False
    in_payload: bool = False
    in_degree: int = 0
    out_degree: int = 0


class GraphEdgeView(CamelModel):
    from_business_key: str = ""
    to_business_key: str = ""
    field: str = ""
    from_dhis2_type: str | None = None
    to_dhis2_type: str | None = None
    edge_kind: str = "ref"


class UnresolvedReferenceView(CamelModel):
    """A dangling metadata reference (BP-TD-009) — a graph annotation, not a finding.

    Cross-section reference problems answer from the validation report (BP-VAL-030);
    the two lists must not be shown as one (S5M UM-CAP-002).
    """

    from_business_key: str = ""
    field: str = ""
    target: str = ""


class GraphFiltersView(CamelModel):
    """The filter echo of a graph request (CP-UI-020): what was narrowed by."""

    dhis2_types: list[str] = Field(default_factory=list)
    business_keys: list[str] = Field(default_factory=list)


class WorkflowObjectGraphView(CamelModel):
    """CP-UI-020 — one build's graph, field-for-field S2's answer (CP-AC-33).

    ``runId`` echoes the request; the build was named by ``runId``, by
    ``blueprintVersion`` or, with neither, by the current version's latest baseline
    compile.  "No graph yet" is a 404 NOT_GENERATED, never this view with empty
    lists (CP-UI-022).  ``orphans`` and ``unresolvedRegistryRefs`` are the complete
    graph's facts even when the request filtered nodes and edges.
    """

    workflow_id: str
    run_id: str | None = None
    build_id: str = ""
    project_id: str = ""
    blueprint_version: str = ""
    kind: str = ""
    environment_key: str | None = None
    schema_version: int = 1
    filters: GraphFiltersView = Field(default_factory=GraphFiltersView)
    nodes: list[GraphNodeView] = Field(default_factory=list)
    edges: list[GraphEdgeView] = Field(default_factory=list)
    #: nodes with no edge in either direction, decided on the unfiltered graph
    orphans: list[str] = Field(default_factory=list)
    unresolved_registry_refs: list[UnresolvedReferenceView] = Field(default_factory=list)


class PackageFileView(CamelModel):
    """One file of the compiled package (S5IF-BP17).

    ``objectCount`` is null for the files that carry no object — a README holds nothing, which is
    a different statement from "this package is empty".
    """

    path: str
    role: str = "document"
    content_hash: str | None = None
    dhis2_type: str | None = None
    object_count: int | None = None


class PackageFilesView(ListEnvelope):
    project_id: str | None = None
    version: str | None = None
    build_id: str | None = None
    #: the build kind once a build exists (BASELINE / ENVIRONMENT / HANDOFF); "nothing
    #: compiled" is a 404 NOT_GENERATED now, not a marker in this field (CP-UI-022).
    kind: str | None = None
    environment_key: str | None = None
    package_hash: str | None = None
    files: list[PackageFileView] = Field(default_factory=list)


class FrontendPackageFileView(CamelModel):
    """One file inside the development package (CP-UI-021, BP-HO-004).

    ``kind`` is the file's role in the dev package (DOCUMENT, CONTRACT, UI_SPEC, ...),
    deliberately S2's word and not the metadata package's ``role``.
    """

    path: str
    kind: str = "DOCUMENT"
    size_bytes: int = 0
    content_hash: str = ""


class FrontendPackageFilesView(CamelModel):
    """What the project's development package holds — S2's listing forwarded whole."""

    artifact_id: str = ""
    project_id: str = ""
    blueprint_version: str = ""
    files: list[FrontendPackageFileView] = Field(default_factory=list)


__all__ = [
    "BindingPage",
    "CandidatePage",
    "CandidateView",
    "BindingSetRequest",
    "BindingView",
    "BlueprintDiffView",
    "DecisionList",
    "DecisionView",
    "DraftView",
    "EntriesQuery",
    "FrontendPackageFileView",
    "FrontendPackageFilesView",
    "GraphEdgeView",
    "GraphFiltersView",
    "GraphNodeView",
    "ManagedScopePage",
    "NrrItemView",
    "NrrPage",
    "ObservationBaselineView",
    "PackageFileView",
    "PackageFilesView",
    "RegistryEntryView",
    "RegistryPage",
    "SectionSchemaIndexView",
    "SectionSchemaView",
    "UidPoolView",
    "UnresolvedReferenceView",
    "ValidationIssuePage",
    "VersionPage",
    "VersionView",
    "WorkflowObjectGraphView",
]
