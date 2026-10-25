"""Data mirrors of the S2 / S3 / S4 public API types.

S2, S3 and S4 are library subsystems that expose Pydantic models from their own
``api`` modules; those packages are not published yet.  S1 therefore declares the
narrow shapes it actually consumes here (SRS 7.2), so:

* services and state machines depend on these types, never on a provider package;
* the fake adapters in :mod:`factory_control.testing.fakes` and the real adapters
  in :mod:`factory_control.adapters` translate to and from whatever the provider
  really exports.

Only information S1 is allowed to know is mirrored — no Blueprint content, no
Prompt bodies, no DHIS2 credentials (SRS 1.3).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel


class _Mirror(BaseModel):
    """Base for provider mirrors.

    ``extra="ignore"`` keeps a provider that adds fields from breaking S1; the
    camelCase aliases accept both the REST-style spelling the SRS examples use and
    the snake_case spelling S1's own code passes.
    """

    model_config = ConfigDict(
        extra="ignore", populate_by_name=True, alias_generator=to_camel, from_attributes=True
    )


# --- Shared ----------------------------------------------------------------


#: What each S1 field is spelled by the providers that fill it.  S2's BP-VAL-001 structure,
#: S3's ``Issue`` and S4's DX-EXE-027 structure agree on most names and not on these, and the
#: mirror must not lose the difference: a finding whose text is dropped is a finding nobody can
#: act on, and a BLOCKING read as the default WARNING makes a failed deployment look like a
#: passing one.
PROVIDER_SPELLINGS: dict[str, tuple[str, ...]] = {
    "level": ("severity",),
    "description": ("message",),
    "suggested_action": ("remediation",),
    "object_uid": ("uid",),
}


def fold_provider_names(data: dict[str, Any]) -> dict[str, Any]:
    """Copy each provider spelling into the S1 name, leaving the S1 name authoritative.

    A provider that already uses S1's name is untouched; one that sends both is read by its
    S1-named field.  Unknown keys are dropped afterwards by ``extra="ignore"``, so exactly one
    name per concept leaves this boundary.
    """
    for field, spellings in PROVIDER_SPELLINGS.items():
        if data.get(field):
            continue
        for spelling in spellings:
            if data.get(spelling) is not None:
                data = {**data, field: data[spelling]}
                break
    return data


class Issue(_Mirror):
    """One finding in a validation / import / compile report.

    ``issue_id`` + ``source_stage`` are required by CP-ASM-02: S1 must be able to
    point a failure at the stage that introduced it (CP-WF-016) and must let a
    WARNING be dispositioned one by one (CP-GATE-008).
    """

    issue_id: str
    rule_id: str | None = None
    level: str = "WARNING"
    object_key: str | None = None
    object_uid: str | None = None
    description: str = ""
    source_stage: str | None = None
    artifact_id: str | None = None
    business_key: str | None = None
    suggested_action: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _one_name_per_concept(cls, data: Any) -> Any:
        """Admit the providers' spellings, never as a second field of S1's own.

        Declaring only ``level``/``description``/``suggested_action``/``object_uid`` is what
        keeps the divergence inside this validator: no S1 code asks "which of the two won",
        and no dump can carry both.  A provider that has not renamed its fields still keeps
        its findings, while a provider that answers none of the spellings leaves S1 a WARNING
        rather than a validation error on the fields S1 always reads.
        """
        if isinstance(data, dict):
            data = fold_provider_names(data)
            if not data.get("level"):
                data = {**data, "level": "WARNING"}
        return data

    @property
    def is_blocking(self) -> bool:
        return self.level.upper() == "BLOCKING"


class BoundRef(_Mirror):
    """``{refType, refId, contentHash}`` — the object a Gate approves (SRS 2)."""

    ref_type: str
    ref_id: str
    content_hash: str


class QuestionDraft(_Mirror):
    """A question as returned by S3 or S2, before S1 persists it (CP-Q-002)."""

    local_id: str
    text: str
    severity: str = "MEDIUM"
    answer_mode: str = "SINGLE"
    affects: list[str] = Field(default_factory=list)
    suggestions: list[dict[str, Any]] = Field(default_factory=list)
    blocking: bool = False


# --- S2 Blueprint ----------------------------------------------------------


class BlueprintVersionInfo(_Mirror):
    version: str
    content_hash: str
    change_level: str | None = None
    artifact_id: str | None = None
    schema_version: int | None = None


class BlueprintDraftInfo(_Mirror):
    draft_id: str
    content_hash: str
    # S2 declares a draft's scope as the list of sections it may touch.
    stage_scope: list[str] = Field(default_factory=list)
    project_id: str | None = None
    base_version: str | None = None
    stage: str | None = None
    status: str | None = None
    change_request_id: str | None = None
    frozen_hash: str | None = None
    promotable: bool | None = None
    open_patch_count: int = 0


class ProjectSemantics(_Mirror):
    """CP-WF-002 / CP-PRJ-005: delivery mode and Fixture-spec presence are S2's to know."""

    delivery_mode: str = "A"
    project_type: str = ""
    has_fixture_spec: bool = False
    draft: BlueprintDraftInfo | None = None
    current_version: BlueprintVersionInfo | None = None
    mapping_frozen: bool = False


