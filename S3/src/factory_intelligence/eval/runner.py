"""The evaluation runner — AI-EVAL-001, AI-EVAL-002, AI-EVAL-003, AI-REG-003.

One call measures one ``(agent, prompt version, model)`` triple against that agent's packaged
set and writes the record AI-REG-003 gates on.  Four properties are the point of this module
rather than incidental detail:

**The ladder is the runtime's.**  Every answer is judged by
:func:`factory_intelligence.output.validate.validate` with the agent's own
:class:`~factory_intelligence.agents.spec.AgentSpec` and a
:class:`~factory_intelligence.agents.checks.base.CheckContext` built from the case.  An
evaluation that used its own softer checks would score a prompt against a contract the runtime
never enforces, and a prompt could then pass evaluation and fail every real run.

**One call per case, no corrective retry.**  AI-RT-011 gives a *run* one retry after a
validation failure.  That retry belongs to the runtime and to its retry budget; crediting it
here would score the retry loop instead of the prompt.  An evaluation therefore measures what
the prompt produces first time — which is also what makes a model-call count mean something.

**Offline and deterministic.**  The provider comes from
:func:`factory_intelligence.deps.get_llm_provider`, so a deployment that installed the mock
agent or the record-replay adapter evaluates with no network at all (AI-MOCK-001, AI-LLM-006,
AI-EVAL-002).  Nothing else reaches outside the process: the case files are the input, S2's
section schemas are read from disk, and the writes are ``ai.eval_run``,
``ai.eval_case_result``, one ``ai.model_call`` row per case with its ``ai.model.called`` audit
(AI-NFR-03 books a call whoever made it, and these name no project because an evaluation
spends no project's budget), the model's recorded score (AI-REG-005) and the idempotency
record.

**A provider failure is not an answer.**  When the model call itself fails the error
propagates and no run row is written.  A transport outage says nothing about the prompt, and a
run row of ``score = 0`` would become the evidence that refuses — or, after a re-run that
happens to pass, that permits — a version switch.  The call is still booked (AI-NFR-03 counts
calls, not outcomes), the idempotency key stays unfinished, so the same call is simply attempted
again.

``EvalRunRow.model`` holds the bare model name and ``EvalRunRow.provider`` its provider, which
is the spelling ``prompts/activation.py`` compares against an agent's model list; the registry
reference is rebuilt from the two for AI-REG-005.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from factory_blueprint.api import Section
from factory_foundation.api import (
    CancellationToken,
    DataClassification,
    NotFoundError,
    ProgressReporter,
    ValidationError,
    external_io,
    idempotency,
    new_id,
    structure_hash,
    unit_of_work,
)

from factory_intelligence.agents.spec import AgentSpec, spec_for
from factory_intelligence.config import settings
from factory_intelligence.context.fence import fence, plain
from factory_intelligence.cost.recording import RecordingProvider
from factory_intelligence.cost.usage import CallContext
from factory_intelligence.deps import get_llm_provider
from factory_intelligence.dto.agent import AgentOutput
from factory_intelligence.dto.result import EvalCaseOutcome, EvalResult
from factory_intelligence.enums import AgentType, ItemSourceKind
from factory_intelligence.errors import EvalSetMissing
from factory_intelligence.eval.scoring import (
    EvalCase,
    aggregate,
    load_eval_set,
    score_case,
    set_version,
)
from factory_intelligence.llm.protocol import LLMProvider
from factory_intelligence.llm.registry import (
    RegisteredModel,
    list_models,
    record_eval_result,
    registered_model,
)
from factory_intelligence.llm.types import AgentHint, ChatMessage, StructuredRequest
from factory_intelligence.models.eval import EvalCaseResultRow, EvalRunRow
from factory_intelligence.models.registry import PromptVersionRow
from factory_intelligence.output.errors import ValidationReport
from factory_intelligence.output.schema import Container, envelope_schema, section_containers
from factory_intelligence.output.validate import validate
from factory_intelligence.prompts.activation import ActivePrompt
from factory_intelligence.prompts.registry import get_prompt
from factory_intelligence.prompts.render import system_message, task_message
from factory_intelligence.registries import IdemScope

#: Context kinds S3 authored, which the fence contract reserves for project content.  Anything
#: else in a case is project material and is fenced exactly as a run would fence it.
TRUSTED_KINDS: frozenset[ItemSourceKind] = frozenset(
    {
        ItemSourceKind.SYSTEM_RULES,
        ItemSourceKind.STAGE_RULES,
        ItemSourceKind.VALIDATION_ERRORS,
        ItemSourceKind.USER_ANSWER,
    }
)

#: What leaves the process during an evaluation is the packaged set, so that is the
#: classification its usage audit names (AI-POL-008) — no project content is ever in scope.
_CASE_CLASSIFICATION = DataClassification.INTERNAL.value


@external_io
def run_evaluation(
    agent_type: AgentType | str,
    prompt_name: str,
    prompt_version: int,
    model: str,
    *,
    idempotency_key: str,
    eval_set_version: str | None = None,
    operator: str = "system",
    progress: ProgressReporter | None = None,
    cancel: CancellationToken | None = None,
) -> EvalResult:
    """Score one prompt version against one agent's set on one model (AI-EVAL-001/002).

    ``model`` is a Model Registry reference ``provider/model``, or a bare model name when
    exactly one registered model of this agent carries it.  A repeated ``idempotency_key``
    returns the recorded measurement without calling the model again (00 doc 6.4).
    """
    agent = AgentType(agent_type)
    spec = spec_for(agent)
    prompt = get_prompt(prompt_name, prompt_version)
    if prompt.agent_type != agent.value:
        raise ValidationError(
            f"prompt {prompt_name}.v{prompt_version} is declared for {prompt.agent_type}, "
            f"not {agent.value}; its set would be judged against the wrong spec and checks",
            code="VALIDATION_ERROR",
            details={"declaredFor": prompt.agent_type, "agentType": agent.value},
        )
    cases = load_eval_set(agent)
    if not cases:
        raise EvalSetMissing(
            f"no evaluation set is packaged for {agent.value}; expected case files under "
            "factory_intelligence/eval/sets/" + agent.value.lower() + "/",
            details={"agentType": agent.value, "prompt": f"{prompt_name}.v{prompt_version}"},
        )
    resolved = _resolve_model(model, agent)
    version = eval_set_version or set_version(agent)
    run_id = new_id("aeval")
    # Every case is a real call on a real endpoint, so the evaluation's provider is the
    # recording wrapper: the rows it books name no project (AI-NFR-03 wants the call,
    # AI-COST-001 can only bill a project) and the totals below read the same figures back.
    provider = RecordingProvider(
        get_llm_provider(resolved.provider_info()),
        model=resolved,
        context=CallContext(
            project_id=None,
            workflow_id=f"eval:{run_id}",
            stage=None,
            agent_type=agent.value,
            run_id=None,
            call_role="evaluation",
        ),
        context_artifacts=({"ref": f"evalset:{version}", "classification": _CASE_CLASSIFICATION},),
        operator=operator,
    )
    envelope = envelope_schema()
    containers = patchable_containers(spec)
    head = _system_head(spec=spec, prompt=prompt, model_ref=resolved.ref)

    digest = structure_hash(
        {
            "agentType": agent.value,
            "prompt": f"{prompt_name}.v{prompt_version}",
            "promptSourceHash": prompt.source_hash,
            "model": resolved.ref,
            "evalSetVersion": version,
            "cases": [case.id for case in cases],
            "operator": operator,
        }
    )
    begun = idempotency.begin(IdemScope.EVALUATION_RUN, idempotency_key, digest)
    if begun.already_completed and begun.first_result is not None:
        return EvalResult.model_validate(begun.first_result)

    _report(progress, "construct case contexts", 0.05, {"cases": len(cases)})
    outcomes: list[EvalCaseOutcome] = []
    for index, case in enumerate(cases, start=1):
        _check_cancelled(cancel)
        _report(progress, f"model call for {case.id}", index / (len(cases) + 1))
        answer, report = _judge(
            case,
            spec=spec,
            prompt=prompt,
            system_head=head,
            provider=provider,
            envelope=envelope,
            containers=containers,
            model_ref=resolved.ref,
        )
        outcomes.append(score_case(case, answer, report))
        _report(progress, f"scored {case.id}", index / (len(cases) + 1))

    score, passed, summary = aggregate(outcomes)
    summary = {
        **summary,
        "modelCalls": provider.calls,
        "inputTokens": provider.usage.input_tokens,
        "outputTokens": provider.usage.output_tokens,
        "estimatedCost": round(
            resolved.price_for(provider.usage.input_tokens, provider.usage.output_tokens), 6
        ),
    }
    result = EvalResult(
        eval_run_id=run_id,
        agent_type=agent.value,
        prompt_name=prompt_name,
        prompt_version=prompt_version,
        model=resolved.model,
        eval_set_version=version,
        score=score,
        passed=passed,
        cases=outcomes,
        injection_cases=sum(1 for case in cases if case.is_injection),
        injection_passed=sum(
            1 for outcome in outcomes if outcome.kind == "injection" and outcome.passed
        ),
    )
    _persist(
        result,
        summary=summary,
        spec=spec,
        prompt=prompt,
        resolved=resolved,
        operator=operator,
        idempotency_key=idempotency_key,
    )
    _report(progress, "evaluation recorded", 1.0, {"score": score, "passed": passed})
    return result


def patchable_containers(spec: AgentSpec) -> dict[str, tuple[Container, ...]]:
    """The containers of every section the agent may write, read from S2 (AI-ASM-03).

    ``CheckContext.containers`` is what rung 1 validates a patch entry against, so the
    evaluation resolves it the way a run does — from ``factory_blueprint.api`` rather than from
    a list kept here, which is the only way it stays true after S2 changes a section.
    """
    return {
        (section.value if isinstance(section, Section) else str(section)): section_containers(section)
        for section in spec.sections
    }


def case_messages(
    case: EvalCase,
    *,
    prompt_body: str,
    system_head: str,
    model_ref: str,
) -> list[ChatMessage]:
    """The context one case sends: system head, task body, run parameters, then the content.

    ``_parameter_block`` is where the prompt's named context slots get their values, so the
    packaged prompt text stays constant and ``source_hash`` is stable across runs; the case's
    own fragments go through the runtime's fence helpers, so an injection case is measured
    against exactly the framing a real run would give it (AI-RT-030, AI-RT-031).
    """
    messages = [
        ChatMessage(role="system", content=system_head),
        task_message(prompt_body),
        plain(kind=ItemSourceKind.STAGE_RULES, text=_parameter_block(case, model_ref=model_ref)),
    ]
    for fragment in case.context.items:
        if fragment.kind in TRUSTED_KINDS:
            messages.append(plain(kind=fragment.kind, text=f"{fragment.label}\n{fragment.content}"))
            continue
        messages.append(
            fence(
                kind=fragment.kind,
                label=fragment.label,
                content=fragment.content,
                artifact_id=fragment.ref,
            )
        )
    return messages


def _parameter_block(case: EvalCase, *, model_ref: str) -> str:
    """The named slots a prompt refers to as "the threshold stated in the context"."""
    context = case.context
    stated = {
        "baseVersion": context.base_version,
        "decisionConfidenceThreshold": context.decision_confidence_threshold,
        "extractionConfidenceThreshold": context.extraction_confidence_threshold,
        "designReferenceEnvironment": context.design_reference_environment,
    }
    defaults = {
        "decisionConfidenceThreshold": settings().default_decision_confidence,
        "extractionConfidenceThreshold": settings().default_extraction_confidence,
    }
    lines = [
        "Run parameters for this evaluation. These are the values the task prompt names:",
        "```run-parameters",
        f"agentType: {case.agent_type}",
        f"stage: {case.stage}",
        f"model: {model_ref}",
    ]
    for name, value in stated.items():
        shown = value if value is not None else defaults.get(name, "unset")
        lines.append(f"{name}: {shown}")
    lines.append(f"mappingFrozen: {str(context.mapping_frozen).lower()}")
    lines.append("```")
    if context.similar_objects:
        lines.append(
            "similarObjects found in the design reference environment. Each one needs a REUSE "
            "or CREATE decision with a reason (AI-AG-011); the name before the colon is the "
            "identity your answer must contain:"
        )
        lines.extend(
            f"- {identity}: {record}"
            for identity, record in zip(
                context.similar_object_keys, context.similar_objects, strict=True
            )
        )
    else:
        lines.append("similarObjects: the search found nothing similar in this environment.")
    return "\n".join(lines)


def _judge(
    case: EvalCase,
    *,
    spec: AgentSpec,
    prompt: PromptVersionRow,
    system_head: str,
    provider: LLMProvider,
    envelope: dict[str, Any],
    containers: Mapping[str, Sequence[Container]],
    model_ref: str,
) -> tuple[AgentOutput | None, ValidationReport]:
    """One case through the provider and the runtime's own ladder (AI-RT-010 … AI-RT-012).

    The provider passed in is the recording wrapper, so this call is booked whether or not the
    answer scores (AI-NFR-03).
    """
    response = provider.generate_structured(
        StructuredRequest(
            messages=tuple(
                case_messages(
                    case,
                    prompt_body=prompt.body,
                    system_head=system_head,
                    model_ref=model_ref,
                )
            ),
            json_schema=dict(envelope),
            schema_name="agent-output",
            temperature=settings().default_temperature,
            max_output_tokens=settings().default_max_output_tokens,
            agent_hint=AgentHint(agent_type=case.agent_type, stage=case.stage),
        )
    )
    return validate(
        response.payload,
        spec=spec,
        context=case.context.check_context(
            agent_type=case.agent_type,
            stage=case.stage,
            containers=containers,
        ),
    )


def _system_head(*, spec: AgentSpec, prompt: PromptVersionRow, model_ref: str) -> str:
    """The system message the prompt file produces, with its prohibition list.

    Rendered through :func:`factory_intelligence.prompts.render.system_message` because an
    evaluation must judge the prompt as the runtime would send it.  The prompt being evaluated
    has no activation row — AI-REG-003 requires this evaluation *before* one may exist — so the
    registered version is wrapped in the record the renderer expects, and the two fields that
    describe an activation rather than a prompt say so.
    """
    return system_message(_candidate_prompt(spec=spec, prompt=prompt, model_ref=model_ref)).content


def _candidate_prompt(*, spec: AgentSpec, prompt: PromptVersionRow, model_ref: str) -> ActivePrompt:
    """An :class:`ActivePrompt` for a version that is not active: the thing under test."""
    return ActivePrompt(
        agent_type=spec.agent_type.value,
        prompt_version_id=prompt.id,
        name=prompt.name,
        version=prompt.version,
        body=prompt.body,
        system_rules=prompt.system_rules or "",
        prohibitions=[dict(item) for item in (prompt.prohibitions or [])],
        model_list=[model_ref],
        activated_by="evaluation",
        eval_run_id="under-evaluation",
        regression_passed_ref="under-evaluation",
    )


def _resolve_model(model: str, agent: AgentType) -> RegisteredModel:
    """A registry reference, or a bare model name when exactly one of this agent's matches."""
    try:
        resolved = registered_model(model)
    except NotFoundError:
        matches = [each for each in list_models(agent_type=agent) if each.model == model]
        if len(matches) != 1:
            raise ValidationError(
                f"model {model!r} is neither a registered 'provider/model' reference nor one "
                "unique model name for this agent; register it (AI-REG-004) or spell the "
                "provider",
                code="VALIDATION_ERROR",
                details={
                    "model": model,
                    "agentType": agent.value,
                    "candidates": sorted(each.ref for each in matches),
                },
            ) from None
        resolved = matches[0]
    if not resolved.enabled:
        raise ValidationError(
            f"model {resolved.ref} is registered but disabled; scoring a model nobody may run "
            "would produce evidence that cannot be activated",
            code="VALIDATION_ERROR",
            details={"model": resolved.ref},
        )
    return resolved


