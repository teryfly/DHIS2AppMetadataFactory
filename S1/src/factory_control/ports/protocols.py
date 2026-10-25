"""Ports for the S2 / S3 / S4 public APIs (SRS 7.2, D-09, D-13).

S1 orchestrates by calling these Protocols.  Two implementations exist:

* :mod:`factory_control.adapters` — thin translation onto
  ``factory_blueprint.api`` / ``factory_intelligence.api`` / ``factory_dhis2.api``,
  used when those packages are installed;
* :mod:`factory_control.testing.fakes` — in-memory substitutes used until the
  providers ship (CP-ASM-08).

Signatures carry the session explicitly where the SRS requires the call to join
S1's unit of work, and carry ``progress`` / ``cancel`` / ``idempotency_key`` on
every long task (CP-ASM-03).  Methods listed in ``EXTERNAL_IO_METHODS`` must be
called with no unit of work open (00 doc 6.2); adapters mark them with S0's
``external_io`` and S1's services assert that precondition.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from sqlalchemy.orm import Session

from factory_control.foundation import CancellationToken, ProgressReporter
from factory_control.ports.dto import (
    ActivationView,
    AgentCatalog,
    AgentRunRequest,
    AgentRunResult,
    Answer,
    BindingInfo,
    BlueprintDraftInfo,
    BlueprintVersionInfo,
    BudgetView,
    BuildContext,
    CapabilityFreshness,
    ChatReply,
    CompileResult,
    ConnectionCheck,
    DecisionInfo,
    DependencyGraph,
    Dhis2ValidationOutcome,
    DiscoveryOutcome,
    DiscoveryResultInfo,
    DriftResult,
    EffectivePolicyView,
    EnableResult,
    EnvironmentInfo,
    EnvironmentProtection,
    EvalResult,
    EvaluationDetail,
    EvaluationSummary,
    ExecutionOutcome,
    ExpiringCredential,
    ExportBundle,
    FreezeResult,
    GraphFilters,
    ImpactAnalysis,
    ImportResult,
    LlmExchangeDetail,
    LlmExchangePage,
    ManagedObjectInfo,
    ModelView,
    ObjectRelation,
    OrgPolicyView,
    PackageFileContent,
    PackageFileListing,
    PackageManifest,
    ParsedInput,
    PatchResult,
    PostVerifyOutcome,
    ProjectSemantics,
    PromotionResult,
    PromptDetailView,
    PromptView,
    ProviderHealthView,
    ProviderView,
    QuestionDraft,
    ReconcileResult,
    RegistryEntryInfo,
    RegressionView,
    RelaxationView,
    SectionCatalog,
    SelectedEntries,
    UsageRollup,
    ValidationOutcome,
    VersionPage,
)

_BUILD_CONTEXT = dict[str, Any]


@runtime_checkable
class BlueprintPort(Protocol):
    """S2 — Blueprint versions, working drafts, patches, compilation, validation."""

    EXTERNAL_IO_METHODS: frozenset[str]

    def prepare_initial_blueprint(
        self, project_id: str, semantics: dict[str, Any]
    ) -> str:
        """Stage the initial Blueprint content outside any transaction (CP-PRJ-001)."""
        ...

    def register_initial_blueprint(
        self, project_id: str, handle: str, *, session: Session | None = None
    ) -> PromotionResult:
        """Register the staged draft as version 0.1.0 inside the caller's UoW."""
        ...

    def get_project_semantics(
        self, project_id: str, version: str | None = None
    ) -> ProjectSemantics: ...

    def get_current_version(self, project_id: str) -> BlueprintVersionInfo | None: ...

    def open_draft(
        self,
        project_id: str,
        stage_scope: str,
        change_request_id: str | None = None,
        *,
        session: Session | None = None,
    ) -> str: ...

    def get_draft(self, draft_id: str) -> BlueprintDraftInfo | None: ...

    def prepare_draft_base(self, project_id: str, draft_id: str) -> str:
        """Fill a freshly opened draft from the current version.

        External I/O: S2 reads the version content and stages it, so no unit of work
        may be open.  An open draft is not patchable or freezable until this ran.
        """
        ...

    def list_versions(
        self, project_id: str, *, page: int = 1, page_size: int = 50
    ) -> VersionPage:
        """S5IF-BP03 — the version index behind the Diff and history pickers."""
        ...

    def get_entries(
        self, project_id: str, selector: dict[str, Any], version: str | None = None
    ) -> SelectedEntries:
        """S5IF-BP04 — raw section JSON plus S2's ``_section``/``_path`` locators.

        Reads artifact bytes, so it is external I/O (00 doc 6.2).
        """
        ...

    def get_registry_entries(
        self, project_id: str, version: str, environment_key: str | None = None
    ) -> list[RegistryEntryInfo]: ...

    def get_managed_scope(
        self, project_id: str, version: str, environment_key: str
    ) -> list[ManagedObjectInfo]: ...

    def get_object_relationships(
        self,
        project_id: str,
        version: str | None = None,
        dhis2_types: list[str] | None = None,
    ) -> list[ObjectRelation]:
        """The dependency graph's edges (BP-IMP-004), for UI-REV-002's domain and mapping views.

        Nodes are not part of this answer: they are ``get_registry_entries``.  No version means
        the project's current one.
        """
        ...

    def get_package_manifest(
        self,
        project_id: str,
        version: str | None = None,
        environment_key: str | None = None,
    ) -> PackageManifest:
        """The files one build produced (BP-CMP-021), for UI-REV-003's by-file view.

        S2 answers an unbuilt version with its sentinel (``build_id == ""``); the
        service turns that into the CP-UI-022 404 rather than showing an empty layout.
        """
        ...

    def get_dependency_graph(
        self, build_id: str, filters: GraphFilters | None = None
    ) -> DependencyGraph:
        """The compiled dependency graph of one build (BP-TD-009, forwarded by CP-UI-020).

        External I/O (reads artifact content).  Nodes are Registry entries only, and the
        degrees, ``orphans`` and ``unresolved_registry_refs`` are computed on the complete
        graph — ``filters`` just trims what comes back (AND across dimensions, any member
        within one; an edge survives when either end is kept).  A build that does not
        exist is S2's ``NOT_FOUND`` with ``reason=NOT_EXIST``; a build with no graph,
        ``reason=NOT_GENERATED``.  S1 never assembles or parses a graph itself.
        """
        ...

    def list_package_files(self, artifact_id: str) -> PackageFileListing:
        """The file listing inside one FRONTEND_PACKAGE artifact (BP-HO-004, CP-UI-021).

        External I/O.  The development-package layout belongs to S2: callers forward
        the listing, they never unpack the artifact.  Unknown artifact is ``NOT_EXIST``;
        an artifact of the wrong type answers ``ARTIFACT_TYPE_MISMATCH``, which is a
        caller (S1) error — S1 maps it to 500 with a warning (CP-UI-021).
        """
        ...

    def read_package_file(self, artifact_id: str, path: str) -> PackageFileContent:
        """One file's content inside a FRONTEND_PACKAGE artifact (BP-HO-004, CP-UI-021).

        External I/O.  A path S2 refuses or does not carry answers ``NOT_EXIST`` — the
        refusal of ``..``, leading ``/``, backslash, ``#``, NUL or empty paths is
        indistinguishable from "no such file", so S1 forwards it without unpacking.
        """
        ...

    def get_bindings(
        self, project_id: str, version: str, environment_key: str | None = None
    ) -> list[BindingInfo]: ...

    def section_catalog(self) -> SectionCatalog:
        """S5IF-BP01 — the section list with each entry container and key field."""
        ...

    def submit_patch(
        self,
        project_id: str,
        draft_id: str,
        patch: dict[str, Any],
        *,
        stage: str,
        idempotency_key: str,
        change_request_id: str | None = None,
        session: Session | None = None,
    ) -> PatchResult:
        """Manual authoring path (CP-WF-029); may return merge questions (CP-Q-001).

        ``idempotency_key`` is S1's, not the provider's: S2 stores it against the
        patch so a retried submission cannot apply the same operations twice.
        """
        ...

    def freeze_draft(
        self,
        draft_id: str,
        target_environment_keys: list[str],
        *,
        progress: ProgressReporter | None = None,
        cancel: CancellationToken | None = None,
    ) -> FreezeResult:
        """CP-GATE-013a — external I/O, run before the approval transaction."""
        ...

    def promote_draft(
        self,
        project_id: str,
        draft_id: str,
        expected_hash: str,
        idempotency_key: str,
        *,
        session: Session | None = None,
    ) -> PromotionResult:
        """CP-GATE-014 — joins the caller's UoW so failure rolls the approval back."""
        ...

    def prepare_mode_upgrade(self, project_id: str) -> str: ...

    def commit_mode_upgrade(
        self,
        project_id: str,
        handle: str,
        idempotency_key: str,
        *,
        session: Session | None = None,
    ) -> PromotionResult: ...

    def analyze_impact(self, project_id: str, business_keys: list[str]) -> ImpactAnalysis: ...

    def registry_object_refs(
        self, project_id: str, version: str, environment_key: str | None = None
    ) -> list[dict[str, Any]]:
        """The explicit object list S4's discovery needs (DX-DSC-002, SRS 7.2)."""
        ...

    def list_object_types(self) -> list[dict[str, Any]]:
        """The object types this Factory can build, with their coverage tier.

        DISCOVERY asks it because a Brownfield project has not mapped anything yet: the
        Registry is filled by MAPPING, which runs *after* discovery, so the types to
        inspect have to come from S2's type descriptor catalog and not from the project
        (FR-DSC-001, FR-DSC-002, FR-COV-001).
        """
        ...

    def replenish_uid_pool(self, project_id: str, environment_key: str) -> int: ...

    def uid_pool_status(self, project_id: str) -> dict[str, Any]: ...

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
    ) -> dict[str, Any]: ...

    def get_section_schema(self, section: str, schema_version: int | None = None) -> dict[str, Any]:
        """S5IF-BP02 — a self-contained Draft 2020-12 document for one section."""
        ...

    def compile_baseline(
        self,
        project_id: str,
        version: str,
        capability_profile_artifact_id: str | None,
        build_context: BuildContext,
        *,
        progress: ProgressReporter | None = None,
        cancel: CancellationToken | None = None,
        idempotency_key: str,
    ) -> CompileResult: ...

    def compile_for_environment(
        self,
        project_id: str,
        version: str,
        environment_key: str,
        discovery_result_artifact_id: str | None,
        capability_profile_artifact_id: str | None,
        change_request_id: str | None,
        build_context: BuildContext,
        *,
        progress: ProgressReporter | None = None,
        cancel: CancellationToken | None = None,
        idempotency_key: str,
        deletion_candidates: list[dict[str, str]] | None = None,
    ) -> CompileResult:
        """R1 — CP-WF-028.

        CP-CR-005 needs both halves of the change request, and S2 reads only what it is told:
        ``change_request_id`` says *which* request authorized this compile, while
        ``deletion_candidates`` is the businessKey list that request named.  BP-CMP-026 admits a
        deletion only to a compile that carries both, so passing the id alone leaves the
        candidate set — and with it DX-EXE-038's cleanup — permanently empty.
        """
        ...

    def validate_build(
        self,
        build_id: str,
        *,
        progress: ProgressReporter | None = None,
        cancel: CancellationToken | None = None,
        idempotency_key: str,
    ) -> ValidationOutcome: ...

    def get_decisions(self, project_id: str, decision_ids: list[str]) -> list[DecisionInfo]: ...

    def diff_objects(
        self, project_id: str, artifact_id: str, against: str
    ) -> dict[str, Any]: ...

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
        """S5IF-BP14 — issues across several reports, filtered, sorted and paged by the provider.

        ``sort_by`` is one of ``level``, ``ruleId``, ``sourceStage``, ``businessKey`` or
        ``objectKey`` and the ordering happens where the rows are, not after a page was
        picked: UI-REV-005 sorts a list that can hold thousands of findings (CP-UI-004).

        One Issue model, S2's BP-VAL-001, with the level field named ``level``; S4 writes
        its import-report issues in the same structure so a single read spans both kinds
        of report.  S1 projects the result to camelCase and invents no fields of its own.
        """
        ...

    def reproduce(self, artifact_id: str) -> dict[str, Any]: ...

    def generate_frontend_package(
        self,
        project_id: str,
        version: str,
        environment_keys: list[str],
        deployment_evidence: dict[str, Any],
        build_context: BuildContext,
        *,
        progress: ProgressReporter | None = None,
        cancel: CancellationToken | None = None,
        idempotency_key: str,
    ) -> CompileResult: ...

    def export_blueprint(self, project_id: str) -> ExportBundle: ...

    def prepare_import(
        self, manifest: dict[str, Any], id_map: dict[str, str]
    ) -> str: ...

    def commit_import(
        self,
        project_id: str,
        handle: str,
        idempotency_key: str,
        *,
        session: Session | None = None,
    ) -> ImportResult: ...

    def delete_project_data(self, project_id: str, *, session: Session | None = None) -> None: ...


