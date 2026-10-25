"""Classification-aware artifact reads (FR-SEC-003).

Two routes need this now: downloading an artifact, and reading the LLM transcript that
quotes one.  Keeping the decision in one place is what keeps ``artifact.read.restricted``
and ``artifact.read.phi`` from being decoration on one of them.
"""

from __future__ import annotations

from fastapi import Request

from factory_control import iam
from factory_control.rest.deps import authorize
from factory_control.rest.middleware import context_of


def authorize_artifact_read(request: Request, artifact_id: str) -> None:
    """FR-SEC-003: a RESTRICTED or PHI_PII artifact needs its own authorisation.

    附录 B already named ``artifact.read.restricted`` and ``artifact.read.phi``; this is where
    they are asked.  The classification is the artifact's own recorded property, so choosing
    which action to authorise is a read rather than a guess, and asking only the coarse
    ``artifact.read`` for a PHI file would leave those two names decoration instead of policy.

    The read comes first and the coarse check second, because the artifact's own ``project_id`` is
    what makes ``artifact.read`` answerable at all: a person whose role lives on the project is
    invisible to a project-less check, so the download button 403'd for exactly the operators the
    download exists for (FR-SEC-004).  An unknown id keeps the old shape — no project, and the
    service answers the 404.
    """
    from factory_control.foundation import get_artifact

    info = get_artifact(artifact_id)
    classification = str(getattr(info, "data_classification", "") or "").upper()
    project_id = str(getattr(info, "project_id", "") or "") or None
    authorize(request, iam.ARTIFACT_READ, project_id)
    if classification not in ("RESTRICTED", "PHI_PII"):
        return
    action = iam.ARTIFACT_READ_PHI if classification == "PHI_PII" else iam.ARTIFACT_READ_RESTRICTED
    authorize(request, action, project_id)
    _record_restricted_read(request, artifact_id, classification, project_id)


def _record_restricted_read(
    request: Request, artifact_id: str, classification: str, project_id: str | None
) -> None:
    """Every restricted read is audited (FR-SEC-003's second clause)."""
    from factory_control.foundation import AuditEventInput, OperatorType, audit, unit_of_work

    holder = context_of(request)
    with unit_of_work() as session:
        audit(
            AuditEventInput(
                event_type="cp.artifact.read_restricted",
                object_type="Artifact",
                object_id=artifact_id,
                project_id=project_id,
                after={"classification": classification, "userId": holder.user_id},
                source_subsystem="S1",
                operator_type=OperatorType.USER,
                operator_name=holder.operator,
            ),
            session=session,
        )


__all__ = ["authorize_artifact_read"]
