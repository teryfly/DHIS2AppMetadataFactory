"""In-stage dialogue (AI-CHAT-001/002/003) — one synchronous turn per message.

The Review Assistant answers inside the stage the person is already in: the context is built
the same way a run builds it (AI-RT-003), the conversation so far is folded in before this
turn's message, and the answer is *advisory* unless the stage's own agent is delivered, a
draft is open and the mapping is not frozen (AI-CHAT-002).  A patch that is permitted goes
through the same ladder a run's patch goes through — S2 accepts or rejects it exactly as it
would for ``start_agent_run``, because "the chat wrote it" must not be a second, softer
validation path.

A turn is a model call, so this is one function call and one answer rather than a run: S1
shows the reply in the panel the person is typing into.  ``runtime/explain.py`` is the
precedent for that shape, and it is also why the transcript, not a run row, is what makes the
next turn continuous (AI-CHAT-003).

Three things this module deliberately does *not* do:

* it does not read S1's Questions: AI-RT-003 lists "every question already open on the stage"
  as context input, but a Question is S1's row (D-14) and ``stage_chat``'s SRS signature carries
  no question list, so the open questions reach the model through S1's own prompt text rather
  than through a second ownership of the same object;
* it does not detect PHI in the person's message — that is ``desensitize``'s M4 job; what it
  does is classify the message as operator input and let the Policy Engine decide whether the
  chosen provider may receive it (AI-CHAT-003), refusing rather than dropping it silently;
* it does not delete history when cleared: the session row flips ``cleared`` and the next turn
  starts a new artifact version, so what was said remains auditable (00 doc 6.6).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from factory_blueprint.api import DesignStage
from factory_foundation.api import (
    ArtifactInfo,
    CancellationToken,
    DataClassification,
    FactoryError,
    IdempotencyStatus,
    NotFoundError,
    ProgressReporter,
    new_id,
    structure_hash,
    unit_of_work,
)
from sqlalchemy.orm import Session

from factory_intelligence import artifacts
from factory_intelligence.agents.checks.base import CheckContext
from factory_intelligence.agents.spec import AgentSpec, spec_for
from factory_intelligence.config import settings
from factory_intelligence.context.builder import BuiltContext, build, continue_transcript
from factory_intelligence.context.fence import plain
from factory_intelligence.context.thresholds import from_entries, retrieve
from factory_intelligence.cost.usage import CallContext, record_call
from factory_intelligence.deps import get_blueprint, get_clock, get_llm_provider
from factory_intelligence.dto.agent import AgentOutput
from factory_intelligence.dto.request import AgentRunRequest
from factory_intelligence.dto.result import ChatReply
from factory_intelligence.enums import AgentType, ItemSourceKind
from factory_intelligence.errors import (
    ContextTooLarge,
    OutputInvalid,
    PromptNotActivated,
    ProviderUnavailable,
)
from factory_intelligence.feature import agent_enabled, require_capability
from factory_intelligence.llm.errors import LlmError
from factory_intelligence.llm.registry import candidates_for_agent, registered_model
from factory_intelligence.llm.types import AgentHint, StructuredRequest
from factory_intelligence.models.deferred import ChatSessionRow
from factory_intelligence.output.schema import envelope_schema, section_containers
from factory_intelligence.output.validate import validate
from factory_intelligence.policy.model import ContextItem, EffectivePolicy
from factory_intelligence.policy.orgpolicy import effective_policy
from factory_intelligence.policy.selection import Selection, select
from factory_intelligence.prompts import activation as prompt_activation
from factory_intelligence.registries import IdemScope
from factory_intelligence.runtime import patching
from factory_intelligence.runtime.questions import canonicalise

#: The stage's own agent for a chat turn that may patch, in declaration order: MAPPING is the
#: first spec whose stage is MAPPING, so a chat at MAPPING writes through the MAPPING agent
#: rather than through METADATA_DESIGN, which owns the same stage for a different output.
_STAGE_AGENT: dict[str, tuple[AgentType, ...]] = {
    DesignStage.REQUIREMENTS.value: (AgentType.REQUIREMENTS, AgentType.REVIEW),
    DesignStage.DOMAIN.value: (AgentType.DOMAIN, AgentType.REVIEW),
    DesignStage.MAPPING.value: (AgentType.MAPPING, AgentType.METADATA_DESIGN, AgentType.REVIEW),
    DesignStage.PAGE_DESIGN.value: (AgentType.API, AgentType.UI, AgentType.REVIEW),
}

CHAT_ARTIFACT_TYPE = "AGENT_OUTPUT"

_TASK = (
    "You are the Review Assistant for this stage. Answer the person's question about the "
    "material above, in their language, naming the businessKey or entry you mean. "
    "If — and only if — the person asked for a change and the stage rules allow you to write "
    "the sections named there, express that change as a patch against the working draft; a "
    "human Gate still decides on it. Otherwise put the change you would recommend into the "
    "summary and raise a question instead. Never state that a patch was submitted unless you "
    "actually returned one in the patch field."
)


@dataclass
class _Turn:
    """What the gap produces for one chat turn, mirroring ``runtime.engine.Turn``'s role."""

    context: BuiltContext
    thresholds: Any
    policy: EffectivePolicy
    selection: Selection
    model: Any
    provider: Any
    spec: AgentSpec
    base_version: str
    mapping_frozen: bool
    draft_id: str | None
    answer: AgentOutput | None = None
    messages_sent: int = 0
    #: the model proposed a change on a turn that was not allowed to write one
    patch_dropped: bool = False


