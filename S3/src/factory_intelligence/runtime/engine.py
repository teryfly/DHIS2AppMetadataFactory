"""The run engine (AI-RT-001 … AI-RT-032).

``execute_turn`` is one turn of a run, shared by ``start_agent_run`` and
``resume_agent_run`` so a resumed run provably goes through the same code.  Its shape is
forced by one constraint: S3's public entry points are external I/O, so **no transaction may
be open across a model call**, yet the patch submission, the ``AGENT_OUTPUT`` artifact, the
run row and its audit must commit together (00 doc 6.2, D-11).  Hence the alternation:

``G1`` read the Blueprint, the inputs and the environment; judge; call; validate →
``T2`` the single commit point (patch, artifact, rows, audit, idempotency) →
``G2`` only when S2 answered ``UID_POOL_EXHAUSTED``: replenish outside the transaction and
re-enter ``T2``.

The two retry budgets are counted apart because they answer to different requirements:
a timeout is not a bad answer (AI-RT-021) and a bad answer is not a timeout, and S2's
rejection (AI-RT-013) carries document-level facts the model cannot see before it submits.
Collapsing them would make AI-AC-01's "exactly two calls" depend on network behaviour.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from factory_blueprint.api import DraftInfo, PatchOutcome, ProjectSemantics, SelectedEntries
from factory_foundation.api import (
    ArtifactInfo,
    CancelledError,
    DataClassification,
    FactoryError,
    ValidationError,
    content_hash,
    idempotency,
    unit_of_work,
)
from sqlalchemy.orm import Session

from factory_intelligence import artifacts
from factory_intelligence.agents.checks.base import CheckContext
from factory_intelligence.agents.spec import AgentSpec, PatchMode
from factory_intelligence.config import settings
from factory_intelligence.context.builder import (
    BuiltContext,
    InputArtifact,
    build,
    continue_transcript,
    entry_body,
    entry_section,
    registered_decision_ids,
)
from factory_intelligence.context.similar import (
    DiscoveryShapeUnreadable,
    ObjectRecord,
    SearchThresholds,
    find_similar,
    hits_to_context_records,
    parse_discovery_result,
)
from factory_intelligence.context.thresholds import Thresholds, from_entries, retrieve
from factory_intelligence.cost import budget as budget_service
from factory_intelligence.cost.usage import CallContext, record_call, rollup
from factory_intelligence.deps import get_blueprint, get_clock, get_llm_provider, get_sleeper
from factory_intelligence.dto.agent import AgentOutput
from factory_intelligence.dto.common import Answer, Issue, Question, RejectedArtifact
from factory_intelligence.dto.request import AgentRunRequest
from factory_intelligence.dto.result import AgentRunResult
from factory_intelligence.enums import ItemSourceKind, PolicyResult, RunState
from factory_intelligence.errors import (
    OutputInvalid,
    PatchRejected,
    PromptNotActivated,
    ProviderUnavailable,
)
from factory_intelligence.llm.errors import LlmError, LlmTransportError
from factory_intelligence.llm.protocol import LLMProvider
from factory_intelligence.llm.registry import (
    RegisteredModel,
    candidates_for_agent,
    registered_model,
)
from factory_intelligence.llm.retry import backoff_seconds, max_attempts, wait_budget
from factory_intelligence.llm.tokens import estimate_messages
from factory_intelligence.llm.types import (
    AgentHint,
    ChatMessage,
    ImageInput,
    ModelResponse,
    StructuredRequest,
)
from factory_intelligence.output.errors import LADDER_ENVELOPE, Finding, ValidationReport
from factory_intelligence.output.schema import envelope_schema, section_containers
from factory_intelligence.output.validate import validate
from factory_intelligence.policy.model import ContextItem, EffectivePolicy
from factory_intelligence.policy.selection import CandidateModel, Selection
from factory_intelligence.prompts.activation import ActivePrompt
from factory_intelligence.runtime import patching
from factory_intelligence.runtime import state as run_state
from factory_intelligence.runtime.contextstore import StoredContext
from factory_intelligence.runtime.contextstore import document as context_document
from factory_intelligence.runtime.contextstore import stage as stage_document
from factory_intelligence.runtime.progress import (
    STEP_COMPLETED,
    STEP_CONTEXT,
    STEP_MODEL_CALL,
    STEP_POLICY,
    STEP_QUESTIONS,
    STEP_SUBMIT,
    STEP_VALIDATE,
    Progress,
)
from factory_intelligence.runtime.questions import blocking_questions, canonicalise


@dataclass
class Execution:
    """One turn's worth of inputs, assembled by ``start.py`` or ``resume.py``."""

    request: AgentRunRequest
    spec: AgentSpec
    run_id: str
    progress: Progress
    attempt: int = 1
    active_prompt: ActivePrompt | None = None
    answers: tuple[Answer, ...] = ()
    stored: StoredContext | None = None

    @property
    def project_id(self) -> str:
        return self.request.project_id


@dataclass
class Turn:
    """The gap's product: a judged context, the chosen model, and what it answered."""

    context: BuiltContext
    thresholds: Thresholds
    policy: EffectivePolicy
    selection: Selection
    model: RegisteredModel | None
    provider: LLMProvider | None
    semantics: ProjectSemantics | None = None
    draft: DraftInfo | None = None
    similar_records: list[dict[str, Any]] = field(default_factory=list)
    #: ``artifactId -> sha256:…`` for every input this turn was built from (AI-RT-001)
    input_hashes: dict[str, str] = field(default_factory=dict)
    #: the hash of the Blueprint version or draft the entries came from (AI-RT-001), read in
    #: the gap so the commit performs no I/O of its own
    blueprint_hash: str | None = None
    #: the operationKeys this page's API Intents declare — AI-AG-005's check cannot resolve
    #: a component's data source without them, and they come from the entries read here
    api_operation_keys: tuple[str, ...] = ()
    #: the decision ids the draft already registers in ``/architecture/decisions``, so
    #: ``decision_registered`` does not refuse a citation of a decision that exists
    existing_decision_ids: tuple[str, ...] = ()
    answer: AgentOutput | None = None
    #: the raw payload of the last provider answer, kept only so a corrective retry can ask
    #: for "fix this answer" instead of "reconstruct an answer you can no longer see".
    last_payload: Any = None
    #: where the freshly built context ends and this run's conversation begins (AI-RT-015).
    #: Persisted so a later turn appends the dialogue without re-sending the context.
    conversation_start: int = 0
    #: whether this agent submits patches; the repair turn then restates the array-path rules
    patch_agent: bool = False
    report: ValidationReport = field(default_factory=ValidationReport)
    calls: int = 0
    rejection: PatchRejected | None = None
    #: the provider failure that ended the ladder, if a provider failure ended it.  Kept apart
    #: from ``report`` because the run's error code must say *why* it stopped: a run that died
    #: of an unreachable provider reported as "S2 rejected my patch" sends the operator to the
    #: wrong subsystem (AI-RT-021, AI-RT-017).
    transport: FactoryError | None = None
    #: the last provider answer stopped at ``max_output_tokens`` (finish_reason length): the
    #: corrective retry says so explicitly rather than implying the model simply chose bad JSON
    truncated: bool = False
    #: non-blocking context warnings from a turn that degraded but continued (AI-AG-011):
    #: surfaced in the run result's issues, never a failure in their own right
    warnings: list[Issue] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return self.selection.chosen is not None

    @property
    def selection_reasons(self) -> list[str]:
        """One line per candidate that was tried and refused (AC-AG-01)."""
        return self.selection.reasons

    @property
    def policy_denials(self) -> list[dict[str, Any]]:
        """The items that made the context unsendable, if any did."""
        return [item.as_payload() for item in self.selection.denied_items]

    @property
    def similar_keys(self) -> list[str]:
        return [
            str(item["observed"])
            for item in self.similar_records
            if isinstance(item, dict) and item.get("observed")
        ]