class PromotionResult(_Mirror):
    """CP-ASM-01: S2 returns the new version, its change level and the affected stages."""

    version: str
    change_level: str = "Minor"
    affected_stages: list[str] = Field(default_factory=list)
    artifact_id: str | None = None
    content_hash: str | None = None
    impact: dict[str, Any] = Field(default_factory=dict)
    applied_patch_ids: list[str] = Field(default_factory=list)
    schema_version: int | None = None


class FreezeResult(_Mirror):
    """CP-GATE-013a: freeze is external I/O and tells S1 whether the draft is promotable."""

    draft_hash: str
    staging_handle: str | None = None
    promotable: bool = True
    issues: list[Issue] = Field(default_factory=list)
    draft_id: str | None = None
    blocking_count: int = 0
    warnings: list[Issue] = Field(default_factory=list)


class PatchResult(_Mirror):
    """CP-WF-029 / CP-Q-001: a manual patch may produce issues and merge questions."""

    status: str = "PROPOSED"
    draft_hash: str | None = None
    issues: list[Issue] = Field(default_factory=list)
    questions: list[QuestionDraft] = Field(default_factory=list)
    newly_assigned_uids: list[str] = Field(default_factory=list)
    # S2 answers ``assigned_uids`` as {businessKey: uid}; UI-MAN-003 shows exactly
    # which UIDs this patch newly allocated (S5IF-FLD11).
    assigned_uids: dict[str, str] = Field(default_factory=dict)


class BuildContext(_Mirror):
    """The deterministic inputs S2 recompiles from (BP-ASM-01, FR-CMP-001).

    S1 owns these: a retry of the same logical build must send the *same* values, so
    ``seed`` is derived from the idempotency key rather than from a random number and
    ``built_at`` comes from the context clock.  A provider that changes its own field
    names is invisible to S1 until the mirror is updated deliberately.
    """

    built_at: datetime
    seed: int
    requester: str
    correlation_id: str | None = None


class CompileResult(_Mirror):
    build_id: str | None = None
    artifact_ids: dict[str, str] = Field(default_factory=dict)
    issues: list[Issue] = Field(default_factory=list)
    payload_hash: str | None = None
    status: str | None = None
    version: str | None = None
    environment_key: str | None = None
    output_hashes: dict[str, str] = Field(default_factory=dict)


class ValidationOutcome(_Mirror):
    """R2 / baseline validation summary used for Gate warnings and BLOCKED decisions."""

    validation_report_artifact_id: str | None = None
    blocking_count: int = 0
    issues: list[Issue] = Field(default_factory=list)
    artifact_ids: dict[str, str] = Field(default_factory=dict)
    source_stage: str | None = None
    build_id: str | None = None
    status: str | None = None
    warning_count: int = 0
    passed: bool = True


class ImpactAnalysis(_Mirror):
    """CP-CR-003, in S1's names.

    S2 answers an ``ImpactSet`` whose fields are ``stages`` / ``business_keys`` /
    ``page_keys`` / ``artifact_types``; the adapter maps them here so nothing in S1
    has to know S2's spelling.  There is deliberately no deletion field: 总体 SRS 15.6 makes a
    directed deletion an authorization the *change request* grants (CP-CR-005), and S2's impact
    read has no such list to answer with.
    """

    affected_stages: list[str] = Field(default_factory=list)
    affected_object_keys: list[str] = Field(default_factory=list)
    affected_pages: list[str] = Field(default_factory=list)
    affected_artifact_types: list[str] = Field(default_factory=list)
    sections: list[str] = Field(default_factory=list)
    operation_keys: list[str] = Field(default_factory=list)


class DecisionInfo(_Mirror):
    """CP-GATE-019: the Gate explanation quotes S2 decisions, it does not store them."""

    decision_id: str
    kind: str | None = None
    type: str | None = None
    subject: Any = None
    selected: Any = None
    rationale: str | None = None
    risks: list[Any] = Field(default_factory=list)
    alternatives: list[Any] = Field(default_factory=list)
    risk: str | None = None
    confidence: float | None = None
    impact_level: str | None = None
    confirmation: Any = None
    affected_artifacts: list[str] = Field(default_factory=list)
    requires_confirmation: bool = False