@runtime_checkable
class IntelligencePort(Protocol):
    """S3 — input parsing and agent runs."""

    EXTERNAL_IO_METHODS: frozenset[str]

    def parse_input(
        self,
        project_id: str,
        input_artifact_id: str,
        *,
        options: dict[str, Any] | None = None,
        operator: str,
        idempotency_key: str,
        progress: ProgressReporter | None = None,
        cancel: CancellationToken | None = None,
    ) -> ParsedInput: ...

    def list_llm_exchanges(
        self,
        project_id: str,
        input_artifact_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> LlmExchangePage: ...

    def get_llm_exchange(
        self, project_id: str, input_artifact_id: str, exchange_id: str
    ) -> LlmExchangeDetail | None: ...

    def purge_llm_exchanges(self) -> int: ...

    def start_agent_run(
        self,
        request: AgentRunRequest,
        *,
        progress: ProgressReporter | None = None,
        cancel: CancellationToken | None = None,
    ) -> AgentRunResult: ...

    def resume_agent_run(
        self,
        run_id: str,
        answers: list[Answer],
        idempotency_key: str,
        *,
        language: str | None = None,
        progress: ProgressReporter | None = None,
        cancel: CancellationToken | None = None,
    ) -> AgentRunResult: ...

    def cancel_agent_run(self, run_id: str) -> None: ...

    def get_agent_run(self, run_id: str) -> AgentRunResult | None: ...

    def explain_issues(
        self,
        project_id: str,
        report_artifact_id: str,
        *,
        environment_policies: dict[str, Any],
        operator: str,
        language: str | None = None,
    ) -> str | None: ...

    # --- Governance, one typed method per capability (CP-UI-006 item 6, AI-GOV-001) -----
    #
    # These replaced ``governance(action, payload)``.  The port is the contract: a
    # capability that S3 exports as a function is declared here as a method with a
    # declared result type, so ``GET /governance/providers`` has a named response to
    # project rather than a dictionary to hope for.  S3's transitional façade is not
    # reachable from this interface at all; only ``adapters/registry.py`` decides which
    # provider function each method calls.

    def list_providers(self) -> list[ProviderView]: ...

    def register_provider(self, spec: dict[str, Any], operator: str) -> ProviderView:
        """``spec`` carries a Key *reference*, never a value (AI-GOV-002, S5IF-GOV02)."""
        ...

    def update_provider(self, name: str, patch: dict[str, Any], operator: str) -> ProviderView:
        """Correct a provider in place; a key ``patch`` omits keeps its stored value.

        Distinct from registration on purpose: registration replaces the row, so an operator
        who only meant to fix the address would clear the Key reference with it (S5IF-GOV03).
        """
        ...

    def set_provider_enabled(self, name: str, enabled: bool, operator: str) -> EnableResult: ...

    def check_provider_health(self, name: str) -> ProviderHealthView:
        """One reachability probe of a provider's address (S5IF-GOV20); never a generation."""
        ...

    def list_models(
        self, agent_type: str | None = None, *, include_disabled: bool = True
    ) -> list[ModelView]: ...

    def register_model(self, spec: dict[str, Any], operator: str) -> ModelView: ...

    def update_model(self, ref: str, patch: dict[str, Any], operator: str) -> ModelView:
        """Correct a model in place; a key ``patch`` omits keeps its stored value.

        The two nullable columns -- AI-LLM-003's per-model address and Key reference -- are why
        this exists next to registration: a re-registration that omitted them writes NULL over
        them, and S1's read path then back-fills the provider's own values so the console shows an
        override nobody set and cannot remove (S5IF-GOV06).
        """
        ...

    def set_model_enabled(self, ref: str, enabled: bool, operator: str) -> EnableResult: ...

    def list_prompts(self, agent_type: str | None = None) -> list[PromptView]: ...

    def get_prompt(self, name: str, version: int) -> PromptDetailView: ...

    def activate_prompt(
        self,
        agent_type: str,
        prompt_name: str,
        prompt_version: int,
        model_list: list[str],
        operator: str,
    ) -> ActivationView: ...

    def record_regression(
        self,
        prompt_name: str,
        prompt_version: int,
        passed: bool,
        model: str | None,
        source: str,
        note: str | None,
        operator: str,
    ) -> RegressionView: ...

    def list_regressions(
        self,
        prompt_name: str | None = None,
        prompt_version: int | None = None,
    ) -> list[RegressionView]: ...

    def get_org_policy(self) -> OrgPolicyView: ...

    def relax_org_policy(
        self, change: dict[str, Any], reason: str, operator: str, scope: str = "organization"
    ) -> RelaxationView:
        """A relaxation without a reason never reaches S3 (AI-GOV-006)."""
        ...

    def get_effective_policy(
        self, environment_policies: dict[str, Any] | None = None
    ) -> EffectivePolicyView: ...

    def list_evaluations(
        self, agent_type: str | None = None, *, limit: int = 50
    ) -> list[EvaluationSummary]: ...

    def get_evaluation(self, eval_run_id: str) -> EvaluationDetail:
        """AI-GOV-004: per-case results and the injection outcome are in the detail."""
        ...

    def get_budget(self, project_id: str) -> BudgetView: ...

    def set_budget(
        self,
        project_id: str,
        limit: float | None,
        thresholds: Sequence[float],
        *,
        currency: str = "USD",
        operator: str,
    ) -> BudgetView:
        """AI-GOV-007's write side, delivered in M4 (S5IF-GOV18).

        The budget row belongs to S3 and stays there: S1 neither holds it nor decides whether
        a run may start on the strength of it (D-08, AI-COST-003).
        """
        ...

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
        progress: ProgressReporter | None = None,
        cancel: CancellationToken | None = None,
    ) -> ChatReply:
        """AI-CHAT-001: one turn of in-stage dialogue, answered synchronously (FR-UI-002).

        External I/O — S3 calls a model here, so S1 must not hold a transaction open (00 doc 6.2).
        """
        ...

    def get_chat(self, project_id: str, stage: str) -> list[dict[str, Any]]:
        """AI-CHAT-003's read side: the turn list S5 renders in the panel.

        Also external I/O: the transcript is an artifact, and S1 reads no ``ai`` row (D-08).
        """
        ...

    def clear_chat(self, project_id: str, stage: str) -> None:
        """AI-CHAT-003: the stage's session starts again; history is superseded, not deleted."""
        ...

    def usage_rollup(
        self,
        project_id: str | None = None,
        *,
        group_by: Sequence[str] = ("stage", "agent", "model"),
    ) -> list[UsageRollup]:
        """S1 validates ``group_by`` first — an unknown dimension must not become a 500."""
        ...

    def list_agents(self) -> AgentCatalog: ...

    def run_evaluation(
        self,
        agent_type: str,
        prompt_version: str,
        model: str,
        eval_set_version: str | None = None,
        *,
        idempotency_key: str,
        operator: str,
    ) -> EvalResult:
        """S5IF-GOV16 — the only way to start an evaluation.

        ``prompt_version`` is the qualified ``name.vN`` string the public signature
        takes.  It is not a governance action: starting one calls models, which is
        external I/O, and S3's façade refuses it for exactly that reason (00 doc 6.2).
        """
        ...

    def list_prompt_activations(self) -> list[ActivationView]:
        """S5IF-GOV10 — the activation table, one row per agent.

        Each row carries the prompt's ``name`` and ``version`` as well as its id, so a
        caller that activated by address and one that lists see the same fields.
        """
        ...

    def delete_project_data(self, project_id: str, *, session: Session | None = None) -> None: ...


