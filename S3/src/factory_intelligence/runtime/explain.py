"""``explain_issues`` — the Metadata Design Agent's explanation mode (AI-AG-014).

Input is a ``VALIDATION_REPORT`` or ``DHIS2_IMPORT_REPORT`` artifact; output is an
``AGENT_OUTPUT`` product holding the explanation and the suggestions.  The function returns
that artifact id, not prose, for two reasons: S1's port types ``str | None`` and prose put
in front of a user without a Gate would breach AI-BR-01 and SRS 1.1's "S3 的输出永远只是候
选"; and a refusal has nowhere to go in a string, so a denial here *raises* (see
:func:`explain`) rather than returning ``None`` — ``None`` means exactly one thing: the
report had nothing to explain.

After ARCHITECTURE_GATE the mapping is frozen, so this path produces no patch at all
(AI-AG-006): any ``patch`` the model returns is dropped before the output is recorded, and
the run of checks that would have refused it is what tells the model, on its one corrective
retry, that this is explanation only.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from factory_blueprint.api import DesignStage
from factory_foundation.api import (
    CancellationToken,
    FactoryError,
    IdempotencyStatus,
    NotFoundError,
    ProgressReporter,
    get_artifact,
    idempotency,
    structure_hash,
    unit_of_work,
)

from factory_intelligence import artifacts
from factory_intelligence.agents.spec import spec_for
from factory_intelligence.config import settings
from factory_intelligence.context.builder import InputArtifact, build
from factory_intelligence.context.thresholds import from_entries, retrieve
from factory_intelligence.cost.usage import CallContext, record_call
from factory_intelligence.deps import get_clock, get_llm_provider
from factory_intelligence.dto.request import AgentRunRequest
from factory_intelligence.enums import AgentType, ItemSourceKind
from factory_intelligence.errors import (
    ContextTooLarge,
    OutputInvalid,
    PromptNotActivated,
    ProviderUnavailable,
)
from factory_intelligence.feature import require_agent
from factory_intelligence.llm.errors import LlmError
from factory_intelligence.llm.registry import candidates_for_agent, registered_model
from factory_intelligence.llm.types import AgentHint, StructuredRequest
from factory_intelligence.output.errors import LADDER_ENVELOPE, Finding, ValidationReport
from factory_intelligence.output.schema import envelope_schema
from factory_intelligence.policy.model import ContextItem
from factory_intelligence.policy.orgpolicy import effective_policy
from factory_intelligence.policy.selection import select
from factory_intelligence.prompts import activation as prompt_activation
from factory_intelligence.registries import IdemScope
from factory_intelligence.runtime.contextstore import stage as stage_document
from factory_intelligence.runtime.engine import Turn

#: The report types SRS AI-AG-014 accepts.  Anything else is refused: explaining an
#: arbitrary artifact would be a general-purpose document reader, which is not in scope.
REPORT_TYPES = frozenset({"VALIDATION_REPORT", "DHIS2_IMPORT_REPORT"})

EXPLANATION_TASK = (
    "Explain the findings below for a person who must fix them, and recommend the earliest "
    "design stage that has to change. Propose no patch: the mapping is frozen, and this "
    "answer is advisory. For each distinct cause give one recommendation naming the "
    "businessKey or object involved, and put anything you cannot resolve from the findings "
    "into questions."
)


def explain(
    project_id: str,
    report_artifact_id: str,
    *,
    environment_policies: Mapping[str, Any] | None = None,
    operator: str = "system",
    language: str | None = None,
    idempotency_key: str | None = None,
    progress: ProgressReporter | None = None,
    cancel: CancellationToken | None = None,
) -> str | None:
    """Return the ``AGENT_OUTPUT`` artifact id, or ``None`` when there is nothing to explain.

    Marked external I/O: it reads an artifact and calls a model, so S1 must call it with no
    transaction open (00 doc 6.2).  A repeated key answers with the explanation already paid
    for (00 doc 6.4), which is why the key is spent in the same transaction as the artifact.
    """
    require_agent(AgentType.METADATA_DESIGN)
    _check_report_type(_require_report(report_artifact_id))
    key = idempotency_key or _derived_key(project_id, report_artifact_id)
    handled, replayed = _begin(key, project_id, report_artifact_id, operator)
    if handled:
        return replayed
    findings = _findings_of(report_artifact_id)
    if not findings:
        # The one case where ``None`` is the honest answer: an empty report has no prose to
        # generate, and paying for a call to say "nothing wrong" would be theatre.  The key is
        # completed with that answer, so a retry gets the same ``None`` and no second call.
        with unit_of_work() as session:
            idempotency.complete(IdemScope.EXPLAIN_ISSUES, key, {"artifactId": None}, session=session)
        return None

    request = _request(
        project_id,
        report_artifact_id,
        environment_policies,
        operator,
        key,
        language=language,
    )
    spec = spec_for(AgentType.METADATA_DESIGN)
    try:
        turn = _prepare(request, spec, findings, progress)
        answer, calls = _ask(request, turn, findings, progress, cancel)
    except FactoryError as exc:
        _fail(key, exc.error_code)
        raise
    prompt = prompt_activation.current_activation(request.agent_type)
    output = stage_document(
        project_id,
        _output_document(
            request,
            turn,
            answer,
            report_artifact_id,
            calls,
            {prompt.name: prompt.version}
            if prompt
            else {spec_for(AgentType.METADATA_DESIGN).prompt_name: 0},
        ),
    )
    with unit_of_work() as session:
        registered = artifacts.register_staged(
            output,
            session=session,
            project_id=project_id,
            artifact_type="AGENT_OUTPUT",
            data_classification=artifacts.source_floor(turn.context.classifications()),
            source_artifact_ids=[report_artifact_id],
            build_record={
                "kind": "issueExplanation",
                "reportArtifactId": report_artifact_id,
                "agentType": AgentType.METADATA_DESIGN.value,
                "model": turn.model.ref if turn.model else None,
                "modelCalls": calls,
            },
        )
        idempotency.complete(
            IdemScope.EXPLAIN_ISSUES,
            key,
            {"artifactId": registered.id},
            session=session,
        )
    return registered.id


def _derived_key(project_id: str, report_artifact_id: str) -> str:
    return f"explain:{structure_hash({'project': project_id, 'report': report_artifact_id})[7:31]}"


def _digest(project_id: str, report_artifact_id: str, operator: str) -> str:
    """Caller intent only: the model registry and the policy of the moment are not it."""
    return structure_hash(
        {"projectId": project_id, "reportArtifactId": report_artifact_id, "operator": operator}
    )


def _begin(key: str, project_id: str, report_artifact_id: str, operator: str) -> tuple[bool, str | None]:
    """Spend the key.  ``(handled, artifact_id)`` — handled means the answer already exists."""
    with unit_of_work() as session:
        begun = idempotency.begin(
            IdemScope.EXPLAIN_ISSUES,
            key,
            _digest(project_id, report_artifact_id, operator),
            session=session,
        )
        if begun.created:
            return False, None
        if begun.status is IdempotencyStatus.IN_PROGRESS:
            # another attempt is inside the model call; there is no answer yet, and a caller
            # that retries is exactly what the key is for (00 doc 6.4)
            raise ProviderUnavailable(
                f"an explanation for key {key!r} is still being produced",
                details={"idempotencyKey": key, "status": begun.status.value},
            )
        first = begun.first_result
        if first is not None and "artifactId" in first:
            artifact_id = first.get("artifactId")
            return True, str(artifact_id) if artifact_id else None
        return False, None  # a spent key with no answer: that attempt failed, so this one runs


def _fail(key: str, error_code: str) -> None:
    with unit_of_work() as session:
        idempotency.fail(IdemScope.EXPLAIN_ISSUES, key, error_code=error_code, session=session)


# --- setup ----------------------------------------------------------------------


def _require_report(artifact_id: str) -> Any:
    return get_artifact(artifact_id)


def _check_report_type(info: Any) -> Any:
    if info.type not in REPORT_TYPES:
        raise NotFoundError(
            f"artifact {info.id} is a {info.type}; issue explanations read a "
            f"VALIDATION_REPORT or DHIS2_IMPORT_REPORT (AI-AG-014)",
            artifact_id=str(info.id),
            details={"allowed": sorted(REPORT_TYPES)},
        )
    return info


def _findings_of(artifact_id: str) -> list[dict[str, Any]]:
    """The report's issues, read tolerantly.

    S2 and S4 shape their reports differently (S2 writes ``issues`` with ``level``; an import
    report typically carries ``errors`` or per-object results), and S3 must not need an S4
    release to explain an import failure.  Anything unrecognisable yields no findings, which
    the caller reports as ``None`` rather than inventing an explanation.
    """
    document, _classification = artifacts.read_json(artifact_id)
    for key in ("issues", "errors", "findings", "results", "blockingIssues"):
        value = document.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            return [dict(item) for item in value.values() if isinstance(item, dict)]
    return [document] if document else []


def _request(
    project_id: str,
    report_artifact_id: str,
    environment_policies: Mapping[str, Any] | None,
    operator: str,
    idempotency_key: str | None,
    *,
    language: str | None = None,
) -> AgentRunRequest:
    return AgentRunRequest(
        project_id=project_id,
        # an explanation is not a workflow stage; these fields exist only because the context
        # builder and the usage ledger key on them, and inventing a second path for each would
        # split the accounting for no benefit
        workflow_id=f"explain:{report_artifact_id}",
        stage_id=report_artifact_id,
        stage="MAPPING",
        agent_type=AgentType.METADATA_DESIGN.value,
        draft_id=None,
        scope=[],
        input_artifact_ids=[report_artifact_id],
        target_environment_policies=dict(environment_policies or {}),
        operator=operator,
        language=language,
        idempotency_key=idempotency_key
        or f"explain:{structure_hash({'project': project_id, 'report': report_artifact_id})[7:31]}",
    )


def _prepare(
    request: AgentRunRequest, spec: Any, findings: list[dict[str, Any]], progress: ProgressReporter | None
) -> Turn:
    from factory_intelligence.context.fence import plain

    report_text = plain(kind=ItemSourceKind.INPUT_ARTIFACT, text=_as_text(findings))
    entries = retrieve(
        request,
        sections=spec.writable_section_values,
        read_only_sections=tuple(section.value for section in spec.read_only_sections),
        draft_materialised=False,
    )
    thresholds = from_entries(entries)
    active_prompt = prompt_activation.current_activation(request.agent_type)
    if active_prompt is None:
        # AI-REG-002: the explanation is a governed answer like any other, so an un-activated
        # Metadata Design agent refuses rather than explaining from an inert placeholder.  This
        # is a side-channel that *raises* (see the module docstring): a refusal here has nowhere
        # to go in a returned artifact id, so it surfaces as an error S1 maps like any AiError.
        raise PromptNotActivated(
            "issue explanation cannot run: the "
            f"{request.agent_type} agent has no activated prompt (AI-REG-002)",
            details={"agentType": request.agent_type, "capability": "explain_issues"},
        )
    context = build(
        request=request,
        active_prompt=active_prompt,
        stage_rules="",
        entries=entries,
        inputs=[
            InputArtifact(
                artifact_id=request.input_artifact_ids[0],
                classification=artifacts.classification_of(request.input_artifact_ids[0]),
                content="",
                media_type="application/json",
            )
        ],
        thresholds=thresholds,
    )
    # the findings themselves are the content worth explaining, so they are carried as one
    # fenced item rather than relying on the report artifact being re-read by the model
    context.messages.append(report_text)
    context.byte_size += len(report_text.content.encode("utf-8"))
    context.items.append(
        ContextItem(
            ref=f"report:{request.input_artifact_ids[0]}",
            classification=artifacts.classification_of(request.input_artifact_ids[0]),
            kind=ItemSourceKind.INPUT_ARTIFACT,
            label="validation findings",
            size_bytes=len(report_text.content.encode("utf-8")),
        )
    )
    if context.byte_size > settings().max_context_bytes:
        raise ContextTooLarge(
            f"the report is {context.byte_size} bytes over the limit of "
            f"{settings().max_context_bytes}; S3 will not truncate findings to explain them",
            details={"bytes": context.byte_size},
        )
    policy = _policy(request)
    with unit_of_work() as session:
        candidates = candidates_for_agent(request.agent_type, needed_tokens=context.token_estimate, session=session)
    selection = select(context.items, policy, candidates, needed_tokens=context.token_estimate)
    if selection.chosen is None:
        raise ProviderUnavailable(
            "no model may serve this explanation under the effective policy: "
            + "; ".join(selection.reasons)[:400],
            details={"denials": selection.payloads},
        )
    with unit_of_work() as session:
        model = registered_model(
            f"{selection.chosen.provider}/{selection.chosen.model}", session=session
        )
    return Turn(
        context=context,
        thresholds=thresholds,
        policy=policy,
        selection=selection,
        model=model,
        provider=get_llm_provider(model.provider_info()),
    )


def _policy(request: AgentRunRequest) -> Any:
    with unit_of_work() as session:
        return effective_policy(
            session=session, environment_policies=request.target_environment_policies
        )


def _as_text(findings: list[dict[str, Any]]) -> str:
    import json

    return json.dumps(findings, ensure_ascii=False, sort_keys=True, default=str)


# --- the call ------------------------------------------------------------------


def _ask(
    request: AgentRunRequest,
    turn: Turn,
    findings: list[dict[str, Any]],
    progress: ProgressReporter | None,
    cancel: CancellationToken | None,
) -> tuple[dict[str, Any], int]:
    """One logical explanation call, with AI-RT-011's single corrective retry."""
    provider = turn.provider
    if provider is None:  # pragma: no cover - selection refused earlier
        raise ProviderUnavailable("no provider was selected for this explanation")
    messages = [*turn.context.messages]
    messages.append(
        _user(EXPLANATION_TASK + "\n\nFindings:\n" + _as_text(findings))
    )
    for attempt in range(settings().corrective_attempts + 1):
        if cancel is not None:
            cancel.raise_if_cancelled()
        calls = attempt + 1
        started = get_clock().now()
        try:
            response = provider.generate_structured(
                StructuredRequest(
                    messages=tuple(messages),
                    json_schema=envelope_schema(),
                    schema_name="metadata_design.explain",
                    temperature=settings().default_temperature,
                    max_output_tokens=settings().default_max_output_tokens,
                    agent_hint=AgentHint(
                        agent_type=AgentType.METADATA_DESIGN.value, stage="MAPPING"
                    ),
                )
            )
        except LlmError as exc:
            # recorded before it propagates: AI-NFR-03 wants 100% of calls in the ledger,
            # and a refusal that costs money but leaves no record is unbudgetable
            _record(request, turn, None, started, error=str(exc.message), calls=calls, request_id=exc.request_id)
            raise
        _record(request, turn, response, started, error=None, calls=calls)
        payload = response.payload or _decode(response.raw_text)
        report = _explain_shape(payload)
        if report.ok:
            return payload, calls
        if attempt >= settings().corrective_attempts:
            raise OutputInvalid(
                "the explanation answer was refused: " + "; ".join(report.messages())[:400],
                details={"findings": report.as_payload()["errors"]},
            )
        messages.append(_user(report.as_prompt_block()))
    raise AssertionError("unreachable")  # pragma: no cover