class VersionDetail(_Mirror):
    """One row of S2's version index (``dto/blueprint.py`` ``VersionInfo``)."""

    blueprint_id: str | None = None
    project_id: str | None = None
    version: str
    parent_version: str | None = None
    schema_version: int | None = None
    change_level: str | None = None
    content_hash: str | None = None
    artifact_id: str | None = None
    status: str | None = None
    source_artifact_ids: list[str] = Field(default_factory=list)
    decision_ids: list[str] = Field(default_factory=list)
    created_by: str | None = None
    created_at: str | None = None
    promoted_from_stage: str | None = None


class VersionPage(_Mirror):
    items: list[VersionDetail] = Field(default_factory=list)
    total: int = 0
    limit: int | None = None
    offset: int | None = None


class SelectedEntries(_Mirror):
    """S2's key-selected section entries.

    ``entries`` are the raw section JSON documents with S2's own ``_section`` and
    ``_path`` locator keys (S5IF-BP04 requires those survive the forwarding).
    """

    project_id: str | None = None
    version: str | None = None
    source: str = "APPROVED"
    entries: list[dict[str, Any]] = Field(default_factory=list)
    not_found: list[str] = Field(default_factory=list)


class ObservationBaseline(_Mirror):
    source_environment: str | None = None
    observed_last_updated: str | None = None
    observed_hash: str | None = None
    discovery_result_id: str | None = None


class RegistryEntryInfo(_Mirror):
    """One S2 Registry row (``dto/registry.py`` ``RegistryEntry``)."""

    business_key: str
    dhis2_type: str | None = None
    disposition: str | None = None
    status: str | None = None
    uid: str | None = None
    code: str | None = None
    managed: bool = False
    controlled_fields: list[str] = Field(default_factory=list)
    decision_id: str | None = None
    observed: ObservationBaseline = Field(default_factory=ObservationBaseline)
    environment_bindings: dict[str, str] = Field(default_factory=dict)


class PackageFile(_Mirror):
    """One file of a compiled metadata package (S2 ``dto/manifest.py``, BP-CMP-021).

    ``object_count`` is filled only for the files that carry objects; ``None`` means the file
    holds no object at all, which is not the same answer as "empty package".
    """

    path: str = ""
    role: str = "document"
    content_hash: str | None = None
    dhis2_type: str | None = None
    object_count: int | None = None


class PackageManifest(_Mirror):
    """What one build of one version put into the package (S5IF-BP17, UI-REV-003 by file).

    S2's read answers an unbuilt version with a sentinel (``build_id == ""``); turning
    that into the CP-UI-022 404 ``NOT_GENERATED`` is the service's job, not the mirror's.
    """

    project_id: str = ""
    blueprint_version: str = ""
    build_id: str = ""
    kind: str = ""
    environment_key: str | None = None
    package_hash: str | None = None
    files: list[PackageFile] = Field(default_factory=list)


class ObjectRelation(_Mirror):
    """One dependency-graph edge (S2 ``dto/graph.py`` ``ObjectRelation``, BP-IMP-004).

    Edges only: the nodes are the Registry rows S1 already reads, so a graph answer that copied
    entry fields would give S1 two things to keep in step.
    """

    from_business_key: str = ""
    to_business_key: str = ""
    field: str = ""
    from_dhis2_type: str | None = None
    to_dhis2_type: str | None = None
    edge_kind: str = "ref"


class GraphNode(_Mirror):
    """One Registry entry as one build saw it (S2 ``dto/graph.py`` GraphNode, BP-TD-009).

    ``uid``/``managed``/``in_payload`` are the build's frozen answers.  The degrees are
    the *complete* graph's: filtering narrows what is returned, never what a node's
    in-degree or orphan status says (BP-TD-009, BP-AC-16) — which is exactly why S1
    forwards them instead of recomputing anything.
    """

    business_key: str
    dhis2_type: str | None = None
    uid: str | None = None
    disposition: str | None = None
    managed: bool = False
    in_payload: bool = False
    in_degree: int = 0
    out_degree: int = 0


class UnresolvedGraphReference(_Mirror):
    """A metadata reference whose target the build never resolved (BP-TD-009).

    A graph annotation, not a validation finding: cross-section problems are the
    validation report's answer (BP-VAL-030), and the two lists must not be conflated.
    """

    from_business_key: str = ""
    field: str = ""
    target: str = ""


class GraphFilters(_Mirror):
    """How to narrow a graph read (S2 ``dto/graph.py`` GraphFilters, BP-TD-009).

    Each supplied dimension narrows and the dimensions combine as AND; within one
    dimension any member matches.  A kept node brings its one-hop edges — an edge is
    returned when either end is kept, so its far end may lie outside the view.  The
    degrees and the orphan / dangling verdicts never change with the filter.
    """

    dhis2_types: tuple[str, ...] | None = None
    business_keys: tuple[str, ...] | None = None