@dataclass
class Transcript:
    """AI-CHAT-003's readable session: the turn list plus the artifact they came from."""

    project_id: str
    stage: str
    session_id: str
    cleared: bool
    context_artifact_id: str | None
    turns: list[dict[str, Any]] = field(default_factory=list)
    classification: DataClassification = DataClassification.INTERNAL


# --- the public entry points ---------------------------------------------------


def chat(
    project_id: str,
    stage: str,
    draft_id: str | None,
    message: str,
    environment_policies: Mapping[str, Any],
    operator: str,
    *,
    language: str | None = None,
    idempotency_key: str,
    progress: ProgressReporter | None = None,
    cancel: CancellationToken | None = None,
) -> ChatReply:
    """One turn of in-stage dialogue (AI-CHAT-001), answered by the Review Assistant.

    Marked external I/O at the ``api.py`` boundary: it reads the transcript and calls a model,
    so S1 must not hold a transaction open across it (00 doc 6.2).  A repeated key answers the
    turn already paid for (00 doc 6.4).
    """
    require_capability("stage_chat", "in-stage dialogue")
    design_stage = _design_stage(stage)
    text = (message or "").strip()
    if not text:
        raise NotFoundError(
            "an in-stage chat turn needs a message",
            details={"projectId": project_id, "stage": design_stage.value},
        )

    handled, replayed = _begin(idempotency_key, project_id, design_stage, text, operator)
    if handled:
        return replayed

    request = _request(
        project_id,
        design_stage,
        draft_id,
        environment_policies,
        operator,
        idempotency_key,
        language=language,
    )
    try:
        turn = _prepare(request, design_stage, draft_id, text, progress)
        answer = _ask(request, turn, text, progress, cancel)
    except FactoryError as exc:
        _fail(idempotency_key, exc.error_code)
        raise

    patch_ids, rejection = _apply_patch(request, turn, answer)
    raised, _mapping = canonicalise(answer.questions)
    reply = ChatReply(answer=_answer_text(answer, turn, rejection), patch_ids=patch_ids, questions=raised)
    # the transcript is what makes the *next* turn continuous, so it is written before the
    # answer is handed back (AI-CHAT-003) and the idempotency key is spent in the same tx
    _record_turn(request, turn, answer, reply, idempotency_key)
    return reply


def clear(project_id: str, stage: str, *, session: Session | None = None) -> Transcript:
    """AI-CHAT-003's clear: the session starts again, the old transcript stays readable."""
    require_capability("clear_chat", "in-stage dialogue")
    design_stage = _design_stage(stage)

    def _write(sess: Session) -> Transcript:
        row = _row_for(sess, project_id, design_stage.value, create=False)
        if row is None:
            return Transcript(project_id=project_id, stage=design_stage.value, session_id="", cleared=False, context_artifact_id=None, turns=[])
        row.cleared = True
        row.context_artifact_id = None
        sess.flush()
        return Transcript(
            project_id=project_id,
            stage=design_stage.value,
            session_id=row.id,
            cleared=True,
            context_artifact_id=None,
            turns=[],
        )

    if session is not None:
        return _write(session)
    with unit_of_work() as sess:
        return _write(sess)


