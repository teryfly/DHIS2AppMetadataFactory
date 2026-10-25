"""Reading the parse-time LLM transcript (S5IF-INP08).

S3 owns the exchange rows; S1 owns the per-input parse status that explains them.  This
service joins the two for the log page, and refuses an artifact that is not a business input
of the project — the transcript quotes the input, so cross-project reading must be a 404
rather than a silent empty page.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from factory_control.errors import NotFoundControlError
from factory_control.foundation import get_artifact
from factory_control.models import InputParseRun
from factory_control.runtime import Runtime


def _assert_business_input(project_id: str, artifact_id: str) -> None:
    artifact = get_artifact(artifact_id)
    if (
        artifact is None
        or getattr(artifact, "type", None) != "BUSINESS_INPUT"
        or str(getattr(artifact, "project_id", "") or "") != project_id
    ):
        raise NotFoundControlError(
            f"{artifact_id} is not a business input of {project_id}",
            details={"artifactId": artifact_id, "projectId": project_id},
        )


def _latest_run(
    runtime: Runtime, project_id: str, artifact_id: str, *, operator: str
) -> InputParseRun | None:
    with runtime.transaction(operator=operator, project_id=project_id) as ctx:
        return ctx.session.execute(
            select(InputParseRun)
            .where(
                InputParseRun.project_id == project_id,
                InputParseRun.input_artifact_id == artifact_id,
            )
            .order_by(InputParseRun.created_at.desc(), InputParseRun.id.desc())
            .limit(1)
        ).scalars().first()


def list_log(
    runtime: Runtime,
    project_id: str,
    artifact_id: str,
    *,
    operator: str,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Summaries of every model call this input made, oldest attempt first."""
    _assert_business_input(project_id, artifact_id)
    run = _latest_run(runtime, project_id, artifact_id, operator=operator)
    page = runtime.intelligence.list_llm_exchanges(
        project_id, artifact_id, limit=limit, offset=offset
    )
    return {
        "parse_status": run.status if run is not None else None,
        "parse_run_id": run.id if run is not None else None,
        "items": [item.model_dump() for item in page.items],
        "total": page.total,
        "limit": page.limit,
        "offset": page.offset,
    }


def get_log(
    runtime: Runtime,
    project_id: str,
    artifact_id: str,
    exchange_id: str,
    *,
    operator: str,
) -> dict[str, Any]:
    """One exchange's full request messages and raw answer."""
    _assert_business_input(project_id, artifact_id)
    detail = runtime.intelligence.get_llm_exchange(project_id, artifact_id, exchange_id)
    if detail is None:
        raise NotFoundControlError(
            f"no exchange {exchange_id} for input {artifact_id}",
            details={"exchangeId": exchange_id, "artifactId": artifact_id},
        )
    return detail.model_dump()


__all__ = ["get_log", "list_log"]