class DependencyGraph(_Mirror):
    """S2's dependency graph for one build (BP-TD-009, forwarded by CP-UI-020).

    The graph file format belongs to S2: S1 asks for the whole answer per build and
    projects it, it never assembles nodes from other reads nor parses a graph file.
    The provider mirror test compares these fields against S2's real ``dto/graph.py``
    models (CP-RT-005).
    """

    build_id: str = ""
    project_id: str = ""
    blueprint_version: str = ""
    kind: str = ""
    environment_key: str | None = None
    schema_version: int = 1
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[ObjectRelation] = Field(default_factory=list)
    #: nodes whose in- and out-degree are both zero on the whole graph
    orphans: list[str] = Field(default_factory=list)
    #: dangling metadata references; a graph annotation, not a validation finding
    unresolved_registry_refs: list[UnresolvedGraphReference] = Field(default_factory=list)


class PackageFileEntry(_Mirror):
    """One file inside a development package, as the listing describes it (BP-HO-004).

    Deliberately not ``PackageFile`` — that one lists a *metadata* package by import
    role; this one lists a FRONTEND_PACKAGE by document kind (overall SRS 17.1).
    """

    path: str = ""
    kind: str = "DOCUMENT"
    size_bytes: int = 0
    content_hash: str = ""


class PackageFileListing(_Mirror):
    """What one development package holds (S2 ``dto/package_files.py``, BP-HO-004)."""

    artifact_id: str = ""
    project_id: str = ""
    blueprint_version: str = ""
    files: list[PackageFileEntry] = Field(default_factory=list)


class PackageFileContent(_Mirror):
    """One file's content inside a FRONTEND_PACKAGE (S2 ``dto/package_files.py``).

    Package Compiler files are UTF-8 text (Markdown, JSON or YAML), so the content
    travels as text and ``media_type`` says which one the caller holds.
    """

    path: str = ""
    kind: str = "DOCUMENT"
    content: str = ""
    media_type: str = "application/octet-stream"
    size_bytes: int = 0
    content_hash: str = ""


class ManagedObjectInfo(_Mirror):
    business_key: str
    dhis2_type: str | None = None
    uid: str | None = None
    disposition: str | None = None
    code: str | None = None
    controlled_fields: list[str] = Field(default_factory=list)
    observed_last_updated: str | None = None
    observed_hash: str | None = None


class BindingInfo(_Mirror):
    environment_key: str | None = None
    business_key: str
    uid: str | None = None
    resolution: str | None = None
    observed_last_updated: str | None = None
    observed_hash: str | None = None
    discovery_result_id: str | None = None
    resolved_by: str | None = None
    note: str | None = None
    pending_reparse: bool = False


class UidPoolStatus(_Mirror):
    project_id: str | None = None
    available: int = 0
    low_water_mark: int = 0
    source_environment: str | None = None
    needs_replenishment: bool = False


class SectionLayout(_Mirror):
    """The entry container and key field of one Blueprint section (S2 ``content/layout``)."""

    section: str
    entry_container: str | None = None
    key_field: str | None = None


class SectionCatalog(_Mirror):
    current_schema_version: int | None = None
    available_versions: list[int] = Field(default_factory=list)
    sections: list[SectionLayout] = Field(default_factory=list)


class NrrItem(_Mirror):
    """One requirement-reconciliation line as S3 writes it into the ``NRR`` artifact."""

    business_key: str | None = None
    statement: str | None = None
    category: str | None = None
    source: dict[str, Any] = Field(default_factory=dict)
    rationale: str | None = None
    priority: str | None = None
    status: str | None = None
    confidence: float | None = None
    actors: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


# --- S3 Intelligence -------------------------------------------------------


class AgentRunRequest(_Mirror):
    """Everything S3 needs, supplied by S1 (S3 never reaches into other schemas)."""

    project_id: str
    workflow_id: str
    stage_id: str
    stage: str
    agent_type: str
    draft_id: str | None = None
    scope: list[str] = Field(default_factory=list)
    input_artifact_ids: list[str] = Field(default_factory=list)
    design_reference_environment_key: str | None = None
    target_environment_policies: dict[str, Any] = Field(default_factory=dict)
    capability_profile_artifact_id: str | None = None
    discovery_result_artifact_id: str | None = None
    operator: str = "system"
    #: the interface language the operator is reading, so the agent's prose matches the screen
    language: str | None = None
    idempotency_key: str


class AgentRunResult(_Mirror):
    """AI-RT-017 shape, restricted to what the orchestrator branches on."""

    run_id: str | None = None
    status: str = "COMPLETED"  # AgentRunStatus
    summary: str | None = None
    confidence: float | None = None
    patch_ids: list[str] = Field(default_factory=list)
    agent_output_artifact_id: str | None = None
    questions: list[QuestionDraft] = Field(default_factory=list)
    rejected_artifacts: list[dict[str, Any]] = Field(default_factory=list)
    budget_status: dict[str, Any] = Field(default_factory=dict)
    denial_reasons: list[str] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)