def history(project_id: str, stage: str) -> Transcript:
    """The session as it stands, for S1's context header and history panel (FR-UI-002).

    No ``session`` parameter, deliberately: the transcript's bytes are read from object
    storage, so this is external I/O and cannot enlist in a caller's transaction (00 doc 6.2).
    """
    design_stage = _design_stage(stage)

    def _read(session: Session) -> tuple[str | None, bool, str | None]:
        row = _row_for(session, project_id, design_stage.value, create=False)
        if row is None:
            return None, False, None
        return str(row.id), bool(row.cleared), row.context_artifact_id

    with unit_of_work() as session:
        row_id, cleared, artifact_id = _read(session)
    if row_id is None:
        return Transcript(
            project_id=project_id, stage=design_stage.value, session_id="",
            cleared=False, context_artifact_id=None, turns=[],
        )
    if artifact_id is None:
        return Transcript(
            project_id=project_id, stage=design_stage.value, session_id=row_id,
            cleared=cleared, context_artifact_id=None, turns=[],
        )
    document, classification = artifacts.read_json(artifact_id)
    turns = [dict(item) for item in document.get("turns") or [] if isinstance(item, dict)]
    return Transcript(
        project_id=project_id,
        stage=design_stage.value,
        session_id=row_id,
        cleared=cleared,
        context_artifact_id=artifact_id,
        turns=[] if cleared else turns,
        classification=classification,
    )


# --- the gap: context, policy, model -------------------------------------------


