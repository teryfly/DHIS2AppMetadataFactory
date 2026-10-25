"""``start_agent_run`` — the first turn (AI-RT-001, AI-RT-002, AI-RT-020, AI-AC-05).

T1 is the run's only pre-flight transaction: the idempotency record, the ``RUNNING`` row,
the prompt-activation snapshot and the budget decision, all committed before a single byte of
project content is read.  That ordering is what makes a retry safe — if the process dies
inside a model call, the next attempt finds the row and either replays it or refuses to race
it, rather than starting a second run that would submit the same patch twice.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from factory_foundation.api import (
    CancellationToken,
    ProgressReporter,
    idempotency,
    structure_hash,
    unit_of_work,
)

from factory_intelligence.agents.spec import AgentSpec, spec_for
from factory_intelligence.cost import budget as budget_service
from factory_intelligence.dto.common import Issue
from factory_intelligence.dto.request import AgentRunRequest
from factory_intelligence.dto.result import AgentRunResult, BudgetView
from factory_intelligence.enums import AgentType, RunState
from factory_intelligence.errors import AgentRunInProgress
from factory_intelligence.feature import agent_enabled
from factory_intelligence.prompts import activation as prompts
from factory_intelligence.registries import IdemScope
from factory_intelligence.runtime import engine
from factory_intelligence.runtime import state as run_state
from factory_intelligence.runtime.get import project
from factory_intelligence.runtime.progress import STEP_STARTED, Progress


@dataclass
class _Begin:
    """What T1 decided: replay an existing run, refuse for budget, or proceed."""

    run_id: str
    replay: AgentRunResult | None = None
    active_prompt: prompts.ActivePrompt | None = None
    over_budget: BudgetView | None = None


def _progress_sink(run_id: str) -> Callable[[str, float, dict[str, Any]], None]:
    """Bind a run's progress events to its own row (AI-RT-019)."""
    from factory_intelligence.runtime import state as run_state

    def _sink(step: str, fraction: float, data: dict[str, Any]) -> None:
        run_state.record_progress(run_id, step, fraction, data)

    return _sink


def start(
    request: Any,
    *,
    progress: ProgressReporter | None = None,
    cancel: CancellationToken | None = None,
) -> AgentRunResult:
    """Start, or continue, the run an idempotency key already names (AI-AC-05)."""
    req = coerce_agent_run_request(request)
    try:
        spec = spec_for(req.agent_type)
    except ValueError:
        return _refused(req, f"unknown agent type {req.agent_type!r}")

    gate = _gate(req, spec)
    if gate is not None:
        return gate

    began = _begin(req)
    if began.replay is not None:
        return began.replay
    execution = engine.Execution(
        request=req,
        spec=spec,
        run_id=began.run_id,
        progress=Progress(progress, cancel, sink=_progress_sink(began.run_id)),
        attempt=1,
        active_prompt=began.active_prompt,
    )
    if began.over_budget is not None:
        return engine.budget_exceeded(execution, began.over_budget)
    execution.progress.report(STEP_STARTED, 0.0, runId=began.run_id)
    return engine.execute_turn(execution)


def coerce_agent_run_request(request: Any) -> AgentRunRequest:
    """Accept S1's DTO mirror instance, not only S3's own model.

    ``DelegatingAdapter`` forwards the caller's object untouched
    (``factory_control/adapters/delegating.py:101``) and S1's mirror is a different class with
    the same fields — hence ``from_attributes`` rather than an ``isinstance`` test that would
    reject it.
    """
    if isinstance(request, AgentRunRequest):
        return request
    return AgentRunRequest.model_validate(request)


def _gate(req: AgentRunRequest, spec: AgentSpec) -> AgentRunResult | None:
    """Refuse before a row exists when this build cannot serve the request at all.

    An undelivered agent returns a result rather than raising: S1 turns a ``FAILED`` status
    into a visible stage outcome and keeps the Gate's manual-authoring path open
    (FR-AG-011), whereas an exception would fail the whole Temporal activity.  That is the
    plan's ruling 2, and it is why ``run_id=None`` is allowed — S1 handles it at
    ``services/workflow.py:356``.
    """
    if not agent_enabled(spec.agent_type):
        return _refused(
            req,
            f"the {spec.agent_type.value} agent is delivered in {spec.milestone.value} and is "
            "not part of this build; author this stage by hand or upgrade S3",
        )
    ordering = _page_order_refusal(req, spec)
    if ordering is not None:
        return ordering
    if spec.requires_draft and req.draft_id is None:
        return _refused(
            req,
            "this stage needs a working draft open before an agent can run (AI-ASM-02) and "
            "none was supplied",
        )
    if prompts.current_activation(req.agent_type) is None:
        # AI-REG-002: a run is refused rather than degraded to an empty placeholder, and the
        # row is never written -- an agent nobody activated is a deployment step left undone,
        # so the honest answer is a visible refusal, not a paid-for run that pretends otherwise.
        return _refused(
            req,
            f"the {req.agent_type} agent has no activated prompt (AI-REG-002): activate a "
            "prompt version for this agent before its stage can run, or author the stage by hand",
            rule_id="AI-REG-002",
            issue_id="ai-prompt-not-activated-001",
            suggested_action="activate a prompt version for this agent (AI-REG-003's two gates)",
            error_code="AI_PROMPT_NOT_ACTIVATED",
        )
    return None