def _persist(
    result: EvalResult,
    *,
    summary: dict[str, Any],
    spec: AgentSpec,
    prompt: PromptVersionRow,
    resolved: RegisteredModel,
    operator: str,
    idempotency_key: str,
) -> None:
    """Run row, case rows, the registry's score and the idempotency record — one commit.

    The run is written whether the set passed or not: AI-REG-003 gates on the *absence* of a
    passing evaluation, so a failure that rolled back would look like a measurement that never
    happened rather than a prompt that was measured and refused.
    """
    with unit_of_work() as session:
        run = EvalRunRow(
            id=result.eval_run_id,
            agent_type=spec.agent_type.value,
            prompt_version_id=prompt.id,
            prompt_name=result.prompt_name,
            prompt_version=result.prompt_version,
            model=resolved.model,
            provider=resolved.provider,
            eval_set_version=result.eval_set_version,
            score=result.score,
            passed=result.passed,
            case_count=len(result.cases),
            summary=summary,
            run_by=operator,
        )
        session.add(run)
        session.flush()
        for outcome in result.cases:
            session.add(
                EvalCaseResultRow(
                    id=new_id("aecr"),
                    eval_run_id=str(result.eval_run_id),
                    case_id=outcome.case_id,
                    kind=outcome.kind,
                    passed=outcome.passed,
                    score=outcome.score,
                    diff=outcome.diff,
                )
            )
        record_eval_result(
            ref=resolved.ref,
            score=result.score,
            eval_run_id=str(result.eval_run_id),
            session=session,
        )
        idempotency.complete(
            IdemScope.EVALUATION_RUN,
            idempotency_key,
            result.model_dump(mode="json"),
            session=session,
        )


def _report(
    progress: ProgressReporter | None,
    step: str,
    fraction: float,
    data: dict[str, Any] | None = None,
) -> None:
    """Report one step when the caller is watching (AI-RT-022's shape, reused here)."""
    if progress is not None:
        progress.report(step=step, completed_fraction=fraction, data=data)


def _check_cancelled(cancel: CancellationToken | None) -> None:
    if cancel is not None:
        cancel.raise_if_cancelled()


__all__ = [
    "TRUSTED_KINDS",
    "case_messages",
    "patchable_containers",
    "run_evaluation",
]