def execute_turn(execution: Execution) -> AgentRunResult:
    """Run one turn to a terminal result; expected refusals are results, not raises."""
    try:
        turn = prepare(execution)
    except CancelledError:
        return cancel_result(execution)
    except FactoryError as exc:
        return failed(execution, exc)
    if not turn.allowed:
        return policy_denied(execution, turn)

    try:
        _ask_until_valid(execution, turn)
    except CancelledError:
        return cancel_result(execution)
    if turn.answer is None:
        return failed(execution, _refusal_error(turn), calls=turn.calls)
    return commit(execution, turn)


def _refusal_error(turn: Turn) -> FactoryError:
    """The error a refused answer reports as, in the rung's own words.

    Three different refusals, three different codes: a provider that never answered is not a
    schema failure, and a schema failure is not S2 saying no to a patch.  Collapsing them
    sends an operator to the wrong subsystem.
    """
    if turn.transport is not None:
        return turn.transport
    if turn.rejection is not None:
        return turn.rejection
    return OutputInvalid(
        _refusal_text(turn.report), details={"findings": turn.report.as_payload()["errors"]}
    )


# --- G1 -------------------------------------------------------------------------


def _entries_classification(
    request: Any, entries: Any
) -> DataClassification:
    """AI-POL-006: the classification of the Blueprint the entries were read from.

    A draft's classified ancestor is the version it was opened from — which is exactly how
    :mod:`factory_intelligence.policy.resolution` reads it, so the runtime and the read-only
    ``evaluate_policy`` answer with one rule instead of two.  A failure is allowed to
    propagate: guessing a level here is the widening this requirement exists to prevent.
    """
    from factory_intelligence.policy.resolution import (
        BLUEPRINT_PREFIX,
        DRAFT_MARKER,
        resolve,
    )

    if request.draft_id:
        ref = f"{BLUEPRINT_PREFIX}{DRAFT_MARKER}{request.draft_id}"
    else:
        version = getattr(entries, "version", None)
        if not version:
            # nothing was selected, so nothing of the Blueprint reaches the model
            return DataClassification.INTERNAL
        ref = f"{BLUEPRINT_PREFIX}{version}"
    return resolve([ref], project_id=request.project_id)[0].classification


def prepare(execution: Execution) -> Turn:
    """Read everything, then judge the whole context before a model is chosen."""
    request = execution.request
    if execution.active_prompt is None:
        # AI-REG-002: start/resume/chat/explain already refuse, so this is the defensive stop
        # for any path that reached the engine without an activated prompt.  An empty
        # placeholder would run an inert prompt, still spend the call, and report success as if
        # prompt governance had taken effect -- which is the failure this guard exists to make
        # impossible rather than merely unlikely.
        raise PromptNotActivated(
            f"the {execution.spec.agent_type.value} agent has no activated prompt (AI-REG-002)",
            details={"agentType": execution.spec.agent_type.value, "runId": execution.run_id},
        )
    execution.progress.report(STEP_CONTEXT, 0.05)
    draft = _load_draft(request.draft_id)
    if draft is not None:
        # S1 has no port method for this and never calls it, so an agent that reached
        # submit_patch first would die on BP_DRAFT_NOT_MATERIALIZED.
        get_blueprint().prepare_draft_base(request.project_id, draft.draft_id)
    semantics = _semantics(request.project_id)
    entries = retrieve(
        request,
        sections=execution.spec.writable_section_values,
        read_only_sections=tuple(section.value for section in execution.spec.read_only_sections),
        draft_materialised=draft is not None,
    )
    thresholds = from_entries(entries)
    inputs = _read_inputs(request.input_artifact_ids)
    _assert_input_kinds(execution, inputs)
    similar, similar_warning = _similar(request, entries)
    context = build(
        request=request,
        active_prompt=execution.active_prompt,
        # AI-RT-003's second segment: the stage's own rules, stated by the agent table.
        # The task prompt promises that the stage-rules block carries ``baseVersion``; without
        # the actual value here the model has to infer it, and a guessed baseVersion is refused
        # by S2 as a rebase conflict.
        stage_rules=(
            execution.spec.stage_rules(
                mapping_frozen=bool(getattr(semantics, "mapping_frozen", False))
            )
            + (f"\nbaseVersion: {draft.base_version}." if draft is not None else "")
        ),
        entries=entries,
        inputs=inputs,
        thresholds=thresholds,
        similar_records=similar,
        # AI-POL-006: an entry is classified as the version or draft artifact it belongs to,
        # not by a default this runtime chose for it — the default was wider than a
        # RESTRICTED blueprint would allow, so a relaxed policy could have sent entries that
        # the project's own classification forbids.
        entries_classification=_entries_classification(request, entries),
        open_questions=[
            question.model_dump(mode="json") for question in _prior_questions(execution)
        ],
    )
    #: the context this turn built; everything after it in the stored transcript is dialogue
    conversation_start = len(context.messages)
    if execution.stored is not None and execution.stored.messages:
        # AI-RT-015: continue the conversation the run already had, not a fresh one built
        # from the same inputs, which would lose what the model itself answered last turn.
        # Only the stored tail is appended: the stored context prefix is this turn's context
        # already, rebuilt from the current prompt, rules and entries.
        continue_transcript(
            context,
            execution.stored.messages,
            classification=execution.stored.classification,
            conversation_start=execution.stored.tail_start,
        )
    for message in _answer_messages(execution):
        _append(context, message)
    # The stored transcript and this turn's answers were added after ``build`` estimated the
    # context; model selection must see the whole conversation, not only the first turn's.
    context.token_estimate = estimate_messages(context.messages)
    policy = _effective_policy(request)
    candidates = _candidates(execution, context)
    selection = select(context, policy, candidates)
    _record_decision(execution, context, policy, selection)
    execution.progress.report(
        STEP_POLICY,
        0.2,
        # AI-RT-022 lists 策略判定 as a step a caller must be able to see; the model that
        # answered is the fact an operator reads next to it
        providerClass=getattr(selection, "provider_class", None),
        model=getattr(selection, "model", None),
        denied=len(getattr(selection, "denials", ()) or ()),
    )
    turn = Turn(
        context=context,
        thresholds=thresholds,
        api_operation_keys=_operation_keys(entries, request),
        existing_decision_ids=registered_decision_ids(entries),
        input_hashes={
            item.artifact_id: item.content_hash
            for item in inputs
            if item.content_hash
        },
        policy=policy,
        selection=selection,
        model=None,
        provider=None,
        semantics=semantics,
        draft=draft,
        similar_records=similar,
        warnings=[] if similar_warning is None else [similar_warning],
        blueprint_hash=_blueprint_hash_of(request.project_id, draft),
        conversation_start=conversation_start,
        patch_agent=execution.spec.patch_mode is PatchMode.PATCH,
    )
    if selection.chosen is not None:
        turn.model = _resolve_registered(selection.chosen)
        turn.provider = get_llm_provider(turn.model.provider_info())
    return turn


def select(
    context: BuiltContext, policy: EffectivePolicy, candidates: Sequence[CandidateModel]
) -> Selection:
    """The first model that policy *and* capability both allow (AI-POL-007)."""
    from factory_intelligence.policy.selection import select as _select

    return _select(
        context.items,
        policy,
        candidates,
        needed_tokens=context.token_estimate,
    )