def _prepare(
    request: AgentRunRequest,
    stage: DesignStage,
    draft_id: str | None,
    message: str,
    progress: ProgressReporter | None,
) -> _Turn:
    # the freeze is read first, because it decides *who* answers: with the mapping frozen the
    # stage's own agent has nothing it may write, so the Review Assistant answers (AI-CHAT-002)
    semantics = _semantics(request.project_id)
    frozen = bool(semantics and getattr(semantics, "mapping_frozen", False))
    patch_draft = draft_id if (draft_id and not frozen) else None
    spec, patchable = _turn_spec(stage, draft_id=patch_draft)
    patchable = patchable and draft_id is not None and not frozen
    draft = get_blueprint().draft(str(draft_id)) if patchable and draft_id else None
    if draft is not None:
        # the same base S2 demands of a run's first patch (engine.py:254): a draft that has
        # never been materialised cannot answer "which version is this operation against"
        get_blueprint().prepare_draft_base(request.project_id, str(draft.draft_id))
    base_version = str(getattr(draft, "base_version", "") or "") if draft is not None else _current_version(request.project_id)

    entries = retrieve(
        request,
        sections=spec.writable_section_values if patchable else (),
        read_only_sections=tuple(section.value for section in spec.read_only_sections),
        draft_materialised=draft is not None,
    )
    thresholds = from_entries(entries)
    prior = _prior_messages(request.project_id, stage.value)

    active_prompt = prompt_activation.current_activation(spec.agent_type)
    if active_prompt is None:
        # AI-REG-002: the dialogue answers with the same governance a run does, so an
        # un-activated agent refuses rather than answering from an inert placeholder -- the
        # Review Assistant's advice would otherwise carry none of the authored prompt it is
        # supposed to reason from, while still costing a model call.
        raise PromptNotActivated(
            f"the stage dialogue cannot run: the {spec.agent_type.value} agent has no "
            "activated prompt (AI-REG-002)",
            details={"agentType": spec.agent_type.value, "capability": "stage_chat"},
        )
    context = build(
        request=request,
        active_prompt=active_prompt,
        stage_rules=spec.stage_rules(mapping_frozen=frozen),
        entries=entries,
        inputs=[],
        thresholds=thresholds,
    )
    if prior:
        continue_transcript(context, [m for m in prior], classification=DataClassification.INTERNAL)
    _push_message(context, message)

    if context.byte_size > settings().max_context_bytes:
        raise ContextTooLarge(
            f"this stage's dialogue is {context.byte_size} bytes over the limit of "
            f"{settings().max_context_bytes}; S3 will not truncate a conversation to answer it",
            details={"bytes": context.byte_size, "turns": len(prior) // 2 + 1},
        )
    if progress is not None:
        progress.report(step="chat.context", completed_fraction=0.2, message="context built")

    policy = _policy(request)
    with unit_of_work() as session:
        candidates = candidates_for_agent(
            spec.agent_type.value, needed_tokens=context.token_estimate, session=session
        )
    selection = select(context.items, policy, candidates, needed_tokens=context.token_estimate)
    if selection.chosen is None:
        # AI-CHAT-003: the message is refused rather than quietly dropped from context
        raise ProviderUnavailable(
            "no model may receive this stage's dialogue under the effective policy: "
            + "; ".join(selection.reasons)[:400],
            details={"denials": selection.payloads, "capability": "stage_chat"},
        )
    with unit_of_work() as session:
        model = registered_model(f"{selection.chosen.provider}/{selection.chosen.model}", session=session)
    return _Turn(
        context=context,
        thresholds=thresholds,
        policy=policy,
        selection=selection,
        model=model,
        provider=get_llm_provider(model.provider_info()),
        spec=spec,
        base_version=base_version,
        mapping_frozen=frozen,
        draft_id=draft_id if patchable else None,
    )


def _ask(
    request: AgentRunRequest,
    turn: _Turn,
    message: str,
    progress: ProgressReporter | None,
    cancel: CancellationToken | None,
) -> AgentOutput:
    """One logical call with AI-RT-011's corrective retry, validated on the run's own ladder."""
    provider = turn.provider
    if provider is None:  # pragma: no cover - selection refused earlier
        raise ProviderUnavailable("no provider was selected for this dialogue turn")
    messages = [*turn.context.messages]
    for attempt in range(settings().corrective_attempts + 1):
        if cancel is not None:
            cancel.raise_if_cancelled()
        started = get_clock().now()
        try:
            response = provider.generate_structured(
                StructuredRequest(
                    messages=tuple(messages),
                    json_schema=envelope_schema(),
                    schema_name="review.chat",
                    temperature=settings().default_temperature,
                    max_output_tokens=settings().default_max_output_tokens,
                    agent_hint=AgentHint(agent_type=turn.spec.agent_type.value, stage=request.stage),
                )
            )
        except LlmError as exc:
            _record(request, turn, None, started, error=str(exc.message), request_id=exc.request_id)
            raise
        turn.messages_sent += 1
        _record(request, turn, response, started, error=None)
        if progress is not None:
            progress.report(step="chat.answered", completed_fraction=0.7, message="model answered")
        payload = _payload_of(response)
        if turn.draft_id is None:
            # AI-AG-008 and AI-AG-006 together: a turn that may not patch does not get to
            # smuggle one past the Gate — the patch part is dropped before validation, the
            # same way the explanation path drops it
            operations = ((payload.get("patch") or {}).get("operations") or []) if isinstance(
                payload.get("patch"), dict
            ) else []
            if operations:
                turn.patch_dropped = True
            payload = {**payload, "patch": None}
        answer, report = validate(payload, spec=turn.spec, context=_check_context(request, turn))
        if answer is not None and report.ok:
            return answer
        if attempt >= settings().corrective_attempts:
            raise OutputInvalid(
                "the dialogue answer was refused: " + "; ".join(report.messages())[:400],
                details={"findings": report.as_payload()["errors"]},
            )
        messages.append(plain(kind=ItemSourceKind.STAGE_RULES, text=report.as_prompt_block()))
    raise AssertionError("unreachable")  # pragma: no cover


def _apply_patch(
    request: AgentRunRequest, turn: _Turn, answer: AgentOutput
) -> tuple[list[str], str | None]:
    """Submit the turn's patch, if this turn was allowed to have one (AI-CHAT-002).

    Returns ``(patch_ids, rejection)``: a rejection is not an error, it is the answer S1 shows,
    because S2 refusing a patch is a normal outcome a person has to read rather than a 500.
    """
    if turn.draft_id is None or not answer.has_patch:
        return [], None
    if not turn.spec.accepts_patch(mapping_frozen=turn.mapping_frozen):
        return [], "the stage rules do not accept a patch from this agent"
    patch = patching.build_patch(
        answer,
        run_id=f"chat:{request.idempotency_key[:24]}",
        base_version=turn.base_version,
        rationale="in-stage dialogue",
    )
    key = patching.patch_idempotency_key(f"chat:{request.idempotency_key[:24]}", turn.messages_sent)
    try:
        with unit_of_work() as session:
            outcome = patching.submit_with_pool(
                project_id=request.project_id,
                draft_id=turn.draft_id,
                stage=DesignStage(request.stage),
                patch=patch,
                idempotency_key=key,
                session=session,
                design_reference_environment_key=None,
            )
    except patching.PoolReplenishNeeded as needed:
        return [], f"the draft needs UIDs before this patch can be retried: {needed.details}"
    except FactoryError as failure:
        return [], f"S2 refused the patch ({failure.error_code}): {failure.message}"[:500]
    if _is_rejected(outcome):
        return [], "S2 refused the patch: " + "; ".join(
            str(getattr(item, "message", item)) for item in list(getattr(outcome, "issues", []) or [])[:3]
        )
    return [str(outcome.patch_id)], None


# --- the durable turn (AI-CHAT-003) --------------------------------------------


def _record_turn(
    request: AgentRunRequest,
    turn: _Turn,
    answer: AgentOutput,
    reply: ChatReply,
    idempotency_key: str,
) -> str:
    """Write the transcript artifact and the session row, then spend the key with the answer."""
    stage = request.stage
    prior_document, prior_classification = _prior_document(request.project_id, stage)
    document = {
        "kind": "chatTranscript",
        "projectId": request.project_id,
        "stage": stage,
        "turns": prior_document
        + [
            {
                "role": "user",
                "content": _last_user_message(turn),
                "at": get_clock().now().isoformat(),
            },
            {
                "role": "assistant",
                "content": reply.answer,
                "patchIds": list(reply.patch_ids),
                "questions": [question.model_dump(mode="json") for question in reply.questions],
                "agentType": turn.spec.agent_type.value,
                "promptVersion": 0 if turn.spec.prompt_name else 0,
                "at": get_clock().now().isoformat(),
            },
        ],
        "contextDigest": turn.context.digest(),
        "model": f"{turn.selection.chosen.provider}/{turn.selection.chosen.model}"
        if turn.selection.chosen is not None
        else None,
    }
    classification = artifacts.source_floor(
        turn.context.classifications(), extra=prior_classification
    )
    handle = artifacts.stage_json(request.project_id, CHAT_ARTIFACT_TYPE, document)
    with unit_of_work() as session:
        row = _row_for(session, request.project_id, stage, create=True)
        if row is None:  # pragma: no cover - create=True always returns a row
            raise FactoryError("the stage's chat session row vanished")
        previous = row.context_artifact_id
        registered: ArtifactInfo = artifacts.register_staged(
            handle,
            project_id=request.project_id,
            artifact_type=CHAT_ARTIFACT_TYPE,
            data_classification=classification,
            source_artifact_ids=[previous] if previous else [],
            workflow_id=None,
            build_record={
                "kind": "chatTranscript",
                "sessionId": row.id,
                "agentType": turn.spec.agent_type.value,
                "promptName": turn.spec.prompt_name,
                "recordedBy": "factory_intelligence",
            },
            prev_version_id=previous,
            session=session,
        )
        row.cleared = False
        row.context_artifact_id = str(registered.id)
        session.flush()
        idempotency_complete(
            idempotency_key,
            {
                "answer": reply.answer,
                "patchIds": list(reply.patch_ids),
                "contextArtifactId": str(registered.id),
            },
            session=session,
        )
    return str(registered.id)


# --- helpers ------------------------------------------------------------------


def _turn_spec(stage: DesignStage, *, draft_id: str | None) -> tuple[AgentSpec, bool]:
    """The agent answering this turn, and whether it may patch.

    A delivered agent *of this stage* answers when a patch is on the table, because its prompt
    is the one that knows the section rules a patch is checked against.  Otherwise the Review
    Assistant answers (AI-AG-008: explain and suggest, never patch), which is also the answer
    for a stage whose agents are not in this build — the person still gets a real reply rather
    than a milestone error.
    """
    for candidate in _STAGE_AGENT.get(stage.value, ()):
        if candidate is AgentType.REVIEW:
            break
        if draft_id and agent_enabled(candidate):
            spec = spec_for(candidate)
            if spec.stage is stage:
                return spec, True
    return spec_for(AgentType.REVIEW), False


def _design_stage(stage: str) -> DesignStage:
    try:
        return DesignStage(str(stage))
    except ValueError as exc:
        raise NotFoundError(
            f"{stage!r} is not a design stage, so it has no dialogue (AI-CHAT-001)",
            details={"stage": stage, "known": [item.value for item in DesignStage]},
        ) from exc


def _request(
    project_id: str,
    stage: DesignStage,
    draft_id: str | None,
    environment_policies: Mapping[str, Any],
    operator: str,
    idempotency_key: str,
    *,
    language: str | None = None,
) -> AgentRunRequest:
    return AgentRunRequest(
        project_id=project_id,
        # a chat turn belongs to a stage, not to a run; these fields exist because the context
        # builder and the usage ledger key on them (same reasoning as explain.py)
        workflow_id=f"chat:{project_id}:{stage.value}",
        stage_id=stage.value,
        stage=stage.value,
        agent_type=AgentType.REVIEW.value,
        draft_id=draft_id,
        scope=[],
        input_artifact_ids=[],
        target_environment_policies=dict(environment_policies or {}),
        operator=operator,
        language=language,
        idempotency_key=idempotency_key,
    )


def _policy(request: AgentRunRequest) -> EffectivePolicy:
    with unit_of_work() as session:
        return effective_policy(
            session=session, environment_policies=request.target_environment_policies
        )


def _check_context(request: AgentRunRequest, turn: _Turn) -> CheckContext:
    return CheckContext(
        agent_type=turn.spec.agent_type.value,
        stage=request.stage,
        containers={
            section: tuple(section_containers(section))
            for section in turn.spec.writable_section_values
        },
        decision_confidence_threshold=turn.thresholds.decision,
        mapping_frozen=turn.mapping_frozen,
        similar_object_keys=(),
        api_operation_keys=(),
        phi_refs=list(turn.context.excluded_phi),
        base_version=turn.base_version,
    )


def _record(
    request: AgentRunRequest,
    turn: _Turn,
    response: Any,
    started: Any,
    *,
    error: str | None,
    request_id: str | None = None,
) -> None:
    """Every call in the ledger (AI-NFR-03), tagged as a dialogue turn (AI-COST-001)."""
    if turn.model is None:  # pragma: no cover
        return
    latency_ms = max(0, int((get_clock().now() - started).total_seconds() * 1000))
    with unit_of_work() as session:
        record_call(
            context=CallContext(
                project_id=request.project_id,
                workflow_id=request.workflow_id,
                stage=request.stage,
                agent_type=turn.spec.agent_type.value,
                run_id=None,
                sequence=turn.messages_sent,
                call_role="chat",
            ),
            model=turn.model,
            response=response,
            temperature=settings().default_temperature,
            max_output_tokens=settings().default_max_output_tokens,
            latency_ms=latency_ms,
            outcome="ERROR" if error else "OK",
            error=error,
            request_id=request_id,
            operator=request.operator,
            session=session,
        )


def _push_message(context: BuiltContext, message: str) -> None:
    """The person's message as one fenced item, so the policy sees it (AI-CHAT-003)."""
    item = plain(kind=ItemSourceKind.USER_ANSWER, text=message)
    context.messages.append(item)
    context.byte_size += len(message.encode("utf-8"))
    context.items.append(
        ContextItem(
            ref="chat:message",
            classification=DataClassification.INTERNAL,
            kind=ItemSourceKind.USER_ANSWER,
            label="stage dialogue message",
            size_bytes=len(message.encode("utf-8")),
        )
    )


def _prior_messages(project_id: str, stage: str) -> list[Any]:
    """The transcript as ChatMessages, oldest first; the system row is added by the builder."""
    from factory_intelligence.llm.types import ChatMessage
    from factory_intelligence.runtime.contextstore import raw_role

    document, _ = _prior_document(project_id, stage)
    out: list[Any] = []
    for item in document:
        role = str(item.get("role") or "")
        if role not in ("user", "assistant"):
            continue
        out.append(ChatMessage(role=raw_role(role), content=str(item.get("content") or "")))
    return out


def _prior_document(project_id: str, stage: str) -> tuple[list[dict[str, Any]], DataClassification]:
    """The session's transcript, read the way S0 requires: row in a tx, bytes outside one.

    The artifact id is taken out of the session as a plain string first: a detached ORM row
    would refresh itself on the next attribute access, and that read of *bytes* would then be
    sitting inside a unit of work, which 00 doc 6.2 forbids.
    """
    pointer: tuple[str | None, bool] = ("", False)

    def _read(session: Session) -> tuple[str | None, bool]:
        row = _row_for(session, project_id, stage, create=False)
        return (None if row is None else row.context_artifact_id, False if row is None else row.cleared)

    with unit_of_work() as session:
        pointer = _read(session)
    artifact_id, cleared = pointer
    if artifact_id is None:
        return [], DataClassification.INTERNAL
    document, classification = artifacts.read_json(artifact_id)
    turns = [dict(item) for item in document.get("turns") or [] if isinstance(item, dict)]
    return ([] if cleared else turns), classification


def _row_for(
    session: Session, project_id: str, stage: str, *, create: bool
) -> ChatSessionRow | None:
    row: ChatSessionRow | None = session.scalar(_select_session(project_id, stage))
    if row is not None or not create:
        return row
    row = ChatSessionRow(id=new_id("acht"), project_id=project_id, stage=stage, cleared=False)
    session.add(row)
    session.flush()
    return row


def _select_session(project_id: str, stage: str) -> Any:
    from sqlalchemy import select

    return select(ChatSessionRow).where(
        ChatSessionRow.project_id == project_id, ChatSessionRow.stage == stage
    )


def _semantics(project_id: str) -> Any:
    try:
        return get_blueprint().project_semantics(project_id)
    except FactoryError:
        # a project with no semantics yet simply has nothing frozen
        return None


def _current_version(project_id: str) -> str:
    try:
        return str(get_blueprint().current_version(project_id).version)
    except FactoryError:
        return "0.0.0"


def _payload_of(response: Any) -> dict[str, Any]:
    payload = getattr(response, "payload", None)
    if isinstance(payload, dict):
        return dict(payload)
    import json

    try:
        parsed = json.loads(getattr(response, "raw_text", "") or "{}")
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _is_rejected(outcome: Any) -> bool:
    return str(getattr(outcome.status, "value", outcome.status)).upper() == "REJECTED"


def _answer_text(answer: AgentOutput, turn: _Turn, rejection: str | None) -> str:
    parts = [answer.summary.strip()]
    if rejection:
        parts.append(f"The change was not applied to the draft: {rejection}")
    elif turn.patch_dropped:
        parts.append(
            "This stage cannot submit a change from the dialogue, so it is advice only."
        )
    return "\n\n".join(part for part in parts if part)


def _last_user_message(turn: _Turn) -> str:
    for message in reversed(turn.context.messages):
        if message.role == "user" and message.content:
            return str(message.content)
    return ""


def _derived_key(project_id: str, stage: str, message: str, operator: str) -> str:
    return f"chat:{structure_hash({'project': project_id, 'stage': stage, 'message': message, 'operator': operator})[7:31]}"


def _begin(key: str, project_id: str, stage: DesignStage, message: str, operator: str) -> tuple[bool, ChatReply]:
    """Spend the key.  ``(handled, replay)`` — handled means this turn was already answered."""
    from factory_foundation.api import idempotency

    with unit_of_work() as session:
        begun = idempotency.begin(
            IdemScope.STAGE_CHAT,
            key,
            structure_hash(
                {
                    "projectId": project_id,
                    "stage": stage.value,
                    "message": message,
                    "operator": operator,
                }
            ),
            session=session,
        )
        if begun.created:
            return False, ChatReply()
        if begun.status is IdempotencyStatus.IN_PROGRESS:
            raise ProviderUnavailable(
                f"a dialogue turn for key {key!r} is still being produced",
                details={"idempotencyKey": key, "status": begun.status.value},
            )
        first = begun.first_result or {}
        if first.get("answer") is not None:
            return True, ChatReply(
                answer=str(first["answer"]),
                patch_ids=[str(item) for item in first.get("patchIds") or []],
            )
        return False, ChatReply()


def idempotency_complete(key: str, result: Mapping[str, Any], *, session: Session) -> None:
    from factory_foundation.api import idempotency

    idempotency.complete(IdemScope.STAGE_CHAT, key, dict(result), session=session)


def _fail(key: str, error_code: str) -> None:
    from factory_foundation.api import idempotency

    with unit_of_work() as session:
        idempotency.fail(IdemScope.STAGE_CHAT, key, error_code=error_code, session=session)


__all__ = ["ChatReply", "Transcript", "chat", "clear", "history"]
