"""One place where a state change becomes an event plus an audit record.

CP-WF-005 and CP-BR-14 require that a transition, its event-stream row and its
audit entry commit together.  Doing that in one call keeps the guarantee from
being forgotten in a service that "just" updates a status column.

The audit write goes through S0 in the caller's session, so a failed audit rolls
the whole unit of work back (S0 D-11, FD-AC-02).
"""

from __future__ import annotations

from typing import Any

from factory_control.events import event_log
from factory_control.foundation import AuditEventInput, OperatorType
from factory_control.runtime import ControlContext

#: Subsystem code that owns the S1 schema (00 doc 4.1).
SUBSYSTEM = "S1"


def record_change(
    ctx: ControlContext,
    *,
    event_type: str,
    audit_event_type: str,
    object_type: str,
    object_id: str,
    payload: dict[str, Any] | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    project_id: str | None = None,
    workflow_id: str | None = None,
    operator_type: OperatorType = OperatorType.USER,
) -> event_log.Event:
    """Append the workflow event and write the audit row in this transaction.

    ``workflow_id`` defaults to the context's workflow; pass it explicitly when a
    change touches several workflows (invalidation propagation does).
    """
    wf_id = workflow_id or ctx.workflow_id or _resolve_workflow(ctx, object_id)
    if wf_id is None:
        # A state change that belongs to no workflow cannot be journalled; failing
        # here keeps CP-EVT-001 honest instead of silently dropping the event.
        raise ValueError(f"{event_type} requires a workflow id for object {object_id}")

    event = event_log.append(
        ctx.session,
        workflow_id=wf_id,
        event_type=event_type,
        payload=dict(payload or {}),
        retention_days=ctx.settings.event_retention_days(),
    )

    write_audit(
        ctx,
        event_type=audit_event_type,
        object_type=object_type,
        object_id=object_id,
        project_id=project_id or ctx.project_id,
        before=before,
        after=after,
        operator_type=operator_type,
    )
    return event


def _resolve_workflow(ctx: ControlContext, object_id: str) -> str | None:
    """Find the workflow a change belongs to when the caller did not bind one.

    Approval and commit paths are opened with only an operator, so the workflow is
    recovered from the object being changed rather than trusting the context.
    """
    from factory_control.models import (
        ChangeRequest,
        DeploymentRun,
        Gate,
        Question,
        Stage,
        Workflow,
    )

    session = ctx.session
    for model in (Stage, Gate, Question, DeploymentRun):
        row = session.get(model, object_id)
        if row is not None:
            found = getattr(row, "workflow_id", None)
            if found:
                return str(found)
    # A ChangeRequest belongs to a project, and a project has exactly one workflow.
    change_request = session.get(ChangeRequest, object_id)
    if change_request is not None:
        workflow = session.execute(
            __import__("sqlalchemy").select(Workflow).where(Workflow.project_id == change_request.project_id)
        ).scalars().first()
        if workflow is not None:
            return str(workflow.id)
    workflow = session.get(Workflow, object_id)
    return str(object_id) if workflow is not None else None


def write_audit(
    ctx: ControlContext,
    *,
    event_type: str,
    object_type: str,
    object_id: str,
    project_id: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    operator_type: OperatorType = OperatorType.USER,
) -> Any:
    """Write one audit record inside the caller's unit of work."""
    from factory_control.foundation import audit

    return audit(
        AuditEventInput(
            event_type=event_type,
            object_type=object_type,
            object_id=object_id,
            project_id=project_id or ctx.project_id,
            before=before,
            after=after,
            source_subsystem=SUBSYSTEM,
            operator_type=operator_type,
            operator_name=ctx.operator,
            correlation_id=ctx.correlation_id,
        ),
        session=ctx.session,
        clock=ctx.clock,
    )


def audit_system(ctx: ControlContext, event_type: str, object_type: str, object_id: str, **fields: Any) -> Any:
    """Audit attributed to the system rather than to a human operator."""
    return write_audit(
        ctx,
        event_type=event_type,
        object_type=object_type,
        object_id=object_id,
        operator_type=OperatorType.SYSTEM,
        **fields,
    )


__all__ = ["SUBSYSTEM", "audit_system", "record_change", "write_audit"]