def _ask_until_valid(execution: Execution, turn: Turn) -> None:
    """Call and validate until the ladder accepts or the configured repair budget is spent.

    S2's rejection is handled around :func:`commit`, not here: the submit happens inside the
    run's only transaction, so a rejection rolls that transaction back and the model is
    asked once more with S2's reasons appended (AI-RT-013) — keeping "one commit point" and
    "the rejection becomes feedback" both literally true.
    """
    provider = turn.provider
    if provider is None:  # pragma: no cover - selection refused earlier
        raise ProviderUnavailable("no provider was selected for this run")
    limit = settings().max_model_calls_per_run
    corrective_left = settings().corrective_attempts
    while True:
        if turn.calls >= limit:
            turn.report = _ceiling_report(turn.report, limit)
            return
        _call_once(execution, turn, provider)
        if turn.answer is not None:
            return
        if corrective_left <= 0:
            return
        corrective_left -= 1
        _append_feedback(turn)


def _call_once(execution: Execution, turn: Turn, provider: LLMProvider) -> None:
    """One logical call, which may contain several transport attempts (AI-RT-021)."""
    request = StructuredRequest(
        messages=tuple(turn.context.messages),
        json_schema=envelope_schema(),
        schema_name=f"{execution.spec.agent_type.value.lower()}.output",
        temperature=settings().default_temperature,
        max_output_tokens=settings().default_max_output_tokens,
        agent_hint=AgentHint(
            agent_type=execution.spec.agent_type.value,
            stage=execution.request.stage,
            run_id=execution.run_id,
            attempt=execution.attempt,
        ),
    )
    attempts = max_attempts()
    budget = wait_budget()
    for transport_attempt in range(attempts):
        _safe_point(execution)
        execution.progress.report(STEP_MODEL_CALL, 0.3, modelCallIndex=turn.calls + 1)
        started = get_clock().now()
        try:
            response = provider.generate_structured(request)
        except LlmTransportError as exc:
            turn.calls += 1
            _record_usage(
                execution,
                turn,
                None,
                started,
                request,
                outcome="ERROR",
                error=str(exc.message),
                request_id=exc.request_id,
            )
            budget.spend((get_clock().now() - started).total_seconds())
            if transport_attempt + 1 >= attempts or budget.remaining() <= 0:
                turn.report, turn.transport = _transport_report(exc), exc
                return
            delay = backoff_seconds(transport_attempt, exc)
            if delay > budget.remaining():
                turn.report, turn.transport = _transport_report(exc), exc
                return
            get_sleeper()(delay)
            budget.spend(delay)
            continue
        except LlmError as exc:
            turn.calls += 1
            _record_usage(
                execution,
                turn,
                None,
                started,
                request,
                outcome="ERROR",
                error=str(exc.message),
                request_id=exc.request_id,
            )
            turn.report, turn.transport = _transport_report(exc), exc
            return
        budget.answered()
        turn.calls += 1
        _record_usage(execution, turn, response, started, request, outcome="OK", error=None)
        # AI-RT-020 says before *and* after each model call.  Without this second one a
        # cancellation raised while the model was answering would still let the run reach S2
        # with its patch, which is the half-finished draft AI-AC-08 exists to avoid.
        _safe_point(execution)
        # Whether the provider stopped at the token ceiling decides how the corrective retry
        # words itself: a JSON that broke off mid-value is a length problem, not a model that
        # ignored the schema, and telling it to "fix these findings" would mislead it.
        turn.truncated = bool(getattr(response, "truncated", False))
        payload = _payload_of(response)
        turn.last_payload = payload
        answer, report = validate(
            payload,
            spec=execution.spec,
            context=_check_context(execution, turn),
        )
        execution.progress.report(STEP_VALIDATE, 0.55)
        turn.answer, turn.report = answer, report
        return


# --- T2 -------------------------------------------------------------------------


def commit(execution: Execution, turn: Turn) -> AgentRunResult:
    """Write everything the turn produced, in one transaction, retrying an S2 rejection.

    A waiting turn submits no patch: AI-RT-016 puts the confirmation record in the *later*
    patch, and the decision a blocking question gates is the one that patch would encode, so
    committing first would answer the question before anyone had been asked it (ruling 4).
    """
    # the last safe point AI-RT-020 asks for: before anything is written.  A patch already
    # staged in the gap has not touched S2 yet, so stopping here leaves no half-applied
    # draft behind, which is the whole reason cancellation is checked here and not at the
    # idempotency record.
    # AI-RT-022: the counts a progress event must carry are the counts this turn produced
    _safe_point(execution)
    rounds = settings().s2_reject_attempts
    while True:
        answer = turn.answer
        assert answer is not None  # the caller checked the ladder accepted
        raised, mapping = canonicalise(answer.questions, attempt=execution.attempt)
        waiting = bool(blocking_questions(raised))
        execution.progress.questions = len(raised)
        if raised:
            execution.progress.report(STEP_QUESTIONS, 0.75, questionCount=len(raised))
        output_handle = stage_document(
            execution.project_id, _output_document(execution, turn, answer, raised)
        )
        # Every turn extends the transcript, not only a waiting one: the attempt counter is
        # read back from this row to build the next turn's idempotency key, so a completing
        # turn that left it behind would make a later round of questions look like a replay of
        # the round before it (AI-RT-015, and the reason the version chain is the evidence).
        context_handle = stage_document(
            execution.project_id,
            context_document(
                run_id=execution.run_id,
                attempt=execution.attempt,
                context=_transcript_with_answer(execution, turn, answer),
                submitted_patch_ids=list(_submitted(execution)),
                raised_questions=raised,
                question_id_map=mapping,
                base_version=_base_version(execution, turn),
                conversation_start=turn.conversation_start,
            ),
        )
        try:
            patch_ids, output = _write_with_pool(
                execution, turn, answer, raised, waiting, output_handle, context_handle
            )
            break
        except PatchRejected as rejection:
            # T2 rolled back with the rejection, so nothing was applied
            if rounds <= 0 or turn.calls >= settings().max_model_calls_per_run:
                return failed(execution, rejection, calls=turn.calls)
            rounds -= 1
            turn.report = _rejection_report(rejection)
            _append_feedback(turn)
            assert turn.provider is not None  # selection refused otherwise, before the loop
            _call_once(execution, turn, turn.provider)
            if turn.answer is None:
                return failed(execution, rejection, calls=turn.calls)
        except FactoryError as exc:
            # S2 raises PatchMalformed for structural RFC 6902 problems instead of returning
            # a rejection outcome.  It is still a bad model answer and buys the same one
            # corrective retry; every other provider error must end the run as FAILED rather
            # than escape with the idempotency record and run row left mid-flight.
            if exc.error_code != "BP_PATCH_MALFORMED":
                return failed(execution, exc, calls=turn.calls)
            rejection = PatchRejected(str(exc.message), details={"reason": exc.error_code})
            if rounds <= 0 or turn.calls >= settings().max_model_calls_per_run:
                return failed(execution, rejection, calls=turn.calls)
            rounds -= 1
            turn.report = _rejection_report(rejection)
            _append_feedback(turn)
            assert turn.provider is not None
            _call_once(execution, turn, turn.provider)
            if turn.answer is None:
                return failed(execution, rejection, calls=turn.calls)
    execution.progress.report(STEP_COMPLETED, 1.0)
    result = AgentRunResult(
        run_id=execution.run_id,
        status=RunState.WAITING_INPUT.value if waiting else RunState.COMPLETED.value,
        summary=answer.summary,
        confidence=answer.confidence,
        patch_ids=list(patch_ids),
        agent_output_artifact_id=output.id,
        questions=raised,
        rejected_artifacts=_rejected(turn),
        budget_status=budget_service.view(execution.project_id).as_result_field(),
        issues=[*_issues_of(turn), *turn.warnings],
    )
    with unit_of_work() as session:
        idempotency.complete(
            idem_scope(execution), execution.request.idempotency_key,
            result.model_dump(mode="json"), session=session,
        )
    return result