def _user(text: str) -> Any:
    from factory_intelligence.context.fence import plain

    return plain(kind=ItemSourceKind.STAGE_RULES, text=text)


def _decode(text: str) -> dict[str, Any]:
    import json

    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _explain_shape(payload: Any) -> ValidationReport:
    """Minimal envelope check, plus the AI-AG-006 ban on a patch in explanation mode.

    The full ladder is not used: there is no draft to patch here, so S2's section schema has
    nothing to validate.  What *is* enforced is that ``patch`` stays empty and that the
    explanation is a summary a human can act on.
    """
    report = ValidationReport()
    if not isinstance(payload, dict):
        report.stopped_at = LADDER_ENVELOPE
        report.add(Finding(rule_id="AIX-001", ladder=LADDER_ENVELOPE, message="the answer is not a JSON object"))
        return report
    summary = str(payload.get("summary") or "").strip()
    if not summary:
        report.stopped_at = LADDER_ENVELOPE
        report.add(
            Finding(
                rule_id="AIX-002",
                ladder=LADDER_ENVELOPE,
                location="summary",
                message="summary must explain the findings in plain language; it is the only "
                "part of this answer that reaches a product",
            )
        )
    patch = payload.get("patch")
    if isinstance(patch, dict) and patch.get("operations"):
        report.stopped_at = LADDER_ENVELOPE
        report.add(
            Finding(
                rule_id="AIX-003",
                ladder=LADDER_ENVELOPE,
                location="patch.operations",
                message="an explanation proposes no change: after ARCHITECTURE_GATE a change "
                "needs an approved Change Request, not a patch from this answer (AI-AG-006)",
            )
        )
    return report