class ParsedInput(_Mirror):
    status: str = "PARSED"
    nrr_artifact_id: str | None = None
    questions: list[QuestionDraft] = Field(default_factory=list)
    rejected_artifacts: list[dict[str, Any]] = Field(default_factory=list)
    parser_version: str | None = None
    parse_id: str | None = None
    parser_type: str | None = None
    # AI-INP-004/CP-UI-007 ask *whether* a model was involved, so this is a bool and not a
    # model name; S3 answers ``False`` and a ``str`` mirror rejected every real parse.
    used_model: bool = False
    error_code: str | None = None
    error_message: str | None = None


class LlmExchangeImage(_Mirror):
    media_type: str
    label: str
    content_hash: str
    size_bytes: int


class LlmExchangeMessage(_Mirror):
    role: str
    content: str
    images: list[LlmExchangeImage] = Field(default_factory=list)


class LlmExchangeRequest(_Mirror):
    messages: list[LlmExchangeMessage] = Field(default_factory=list)
    schema_name: str | None = None
    json_schema: dict[str, Any] | None = None
    temperature: float | None = None
    max_output_tokens: int | None = None
    agent_hint: dict[str, Any] | None = None


class LlmExchangeResponse(_Mirror):
    text: str | None = None
    payload: dict[str, Any] | None = None
    finish_reason: str | None = None
    truncated: bool = False
    model: str | None = None
    request_id: str | None = None
    usage: dict[str, int] | None = None
    storage_truncated: bool = False


class LlmExchangeSummary(_Mirror):
    exchange_id: str
    sequence: int
    status: str
    provider: str
    provider_class: str | None = None
    model: str
    request_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime | None = None
    completed_at: datetime | None = None


class LlmExchangePage(_Mirror):
    items: list[LlmExchangeSummary] = Field(default_factory=list)
    total: int = 0
    limit: int = 50
    offset: int = 0


class LlmExchangeDetail(LlmExchangeSummary):
    input_artifact_id: str
    request: LlmExchangeRequest
    response: LlmExchangeResponse | None = None
    expires_at: datetime | None = None


class Answer(_Mirror):
    """CP-Q-005: an answer picks suggestions and/or supplies custom text.

    ``suggestion_ids``/``suggestion_texts`` carry a MULTIPLE answer; ``suggestion_id`` stays
    for SINGLE answers and for confirmation records S3 already knows how to write.
    """

    question_local_id: str
    suggestion_id: str | None = None
    suggestion_ids: list[str] = Field(default_factory=list)
    suggestion_texts: list[str] = Field(default_factory=list)
    custom_text: str | None = None
    answered_by: str


# --- S4 Execution ----------------------------------------------------------


class CredentialMirror(_Mirror):
    """One credential of an environment: reference and expiry only (S5IF-G09).

    The value, a mask of it, its length and any prefix must never appear here —
    S4 owns the secret and S1 only forwards the reference.
    """

    scope: str
    secret_ref: str
    expires_at: datetime | None = None
    label: str | None = None


class EnvironmentInfo(_Mirror):
    """S4's Environment as S5 must show it (S4 SRS 3.1, DX-ENV-001).

    Field *names* are S1's projection; the values come from S4's own DTO.
    """

    environment_key: str
    name: str | None = None
    base_url: str | None = None
    version: str | None = None
    enabled: bool = True
    environment_class: str = "TEST"
    version_constraint: str | None = None
    auth_mode: str = "PAT"
    tls_required: bool = False
    llm_data_policy: str = "PRIVATE_NETWORK"
    timeout_seconds: int = 60
    status: str = "ACTIVE"
    notes: str | None = None
    secret_refs: dict[str, str] = Field(default_factory=dict)
    credentials: list[CredentialMirror] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class EnvironmentProtection(_Mirror):
    """CP-ASM-05 — read live, never cached (CP-WF-027)."""

    environment_key: str
    environment_class: str = "TEST"
    timezone: str = "UTC"
    commit_windows: list[dict[str, Any]] = Field(default_factory=list)
    within_commit_window: bool = True
    emergency_locked: bool = False
    lock_reason: str | None = None
    lock_changed_at: datetime | None = None
    lock_changed_by: str | None = None
    deletion_policy: str | None = None
    required_approvals: int | None = None
    operator_role: str | None = None
    data_export_policy: dict[str, Any] = Field(default_factory=dict)
    allows_fixture_import: bool | None = None
    llm_data_policy: str | None = None


class CapabilityFreshness(_Mirror):
    """CP-WF-022."""

    state: str = "VALID"  # CapabilityFreshness
    capability_profile_artifact_id: str | None = None
    version: str | None = None
    freshness: str | None = None
    profile_version: str | None = None
    dhis2_version: str | None = None
    probed_at: datetime | None = None
    stale: bool | None = None
    stale_reason: str | None = None
    artifact_id: str | None = None
    content_hash: str | None = None
    content: dict[str, Any] | None = None