def _write_with_pool(
    execution: Execution,
    turn: Turn,
    answer: AgentOutput,
    raised: list[Question],
    waiting: bool,
    output_handle: Any,
    context_handle: Any,
) -> tuple[list[str], ArtifactInfo]:
    """The commit, re-entered after a UID replenishment (which is not a retry of anything)."""
    attempts = settings().uid_replenish_attempts
    for attempt in range(attempts + 1):
        try:
            return _write(
                execution, turn, answer, raised, waiting, output_handle, context_handle
            )
        except patching.PoolReplenishNeeded:
            if attempt >= attempts:
                raise PatchRejected(
                    "S2's UID pool stayed empty after replenishing it; the design-reference "
                    "environment may be unreachable",
                    details={"runId": execution.run_id},
                ) from None
            _replenish(execution)
    raise AssertionError("unreachable: the loop returns or raises")  # pragma: no cover


def _output_document(
    execution: Execution, turn: Turn, answer: AgentOutput, raised: list[Question]
) -> dict[str, Any]:
    """The ``AGENT_OUTPUT`` product: the accepted answer plus the run's own facts.

    AI-RT-014: only what was decided is recorded.  A provider's raw text or a reasoning
    field contributes nothing, so nothing here can carry it — which is *enforced* rather
    than assumed, because the decision envelope leaves its extra properties open
    (``output/schema.py``): a model that answers with ``reasoning`` or ``chain_of_thought``
    would otherwise have that text stored as part of the auditable decision summary, and
    AI-BR-08 forbids persisting a model's internal reasoning whatever it is called.
    """
    return {
        "agentType": execution.spec.agent_type.value,
        "stage": execution.request.stage,
        "runId": execution.run_id,
        "promptVersions": _prompt_versions(execution),
        "model": turn.model.ref if turn.model else None,
        "summary": answer.summary,
        "confidence": answer.confidence,
        "decisions": [_scrub(item) for item in answer.decisions],
        "patch": {
            "baseVersion": _base_version(execution, turn),
            "operations": list(answer.patch.operations) if answer.patch else [],
        },
        "questions": [_scrub(question.model_dump(mode="json")) for question in raised],
    }


def _write(
    execution: Execution,
    turn: Turn,
    answer: AgentOutput,
    raised: list[Question],
    waiting: bool,
    output_handle: Any,
    context_handle: Any,
) -> tuple[list[str], ArtifactInfo]:
    """The single commit point (00 doc 6.2, D-11)."""
    patch_ids: list[str] = []
    with unit_of_work() as session:
        if not waiting and execution.spec.accepts_patch(mapping_frozen=_frozen(turn)):
            patch_ids = [_submit(execution, turn, answer, session)].copy()
        output = _register_output(execution, turn, output_handle, session)
        if context_handle is not None:
            _persist_context(execution, turn, context_handle, raised, patch_ids, session)
        _finish(execution, turn, answer, raised, patch_ids, output, session)
    return patch_ids, output


def _submit(execution: Execution, turn: Turn, answer: AgentOutput, session: Session) -> str:
    records = (
        patching.confirmations(
            run_id=execution.run_id,
            answers=list(execution.answers),
            stored=turn_stored(execution),
            session=session,
        )
        if execution.stored is not None
        else []
    )
    patch = patching.build_patch(
        answer,
        run_id=execution.run_id,
        base_version=_base_version(execution, turn),
        confirmations=records,
    )
    outcome = patching.submit_with_pool(
        project_id=execution.project_id,
        draft_id=_draft_id(execution),
        stage=execution.spec.stage,
        patch=patch,
        idempotency_key=patching.patch_idempotency_key(execution.run_id, execution.attempt),
        design_reference_environment_key=execution.request.design_reference_environment_key,
        session=session,
    )
    execution.progress.patches += 1
    execution.progress.report(STEP_SUBMIT, 0.85, patchId=outcome.patch_id)
    if _is_rejected(outcome):
        issues = patching.rejections_as_issues(outcome)
        raise PatchRejected(
            "S2 refused the patch: " + "; ".join(issue.description for issue in issues)[:400],
            details={"issues": [issue.model_dump(mode="json") for issue in issues]},
        )
    return outcome.patch_id


def turn_stored(execution: Execution) -> StoredContext:
    assert execution.stored is not None  # guarded by the caller
    return execution.stored


# --- results ------------------------------------------------------------------


_STATUS_BY_STATE: dict[RunState, str] = {
    RunState.COMPLETED: "COMPLETED",
    RunState.WAITING_INPUT: "WAITING_INPUT",
    RunState.FAILED: "FAILED",
    RunState.POLICY_DENIED: "POLICY_DENIED",
    RunState.BUDGET_EXCEEDED: "BUDGET_EXCEEDED",
    RunState.CANCELLED: "CANCELLED",
}


def _issues_of(turn: Turn) -> list[Issue]:
    """AI-RT-018: the ladder's findings, as unified Issues, not only as prose reasons.

    A run that reached ``commit`` passed the ladder, so this is empty on a clean success;
    a run that got here after a corrective retry carries what the first answer got wrong,
    and a patch S2 refused carries S2's own issues — which is what a reviewer needs to see
    in the workbench rather than a sentence in ``denial_reasons``.
    """
    report = turn.report
    if report is None:
        return []
    return [Issue.from_finding(finding) for finding in report.sorted_findings()]


def failed(execution: Execution, error: FactoryError, *, calls: int = 0) -> AgentRunResult:
    """Record a failure and report it — the row, the audit and the result agree."""
    with unit_of_work() as session:
        row = run_state.require_run(execution.run_id, session=session)
        run_state.finish(
            row,
            session=session,
            state=RunState.FAILED,
            error_code=error.error_code,
            error_message=str(error.message),
            model_calls=calls,
        )
        idempotency.fail(
            idem_scope(execution),
            execution.request.idempotency_key,
            error_code=error.error_code,
            session=session,
        )
    return AgentRunResult(
        run_id=execution.run_id,
        status="FAILED",
        summary=None,
        patch_ids=[],
        # the refusal itself is a finding: 00 doc 6.10 says so, and a workbench that only
        # gets a sentence cannot mark it blocking
        issues=[
            Issue(
                issue_id=f"aiss-{execution.run_id}",
                rule_id=str(error.code),
                level="BLOCKING",
                message=str(error.message),
                details=dict(error.details or {}),
            )
        ],
        denial_reasons=[str(error.message)],
        budget_status=budget_service.view(execution.project_id).as_result_field(),
    )


def policy_denied(execution: Execution, turn: Turn) -> AgentRunResult:
    """AI-POL-003, AC-POL-01, AC-AG-01: refuse, list what was refused and why."""
    with unit_of_work() as session:
        row = run_state.require_run(execution.run_id, session=session)
        run_state.finish(
            row,
            session=session,
            state=RunState.POLICY_DENIED,
            error_code="POLICY_DENIED",
            error_message="; ".join(turn.selection_reasons)[:600],
            model_calls=turn.calls,
        )
        idempotency.complete(
            idem_scope(execution),
            execution.request.idempotency_key,
            {"status": "POLICY_DENIED"},
            session=session,
        )
    return AgentRunResult(
        run_id=execution.run_id,
        status="POLICY_DENIED",
        rejected_artifacts=_rejected(turn),
        denial_reasons=turn.selection_reasons,
        budget_status=budget_service.view(execution.project_id).as_result_field(),
    )


