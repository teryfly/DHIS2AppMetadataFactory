"""Usage recording (AI-LLM-004, AI-COST-001, AI-NFR-03).

Each call is written in its **own short transaction**, immediately after the provider
returns and before the output is validated.  That placement is the requirement, not an
optimisation: AI-NFR-03 wants 100% of model calls to have a usage record, and the calls
most worth billing are precisely the ones whose runs then fail — a Schema rejection, a
constraint violation, an S2 refusal.  If the ledger joined the run's final commit it would
be rolled back exactly when the evidence matters, and the cost of a failed attempt would
be invisible while still being real.

``ai.model_call`` is immutability-guarded by a trigger (migration 0001), so a usage row
cannot be edited afterwards into a smaller bill, and
:func:`factory_intelligence.cleanup.delete_project_data` deliberately does not touch it:
AI-DEL-001 keeps usage as aggregate values, and these columns are aggregate values — no
prompt, no answer, no artifact content.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from factory_foundation.api import (
    ValidationError,
    current_session,
    has_active_uow,
    new_id,
    unit_of_work,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from factory_intelligence import audit_log
from factory_intelligence.dto.result import UsageRollup
from factory_intelligence.llm.registry import RegisteredModel
from factory_intelligence.llm.types import ModelResponse
from factory_intelligence.models.call import ModelCallRow


@dataclass(frozen=True)
class CallContext:
    """The rollup keys a usage row must outlive.

    ``stage``, ``agent_type`` and ``workflow_id`` are copied onto the row rather than
    joined from ``ai.agent_run``, because the run row is deleted with the project
    (AI-DEL-001) and the cost history is not (AI-COST-001 asks for a rollup by those
    dimensions after it is gone).

    ``project_id`` may be absent for the one class of call that serves no project — an
    evaluation (AI-NFR-03 still wants its row, AI-COST-001 still cannot bill it to anybody).
    """

    project_id: str | None
    workflow_id: str | None
    stage: str | None
    agent_type: str | None
    run_id: str | None
    sequence: int = 1
    call_role: str = "initial"
    #: set only by a business-input parse: the artifact whose exchange log this call belongs
    #: to.  Agent runs and evaluations leave it None, which keeps the parse transcript free
    #: of every other kind of call.
    input_artifact_id: str | None = None


def record_call(
    *,
    context: CallContext,
    model: RegisteredModel,
    response: ModelResponse | None,
    temperature: float,
    max_output_tokens: int | None,
    latency_ms: int | None,
    outcome: str = "OK",
    error: str | None = None,
    request_id: str | None = None,
    context_artifacts: Sequence[dict[str, Any]] = (),
    session: Session | None = None,
    operator: str | None = None,
) -> ModelCallRow:
    """Write one usage row and its ``ai.model.called`` audit event.

    A failed call is still a call: providers bill a request they processed and refused to
    answer, so ``outcome="ERROR"`` rows are written too — otherwise the ledger would
    understate exactly the attempts that teach us something (AI-LLM-004 records "错误" as a
    field of the call, not as an absence).  A refusal has no response object, so its
    ``request_id`` arrives as an argument: the vendor's request id is the one diagnostic that
    survives a failure, and a 429 row has to be citable like any other.
    """
    input_tokens = response.usage.input_tokens if response else 0
    output_tokens = response.usage.output_tokens if response else 0
    estimated_cost = model.price_for(input_tokens, output_tokens)
    # A header from the vendor, so its length is not trusted: cut to what the column holds
    # rather than letting an over-long request id fail the write that records the call.
    diagnostic_id = (response.request_id if response is not None else None) or request_id
    diagnostic_id = diagnostic_id[:128] if diagnostic_id else None
    row = ModelCallRow(
        id=new_id("amcl"),
        run_id=context.run_id,
        project_id=context.project_id,
        workflow_id=context.workflow_id,
        stage=context.stage,
        agent_type=context.agent_type,
        provider=model.provider,
        provider_class=model.provider_class.value,
        model=model.model,
        temperature=temperature,
        max_tokens=max_output_tokens,
        request_id=diagnostic_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
        estimated_cost=estimated_cost,
        outcome=outcome,
        error=_bounded(error),
        sequence=context.sequence,
        call_role=context.call_role,
    )

    def _write(sess: Session) -> ModelCallRow:
        sess.add(row)
        sess.flush()
        audit_log.record(
            "ai.model.called",
            session=sess,
            object_type="modelCall",
            object_id=row.id,
            project_id=context.project_id,
            after={
                "provider": model.provider,
                "providerClass": model.provider_class.value,
                "model": model.model,
                "runId": context.run_id,
                "temperature": temperature,
                "maxTokens": max_output_tokens,
                "requestId": row.request_id,
                "inputTokens": input_tokens,
                "outputTokens": output_tokens,
                "latencyMs": latency_ms,
                "estimatedCost": estimated_cost,
                "outcome": outcome,
                # AI-POL-008: an audit of a call names the artifacts that went out and at
                # what classification.  Supplied by the runtime, which is the only place
                # the context still exists as a list; recording nothing here would leave
                # the audit answering "who was called" but not "with what".
                "contextArtifacts": list(context_artifacts),
            },
            operator=operator,
        )
        return row

    if session is not None or has_active_uow():
        return _write(session if session is not None else current_session())
    with unit_of_work() as sess:
        return _write(sess)


#: The dimensions ``usage_rollup`` accepts (AI-COST-001).
ROLLUP_DIMENSIONS: tuple[str, ...] = (
    "project",
    "workflow",
    "stage",
    "agent",
    "model",
    "provider",
)

_COLUMN_BY_DIMENSION: dict[str, str] = {
    "project": "project_id",
    "workflow": "workflow_id",
    "stage": "stage",
    "agent": "agent_type",
    "model": "model",
    "provider": "provider",
}


def rollup(
    project_id: str | None = None,
    *,
    group_by: Sequence[str] = ("stage", "agent", "model"),
    session: Session | None = None,
) -> list[UsageRollup]:
    """Sum calls and cost by any subset of :data:`ROLLUP_DIMENSIONS` (AI-COST-001).

    ``project_id=None`` rolls every project up, which is what an operator budget review
    asks; a row then still names the project it came from, because ``project_id`` is added
    to the grouping in that case rather than reported as a blank.

    An unknown dimension is a ``VALIDATION_ERROR``, not a bare ``ValueError``: AI-GOV-005
    forbids an unclassified exception on this path, because S1 maps only ``FactoryError``
    to the common REST error envelope and a ``ValueError`` becomes a 500 for a typo in a
    query string.
    """
    unknown = [item for item in group_by if item not in ROLLUP_DIMENSIONS]
    if unknown:
        raise ValidationError(
            f"unknown rollup dimension(s) {unknown}; valid: {list(ROLLUP_DIMENSIONS)}",
            code="VALIDATION_ERROR",
            details={
                "groupBy": list(group_by),
                "unknown": unknown,
                "allowed": list(ROLLUP_DIMENSIONS),
            },
        )
    columns = [_COLUMN_BY_DIMENSION[item] for item in group_by]
    if project_id is None:
        columns = ["project_id", *[item for item in columns if item != "project_id"]]

    def _read(sess: Session) -> list[UsageRollup]:
        selected = [getattr(ModelCallRow, name) for name in columns]
        query = (
            select(
                *selected,
                func.count(ModelCallRow.id),
                func.coalesce(func.sum(ModelCallRow.input_tokens), 0),
                func.coalesce(func.sum(ModelCallRow.output_tokens), 0),
                func.coalesce(func.sum(ModelCallRow.estimated_cost), 0.0),
                func.count(ModelCallRow.id).filter(ModelCallRow.outcome == "ERROR"),
            )
            .group_by(*selected)
            .order_by(*selected)
        )
        if project_id is not None:
            query = query.where(ModelCallRow.project_id == project_id)
        out: list[UsageRollup] = []
        for record in sess.execute(query).all():
            values = list(record)
            grouped = dict(zip(columns, values[: len(columns)], strict=True))
            out.append(
                UsageRollup(
                    project_id=_maybe_str(grouped.get("project_id")) or project_id,
                    workflow_id=_maybe_str(grouped.get("workflow_id")),
                    stage=_maybe_str(grouped.get("stage")),
                    agent_type=_maybe_str(grouped.get("agent_type")),
                    model=_maybe_str(grouped.get("model")),
                    provider=_maybe_str(grouped.get("provider")),
                    calls=int(values[len(columns)]),
                    input_tokens=int(values[len(columns) + 1]),
                    output_tokens=int(values[len(columns) + 2]),
                    estimated_cost=_maybe_float(values[len(columns) + 3]),
                    failed_calls=int(values[len(columns) + 4]),
                )
            )
        return out

    if session is not None or has_active_uow():
        return _read(session if session is not None else current_session())
    with unit_of_work() as sess:
        return _read(sess)


def total_spent(project_id: str, *, session: Session | None = None) -> float:
    """AI-COST-003: "已发生成本" at the moment a run starts, from the ledger itself."""

    def _read(sess: Session) -> float:
        value = sess.execute(
            select(func.coalesce(func.sum(ModelCallRow.estimated_cost), 0.0)).where(
                ModelCallRow.project_id == project_id
            )
        ).scalar_one()
        return _maybe_float(value)

    if session is not None or has_active_uow():
        return _read(session if session is not None else current_session())
    with unit_of_work() as sess:
        return _read(sess)


def call_count(run_id: str, *, session: Session | None = None) -> int:
    """How many times a run called a model — what AI-AC-01 asserts an exact number of."""

    def _read(sess: Session) -> int:
        return int(
            sess.execute(
                select(func.count(ModelCallRow.id)).where(ModelCallRow.run_id == run_id)
            ).scalar_one()
        )

    if session is not None or has_active_uow():
        return _read(session if session is not None else current_session())
    with unit_of_work() as sess:
        return _read(sess)


def _maybe_float(value: Any) -> float:
    """A ``SUM`` over an empty group comes back as ``NULL``, which means zero cost."""
    return 0.0 if value is None else float(value)


def _maybe_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _bounded(text: str | None, limit: int = 2000) -> str | None:
    """A provider error message, cut short.

    Never ``repr()`` of a request object: that object holds the ``Authorization`` header,
    and this column is readable by anyone who can query ``ai`` (AI-LLM-005).
    """
    if text is None:
        return None
    return text if len(text) <= limit else text[:limit] + "…"


__all__ = [
    "CallContext",
    "record_call",
    "rollup",
    "total_spent",
    "call_count",
    "ROLLUP_DIMENSIONS",
]