class DiscoveryOutcome(_Mirror):
    discovery_result_artifact_id: str | None = None
    capability_profile_artifact_id: str | None = None
    blocking_issues: list[Issue] = Field(default_factory=list)
    version: str | None = None
    default_object_ids: dict[str, str] = Field(default_factory=dict)


class Dhis2ValidationOutcome(_Mirror):
    """S4 VALIDATE: plan + snapshot + import report + drift + issues (R3)."""

    execution_plan_artifact_id: str | None = None
    snapshot_artifact_id: str | None = None
    import_report_artifact_id: str | None = None
    payload_artifact_id: str | None = None
    payload_hash: str | None = None
    passed: bool = True
    issues: list[Issue] = Field(default_factory=list)
    import_options: dict[str, Any] = Field(default_factory=dict)
    maintenance_actions: list[dict[str, Any]] = Field(default_factory=list)
    requires_confirmation_items: list[dict[str, Any]] = Field(default_factory=list)


class DriftResult(_Mirror):
    drifted: bool = False
    objects: list[dict[str, Any]] = Field(default_factory=list)


class ExecutionOutcome(_Mirror):
    """CP-WF-008: COMMIT may come back unconfirmable, which is MANUAL_CHECK."""

    execution_id: str | None = None
    status: str = "COMPLETED"  # ExecutionStatus
    report_artifact_id: str | None = None
    import_report_artifact_id: str | None = None
    applied: bool | None = None
    payload_hash: str | None = None
    steps: list[dict[str, Any]] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    # Both exist on S4's own ``dto.execution.ExecutionOutcome`` (COMMIT, Fixture, DELETE and
    # maintenance share it), so mirroring them invents nothing:
    #
    # * ``environment_key`` — DX-BR-02 says an operation is bound to one environment explicitly
    #   and there is no default; a caller that asked about key A has to be able to see it was
    #   not answered by key B.
    # * ``detail`` — a *refused* execution (DX-EXE-038's admission checks) comes back as a
    #   FAILED outcome with the execution record kept, not as an exception, so this is the only
    #   field in which "why was my deletion refused" survives the boundary.
    environment_key: str | None = None
    detail: str | None = None


class ReconcileResult(_Mirror):
    outcome: str = "UNKNOWN"  # APPLIED | NOT_APPLIED | PARTIAL_MISMATCH | UNKNOWN
    evidence: dict[str, Any] = Field(default_factory=dict)


class PostVerifyOutcome(_Mirror):
    report_artifact_id: str | None = None
    consistent: bool = True
    issues: list[Issue] = Field(default_factory=list)


class ExpiringCredential(_Mirror):
    environment_key: str
    secret_ref: str
    expires_at: datetime | None = None
    scope: str | None = None


class ConnectionCheck(_Mirror):
    """S4's eight-part connection test (DX-CON-001/002), forwarded as a projection."""

    ok: bool = False
    version: str | None = None
    checks: list[dict[str, Any]] = Field(default_factory=list)
    capability_profile_artifact_id: str | None = None
    message: str | None = None
    environment_key: str | None = None
    result: str | None = None
    dhis2_version: str | None = None
    tested_at: datetime | None = None
    required_scopes: list[str] = Field(default_factory=list)
    missing_authorities: dict[str, list[str]] = Field(default_factory=dict)


class DiscoveryResultInfo(_Mirror):
    """One stored DISCOVERY_RESULT index row (S4 ``models/profile.py``)."""

    artifact_id: str
    project_id: str | None = None
    scope: str | None = None
    object_count: int = 0
    blocking_issues: list[Issue] = Field(default_factory=list)
    version: str | None = None
    discovered_at: datetime | None = None
    content_hash: str | None = None


# --- Provider call envelopes ----------------------------------------------


class ExportBundle(_Mirror):
    """CP-PRJ-008 pieces that belong to S2 (Blueprint versions + Registry)."""

    artifact_ids: list[str] = Field(default_factory=list)
    export_artifact_id: str | None = None
    manifest: dict[str, Any] = Field(default_factory=dict)
    content_hash: str | None = None
    versions: list[str] = Field(default_factory=list)
    current_version: str | None = None
    referenced_artifact_ids: list[str] = Field(default_factory=list)


class ImportResult(_Mirror):
    restored_versions: list[str] = Field(default_factory=list)
    pending_environment_bindings: list[str] = Field(default_factory=list)
    project_id: str | None = None
    batch_id: str | None = None
    current_version: str | None = None
    counts: dict[str, int] = Field(default_factory=dict)
    unmapped_artifact_ids: list[str] = Field(default_factory=list)