def cancel_result(execution: Execution) -> AgentRunResult:
    """AI-AC-08: stop at the next safe point, with no patch from this turn."""
    with unit_of_work() as session:
        row = run_state.require_run(execution.run_id, session=session)
        run_state.finish(row, session=session, state=RunState.CANCELLED, error_message="cancelled")
        idempotency.fail(
            idem_scope(execution),
            execution.request.idempotency_key,
            error_code="CANCELLED",
            session=session,
        )
    return AgentRunResult(run_id=execution.run_id, status="CANCELLED")


def budget_exceeded(execution: Execution, view: Any) -> AgentRunResult:
    """AI-COST-002: refuse a *new* run; nothing already running is touched."""
    with unit_of_work() as session:
        row = run_state.require_run(execution.run_id, session=session)
        run_state.finish(
            row, session=session, state=RunState.BUDGET_EXCEEDED, error_code="AI_BUDGET_EXCEEDED"
        )
        idempotency.complete(
            idem_scope(execution),
            execution.request.idempotency_key,
            {"status": "BUDGET_EXCEEDED"},
            session=session,
        )
    return AgentRunResult(
        run_id=execution.run_id,
        status="BUDGET_EXCEEDED",
        budget_status=view.as_result_field(),
        denial_reasons=[f"project spend {view.spent} exceeded its limit {view.limit}"],
    )


# --- Turn helpers ---------------------------------------------------------------


def _rejected(turn: Turn) -> list[RejectedArtifact]:
    """The refused products, named rather than dropped (AI-POL-003, AI-INP-007, AC-POL-02).

    A PHI_PII item never reaches the policy engine at all — the context builder will not put
    it in a message — so its exclusion has no denial verdict to ride on.  Reporting it here is
    what keeps "excluded" different from "invisible".
    """
    payloads = list(turn.policy_denials)
    payloads.extend(
        {
            "ref": ref,
            "classification": DataClassification.PHI_PII.value,
            "reason": (
                "PHI_PII content never enters a model context under any policy (AC-POL-02)"
            ),
            "allowedClasses": [],
        }
        for ref in turn.context.excluded_phi
        if not any(str(item.get("ref")) == ref for item in payloads)
    )
    return [_rejected_artifact(item) for item in payloads]


def _rejected_artifact(item: Mapping[str, Any]) -> RejectedArtifact:
    return RejectedArtifact(
        ref=str(item.get("ref", "")),
        classification=str(item.get("classification", "")),
        reason=str(item.get("reason", "")),
        allowed_classes=[str(each) for each in item.get("allowedClasses", [])],
    )


def idem_scope(execution: Execution) -> str:
    """The scope a repeated key is stored under (AI-AC-05, AI-RT-020).

    Start and resume keep separate scopes because S1 reuses one resume key for the whole
    run: ``resume:{run_id}`` is sent again on every turn, so the internal key is scoped by
    attempt (see ``runtime.resume``) and a genuine replay is still recognised.
    """
    from factory_intelligence.registries import IdemScope

    return IdemScope.AGENT_RESUME if execution.attempt > 1 else IdemScope.AGENT_START


def _safe_point(execution: Execution) -> None:
    """Token and database flag (AI-RT-020): S1 passes ``cancel=None``, so the row is the
    only channel that crosses workers."""
    execution.progress.safe_point()
    if run_state.cancel_requested(execution.run_id):
        raise CancelledError("agent run cancelled at a safe point", details={"runId": execution.run_id})


def _transcript_with_answer(
    execution: Execution, turn: Turn, answer: AgentOutput
) -> BuiltContext:
    """The transcript a later turn continues from: the input plus what the model answered.

    ``context_document`` used to persist only the messages that were *sent*, so a resumed
    turn saw its questions and the user's answers but not the decisions and rejected
    alternatives it had written.  The model then re-emitted its decisions from the question
    ids alone, and ``decision_coverage`` refused them for having no alternative.  Keeping the
    accepted answer in the transcript is what makes AI-RT-015's "same run context" literal.
    """
    payload = turn.last_payload
    if not isinstance(payload, dict) or not payload:
        payload = answer.model_dump(mode="json", by_alias=True)
    text = json.dumps(_scrub(payload), ensure_ascii=False)
    context = _with_tail(turn.context, ChatMessage(role="assistant", content=text))
    size = len(text.encode("utf-8"))
    return BuiltContext(
        messages=context.messages,
        items=[
            *context.items,
            ContextItem(
                ref=f"answer:{execution.run_id}:{execution.attempt}",
                classification=DataClassification.INTERNAL,
                kind=ItemSourceKind.AGENT_ANSWER,
                label="prior answer",
                size_bytes=size,
            ),
        ],
        excluded_phi=list(context.excluded_phi),
        open_questions=list(context.open_questions),
        byte_size=context.byte_size,
        token_estimate=estimate_messages(context.messages),
    )


def _append(context: BuiltContext, message: ChatMessage) -> None:
    """Fold a resume answer into the judged context.

    It is added as an item as well as a message: what leaves the process must be in the
    list the policy engine saw, and an answer is content too (AI-RT-006).
    """
    size = len(message.content.encode("utf-8"))
    context.messages.append(message)
    context.byte_size += size
    context.items.append(
        ContextItem(
            ref=f"answer:{message.content[:24].replace(chr(10), ' ')}",
            classification=DataClassification.INTERNAL,
            kind=ItemSourceKind.USER_ANSWER,
            label="user answer",
            size_bytes=size,
        )
    )