def _record(
    request: AgentRunRequest,
    turn: Turn,
    response: Any,
    started: Any,
    *,
    error: str | None,
    calls: int = 1,
    request_id: str | None = None,
) -> None:
    if turn.model is None:  # pragma: no cover
        return
    with unit_of_work() as session:
        record_call(
            context=CallContext(
                project_id=request.project_id,
                workflow_id=request.workflow_id,
                stage="MAPPING",
                agent_type=AgentType.METADATA_DESIGN.value,
                run_id=None,
                sequence=1,
                call_role="explanation",
            ),
            model=turn.model,
            response=response,
            temperature=settings().default_temperature,
            max_output_tokens=settings().default_max_output_tokens,
            latency_ms=int((get_clock().now() - started).total_seconds() * 1000) if started else None,
            outcome="OK" if response is not None and error is None else "ERROR",
            error=error,
            request_id=request_id,
            context_artifacts=[
                {"ref": item.ref, "classification": item.classification.value}
                for item in turn.context.items
            ],
            session=session,
            operator=request.operator,
        )


def _output_document(
    request: AgentRunRequest,
    turn: Turn,
    answer: dict[str, Any],
    report_artifact_id: str,
    calls: int,
    prompt_versions: dict[str, Any],
) -> dict[str, Any]:
    """The recorded explanation — with any patch the model returned dropped."""
    return {
        "agentType": AgentType.METADATA_DESIGN.value,
        "kind": "issueExplanation",
        "stage": DesignStage.MAPPING.value,
        "reportArtifactId": report_artifact_id,
        "summary": str(answer.get("summary") or ""),
        "decisions": [dict(item) for item in (answer.get("decisions") or []) if isinstance(item, dict)],
        "questions": [dict(item) for item in (answer.get("questions") or []) if isinstance(item, dict)],
        "confidence": answer.get("confidence"),
        "model": turn.model.ref if turn.model else None,
        "modelCalls": calls,
        "promptVersions": dict(prompt_versions),
        # AI-AG-006: explanation mode produces no patch, and the omission is explicit so a
        # reader of the artifact can tell a dropped patch from a missing one.
        "patch": None,
    }


__all__ = ["explain", "REPORT_TYPES", "EXPLANATION_TASK"]