# --- S3 Governance (CP-UI-006, AI-GOV-001) ---------------------------------
#
# One mirror per typed governance function, named as S3 names its own result so
# ``test_provider_mirrors`` compares the two field by field.  Before these existed the
# whole surface travelled as ``governance(action, payload)`` answering hand-built dicts,
# which is what CP-UI-006 item 6 now forbids on the port: a dictionary cannot be the
# declared type of ``GET /governance/providers``, and S1 -- not S3 -- owns the camelCase
# projection into the REST body (00 doc 6.11).


class CapabilitiesView(_Mirror):
    structured_output: bool = False
    vision: bool = False
    max_context_tokens: int | None = None


class ProviderView(_Mirror):
    """Address and Key *reference* only (AI-GOV-002, AC-13): no value, mask or length."""

    id: str = ""
    name: str = ""
    provider_class: str = ""
    endpoint: str | None = None
    enabled: bool = True
    adapter: str | None = None
    api_key_secret_ref: str | None = None
    #: the catalogue preset the row started from (a console label, not a capability)
    vendor: str = "custom"
    #: candidate model names for the model form; ``ai.model`` remains the registry
    suggested_models: list[str] = Field(default_factory=list)


class ModelView(_Mirror):
    id: str = ""
    provider: str = ""
    model: str = ""
    ref: str = ""
    provider_class: str = ""
    capabilities: CapabilitiesView = Field(default_factory=CapabilitiesView)
    enabled: bool = True
    unit_price_input_per_1k: float = 0.0
    unit_price_output_per_1k: float = 0.0
    applicable_agents: dict[str, int] = Field(default_factory=dict)
    last_eval_score: float | None = None
    last_eval_run_id: str | None = None
    endpoint: str | None = None
    api_key_secret_ref: str | None = None
    adapter: str | None = None


class EnableResult(_Mirror):
    ref: str = ""
    enabled: bool = False


class ProviderHealthView(_Mirror):
    """One reachability answer about a provider's address (S5IF-GOV20).

    S3 spells the provider name ``ref``; nothing here is a credential — the probe resolves the Key
    reference inside the transport and only the adapter's own sentence comes back (AI-LLM-005).
    """

    ref: str = ""
    healthy: bool = False
    detail: str | None = None


class PromptView(_Mirror):
    id: str = ""
    name: str = ""
    version: int = 0
    qualified_name: str = ""
    agent_type: str | None = None
    blueprint_schema_range: str | None = None
    dhis2_version_range: str | None = None
    prohibitions: list[Any] = Field(default_factory=list)
    source_hash: str | None = None
    active: bool = False


class PromptDetailView(_Mirror):
    id: str = ""
    name: str = ""
    version: int = 0
    qualified_name: str = ""
    agent_type: str | None = None
    blueprint_schema_range: str | None = None
    dhis2_version_range: str | None = None
    prohibitions: list[Any] = Field(default_factory=list)
    source_hash: str | None = None
    active: bool = False
    system_rules: str | None = None
    body: str | None = None
    input_schema_ref: str | None = None
    output_schema_ref: str | None = None
    applicable_stages: list[Any] = Field(default_factory=list)


class ActivationView(_Mirror):
    agent_type: str = ""
    prompt_version_id: str = ""
    name: str | None = None
    version: int | None = None
    model_list: list[str] = Field(default_factory=list)
    eval_run_id: str | None = None
    regression_passed_ref: str | None = None
    activated_by: str | None = None


class RegressionView(_Mirror):
    id: str = ""
    passed: bool = False
    prompt_version_id: str = ""


class PolicyMatrixView(_Mirror):
    allowed: dict[str, list[str]] = Field(default_factory=dict)
    sources: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class EffectivePolicyView(_Mirror):
    allowed: dict[str, list[str]] = Field(default_factory=dict)
    sources: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class OrgPolicyView(_Mirror):
    effective: PolicyMatrixView = Field(default_factory=PolicyMatrixView)
    is_default: bool = True
    updated_by: str | None = None
    #: The outermost policy a relaxation may reach (AI-POL-002).  Read with ``effective``, never
    #: instead of it: a form that has only the first cannot tell a legal widening from one S3
    #: refuses, and ``_Mirror``'s ``extra="ignore"`` would silently drop it here if it were not
    #: declared.
    ceiling: PolicyMatrixView = Field(default_factory=PolicyMatrixView)


class RelaxationView(_Mirror):
    id: str = ""
    scope: str = ""
    operator: str = ""
    reason: str | None = None
    change: dict[str, Any] = Field(default_factory=dict)
    role_checked: bool = False
    role_gate: str | None = None
    created_at: datetime | None = None


class EvaluationSummary(_Mirror):
    id: str = ""
    agent_type: str = ""
    prompt: str = ""
    model: str = ""
    eval_set_version: str = ""
    score: float = 0.0
    passed: bool = False
    case_count: int = 0
    summary: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None


