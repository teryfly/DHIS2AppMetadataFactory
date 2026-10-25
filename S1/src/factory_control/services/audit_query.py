"""Audit queries and the COMMIT trace (SRS 8.1 audit group, AC-AUD-01).

S0 owns the audit store; S1 only projects it and assembles the cross-subsystem
trace for one commit.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from factory_control import store as S
from factory_control.foundation import AuditQuery, query_audit, run_full_chain_verify, verify_chain
from factory_control.models import DeploymentRun
from factory_control.runtime import Runtime

MAX_AUDIT_PAGE = 200


def _record_view(record: Any) -> dict[str, Any]:
    return {
        "id": record.id,
        "sequence": record.seq,
        "occurredAt": record.occurred_at,
        "operatorType": record.operator_type,
        "operatorName": record.operator_name,
        "sourceSubsystem": record.source_subsystem,
        "eventType": record.event_type,
        "objectType": record.object_type,
        "objectId": record.object_id,
        "projectId": record.project_id,
        "before": record.before,
        "after": record.after,
        "correlationId": record.correlation_id,
        "redacted": record.redacted,
        "hash": record.hash,
        "prevHash": record.prev_hash,
    }


def query_for_project(
    runtime: Runtime,
    project_id: str,
    *,
    operator: str,
    limit: int = 50,
    cursor: str | None = None,
    event_type: str | None = None,
    operator_name: str | None = None,
    object_id: str | None = None,
    occurred_after: datetime | None = None,
    occurred_before: datetime | None = None,
) -> dict[str, Any]:
    """S5IF-G04: the audit list is a cursor list, and ``total`` is not in its contract.

    The filters are S0's own query fields (UI-OPS-002 asks for object, event type, operator and
    time); passing them through here rather than filtering a page in the browser matters because
    a page of 50 newest records does not contain the answer to "what did this operator change
    last week" -- the filter has to narrow the query, not the page.
    """
    from factory_control.rest.paging import clamp_limit, decode_cursor, next_cursor

    page_size = clamp_limit(limit)
    offset = decode_cursor(cursor)
    with runtime.transaction(operator=operator, project_id=project_id) as ctx:
        page = query_audit(
            AuditQuery(
                project_id=project_id,
                event_type=event_type,
                operator_name=operator_name,
                object_id=object_id,
                occurred_after=occurred_after,
                occurred_before=occurred_before,
                limit=page_size,
                offset=offset,
            ),
            session=ctx.session,
        )
        return {
            "items": [_record_view(r) for r in page.items],
            "limit": page_size,
            "nextCursor": next_cursor(offset=offset, returned=len(page.items), total=page.total),
        }


def query_all(
    runtime: Runtime,
    *,
    operator: str,
    event_type: str | None = None,
    operator_name: str | None = None,
    object_id: str | None = None,
    occurred_after: datetime | None = None,
    occurred_before: datetime | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> dict[str, Any]:
    from factory_control.rest.paging import clamp_limit, decode_cursor, next_cursor

    page_size = clamp_limit(limit)
    offset = decode_cursor(cursor)
    with runtime.transaction(operator=operator) as ctx:
        page = query_audit(
            AuditQuery(
                event_type=event_type,
                operator_name=operator_name,
                object_id=object_id,
                occurred_after=occurred_after,
                occurred_before=occurred_before,
                limit=page_size,
                offset=offset,
            ),
            session=ctx.session,
        )
        return {
            "items": [_record_view(r) for r in page.items],
            "limit": page_size,
            "nextCursor": next_cursor(offset=offset, returned=len(page.items), total=page.total),
        }


def verify(
    runtime: Runtime,
    *,
    operator: str,
    first_seq: int | None = None,
    last_seq: int | None = None,
) -> dict[str, Any]:
    """CP-SET-002 / AC-SEC-01 — verify a range, or the whole chain."""
    from factory_control.foundation import NotFoundError, link_pending

    # S0 links the hash chain after commit (FD-AUD-004, within 10 s). Verifying a
    # range without linking first would report the unlinked tail as a break.
    link_pending()
    with runtime.transaction(operator=operator) as ctx:
        from factory_control.foundation import NotFoundError

        try:
            if first_seq is not None or last_seq is not None:
                result = verify_chain(
                    start_seq=int(first_seq or 1), end_seq=last_seq, session=ctx.session
                )
            else:
                result = run_full_chain_verify(session=ctx.session)
        except NotFoundError:
            return {"verified": True, "empty": True, "reason": "NO_AUDIT_RECORDS"}
    return _verify_view(result)


def _verify_view(result: Any) -> dict[str, Any]:
    payload = result.model_dump() if hasattr(result, "model_dump") else dict(result.__dict__)
    return {
        "verified": bool(getattr(result, "ok", payload.get("ok", False))),
        "checked": payload.get("checked"),
        "firstBrokenSequence": payload.get("first_broken_seq") or payload.get("firstBrokenSequence"),
        "reason": payload.get("reason"),
        "details": payload,
    }


def commit_trace(runtime: Runtime, execution_id: str, *, operator: str) -> dict[str, Any]:
    """AC-AUD-01: environment, operator, approvers, payload and hashes, version,
    Gate, execution record and ImportReport — for one COMMIT."""
    with runtime.transaction(operator=operator) as ctx:
        from sqlalchemy import select

        run = ctx.session.execute(
            select(DeploymentRun).where(DeploymentRun.execution_id == execution_id)
        ).scalars().first()
        if run is None:
            from factory_control.errors import NotFoundControlError

            raise NotFoundControlError(
                f"no deployment run for execution {execution_id}", details={"executionId": execution_id}
            )
        outcome = ctx.execution.get_execution(execution_id) or ctx.execution.get_execution_by_key(
            f"commit:{run.id}"
        )
        gates = [g for g in S.gates_of(ctx.session, run.workflow_id) if g.deployment_run_id == run.id]
        deploy_gate = next((g for g in gates if g.type == "DEPLOY_GATE"), None)
        package_artifact_id, package_hash = _committed_package(ctx, run)

        audit_records = query_audit(
            AuditQuery(object_type="DeploymentRun", object_id=run.id, limit=MAX_AUDIT_PAGE),
            session=ctx.session,
        )
        return {
            "executionId": execution_id,
            "deploymentRunId": run.id,
            "environmentKey": run.environment_key,
            "blueprintVersion": run.blueprint_version,
            "commitOperator": run.commit_operator,
            "validateOperator": run.validate_operator,
            "payloadArtifactId": package_artifact_id,
            "payloadHash": package_hash or run.validate_payload_hash,
            "gateId": deploy_gate.id if deploy_gate else None,
            "gateStatus": deploy_gate.status if deploy_gate else None,
            "approvals": [
                {"approverName": a.get("approverName"), "decision": a.get("decision")}
                for a in (deploy_gate.approvals or [])
            ]
            if deploy_gate
            else [],
            "execution": outcome.model_dump() if outcome is not None else None,
            "importReportArtifactId": outcome.import_report_artifact_id if outcome else None,
            "auditTrail": [_record_view(r) for r in audit_records.items],
        }


def _committed_package(ctx: Any, run: DeploymentRun) -> tuple[str | None, str | None]:
    from sqlalchemy import select

    from factory_control.domain.enums import StageType
    from factory_control.foundation import get_artifact
    from factory_control.models import Stage

    stage = ctx.session.execute(
        select(Stage).where(
            Stage.deployment_run_id == run.id, Stage.stage_type == StageType.BIND_COMPILE.value
        )
    ).scalars().first()
    if stage is None:
        return None, None
    for artifact_id in stage.output_artifact_ids or []:
        artifact = get_artifact(artifact_id, session=ctx.session)
        if artifact is not None and artifact.type == "METADATA_PACKAGE":
            return artifact.id, artifact.content_hash
    return None, None


def link_pending_now(runtime: Runtime) -> int:
    """CP-SET-002: the 5-second scheduler body."""
    from factory_control.foundation import link_pending

    _ = runtime
    return int(link_pending() or 0)


__all__ = [
    "commit_trace",
    "link_pending_now",
    "query_all",
    "query_for_project",
    "verify",
]