def _append_feedback(turn: Turn) -> None:
    from factory_intelligence.context.fence import plain
    from factory_intelligence.enums import ItemSourceKind

    # When the provider stopped at the token ceiling the JSON broke off mid-value; that is not
    # the model choosing a shape the ladder refuses, so say what happened and how to fix it --
    # compress and return a *complete* object -- rather than pointing it at findings that were
    # only ever an artifact of the cut.
    lead = (
        "The previous answer was truncated: it reached max_output_tokens, so its JSON is "
        "incomplete. Compress your output and return one complete JSON object.\n\n"
        if turn.truncated
        else ""
    )
    previous = ""
    if turn.last_payload is not None and not turn.truncated:
        try:
            serialized = json.dumps(turn.last_payload, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            serialized = ""
        # Keep the corrective turn inside the model's window.  A 30k-token answer is not
        # repeated; it is regenerated from the same context and the findings.
        if serialized and len(serialized) <= 24_000:
            previous = "Your previous answer was:\n" + serialized + "\n\n"
        elif serialized:
            previous = (
                "Your previous answer was too large to repeat here; produce a complete new "
                "answer that fixes the findings and changes nothing else.\n\n"
            )
    rules = [
        "Your previous answer was refused; nothing from it was applied to the draft.",
        "Return one COMPLETE JSON object with every envelope key (summary, confidence, "
        "decisions, patch, questions), not a fragment or a delta.",
        "Start from the draft shown in context, not from your previous answer: a turn that "
        "ended WAITING_INPUT was never submitted, so its entries do not exist yet. Redo "
        "every operation the current target state needs.",
        "Mechanical findings (schema types, array paths or indexes, missing decision "
        "alternatives) are yours to fix directly. Ask only for a business fact a human must "
        "supply.",
        "If a finding cannot be fixed from the context because a business fact is missing or "
        "unconfirmed — a requirement, role or ownership rule, lifecycle transition, threshold "
        "or formula, or scope decision — do not invent it and do not repeat the rejected "
        "patch. Return a complete answer with an empty patch (`operations: []`) and blocking "
        "`questions`; each question must name the affected keys in `affects`, offer 2–5 "
        "complete suggested answers with a rationale, and mark exactly one recommended (at "
        "least one for MULTIPLE). The human's answers are added to the next turn, which then "
        "produces the patch.",
        "Do not ask for the previous answer, and do not renumber or drop decisions: every "
        "decision is judged again in full, so repeat its selected option, at least one "
        "rejected alternative with the reason (or confidence 1.0 for a forced choice), its "
        "risks and its confidence.",
    ]
    if turn.patch_agent:
        rules.extend(
            [
                "Patch paths are RFC 6902 pointers over JSON arrays. `add /<container>/-` "
                "appends a complete new entry (include all of its fields, including arrays such "
                "as steps); `replace /<container>/<index>` or `/<container>/<index>/<field>` "
                "changes an entry that already exists in the current draft. A businessKey is "
                "never an array index. If a finding says `array index N out of range`, that "
                "entry is absent: create it with `add /<container>/-` instead of replacing N.",
                "If a finding says `references unknown decisionId 'ID'`, your `decisions` "
                "array is not what was searched: a decision exists for the Blueprint only as "
                "an entry of `/architecture/decisions`. Fix it by adding that decision there "
                "in this same patch — `add /architecture/decisions/-` with `decisionId` set to "
                "the cited ID and `status` PROPOSED — or cite an ID the draft already "
                "registers. Renaming the decision in your answer fixes nothing.",
            ]
        )
    turn.context = _with_tail(
        turn.context,
        plain(
            kind=ItemSourceKind.VALIDATION_ERRORS,
            text=(
                lead
                + previous
                + "REPAIR TURN — fix exactly the findings below:\n"
                + "\n".join(f"- {rule}" for rule in rules)
                + "\n\nFindings:\n"
                + turn.report.as_prompt_block()
            ),
        ),
    )


def _with_tail(context: BuiltContext, message: ChatMessage) -> BuiltContext:
    size = len(message.content.encode("utf-8"))
    return BuiltContext(
        messages=[*context.messages, message],
        items=list(context.items),
        excluded_phi=list(context.excluded_phi),
        open_questions=list(context.open_questions),
        byte_size=context.byte_size + size,
        token_estimate=context.token_estimate,
    )


def _check_context(execution: Execution, turn: Turn) -> CheckContext:
    answered = {str(answer.question_local_id) for answer in execution.answers}
    answered_affects = [
        str(target)
        for question in _prior_questions(execution)
        if question.local_id in answered
        for target in question.affects
    ]
    return CheckContext(
        agent_type=execution.spec.agent_type.value,
        stage=execution.request.stage,
        containers={
            section: tuple(section_containers(section))
            for section in execution.spec.writable_section_values
        },
        decision_confidence_threshold=turn.thresholds.decision,
        answered_affects=answered_affects,
        mapping_frozen=_frozen(turn),
        similar_object_keys=turn.similar_keys,
        api_operation_keys=turn.api_operation_keys,
        existing_decision_ids=turn.existing_decision_ids,
        phi_refs=list(turn.context.excluded_phi),
        base_version=_base_version(execution, turn),
    )


def _record_usage(
    execution: Execution,
    turn: Turn,
    response: ModelResponse | None,
    started: Any,
    request: StructuredRequest,
    *,
    outcome: str,
    error: str | None,
    request_id: str | None = None,
) -> None:
    """A usage row per call, in its own transaction (AI-NFR-03).

    Written before validation and before the run's commit so a run that then fails still
    shows the call it paid for.
    """
    if turn.model is None:  # pragma: no cover - selection refused earlier
        return
    latency = int((get_clock().now() - started).total_seconds() * 1000)
    context = CallContext(
        project_id=execution.project_id,
        workflow_id=execution.request.workflow_id,
        stage=execution.request.stage,
        agent_type=execution.spec.agent_type.value,
        run_id=execution.run_id,
        sequence=turn.calls,
        call_role="initial" if turn.calls <= 1 else "corrective",
    )
    with unit_of_work() as session:
        record_call(
            context=context,
            model=turn.model,
            response=response,
            temperature=request.temperature,
            max_output_tokens=request.max_output_tokens,
            latency_ms=latency,
            outcome=outcome,
            error=error,
            request_id=request_id,
            context_artifacts=[
                {"ref": item.ref, "classification": item.classification.value}
                for item in turn.context.items
            ],
            session=session,
            operator=execution.request.operator,
        )


def _register_output(
    execution: Execution, turn: Turn, handle: Any, session: Session
) -> ArtifactInfo:
    return artifacts.register_staged(
        handle,
        session=session,
        project_id=execution.project_id,
        artifact_type="AGENT_OUTPUT",
        data_classification=artifacts.source_floor(turn.context.classifications()),
        source_artifact_ids=artifact_ids(_artifact_items(turn.context)),
        workflow_id=execution.request.workflow_id,
        build_record={
            "agentRunId": execution.run_id,
            "agentType": execution.spec.agent_type.value,
            "promptVersions": _prompt_versions(execution),
            "model": turn.model.ref if turn.model else None,
            "modelCalls": turn.calls,
        },
    )


def _persist_context(
    execution: Execution,
    turn: Turn,
    handle: Any,
    raised: list[Question],
    patch_ids: Sequence[str],
    session: Session,
) -> None:
    previous = execution.stored.context_artifact_id if execution.stored else None
    info = artifacts.register_staged(
        handle,
        session=session,
        project_id=execution.project_id,
        artifact_type="AGENT_OUTPUT",
        data_classification=artifacts.source_floor(turn.context.classifications()),
        source_artifact_ids=artifact_ids(_artifact_items(turn.context)),
        workflow_id=execution.request.workflow_id,
        prev_version_id=previous,
        build_record={"kind": "agentRunContext", "runId": execution.run_id},
    )
    run_state.save_context(
        run_state.require_run(execution.run_id, session=session),
        session=session,
        context_artifact_id=info.id,
        context_hash=info.content_hash,
        pending_question_local_ids=[item.local_id for item in blocking_questions(raised)],
        question_id_map={
            str((question.details or {}).get("modelId") or question.local_id): question.local_id
            for question in raised
        },
        submitted_patch_ids=[*_submitted(execution), *patch_ids],
        raised_question_local_ids=[
            *(execution.stored.raised_question_local_ids if execution.stored else []),
            *[question.local_id for question in raised],
        ],
        base_version=_base_version(execution, turn),
        attempt=execution.attempt,
    )


def _finish(
    execution: Execution,
    turn: Turn,
    answer: AgentOutput,
    raised: list[Question],
    patch_ids: Sequence[str],
    output: ArtifactInfo,
    session: Session,
) -> None:
    row = run_state.require_run(execution.run_id, session=session)
    # AI-RT-001: the run records the *bytes* it was told about, not only their ids, so a
    # later review can prove the input had not changed under it
    row.input_hashes = dict(turn.input_hashes)
    waiting = bool(blocking_questions(raised))
    totals = rollup(execution.project_id, group_by=["model"], session=session)
    run_state.finish(
        row,
        session=session,
        state=RunState.WAITING_INPUT if waiting else RunState.COMPLETED,
        summary=answer.summary,
        confidence=answer.confidence,
        output_artifact_id=output.id,
        patch_ids=list(patch_ids),
        question_local_ids=[question.local_id for question in raised],
        provider=turn.model.provider if turn.model else None,
        provider_class=turn.model.provider_class.value if turn.model else None,
        model=turn.model.model if turn.model else None,
        prompt_versions=_prompt_versions(execution),
        blueprint_ref={
            "baseVersion": _base_version(execution, turn),
            "draftId": execution.request.draft_id,
            "contextDigest": turn.context.digest(),
            # AI-RT-001: the version or draft the patch was based on, by hash as well as by
            # id, so a run's evidence outlives a draft that was later re-opened
            "contentHash": turn.blueprint_hash,
        },
        usage={
            "modelCalls": turn.calls,
            "inputTokens": sum(item.input_tokens for item in totals),
            "outputTokens": sum(item.output_tokens for item in totals),
        },
        estimated_cost=sum(item.estimated_cost for item in totals),
        model_calls=turn.calls,
    )
    idempotency.complete(
        idem_scope(execution),
        execution.request.idempotency_key,
        {"status": "recorded", "runId": execution.run_id},
        session=session,
    )


# --- small readers and fallbacks ------------------------------------------------


def _load_draft(draft_id: str | None) -> DraftInfo | None:
    if draft_id is None:
        return None
    return get_blueprint().draft(draft_id)


def _semantics(project_id: str) -> ProjectSemantics | None:
    try:
        return get_blueprint().project_semantics(project_id)
    except FactoryError:
        # no approved version yet means no mapping can have been frozen, which is the only
        # safe reading for a project's first run
        return None


def _read_inputs(artifact_ids: Sequence[str]) -> list[InputArtifact]:
    out: list[InputArtifact] = []
    for artifact_id in artifact_ids:
        payload, classification, media_type = artifacts.read_bytes(artifact_id)
        is_image = media_type.startswith("image/")
        digest = content_hash(payload)
        out.append(
            InputArtifact(
                artifact_id=artifact_id,
                classification=classification,
                content="" if is_image else payload.decode("utf-8", "replace"),
                media_type=media_type,
                images=(
                    (ImageInput(media_type=media_type, data_base64=_b64(payload), label=artifact_id),)
                    if is_image
                    else ()
                ),
                # AI-RT-001 records the hash of every input a run was built from, and the
                # fence prints it, so a reviewer can tell which bytes a decision was about
                content_hash=digest,
            )
        )
    return out


def _b64(payload: bytes) -> str:
    return base64.b64encode(payload).decode("ascii")


#: A model's own working, whatever it is called, is not a decision (AI-BR-08, AI-RT-014).
#: The decision envelope leaves its extra properties open so a provider that adds a field
#: does not lose the answer, which is exactly why the run strips these before anything is
#: validated, recorded or replayed — otherwise "we do not persist reasoning" would be a
#: promise about prompt wording rather than a property of the code.
_REASONING_KEYS = frozenset(
    {
        "reasoning",
        "reasoning_content",
        "thinking",
        "thought",
        "thoughts",
        "chain_of_thought",
        "chainofthought",
        "cot",
        "internal",
        "scratchpad",
    }
)


def _scrub(value: Any) -> Any:
    """Drop reasoning-shaped keys, recursively, from one model-authored structure."""
    if isinstance(value, dict):
        return {
            key: _scrub(item)
            for key, item in value.items()
            if str(key).strip().lower() not in _REASONING_KEYS
        }
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value


def _payload_of(response: ModelResponse) -> Any:
    if response.payload:
        return _scrub(response.payload)
    text = response.raw_text.strip()
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return {}
    return _scrub(parsed) if isinstance(parsed, dict) else {}


def _effective_policy(request: AgentRunRequest) -> EffectivePolicy:
    with unit_of_work() as session:
        from factory_intelligence.policy.orgpolicy import effective_policy

        return effective_policy(session=session, environment_policies=request.target_environment_policies)


def _candidates(execution: Execution, context: BuiltContext) -> list[CandidateModel]:
    with unit_of_work() as session:
        return candidates_for_agent(
            execution.spec.agent_type, needed_tokens=context.token_estimate, session=session
        )


def _resolve_registered(candidate: CandidateModel) -> RegisteredModel:
    with unit_of_work() as session:
        return registered_model(f"{candidate.provider}/{candidate.model}", session=session)


def _record_decision(
    execution: Execution,
    context: BuiltContext,
    policy: EffectivePolicy,
    selection: Selection,
) -> None:
    """Every attempt is recorded, allowed or refused (AI-POL-008)."""
    with unit_of_work() as session:
        row = run_state.require_run(execution.run_id, session=session)
        run_state.record_policy_decision(
            row,
            session=session,
            effective_policy=policy.to_payload(),
            provider_class=selection.chosen.provider_class.value if selection.chosen else None,
            model=selection.chosen.model if selection.chosen else None,
            items=[
                {"ref": item.ref, "classification": item.classification.value}
                for item in context.items
            ],
            result=PolicyResult.ALLOWED.value if selection.chosen else PolicyResult.DENIED.value,
            denied_items=[item.as_payload() for item in selection.denied_items],
            model_denials=selection.payloads,
            warnings=list(policy.warnings),
        )


def _replenish(execution: Execution) -> None:
    key = execution.request.design_reference_environment_key
    if not key:
        raise PatchRejected(
            "S2 needs a UID and no design-reference environment was supplied to replenish "
            "from (AI-ASM-01)",
            details={"agentType": execution.spec.agent_type.value},
        )
    get_blueprint().replenish_uid_pool(execution.project_id, key)


#: artifact types a mapping-related agent may be handed as "input" (AI-INP-001)
_NRR_INPUT_TYPES = frozenset({"NRR", "AGENT_OUTPUT"})


def _assert_input_kinds(execution: Execution, inputs: Sequence[InputArtifact]) -> None:
    """Refuse a raw business input for an agent that reads only NRR (AI-INP-001)."""
    if not execution.spec.nrr_only:
        return
    from factory_foundation.api import get_artifact

    raw = [
        item.artifact_id
        for item in inputs
        if get_artifact(item.artifact_id).type not in _NRR_INPUT_TYPES
    ]
    if raw:
        raise ValidationError(
            f"the {execution.spec.agent_type.value} agent consumes NRR and approved Blueprint "
            "entries only; these inputs are raw business material and must be parsed first "
            "(AI-INP-001, AI-BR-02)",
            code="VALIDATION_ERROR",
            details={"agentType": execution.spec.agent_type.value, "inputArtifactIds": raw},
        )


def _operation_keys(entries: Any, request: Any) -> tuple[str, ...]:
    """Every operationKey of this page's API Intents (AI-AG-005, FR-UII-002).

    Read here rather than in the check because the check is a pure function of its context:
    fetching Blueprint entries inside it would make a constraint check perform I/O.
    """
    if entries is None:
        return ()
    pages = {str(item).removeprefix("page:") for item in (request.scope or [])}
    out: list[str] = []
    for entry in entries.entries:
        body = entry_body(entry)
        if entry_section(entry) != "apiIntents":
            continue
        page_key = str(body.get("pageKey") or "")
        if pages and page_key and page_key not in pages:
            continue
        key = body.get("operationKey")
        if isinstance(key, str) and key:
            out.append(key)
    return tuple(sorted(set(out)))


def _blueprint_hash_of(project_id: str, draft: DraftInfo | None) -> str | None:
    """The content hash of the draft or current version a run read (AI-RT-001).

    Read in the gap: a commit that performed its own I/O would break the T1/G1/T2
    alternation the runtime is built on (00 doc 6.2, D-11).  A draft that has never been
    patched carries no hash of its own, and then the version it was opened from is the
    Blueprint content the entries came out of.
    """
    digest = getattr(draft, "content_hash", None)
    if isinstance(digest, str) and digest:
        return digest
    try:
        current = get_blueprint().current_version(project_id)
    except FactoryError:
        return None
    value = getattr(current, "content_hash", None)
    return value if isinstance(value, str) and value else None


def _base_version(execution: Execution, turn: Turn) -> str:
    if turn.draft is not None:
        return turn.draft.base_version
    if turn.semantics is not None:
        return turn.semantics.version
    return "0.0.0"


def _draft_id(execution: Execution) -> str:
    if execution.request.draft_id is None:
        raise PatchRejected(
            "this agent needs a draft open before it can submit a patch (AI-ASM-02)",
            details={"agentType": execution.spec.agent_type.value},
        )
    return execution.request.draft_id


def _submitted(execution: Execution) -> list[str]:
    return list(execution.stored.submitted_patch_ids) if execution.stored else []


def _prior_questions(execution: Execution) -> list[Question]:
    """The questions this run already asked, so a resumed turn does not ask them again.

    They come from the transcript rather than the database, because the text is the part the
    model needs (AI-RT-015: the run continues *in* its persisted context).
    """
    return list(execution.stored.raised_questions) if execution.stored else []


def _answer_messages(execution: Execution) -> list[ChatMessage]:
    if not execution.answers:
        return []
    from factory_intelligence.context.fence import plain
    from factory_intelligence.enums import ItemSourceKind

    asked = {question.local_id: question.text for question in _prior_questions(execution)}
    body = "\n".join(
        f"- {answer.question_local_id}"
        + (f" ({asked[answer.question_local_id]})" if answer.question_local_id in asked else "")
        + ": "
        + _answer_value(answer)
        + f" (answered by {answer.answered_by})"
        for answer in execution.answers
    )
    return [
        plain(
            kind=ItemSourceKind.USER_ANSWER,
            text=(
                "Answers to the questions this run raised. Produce the patch that applies "
                "them now; the confirmation record for each answered question is added by "
                "the runtime, not by you. Every decision you list is still judged in full: "
                "repeat its selected option, at least one rejected alternative with the "
                "reason it was rejected (or confidence 1.0 for a forced choice), its risks "
                "and its confidence, even when you are re-listing a decision from your "
                "previous answer.\n\n" + body
            ),
        )
    ]


def _answer_value(answer: Answer) -> str:
    """What the model is told the human chose.

    A MULTIPLE answer carries every selected option's text; the ids alone would force the
    model to look them up in its previous turn and guess.  Custom text is appended as the
    human's own addition, which is what the console's "Other" option produces.
    """
    if answer.suggestion_texts:
        chosen = " + ".join(item for item in answer.suggestion_texts if item)
    elif answer.suggestion_ids:
        chosen = " + ".join(answer.suggestion_ids)
    else:
        chosen = answer.suggestion_id or ""
    if answer.custom_text:
        return f"{chosen}; additional detail: {answer.custom_text}" if chosen else answer.custom_text
    return chosen or "confirmed as proposed"


def _prompt_versions(execution: Execution) -> dict[str, Any]:
    prompt = execution.active_prompt
    if prompt is None:
        return {execution.spec.prompt_name: 0}
    return {prompt.name: prompt.version}


def _artifact_items(context: BuiltContext) -> list[ContextItem]:
    """Context items that came from a product, which is what a derived artifact cites."""
    return [item for item in context.items if item.ref.startswith("artifact:")]


def artifact_ids(items: Sequence[ContextItem]) -> list[str]:
    return [item.ref[len("artifact:") :] for item in items]


def _similar(
    request: AgentRunRequest, entries: SelectedEntries | None
) -> tuple[list[dict[str, Any]], Issue | None]:
    """AI-AG-011: find, without a model, what the design basis already has.

    Deterministic by requirement: an agent that saw a different similar-object list on two
    runs of the same input could not be evaluated, and the Golden Project could not pin it.

    Returns ``(records, warning)``.  A discovery artifact that cannot be parsed into readable
    objects is *reported*, not swallowed: the run continues with no similar context (so a
    provider that answers fine is not failed over it), but a WARNING rides out in the result so
    a reviewer of a brownfield run can see that the reuse evidence this run was built without
    was missing rather than empty.  ``parse_discovery_result`` is the single judge of "readable"
    — the old hand-rolled ``json.loads`` here agreed with nothing else in the subsystem.
    """
    if not request.discovery_result_artifact_id:
        return [], None
    payload, _classification, _media = artifacts.read_bytes(
        request.discovery_result_artifact_id
    )
    try:
        document = json.loads(payload.decode("utf-8", "replace"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        document = None
    try:
        observed = parse_discovery_result(document)
    except DiscoveryShapeUnreadable as exc:
        warning = Issue(
            issue_id="ai-similar-001",
            rule_id="AI-AG-011",
            level="WARNING",
            message=(
                "the discovery result for this run could not be read, so no similar objects "
                "were compared and this run carries no reuse evidence; the design basis may "
                "already hold objects the agent would recreate (AI-AG-011)"
            ),
            details={"reason": str(exc.message), "artifactId": request.discovery_result_artifact_id},
        )
        return [], warning
    proposed = [
        ObjectRecord(
            kind=str(body.get("dhis2Type") or entry_section(entry) or ""),
            code=body.get("code"),
            name=str(body.get("displayName") or body.get("name") or ""),
        )
        for entry in (entries.entries if entries is not None else [])
        if isinstance(entry, dict)
        for body in [entry_body(entry)]
    ]
    if not proposed:
        return [], None
    hits = find_similar(
        proposed,
        observed,
        SearchThresholds(
            name=settings().similar_name_threshold,
            options=settings().similar_option_threshold,
            max_results=settings().similar_max_results,
        ),
    )
    return hits_to_context_records(item for items in hits.values() for item in items), None


def _frozen(turn: Turn) -> bool:
    return bool(turn.semantics and turn.semantics.mapping_frozen)


def _is_rejected(outcome: PatchOutcome) -> bool:
    return str(getattr(outcome.status, "value", outcome.status)).upper() == "REJECTED"


def _transport_report(error: FactoryError) -> ValidationReport:
    report = ValidationReport()
    report.stopped_at = LADDER_ENVELOPE
    report.add(
        Finding(
            rule_id=error.error_code,
            ladder=LADDER_ENVELOPE,
            message=f"the provider could not answer: {error.message}",
        )
    )
    return report


def _rejection_report(rejection: PatchRejected) -> ValidationReport:
    from factory_intelligence.output.errors import LADDER_BLUEPRINT

    report = ValidationReport()
    report.stopped_at = LADDER_BLUEPRINT
    for issue in rejection.details.get("issues", []) or []:
        report.add(
            Finding(
                rule_id=str(issue.get("rule_id") or "BP-REJECT"),
                ladder=LADDER_BLUEPRINT,
                message=str(issue.get("description") or rejection.message),
                location=issue.get("object_key"),
            )
        )
    if not report.findings:
        report.add(
            Finding(
                rule_id="BP-REJECT",
                ladder=LADDER_BLUEPRINT,
                message=str(rejection.message),
            )
        )
    return report


def _ceiling_report(previous: ValidationReport, limit: int) -> ValidationReport:
    report = previous
    report.add(
        Finding(
            rule_id="AI-CALLS-001",
            ladder=LADDER_ENVELOPE,
            message=f"the run reached its ceiling of {limit} model calls",
        )
    )
    report.stopped_at = LADDER_ENVELOPE
    return report


def _refusal_text(report: ValidationReport) -> str:
    return "the agent answer was refused: " + "; ".join(report.messages())[:500]


__all__ = [
    "Execution",
    "Turn",
    "execute_turn",
    "prepare",
    "commit",
    "select",
    "failed",
    "policy_denied",
    "cancel_result",
    "budget_exceeded",
]