class EvalCaseView(_Mirror):
    case_id: str = ""
    passed: bool = False
    score: float = 0.0
    kind: str | None = None
    diff: dict[str, Any] = Field(default_factory=dict)


class EvaluationDetail(_Mirror):
    """AI-GOV-004: the per-case results and the injection outcome are part of the detail."""

    id: str = ""
    agent_type: str = ""
    prompt: str = ""
    model: str = ""
    eval_set_version: str = ""
    score: float = 0.0
    passed: bool = False
    case_count: int = 0
    summary: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    cases: list[EvalCaseView] = Field(default_factory=list)
    injection_cases: int = 0
    injection_passed: int = 0


class AgentCatalogEntry(_Mirror):
    agent_type: str = ""
    stage: str = ""
    milestone: str = ""
    enabled: bool = False
    prompt_name: str = ""
    active_prompt: str | None = None
    per_page: bool = False
    patch_mode: str | None = None


class AgentCatalog(_Mirror):
    milestone: str | None = None
    items: list[AgentCatalogEntry] = Field(default_factory=list)


class UsageRollup(_Mirror):
    project_id: str | None = None
    workflow_id: str | None = None
    stage: str | None = None
    agent_type: str | None = None
    model: str | None = None
    provider: str | None = None
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: float = 0.0
    failed_calls: int = 0


class BudgetView(_Mirror):
    state: str = "UNSET"
    spent: float = 0.0
    limit: float | None = None
    currency: str = "USD"
    threshold_reached: float | None = None


class ChatReply(_Mirror):
    """One in-stage dialogue turn answered by S3 (AI-CHAT-001/002).

    ``questions`` mirrors S3's ``Question`` shape, which is ``QuestionDraft`` here: the
    questions a chat turn raised are shown, but S1 does not persist them as stage Questions —
    those come from a run (CP-Q-002), and a dialogue answer that asks something is not a Gate
    blocker.
    """

    answer: str = ""
    patch_ids: list[str] = Field(default_factory=list)
    questions: list[QuestionDraft] = Field(default_factory=list)


class EvalCaseOutcome(_Mirror):
    case_id: str = ""
    passed: bool = False
    score: float = 0.0
    kind: str | None = None
    diff: dict[str, Any] = Field(default_factory=dict)


class EvalResult(_Mirror):
    """One evaluation run as S3 reports it (AI-EVAL-001), per case included."""

    eval_run_id: str | None = None
    agent_type: str = ""
    prompt_name: str = ""
    prompt_version: int = 0
    model: str = ""
    eval_set_version: str = ""
    score: float = 0.0
    passed: bool = False
    cases: list[EvalCaseOutcome] = Field(default_factory=list)
    injection_cases: int = 0
    injection_passed: int = 0


__all__ = [
    "AgentRunRequest",
    "AgentRunResult",
    "Answer",
    "BindingInfo",
    "BlueprintDraftInfo",
    "BlueprintVersionInfo",
    "BoundRef",
    "CapabilityFreshness",
    "CompileResult",
    "ConnectionCheck",
    "CredentialMirror",
    "DecisionInfo",
    "DiscoveryOutcome",
    "DiscoveryResultInfo",
    "DriftResult",
    "EnvironmentInfo",
    "EnvironmentProtection",
    "ExecutionOutcome",
    "ExportBundle",
    "FreezeResult",
    "ImpactAnalysis",
    "ImportResult",
    "Issue",
    "ManagedObjectInfo",
    "ObjectRelation",
    "PackageFile",
    "PackageFileContent",
    "PackageFileEntry",
    "PackageFileListing",
    "PackageManifest",
    "DependencyGraph",
    "GraphNode",
    "GraphFilters",
    "UnresolvedGraphReference",
    "NrrItem",
    "ObservationBaseline",
    "ParsedInput",
    "PatchResult",
    "PostVerifyOutcome",
    "ProjectSemantics",
    "PromotionResult",
    "QuestionDraft",
    "ReconcileResult",
    "RegistryEntryInfo",
    "SectionCatalog",
    "SectionLayout",
    "SelectedEntries",
    "ExpiringCredential",
    "UidPoolStatus",
    "ValidationOutcome",
    "VersionDetail",
    "VersionPage",
    "Dhis2ValidationOutcome",
    "BuildContext",
    # S3 governance, one mirror per typed function (CP-UI-006 item 6)
    "ActivationView",
    "AgentCatalog",
    "AgentCatalogEntry",
    "BudgetView",
    "CapabilitiesView",
    "EnableResult",
    "EvalCaseOutcome",
    "EvalCaseView",
    "EvalResult",
    "EffectivePolicyView",
    "EvaluationDetail",
    "EvaluationSummary",
    "ModelView",
    "OrgPolicyView",
    "PolicyMatrixView",
    "PromptDetailView",
    "PromptView",
    "ProviderView",
    "RegressionView",
    "RelaxationView",
    "UsageRollup",
]