@runtime_checkable
class ExecutionPort(Protocol):
    """S4 — environments, capabilities, discovery, VALIDATE / COMMIT, verification."""

    EXTERNAL_IO_METHODS: frozenset[str]

    def get_environment(self, environment_key: str) -> EnvironmentInfo | None: ...

    def list_environments(self) -> list[EnvironmentInfo]: ...

    def create_environment(self, spec: dict[str, Any]) -> EnvironmentInfo: ...

    def update_environment(self, environment_key: str, patch: dict[str, Any]) -> EnvironmentInfo: ...

    def disable_environment(self, environment_key: str, reason: str | None = None) -> None: ...

    def get_protection(self, environment_key: str) -> EnvironmentProtection: ...

    def set_emergency_lock(
        self, environment_key: str, locked: bool, reason: str | None, operator: str
    ) -> dict[str, Any]: ...

    def list_expiring_credentials(self, within_days: int) -> list[ExpiringCredential]: ...

    def test_connection(
        self,
        environment_key: str,
        required_scopes: list[str] | None = None,
        *,
        idempotency_key: str,
    ) -> ConnectionCheck: ...

    def get_last_connection_test(self, environment_key: str) -> ConnectionCheck | None:
        """The most recent stored connection test, without touching DHIS2 (S5IF-UP10).

        S4 persists every test (DX-CON-004), so the deployment console can show the last
        result without asking the target instance again — which is what makes the read
        usable inside a GET (S5IF-FLD05).  The name is S4 SRS 7.1's, so the adapter
        resolves it unchanged.  ``None`` when the environment was never tested or the
        provider build predates the read.
        """
        ...

    def check_capability_freshness(self, environment_key: str) -> CapabilityFreshness: ...

    def search_discovery_objects(
        self,
        discovery_artifact_id: str,
        *,
        dhis2_type: str | None = None,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """S5IF-BP11 — candidates inside one stored discovery result (S5IF-UP10).

        Owned by S4 because the DISCOVERY_RESULT format is S4's: S1 forwarding a read of
        the artifact content would make S1 depend on another subsystem's internal shape.
        It reads the artifact only and **never** the target instance, which is what makes
        it safe for the deployment screen's search box (UI-DEP-003).
        """
        ...

    def list_discovery_results(
        self,
        environment_key: str,
        project_id: str | None = None,
        scope: str | None = None,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[DiscoveryResultInfo]:
        """S5IF-ENV11 — the stored DISCOVERY_RESULT index for one environment.

        ``discover()`` runs a discovery and returns one artifact id; it cannot list
        what a previous run found, so the review and deployment views need this.
        """
        ...

    def probe_capabilities(self, environment_key: str, *, idempotency_key: str) -> str | None: ...

    def discover(
        self,
        environment_key: str,
        project_id: str | None,
        scope: str | dict[str, Any],
        explicit_refs: list[dict[str, Any]] | None,
        *,
        idempotency_key: str,
        progress: ProgressReporter | None = None,
        cancel: CancellationToken | None = None,
    ) -> DiscoveryOutcome:
        """Run one discovery.

        ``scope`` is either a bare kind name or S4's ``DiscoveryScope`` document, because
        the design line needs more than a label here: a Greenfield project must read only
        version, capabilities and the default objects (SRS 7.1), while a Brownfield one has
        to inspect the object types its mapping will fall onto before any Registry entry
        exists to name them.
        """
        ...

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
        progress: ProgressReporter | None = None,
        cancel: CancellationToken | None = None,
    ) -> Dhis2ValidationOutcome: ...

    def check_drift(self, environment_key: str, package_artifact_id: str) -> DriftResult: ...

    def commit(
        self,
        environment_key: str,
        project_id: str,
        package_artifact_id: str,
        expected_hash: str,
        *,
        operator: str,
        idempotency_key: str,
        progress: ProgressReporter | None = None,
        cancel: CancellationToken | None = None,
    ) -> ExecutionOutcome: ...

    def get_execution_by_key(self, idempotency_key: str) -> ExecutionOutcome | None:
        """CP-GATE-018 / AC-EXE-05 — the only safe way to retry COMMIT."""
        ...

    def get_execution(self, execution_id: str) -> ExecutionOutcome | None: ...

    def reconcile(self, execution_id: str) -> ReconcileResult: ...

    def resolve_manual_check(
        self, execution_id: str, outcome: str, note: str, operator: str
    ) -> dict[str, Any]: ...

    def post_verify(
        self,
        environment_key: str,
        project_id: str,
        package_artifact_id: str,
        execution_id: str | None,
        *,
        operator: str,
        idempotency_key: str,
        progress: ProgressReporter | None = None,
        cancel: CancellationToken | None = None,
    ) -> PostVerifyOutcome: ...

    def import_fixtures(
        self,
        environment_key: str,
        project_id: str,
        fixture_artifact_id: str,
        *,
        operator: str,
        idempotency_key: str,
        progress: ProgressReporter | None = None,
        cancel: CancellationToken | None = None,
    ) -> ExecutionOutcome: ...

    def delete_objects(
        self,
        environment_key: str,
        project_id: str,
        deletion_artifact_id: str,
        expected_hash: str,
        *,
        operator: str,
        idempotency_key: str,
        progress: ProgressReporter | None = None,
        cancel: CancellationToken | None = None,
    ) -> ExecutionOutcome:
        """DX-EXE-038: submit a deletion payload as its own execution (S4 SRS 7.4).

        The candidate set is a payload of its own — S4 reads it from the artifact's manifest
        ``deletionCandidates`` plus its ``@DELETE`` collection — and is never the last deployed
        package run backwards, which is why this takes a ``deletion_artifact_id`` and not the
        package a Run committed.

        S1 must not decide anything here.  S4 validates the same payload it is about to submit
        (DX-BR-05), re-runs the associated-data check *after* that validation and immediately
        before the request (DX-QRY-004: a row written between the two is exactly what would
        orphan data), honours the environment's ``deletionPolicy`` and its emergency lock
        (DX-ENV-007), and refuses the whole batch if one candidate is refused — deletion is the
        one action an instance cannot roll back.  A refusal is returned as a FAILED
        ``ExecutionOutcome``/``DX_DELETION_FORBIDDEN``, so a guard added on S1's side could only
        weaken it or contradict it.

        ``idempotency_key`` is not decorative: replaying a key returns the first verdict and
        never deletes a second time (NFR-REL-003).
        """
        ...

    def delete_project_data(self, project_id: str, *, session: Session | None = None) -> None: ...


class ProviderUnavailableError(RuntimeError):
    """A provider package is not installed and no fake is configured."""


def questions_from(result: AgentRunResult | ParsedInput) -> list[QuestionDraft]:
    """Small helper so services do not repeat the attribute dance."""
    return list(result.questions)


__all__ = [
    "AgentRunRequest",
    "BlueprintPort",
    "ExecutionPort",
    "IntelligencePort",
    "ProviderUnavailableError",
    "questions_from",
]
