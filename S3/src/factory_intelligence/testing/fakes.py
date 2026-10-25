"""``factory_intelligence.testing.fakes`` — S3's test doubles (AI-API-001, 00 doc 6.1).

S1 must be able to develop against S3's interface without S3's dependencies: a database, a
registered model, or a network.  :class:`FakeIntelligenceApi` implements every name in
``factory_intelligence.api`` in memory and returns S3's *real* DTOs, so a field-name or type
error in S1's coercion fails in S1's own test suite rather than at a customer's runtime.

Three properties are deliberate, each because a laxer Fake would let a caller pass against a
behaviour S3 does not have:

* **Idempotency is simulated.**  A repeated start key answers with the run it already
  recorded and calls nothing (AI-AC-05), because that is the contract S1 relies on when a
  Temporal activity is retried.
* **Signatures are S3's, not convenient ones.**  A keyword the real function does not
  accept is rejected here too, so drift shows up as a ``TypeError`` in the caller's tests.
* **Gated capabilities raise.**  ``desensitize`` raises
  :class:`~factory_intelligence.errors.FeatureNotEnabled` exactly as ``api.py`` does — a Fake
  that silently succeeded would let a caller build on M4 work this build does not ship.
  ``stage_chat``, ``clear_chat`` and ``set_budget`` are *not* gated: they are in
  :data:`factory_intelligence.feature.DELIVERED_CAPABILITIES`, so the Fake answers them for
  real, in memory, with the same DTO the live path returns.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from factory_foundation.api import NotFoundError, ValidationError

from factory_intelligence.agents.spec import AGENT_SPECS
from factory_intelligence.cost.budget import classify
from factory_intelligence.dto.common import Answer, Question, RejectedArtifact
from factory_intelligence.dto.governance import (
    ActivationView,
    AgentCatalog,
    AgentCatalogEntry,
    ColumnMappingProposal,
    EffectivePolicyView,
    EnableResult,
    EvaluationDetail,
    EvaluationList,
    EvaluationSummary,
    LlmExchangeDetail,
    LlmExchangePage,
    LlmExchangeSummary,
    ModelList,
    ModelPatch,
    ModelSpec,
    ModelView,
    NrrEntryView,
    NrrFilters,
    NrrQueryResult,
    OrgPolicyView,
    Page,
    ParseRecord,
    ParseRecordList,
    PolicyMatrixView,
    PromptDetailView,
    PromptList,
    PromptReloadResult,
    PromptView,
    ProviderHealthView,
    ProviderList,
    ProviderPatch,
    ProviderSpec,
    ProviderView,
    RegressionView,
    RelaxationView,
    wire,
)
from factory_intelligence.dto.request import AgentRunRequest, ContextItemRef
from factory_intelligence.dto.result import (
    AgentRunResult,
    BudgetView,
    ChatReply,
    EvalResult,
    ParsedInput,
    PolicyOutcome,
    UsageRollup,
)
from factory_intelligence.enums import (
    CURRENT_MILESTONE,
    POLICY_MATRIX,
    SYSTEM_DEFAULT_MATRIX,
    BudgetState,
    ParseStatus,
    PolicyResult,
    ProviderClass,
    RunState,
    as_provider_class,
    milestone_at_least,
)
from factory_intelligence.errors import BudgetInvalid, FeatureNotEnabled
from factory_intelligence.prompts.registry import split_qualified_prompt
from factory_intelligence.runtime.start import coerce_agent_run_request

#: capabilities this build gates; the Fake refuses them the way ``api.py`` does
GATED_CAPABILITIES = ("desensitize",)

#: AI-POL-002's default, which is what an unscripted organisation policy answers with.
_DEFAULT_MATRIX: dict[str, Any] = {
    "allowed": {
        level.value: sorted(member.value for member in classes)
        for level, classes in SYSTEM_DEFAULT_MATRIX.items()
    },
    "sources": ["org"],
    "warnings": [],
}

#: The ceiling the same unscripted policy reports next to it, so a caller that subtracts one
#: from the other gets the choices a relaxation may actually make.
_CEILING_MATRIX: dict[str, Any] = {
    "allowed": {
        level.value: sorted(member.value for member in classes)
        for level, classes in POLICY_MATRIX.items()
    },
    "sources": ["ceiling"],
    "warnings": [],
}


@dataclass
class FakeIntelligenceApi:
    """An in-memory S3, so a caller can develop without S3's dependencies."""

    #: results served in order by ``start_agent_run``; each is stamped with a run id
    run_results: list[AgentRunResult] = field(default_factory=list)
    #: completed results by start idempotency key — the replay table (AI-AC-05)
    runs_by_key: dict[str, AgentRunResult] = field(default_factory=dict)
    #: parse results by idempotency key
    parse_results: dict[str, ParsedInput] = field(default_factory=dict)
    #: policy verdicts by the joined refs of the request
    policy_verdicts: dict[str, PolicyOutcome] = field(default_factory=dict)
    #: evaluation results by ``agent/prompt/model``
    eval_results: dict[str, EvalResult] = field(default_factory=dict)
    #: what to raise, per function name; armed before any other behaviour
    raises: dict[str, Exception | type[Exception]] = field(default_factory=dict)
    #: ``explain_issues`` answers with ``None`` (the report held nothing) when true
    explain_nothing: bool = False
    #: providers scripted to answer unhealthy, by name → the reason a probe reports
    unhealthy_providers: dict[str, str] = field(default_factory=dict)
    #: parse transcripts per ``(project, artifact)``, oldest first
    llm_exchanges: dict[tuple[str, str], list[LlmExchangeSummary]] = field(default_factory=dict)
    #: full transcript rows by exchange id
    llm_exchange_details: dict[str, LlmExchangeDetail] = field(default_factory=dict)
    #: every call, in order, as ``"function:detail"``
    calls: list[str] = field(default_factory=list)
    #: governance payloads, in order
    governance_log: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    #: projects passed to ``delete_project_data``
    deleted_projects: list[str] = field(default_factory=list)
    #: budget rows reported by ``usage_rollup``
    usage: list[UsageRollup] = field(default_factory=list)
    #: activation rows ``list_activations`` reports; ``governance("prompt.activate")`` appends
    activations: list[dict[str, Any]] = field(default_factory=list)
    # --- scripted governance state (AI-API-001 covers the typed surface too) -----
    providers: list[ProviderView] = field(default_factory=list)
    models: list[ModelView] = field(default_factory=list)
    prompts: list[PromptView] = field(default_factory=list)
    regressions: list[RegressionView] = field(default_factory=list)
    org_policy: OrgPolicyView | None = None
    relaxations: list[RelaxationView] = field(default_factory=list)
    effective_policy: EffectivePolicyView | None = None
    evaluations: list[EvaluationSummary] = field(default_factory=list)
    budgets: dict[str, BudgetView] = field(default_factory=dict)
    #: scripted ``stage_chat`` answers, keyed ``"<projectId>/<stage>"``
    chat_replies: dict[str, ChatReply] = field(default_factory=dict)
    #: every turn the Fake answered, as ``(project, stage, message)``
    chat_turns: list[tuple[str, str, str]] = field(default_factory=list)
    #: stages whose session was cleared, as ``(project, stage)``
    chat_cleared: list[tuple[str, str]] = field(default_factory=list)
    agents: AgentCatalog | None = None
    parses: list[ParseRecord] = field(default_factory=list)
    mapping_proposals: dict[str, ColumnMappingProposal] = field(default_factory=dict)
    nrr_entries: list[NrrEntryView] = field(default_factory=list)
    _counter: int = 0

    # --- scripting -------------------------------------------------------------

    def queue_run(self, *results: AgentRunResult) -> None:
        """Serve these results, in order, for subsequent starts."""
        self.run_results.extend(results)

    def queue_waiting(self, *questions: Question) -> AgentRunResult:
        """Convenience: the next start comes back WAITING_INPUT with these questions."""
        result = AgentRunResult(status=RunState.WAITING_INPUT, questions=list(questions))
        self.run_results.append(result)
        return result

    def queue_policy_denied(self, *refs: str) -> AgentRunResult:
        """Convenience: the next start is refused, listing ``refs`` as rejected."""
        result = AgentRunResult(
            status=RunState.POLICY_DENIED,
            rejected_artifacts=[
                RejectedArtifact(ref=ref, reason="denied by the fake") for ref in refs
            ],
            denial_reasons=["fake: every candidate model is refused"],
        )
        self.run_results.append(result)
        return result

    def queue_failed(self, *reasons: str) -> AgentRunResult:
        """Convenience: the next start fails, reporting ``reasons``."""
        result = AgentRunResult(status=RunState.FAILED, denial_reasons=list(reasons))
        self.run_results.append(result)
        return result

    def reset(self) -> None:
        """Forget everything scripted and recorded; used between tests."""
        for item in (
            self.run_results,
            self.runs_by_key,
            self.parse_results,
            self.policy_verdicts,
            self.eval_results,
            self.raises,
            self.calls,
            self.governance_log,
            self.deleted_projects,
            self.usage,
            self.activations,
            self.providers,
            self.models,
            self.prompts,
            self.regressions,
            self.relaxations,
            self.evaluations,
            self.parses,
            self.nrr_entries,
        ):
            item.clear()
        self.budgets.clear()
        self.chat_replies.clear()
        self.chat_turns.clear()
        self.chat_cleared.clear()
        self.mapping_proposals.clear()
        self.org_policy = None
        self.effective_policy = None
        self.agents = None
        self.explain_nothing = False
        self.unhealthy_providers.clear()
        self._counter = 0

    # --- 7.1 input parsing -----------------------------------------------------

    def parse_input(
        self,
        project_id: str,
        input_artifact_id: str,
        *,
        options: Mapping[str, Any] | None = None,
        operator: str,
        idempotency_key: str,
        progress: Any = None,
        cancel: Any = None,
    ) -> ParsedInput:
        self._note("parse_input", idempotency_key)
        self._raise_if_armed("parse_input")
        recorded = self.parse_results.get(idempotency_key)
        if recorded is not None:
            return recorded
        result = ParsedInput(
            status=ParseStatus.PARSED,
            nrr_artifact_id=self._id("art-fake-nrr"),
            parser_version="fake-1",
            parser_type="CSV",
            parse_id=self._id("aipr-fake"),
        )
        self.parse_results[idempotency_key] = result
        return result

    # --- 7.2 agent runtime -----------------------------------------------------

    def start_agent_run(
        self,
        request: AgentRunRequest | Mapping[str, Any],
        *,
        progress: Any = None,
        cancel: Any = None,
    ) -> AgentRunResult:
        request = self._request(request)
        self._note("start_agent_run", request.idempotency_key)
        self._raise_if_armed("start_agent_run")
        replayed = self.runs_by_key.get(request.idempotency_key)
        if replayed is not None:
            return replayed
        result = self.run_results.pop(0) if self.run_results else AgentRunResult(
            summary="fake: nothing scripted", status=RunState.COMPLETED
        )
        stamped = result.model_copy(update={"run_id": result.run_id or self._id("arun-fake")})
        self.runs_by_key[request.idempotency_key] = stamped
        return stamped

    def resume_agent_run(
        self,
        run_id: str,
        answers: Sequence[Answer | Mapping[str, Any]],
        idempotency_key: str,
        *,
        language: str | None = None,
        progress: Any = None,
        cancel: Any = None,
    ) -> AgentRunResult:
        self._note("resume_agent_run", run_id)
        self._raise_if_armed("resume_agent_run")
        recorded = self.runs_by_key.get(idempotency_key)
        if recorded is not None:
            return recorded
        result = self.run_results.pop(0) if self.run_results else AgentRunResult(
            status=RunState.COMPLETED, summary="fake: resumed"
        )
        answered = [self._answer(answer) for answer in answers]
        stamped = result.model_copy(
            update={
                "run_id": run_id,
                "questions": [q for q in result.questions if not self._answered(q, answered)],
            }
        )
        self.runs_by_key[idempotency_key] = stamped
        return stamped

    def cancel_agent_run(self, run_id: str, *, reason: str | None = None) -> None:
        self._note("cancel_agent_run", run_id)
        self._raise_if_armed("cancel_agent_run")
        for key, result in list(self.runs_by_key.items()):
            if result.run_id == run_id:
                self.runs_by_key[key] = result.model_copy(
                    update={"status": RunState.CANCELLED}
                )

    def get_agent_run(self, run_id: str) -> AgentRunResult | None:
        self._note("get_agent_run", run_id)
        for result in self.runs_by_key.values():
            if result.run_id == run_id:
                return result
        return None

    def explain_issues(
        self,
        project_id: str,
        report_artifact_id: str,
        *,
        environment_policies: Mapping[str, Any],
        operator: str,
        language: str | None = None,
        idempotency_key: str | None = None,
        progress: Any = None,
        cancel: Any = None,
    ) -> str | None:
        self._note("explain_issues", report_artifact_id)
        self._raise_if_armed("explain_issues")
        if self.explain_nothing:
            return None
        return self._id("art-fake-explanation")

    def governance(self, action: str, payload: Mapping[str, Any]) -> Any:
        self._note("governance", action)
        self.governance_log.append((action, dict(payload or {})))
        self._raise_if_armed("governance")
        if action == "prompt.activate":
            body = dict(payload or {})
            record = self.activate_prompt(
                str(body.get("agentType") or body.get("agent_type") or ""),
                str(
                    body.get("promptName")
                    or body.get("prompt_name")
                    or body.get("name")
                    or ""
                ),
                int(
                    body.get("promptVersion")
                    or body.get("prompt_version")
                    or body.get("version")
                    or 0
                ),
                [
                    str(item)
                    for item in (body.get("modelList") or body.get("model_list") or [])
                ],
                str(body.get("operator") or "system"),
            )
            # …and the wire answer is the row in the spelling ``governance/dispatcher.py``
            # uses, so a caller that activates by action and lists by function sees one shape
            return wire(record)
        if action == "agent.list":
            return wire(self.list_agents())
        if action.endswith(".list"):
            # the envelope is S3's declared model, not a dict assembled here: a caller that
            # reads the Fake must see the keys the real façade declares (AI-GOV-009)
            envelopes: dict[str, Callable[[], dict[str, Any]]] = {
                "provider.list": lambda: wire(ProviderList(items=self.list_providers())),
                "model.list": lambda: wire(ModelList(items=self.list_models())),
                "prompt.list": lambda: wire(PromptList(items=self.list_prompts())),
                "eval.list": lambda: wire(
                    EvaluationList(
                        items=self.list_evaluations(
                            str(dict(payload or {}).get("agentType") or "") or None
                        )
                    )
                ),
            }
            listed = envelopes.get(action)
            if listed is not None:
                return listed()
            return {"items": [], "milestone": CURRENT_MILESTONE.value}
        if action == "prompt.source.reload":
            return wire(self.reload_prompt_sources(dict(payload or {}).get("root")))
        if action == "policy.effective.get":
            body = dict(payload or {})
            return wire(
                self.get_effective_policy(
                    body.get("environmentPolicies") or body.get("environment_policies") or {}
                )
            )
        if action == "input.parseList":
            body = dict(payload or {})
            return wire(
                ParseRecordList(
                    items=self.list_input_parses(
                        str(body.get("projectId") or body.get("project_id") or ""),
                        body.get("inputArtifactIds") or body.get("input_artifact_ids"),
                    )
                )
            )
        if action == "input.columnMapping":
            body = dict(payload or {})
            return wire(
                self.get_column_mapping_proposal(
                    str(body.get("projectId") or body.get("project_id") or ""),
                    str(body.get("inputArtifactId") or body.get("input_artifact_id") or ""),
                )
            )
        if action == "nrr.query":
            body = dict(payload or {})
            return wire(
                self.query_nrr(
                    str(body.get("projectId") or body.get("project_id") or ""),
                    body.get("filters") or {},
                    body.get("page") or {},
                )
            )
        return {"action": action, "accepted": True}

    def delete_project_data(self, project_id: str, *, session: Any = None) -> None:
        self._note("delete_project_data", project_id)
        self._raise_if_armed("delete_project_data")
        self.deleted_projects.append(project_id)
        self.runs_by_key.clear()
        self.parse_results.clear()

    # --- 7.3 policy, evaluation, cost ------------------------------------------

    def evaluate_policy(
        self,
        project_id: str,
        item_refs: Sequence[ContextItemRef | str],
        provider_class: ProviderClass | str,
        environment_policies: Mapping[str, Any] | None = None,
        *,
        session: Any = None,
    ) -> PolicyOutcome:
        key = "|".join(str(getattr(ref, "ref", ref)) for ref in item_refs)
        self._note("evaluate_policy", key)
        scripted = self.policy_verdicts.get(key)
        if scripted is not None:
            return scripted
        return PolicyOutcome(result=PolicyResult.ALLOWED, provider_class=_class(provider_class))

    def run_evaluation(
        self,
        agent_type: Any,
        prompt_version: str,
        model: str,
        eval_set_version: str | None = None,
        *,
        idempotency_key: str,
        operator: str = "system",
        progress: Any = None,
        cancel: Any = None,
    ) -> EvalResult:
        agent = str(getattr(agent_type, "value", agent_type))
        self._note("run_evaluation", f"{agent}/{prompt_version}")
        self._raise_if_armed("run_evaluation")
        scripted = self.eval_results.get(f"{agent}/{prompt_version}/{model}")
        if scripted is not None:
            return scripted
        name, version = split_qualified_prompt(prompt_version)
        return EvalResult(
            eval_run_id=self._id("aeval-fake"),
            agent_type=agent,
            prompt_name=name,
            prompt_version=version,
            model=model,
            eval_set_version=eval_set_version or "1",
            score=1.0,
            passed=True,
            injection_cases=1,
            injection_passed=1,
        )

    def usage_rollup(
        self,
        project_id: str | None = None,
        *,
        group_by: Sequence[str] = ("stage", "agent", "model"),
        session: Any = None,
    ) -> list[UsageRollup]:
        self._note("usage_rollup", project_id or "*")
        if project_id is None:
            return list(self.usage)
        return [row for row in self.usage if row.project_id == project_id]

    # --- 7.4 governance, one typed function per capability (AI-GOV-001) ---------
    #
    # The Fake holds these in memory and answers in the same models ``api`` returns, so a
    # caller can develop the whole operator surface — register a provider, activate a prompt,
    # read an evaluation — with no database and no model behind it (AI-API-001).

    def list_providers(self, *, session: Any = None) -> list[ProviderView]:
        self._note("list_providers", "")
        self._raise_if_armed("list_providers")
        return list(self.providers)

    def register_provider(
        self,
        spec: ProviderSpec | Mapping[str, Any],
        operator: str = "system",
        *,
        session: Any = None,
    ) -> ProviderView:
        model = spec if isinstance(spec, ProviderSpec) else ProviderSpec.model_validate(dict(spec))
        self._note("register_provider", model.name)
        self._raise_if_armed("register_provider")
        row = ProviderView(
            id=self._id("apvd-fake"),
            name=model.name,
            provider_class=_class(model.provider_class),
            endpoint=model.endpoint,
            enabled=model.enabled,
            adapter=model.adapter,
            api_key_secret_ref=model.api_key_secret_ref,
            vendor=model.vendor,
            suggested_models=list(model.suggested_models),
        )
        self.providers = [*[p for p in self.providers if p.name != row.name], row]
        return row

    def update_provider(
        self,
        name: str,
        patch: ProviderPatch | Mapping[str, Any],
        operator: str = "system",
        *,
        session: Any = None,
    ) -> ProviderView:
        model = patch if isinstance(patch, ProviderPatch) else ProviderPatch.model_validate(dict(patch))
        self._note("update_provider", name)
        self._raise_if_armed("update_provider")
        supplied = model.model_dump(exclude_none=True)
        for row in self.providers:
            if row.name == name:
                for key, value in supplied.items():
                    setattr(row, key, _class(value) if key == "provider_class" else value)
                return row
        raise NotFoundError(
            f"provider {name!r} is not registered", details={"provider": name}
        )

    def set_provider_enabled(
        self,
        name: str,
        enabled: bool,
        operator: str = "system",
        *,
        session: Any = None,
    ) -> EnableResult:
        self._note("set_provider_enabled", f"{name}={enabled}")
        for row in self.providers:
            if row.name == name:
                row.enabled = bool(enabled)
        return EnableResult(ref=name, enabled=bool(enabled))

    def check_provider_health(self, name: str, *, session: Any = None) -> ProviderHealthView:
        self._note("check_provider_health", name)
        self._raise_if_armed("check_provider_health")
        if not any(row.name == name for row in self.providers):
            raise NotFoundError(f"provider {name!r} is not registered", details={"name": name})
        detail = self.unhealthy_providers.get(name)
        return ProviderHealthView(ref=name, healthy=detail is None, detail=detail)

    def list_models(
        self,
        agent_type: str | None = None,
        *,
        include_disabled: bool = True,
        session: Any = None,
    ) -> list[ModelView]:
        self._note("list_models", agent_type or "*")
        out = [
            row
            for row in self.models
            if (include_disabled or row.enabled)
            and (agent_type is None or agent_type in row.applicable_agents)
        ]
        return out

    def register_model(
        self,
        spec: ModelSpec | Mapping[str, Any],
        operator: str = "system",
        *,
        session: Any = None,
    ) -> ModelView:
        model = spec if isinstance(spec, ModelSpec) else ModelSpec.model_validate(dict(spec))
        self._note("register_model", f"{model.provider}/{model.model}")
        self._raise_if_armed("register_model")
        row = ModelView(
            id=self._id("amdl-fake"),
            provider=model.provider,
            model=model.model,
            ref=f"{model.provider}/{model.model}",
            provider_class=_class(
                next(
                    (
                        p.provider_class
                        for p in self.providers
                        if p.name == model.provider
                    ),
                    ProviderClass.LOCAL_ONLY,
                )
            ),
            capabilities=model.capabilities,
            enabled=model.enabled,
            unit_price_input_per_1k=model.unit_price_input_per_1k,
            unit_price_output_per_1k=model.unit_price_output_per_1k,
            applicable_agents=dict(model.applicable_agents),
            endpoint=model.endpoint,
            api_key_secret_ref=model.api_key_secret_ref,
        )
        self.models = [*[m for m in self.models if m.ref != row.ref], row]
        return row

    def update_model(
        self,
        ref: str,
        patch: ModelPatch | Mapping[str, Any],
        operator: str = "system",
        *,
        session: Any = None,
    ) -> ModelView:
        """Correct one model; a key the patch omits keeps its stored value (S5IF-GOV06).

        The two nullable overrides are the reason this exists: a fake that rebuilt the row from
        the patch alone would clear them, which is precisely the bug the real edit prevents.
        """
        model = patch if isinstance(patch, ModelPatch) else ModelPatch.model_validate(dict(patch))
        self._note("update_model", ref)
        self._raise_if_armed("update_model")
        for index, row in enumerate(self.models):
            if row.ref != ref:
                continue
            supplied = model.model_dump(exclude_none=True)
            merged = row.model_dump()
            capabilities = supplied.pop("capabilities", None)
            merged.update(supplied)
            if capabilities is not None:
                # named keys only, or raising the context window would also switch vision off
                merged["capabilities"] = {
                    **row.capabilities.model_dump(),
                    **{key: value for key, value in capabilities.items() if value is not None},
                }
            updated = ModelView.model_validate(merged)
            self.models = [*self.models[:index], updated, *self.models[index + 1:]]
            return updated
        raise NotFoundError(f"model {ref!r} is not registered", details={"ref": ref})

    def set_model_enabled(
        self,
        ref: str,
        enabled: bool,
        operator: str = "system",
        *,
        session: Any = None,
    ) -> EnableResult:
        self._note("set_model_enabled", f"{ref}={enabled}")
        for row in self.models:
            if row.ref == ref:
                row.enabled = bool(enabled)
        return EnableResult(ref=ref, enabled=bool(enabled))

    def list_prompts(
        self,
        agent_type: str | None = None,
        *,
        session: Any = None,
    ) -> list[PromptView]:
        self._note("list_prompts", agent_type or "*")
        active = {str(row.get("promptVersionId")) for row in self.activations}
        return [
            PromptView(
                **{
                    **row.model_dump(),
                    "active": row.qualified_name in active or row.id in active,
                }
            )
            for row in self.prompts
            if agent_type is None or row.agent_type == agent_type
        ]

    def get_prompt(self, name: str, version: int, *, session: Any = None) -> PromptDetailView:
        self._note("get_prompt", f"{name}.v{version}")
        for row in self.prompts:
            if row.name == name and row.version == int(version):
                return PromptDetailView(
                    **{key: value for key, value in row.model_dump().items()},
                    body=f"fake body of {name}.v{version}",
                    system_rules="fake rules",
                    applicable_stages=[],
                )
        return PromptDetailView(
            id=f"{name}.v{version}",
            name=name,
            version=int(version),
            qualified_name=f"{name}.v{version}",
            body=f"fake body of {name}.v{version}",
        )

    def list_activations(self, *, session: Any = None) -> list[ActivationView]:
        self._note("list_activations", "")
        return [ActivationView.model_validate(row) for row in self.activations]

    def activate_prompt(
        self,
        agent_type: str,
        prompt_name: str,
        prompt_version: int,
        model_list: Sequence[str],
        operator: str = "system",
        *,
        session: Any = None,
    ) -> ActivationView:
        record = {
            "agentType": agent_type,
            "promptVersionId": f"{prompt_name}.v{prompt_version}",
            "name": prompt_name,
            "version": int(prompt_version),
            "modelList": [str(item) for item in model_list],
            "evalRunId": self._id("aeval-fake"),
            "regressionPassedRef": self._id("areg-fake"),
            "activatedBy": operator,
        }
        self.activations = [
            *[a for a in self.activations if a.get("agentType") != agent_type],
            record,
        ]
        self._note("activate_prompt", f"{agent_type}={prompt_name}.v{prompt_version}")
        return ActivationView.model_validate(record)

    def record_regression(
        self,
        prompt_name: str,
        prompt_version: int,
        passed: bool = True,
        model: str | None = None,
        source: str = "golden-project",
        note: str | None = None,
        operator: str = "test-group",
        *,
        session: Any = None,
    ) -> RegressionView:
        self._note("record_regression", f"{prompt_name}.v{prompt_version}={passed}")
        row = RegressionView(
            id=self._id("argr-fake"),
            passed=bool(passed),
            prompt_version_id=f"{prompt_name}.v{prompt_version}",
        )
        self.regressions.append(row)
        return row

    def list_regressions(
        self,
        prompt_name: str | None = None,
        prompt_version: int | None = None,
        *,
        session: Any = None,
    ) -> list[RegressionView]:
        self._note("list_regressions", f"{prompt_name}.v{prompt_version}")
        wanted = None
        if prompt_name and prompt_version:
            wanted = f"{prompt_name}.v{prompt_version}"
        return [
            row
            for row in self.regressions
            if wanted is None or row.prompt_version_id == wanted
        ]

    def reload_prompt_sources(
        self, root: str | None = None, *, session: Any = None
    ) -> PromptReloadResult:
        self._note("reload_prompt_sources", root or "<packaged>")
        names = [f"{row.name}.v{row.version}" for row in self.prompts]
        return PromptReloadResult(registered=len(names), names=names)

    def get_org_policy(self, *, session: Any = None) -> OrgPolicyView:
        self._note("get_org_policy", "")
        if self.org_policy is not None:
            return self.org_policy
        return OrgPolicyView(
            effective=PolicyMatrixView(**_DEFAULT_MATRIX),
            ceiling=PolicyMatrixView(**_CEILING_MATRIX),
        )

    def relax_org_policy(
        self,
        change: Mapping[str, Any],
        reason: str,
        operator: str,
        scope: str = "organization",
        *,
        session: Any = None,
    ) -> RelaxationView:
        self._note("relax_org_policy", f"{scope}:{reason}")
        if not str(reason).strip():
            raise ValidationError(
                "a relaxation must say why (AI-POL-002)",
                code="VALIDATION_ERROR",
                details={"missing": "reason"},
            )
        row = RelaxationView(
            id=self._id("aplax-fake"),
            scope=scope,
            operator=operator,
            reason=str(reason),
            change=dict(change),
        )
        self.relaxations.append(row)
        return row

    def get_effective_policy(
        self,
        environment_policies: Mapping[str, Any] | None = None,
        *,
        session: Any = None,
    ) -> EffectivePolicyView:
        """The scripted answer, or the org policy unchanged.

        A caller that wants D-07's strictest-of behaviour from the Fake scripts
        :attr:`effective_policy`; combining real environment documents is the policy
        engine's job and is exercised against the database, not here.
        """
        self._note("get_effective_policy", str(sorted(environment_policies or {})))
        if self.effective_policy is not None:
            return self.effective_policy
        base = self.get_org_policy()
        return EffectivePolicyView(**base.effective.model_dump())

    def list_evaluations(
        self,
        agent_type: str | None = None,
        *,
        limit: int = 50,
        session: Any = None,
    ) -> list[EvaluationSummary]:
        self._note("list_evaluations", agent_type or "*")
        rows = [
            row for row in self.evaluations if agent_type is None or row.agent_type == agent_type
        ]
        return rows[: int(limit)]

    def get_evaluation(self, eval_run_id: str, *, session: Any = None) -> EvaluationDetail:
        self._note("get_evaluation", eval_run_id)
        for row in self.evaluations:
            if row.id == eval_run_id:
                return EvaluationDetail.model_validate(row.model_dump())
        raise NotFoundError(f"evaluation run {eval_run_id} not found")

    def get_budget(self, project_id: str, *, session: Any = None) -> BudgetView:
        self._note("get_budget", project_id)
        return self.budgets.get(project_id, BudgetView())

    def list_agents(self, *, session: Any = None) -> AgentCatalog:
        self._note("list_agents", "")
        if self.agents is not None:
            return self.agents
        return AgentCatalog(
            milestone=CURRENT_MILESTONE,
            items=[
                AgentCatalogEntry(
                    agent_type=spec.agent_type,
                    stage=spec.stage.value,
                    milestone=spec.milestone.value,
                    enabled=milestone_at_least(spec.milestone),
                    prompt_name=spec.prompt_name,
                    per_page=spec.per_page,
                    patch_mode=spec.patch_mode.value,
                )
                for spec in AGENT_SPECS.values()
            ],
        )

    # --- 7.5 parse records, column mapping and NRR reads (AI-INP-013/014/015) ----

    def list_input_parses(
        self,
        project_id: str,
        input_artifact_ids: Sequence[str] | None = None,
        *,
        session: Any = None,
    ) -> list[ParseRecord]:
        self._note("list_input_parses", project_id)
        wanted = {str(item) for item in (input_artifact_ids or [])}
        latest: dict[str, ParseRecord] = {}
        for row in self.parses:
            if row.project_id != project_id or (wanted and row.input_artifact_id not in wanted):
                continue
            current = latest.get(row.input_artifact_id)
            # the newest wins, which is what "most recent parse of each input" means; an
            # untimestamped row sorts first, so scripted records keep their order
            if current is None or (row.created_at is not None and (
                current.created_at is None or row.created_at >= current.created_at
            )):
                latest[row.input_artifact_id] = row
        return list(latest.values())

    def get_column_mapping_proposal(
        self,
        project_id: str,
        input_artifact_id: str,
        *,
        session: Any = None,
    ) -> ColumnMappingProposal:
        self._note("get_column_mapping_proposal", input_artifact_id)
        scripted = self.mapping_proposals.get(input_artifact_id)
        if scripted is not None:
            return scripted
        return ColumnMappingProposal(
            input_artifact_id=input_artifact_id, project_id=project_id
        )

    def query_nrr(
        self,
        project_id: str,
        filters: NrrFilters | Mapping[str, Any] | None = None,
        page: Page | Mapping[str, Any] | None = None,
        *,
        session: Any = None,
    ) -> NrrQueryResult:
        chosen = (
            filters if isinstance(filters, NrrFilters) else NrrFilters.model_validate(dict(filters or {}))
        )
        window = page if isinstance(page, Page) else Page.model_validate(dict(page or {}))
        self._note("query_nrr", f"{project_id}:{chosen.categories}")
        rows = [
            row
            for row in self.nrr_entries
            if (not chosen.categories or row.category in chosen.categories)
            and (
                not chosen.input_artifact_ids
                or row.artifact_id in set(chosen.input_artifact_ids)
            )
            and (chosen.min_confidence is None or row.confidence >= chosen.min_confidence)
            and (chosen.max_confidence is None or row.confidence <= chosen.max_confidence)
            and (
                chosen.confirmed_only is None
                or row.confirmed is chosen.confirmed_only
            )
        ]
        return NrrQueryResult(
            items=rows[window.offset : window.offset + window.limit],
            total=len(rows),
            limit=window.limit,
            offset=window.offset,
        )

    def list_llm_exchanges(
        self,
        project_id: str,
        input_artifact_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        session: Any = None,
    ) -> LlmExchangePage:
        self._note("list_llm_exchanges", input_artifact_id)
        rows = self.llm_exchanges.get((project_id, input_artifact_id), [])
        return LlmExchangePage(
            items=rows[offset : offset + limit],
            total=len(rows),
            limit=limit,
            offset=offset,
        )

    def get_llm_exchange(
        self,
        project_id: str,
        input_artifact_id: str,
        exchange_id: str,
        *,
        session: Any = None,
    ) -> LlmExchangeDetail | None:
        self._note("get_llm_exchange", exchange_id)
        detail = self.llm_exchange_details.get(exchange_id)
        if detail is None or detail.input_artifact_id != input_artifact_id:
            return None
        return detail

    def purge_llm_exchanges(self, *, session: Any = None) -> int:
        self._note("purge_llm_exchanges", "")
        return 0

    # --- M3/M4 surface ---------------------------------------------------------

    def stage_chat(
        self,
        project_id: str,
        stage: str,
        draft_id: str | None,
        message: str,
        environment_policies: Mapping[str, Any],
        operator: str,
        *,
        language: str | None = None,
        idempotency_key: str,
        progress: Any = None,
        cancel: Any = None,
    ) -> ChatReply:
        """AI-CHAT-001: an answer in memory, with the turn recorded for assertions.

        The scripted reply is returned *once*: a second turn on the same stage answers from
        the default again, so a test cannot mistake a stale script for continuing history.
        """
        self._note("stage_chat", f"{project_id}/{stage}")
        self.chat_turns.append((project_id, stage, message))
        self._raise_if_armed("stage_chat")
        key = f"{project_id}/{stage}"
        scripted = self.chat_replies.pop(key, None)
        if scripted is not None:
            return scripted
        return ChatReply(answer=f"fake: {message[:200]}")

    def clear_chat(self, project_id: str, stage: str, *, session: Any = None) -> None:
        """AI-CHAT-003: the session is emptied, recorded so a test can assert it."""
        self._note("clear_chat", f"{project_id}/{stage}")
        self.chat_cleared.append((project_id, stage))
        self.chat_replies.pop(f"{project_id}/{stage}", None)
        self.chat_turns = [turn for turn in self.chat_turns if turn[:2] != (project_id, stage)]

    def get_chat(self, project_id: str, stage: str) -> Any:
        """AI-CHAT-003's read side: the turns the Fake answered, in order."""
        self._note("get_chat", f"{project_id}/{stage}")
        turns: list[dict[str, Any]] = []
        for _project, _stage, message in self.chat_turns:
            if (_project, _stage) != (project_id, stage):
                continue
            turns.append({"role": "user", "content": message})
            reply = self.chat_replies.get(f"{project_id}/{stage}")
            turns.append({"role": "assistant", "content": (reply.answer if reply else f"fake: {message[:200]}")})
        return turns

    def desensitize(
        self,
        project_id: str,
        artifact_id: str,
        rules: Mapping[str, Any],
        *,
        operator: str,
        idempotency_key: str,
    ) -> Any:
        raise self._gated("desensitize", "M4", "desensitisation (AI-POL-009)")

    def set_budget(
        self,
        project_id: str,
        limit: float | None,
        thresholds: Sequence[float],
        *,
        currency: str = "USD",
        operator: str,
        session: Any = None,
    ) -> BudgetView:
        """AI-COST-002 with the live path's shape checks, so a bad payload fails here too."""
        self._note("set_budget", f"{project_id}:{limit}")
        self._raise_if_armed("set_budget")
        steps = sorted({float(item) for item in thresholds})
        code = str(currency or "").strip().upper()
        if limit is not None and (not math.isfinite(limit) or limit < 0.0):
            raise BudgetInvalid(
                "a budget limit is a non-negative finite amount, or no limit at all",
                details={"limit": limit},
            )
        if any(not 0.0 < item < 1.0 for item in steps):
            raise BudgetInvalid(
                "budget thresholds are fractions of the limit, strictly between 0 and 1",
                details={"thresholds": steps},
            )
        if len(code) != 3 or not code.isalpha():
            raise BudgetInvalid(
                "budget currency is a three-letter ISO-4217 code", details={"currency": currency}
            )
        spent = self.budgets.get(project_id, BudgetView()).spent
        if limit is None:
            view = BudgetView(state=BudgetState.OK, spent=spent, limit=None, currency=code)
        else:
            state, reached = classify(spent, limit, steps)
            view = BudgetView(
                state=state, spent=spent, limit=limit, currency=code, threshold_reached=reached
            )
        self.budgets[project_id] = view
        return view

    # --- internals --------------------------------------------------------------

    def _request(self, request: AgentRunRequest | Mapping[str, Any]) -> AgentRunRequest:
        return coerce_agent_run_request(request)

    def _answer(self, answer: Answer | Mapping[str, Any]) -> Answer:
        return answer if isinstance(answer, Answer) else Answer.model_validate(answer)

    def _answered(self, question: Question, answers: Sequence[Answer]) -> bool:
        return any(answer.question_local_id == question.local_id for answer in answers)

    def _id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}-{self._counter:04d}"

    def _note(self, function: str, detail: str) -> None:
        self.calls.append(f"{function}:{detail}")

    def _raise_if_armed(self, function: str) -> None:
        error = self.raises.get(function)
        if error is None:
            return
        if isinstance(error, type) and issubclass(error, BaseException):
            raise error("armed by FakeIntelligenceApi")
        raise error

    @staticmethod
    def _gated(capability: str, milestone: str, what: str) -> FeatureNotEnabled:
        return FeatureNotEnabled(
            f"{what} is delivered in {milestone}, not in this build",
            details={"capability": capability, "milestone": milestone},
        )


def _class(value: ProviderClass | str) -> ProviderClass:
    return as_provider_class(value)


__all__ = ["GATED_CAPABILITIES", "FakeIntelligenceApi"]
