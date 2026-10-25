"""执行报告（DX-EXE-046、总体 SRS 15.2 REPORT、FR-SEC-014）。

报告要能回答“哪些步骤已生效”：一次多执行器的部署里前一步失败时后一步不执行
（DX-EXE-037），审批人与运维只能从报告里看出边界在哪里。因此报告里逐项列出步骤状态、
已生效范围、ImportReport 与 Snapshot 引用、耗时与操作者——都是已持久化的事实，不做
任何推断。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from factory_foundation.api import unit_of_work
from sqlalchemy.orm import Session

from factory_dhis2.artifacts import register_staged, stage_document
from factory_dhis2.enums import ExecutionStatus
from factory_dhis2.execution.context import RunContext
from factory_dhis2.models.execution import ExecutionRow


def build_document(
    *,
    context: RunContext,
    execution_id: str,
    status: ExecutionStatus,
    applied: bool | None,
    steps: list[Any],
    import_report_artifact_id: str | None,
    snapshot_artifact_id: str | None,
    operator: str | None,
    issues: list[Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """组装 EXECUTION_REPORT 文档。"""
    started: datetime | None = None
    completed: datetime | None = None
    with unit_of_work() as session:
        row = session.get(ExecutionRow, execution_id)
        if row is not None:
            started, completed = row.started_at, row.completed_at
    duration_ms = (
        (completed - started).total_seconds() * 1000.0
        if started is not None and completed is not None
        else None
    )
    return {
        "schemaVersion": 1,
        "kind": "EXECUTION_REPORT",
        "executionId": execution_id,
        "environmentKey": context.environment_key,
        "environmentClass": context.target.environment_class.value,
        "projectKey": context.project_id,
        "payload": {
            "artifactId": context.package.artifact_id,
            "hash": context.package.artifact_hash,
            "blueprintVersion": context.package.blueprint_version,
            "objectCount": context.package.object_count,
        },
        "status": status.value,
        "applied": applied,
        "steps": [item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item) for item in steps],
        "artifacts": {
            "importReport": import_report_artifact_id,
            "snapshot": snapshot_artifact_id,
        },
        "issues": [
            issue.model_dump(mode="json") if hasattr(issue, "model_dump") else dict(issue)
            for issue in (issues or [])
        ],
        "operator": operator,
        "startedAt": started.isoformat() if started else None,
        "completedAt": completed.isoformat() if completed else None,
        "durationMs": duration_ms,
        "generatedAt": datetime.now(UTC).isoformat(),
        "extra": dict(extra or {}),
    }


def save(context: RunContext, document: dict[str, Any]) -> str:
    """登记为 EXECUTION_REPORT 产物（DX-EXE-046）；暂存在事务外。"""
    owner = str(document.get("projectKey") or f"env-{context.environment_key}")
    handle = stage_document(document, project_id=owner, artifact_type="EXECUTION_REPORT")
    with unit_of_work() as session:
        info = register_staged(
            handle,
            project_id=owner,
            artifact_type="EXECUTION_REPORT",
            source_artifact_ids=[context.package.artifact_id],
            session=session,
        )
    return info.id


def _session() -> Session:
    from factory_foundation.api import current_session, has_active_uow, new_session

    if has_active_uow():
        return current_session()
    return new_session()


__all__ = ["build_document", "save"]