def _page_order_refusal(req: AgentRunRequest, spec: AgentSpec) -> AgentRunResult | None:
    """AI-AG-013: a page's UI agent runs only after that page's API agent completed.

    A UI Intent's component data sources are operationKeys of the same page's API Intent
    (AI-AG-005); designing the screen first would leave every source unresolvable, and the
    constraint check would rightly refuse the answer.  Refusing here says so in one line
    instead of spending a model call on an answer that cannot be accepted.
    """
    if spec.agent_type is not AgentType.UI:
        return None
    pages = [str(item).removeprefix("page:") for item in (req.scope or [])]
    if not pages:
        return None  # the scope check (AIC-012) refuses this run for the reason that fits
    from factory_intelligence.runtime import state as run_state

    missing = [
        page
        for page in pages
        if run_state.completed_page_run(
            req.project_id, req.stage, AgentType.API.value, page
        )
        is None
    ]
    if not missing:
        return None
    return _refused(
        req,
        "this page's API agent has not completed a run yet, and a UI Intent's data sources "
        "are that page's API operations (AI-AG-013, AI-AG-005): run the API agent for "
        + ", ".join(sorted(missing))
        + " first",
    )


def _begin(req: AgentRunRequest) -> _Begin:
    """T1: idempotency, budget, row, prompt snapshot — one transaction."""
    digest = structure_hash(req.digest_payload())
    with unit_of_work() as session:
        idempotency.begin(IdemScope.AGENT_START, req.idempotency_key, digest, session=session)
        existing = run_state.find_by_idempotency_key(req.idempotency_key, session=session)
        if existing is not None:
            return _Begin(run_id=existing.id, replay=_replay(existing, req))

        view = budget_service.view(req.project_id, session=session)
        run_id = run_state.new_run_id()
        run_state.create_run(req, run_id=run_id, session=session)
        if not budget_service.may_start_run(view):
            # the row exists so the refusal is auditable and queryable like any other outcome
            return _Begin(run_id=run_id, over_budget=view)
        return _Begin(
            run_id=run_id,
            # fixed for the whole run: AI-RT-001 records one prompt version per run, so a
            # switch by another operator mid-run cannot change what this run reports using
            active_prompt=prompts.current_activation(req.agent_type, session=session),
        )


def _replay(row: Any, req: AgentRunRequest) -> AgentRunResult:
    """Answer a repeated key from the durable row, calling no model (AI-AC-05)."""
    if row.status == RunState.RUNNING.value:
        # "continue the same run" cannot be a result while the first attempt is still in it,
        # and S1's status enum has no seventh value; a retryable error lets the activity back
        # off and re-enter, which is S2's precedent for BP_IN_PROGRESS.
        raise AgentRunInProgress(
            f"run {row.id} for this idempotency key is still executing",
            details={"runId": row.id, "idempotencyKey": req.idempotency_key},
        )
    result = project(row.id)
    return result or AgentRunResult(run_id=row.id, status=str(row.status))


def _refused(
    req: AgentRunRequest,
    reason: str,
    *,
    rule_id: str = "AI-RT-002",
    issue_id: str = "ai-gate-001",
    suggested_action: str = "use the stage's manual authoring path, or deliver this milestone",
    error_code: str | None = None,
) -> AgentRunResult:
    """No row is written: a run that never started should not appear in the usage view."""
    return AgentRunResult(
        run_id=None,
        status="FAILED",
        denial_reasons=[reason],
        issues=[
            Issue(
                issue_id=issue_id,
                rule_id=rule_id,
                severity="BLOCKING",
                description=reason,
                source_stage=req.stage,
                suggested_action=suggested_action,
                details={} if error_code is None else {"errorCode": error_code},
            )
        ],
        budget_status=budget_service.view(req.project_id).as_result_field(),
    )


__all__ = ["start"]
