"""S0 artifact gateway (00 doc 6.6, AI-INP-004, AI-RT-014).

Every product S3 writes is JSON S3 composed — an NRR, an ``AGENT_OUTPUT``, a run
context — so the two operations that matter are "content to staging" and "staging to
registered metadata", and they sit on opposite sides of the transaction boundary:
:func:`stage_json` is external I/O and must run with no unit of work open, while
:func:`register_staged` writes ``core`` metadata and must run inside one (S0's own
``@external_io`` markers enforce both, ``factory_foundation/artifacts/service.py:119``
and ``:184``).  Keeping them apart is the reason the runtime alternates transactions and
gaps rather than holding one open across a model call.

The classification rule is centralised here because getting it wrong fails late and
far away: S0 refuses a derived artifact classified below the maximum of its sources
(``artifacts/service.py:219``), so every writer must compute the same floor.  A PHI_PII
source is refused outright — not downgraded, not dropped — because AI-POL-005 says no
policy widens that, and an artifact that silently absorbed a PHI source would carry it
into every later context that cites the artifact.
"""

from __future__ import annotations

import json
from typing import Any

from factory_foundation.api import (
    ArtifactCreate,
    ArtifactError,
    ArtifactInfo,
    DataClassification,
    StagingHandle,
    TransactionMisuseError,
    file_json,
    get_artifact,
    read_content_bytes,
    register_artifact,
    stage_content,
)
from sqlalchemy.orm import Session

from factory_intelligence.deps import get_clock
from factory_intelligence.enums import NrrStatus
from factory_intelligence.errors import ContextContainsPhi

_JSON_MEDIA_TYPE = "application/json"


def classification_of(artifact_id: str, *, session: Session | None = None) -> DataClassification:
    """The data classification recorded on an artifact (AI-POL-006)."""
    return get_artifact(artifact_id, session=session).data_classification


def read_json(artifact_id: str) -> tuple[dict[str, Any], DataClassification]:
    """Read a JSON artifact's content.  External I/O: never inside a unit of work."""
    payload, classification, _media_type = read_content_bytes(artifact_id)
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactError(
            f"artifact {artifact_id} is not readable JSON: {exc}",
            artifact_id=artifact_id,
            details={"mediaTypeHint": "application/json"},
        ) from exc
    if not isinstance(document, dict):
        raise ArtifactError(
            f"artifact {artifact_id} holds a JSON {type(document).__name__}, not an object",
            artifact_id=artifact_id,
        )
    return document, classification


def read_bytes(artifact_id: str) -> tuple[bytes, DataClassification, str]:
    """Raw content, its classification and its media type (parsers and vision input)."""
    return read_content_bytes(artifact_id)


def stage_json(
    project_id: str,
    artifact_type: str,
    document: dict[str, Any],
    *,
    media_type: str = _JSON_MEDIA_TYPE,
) -> StagingHandle:
    """Write ``document`` to object storage and verify it (external I/O).

    Serialised through S0's canonical file form so the same content always produces the
    same hash (00 doc 6.7), which is what makes AI-INP-010's "two parses give the same
    result" checkable by comparing artifact hashes rather than by diffing dicts.
    """
    return stage_content(
        file_json(document),
        project_id=project_id,
        artifact_type=artifact_type,
        media_type=media_type,
        clock=get_clock(),
    )


def stage_bytes(
    project_id: str,
    artifact_type: str,
    payload: bytes,
    *,
    media_type: str = _JSON_MEDIA_TYPE,
) -> StagingHandle:
    """Same as :func:`stage_json` for content that is not JSON S3 composed."""
    return stage_content(
        payload,
        project_id=project_id,
        artifact_type=artifact_type,
        media_type=media_type,
        clock=get_clock(),
    )


def source_floor(
    classifications: list[DataClassification],
    extra: DataClassification | None = None,
) -> DataClassification:
    """The minimum classification for an artifact derived from ``classifications``.

    ``DataClassification.max_of`` is S0's own ordering, so S3 does not keep a second
    ranking that can disagree with the one the check enforces.
    """
    values = list(classifications)
    if extra is not None:
        values.append(extra)
    return DataClassification.max_of(values)


def assert_model_safe(
    items: list[tuple[str, DataClassification]],
) -> None:
    """Refuse a context that contains PHI_PII (AC-POL-02, AI-POL-005).

    Called before the policy engine is consulted at all, because a relaxation reaching
    ``evaluate`` would still be the wrong answer: the requirement is that PHI_PII content
    does not enter a model context, not that it is usually denied.
    """
    offenders = [ref for ref, classification in items if classification is DataClassification.PHI_PII]
    if offenders:
        raise ContextContainsPhi(
            "PHI_PII content may not enter a model context under any policy",
            details={"artifacts": offenders},
        )


def register_staged(
    handle: StagingHandle | str,
    *,
    project_id: str,
    artifact_type: str,
    data_classification: DataClassification,
    source_artifact_ids: list[str] | None = None,
    workflow_id: str | None = None,
    build_record: dict[str, Any] | None = None,
    prev_version_id: str | None = None,
    status: str = "DRAFT",
    redaction_ref: str | None = None,
    session: Session,
) -> ArtifactInfo:
    """Record staged metadata in the caller's transaction.

    ``build_record`` is where ``parserVersion`` and the prompt versions of a call go
    (AI-INP-004, AI-RT-001), so an artifact states how it was made rather than leaving a
    reader to infer it from a run row.
    """
    from factory_foundation.api import ArtifactStatus

    return register_artifact(
        handle,
        ArtifactCreate(
            project_id=project_id,
            type=artifact_type,
            media_type=_JSON_MEDIA_TYPE,
            data_classification=data_classification,
            creator_subsystem="S3",
            workflow_id=workflow_id,
            build_record=build_record,
            source_artifact_ids=list(source_artifact_ids or []),
            prev_version_id=prev_version_id,
            status=ArtifactStatus(status),
            redaction_ref=redaction_ref,
        ),
        session=session,
        clock=get_clock(),
    )


def write_json_artifact(
    *,
    project_id: str,
    artifact_type: str,
    document: dict[str, Any],
    source_artifact_ids: list[str],
    workflow_id: str | None = None,
    build_record: dict[str, Any] | None = None,
    prev_version_id: str | None = None,
    session: Session | None = None,
) -> ArtifactInfo:
    """Stage and register in one call, for a caller with no transaction to join.

    The two halves may not be reordered and may not happen inside one transaction, so
    this helper opens its own unit of work for the metadata step only.  A runtime that
    is already inside a transaction calls :func:`stage_json` and :func:`register_staged`
    separately instead.
    """
    from factory_foundation.api import has_active_uow, unit_of_work

    if has_active_uow() and session is None:
        raise TransactionMisuseError(
            "write_json_artifact cannot be used inside a unit of work: staging is external "
            "I/O; call stage_json() in the gap and register_staged() in the transaction",
            details={"artifactType": artifact_type},
        )
    classifications = [classification_of(artifact_id) for artifact_id in source_artifact_ids]
    handle = stage_json(project_id, artifact_type, document)
    if session is not None:
        return register_staged(
            handle,
            project_id=project_id,
            artifact_type=artifact_type,
            data_classification=source_floor(classifications),
            source_artifact_ids=source_artifact_ids,
            workflow_id=workflow_id,
            build_record=build_record,
            prev_version_id=prev_version_id,
            session=session,
        )
    with unit_of_work() as sess:
        return register_staged(
            handle,
            project_id=project_id,
            artifact_type=artifact_type,
            data_classification=source_floor(classifications),
            source_artifact_ids=source_artifact_ids,
            workflow_id=workflow_id,
            build_record=build_record,
            prev_version_id=prev_version_id,
            session=sess,
        )


def is_confirmed(status: str | None) -> bool:
    """Whether an NRR item's status belongs in the confirmed part (AI-INP-008)."""
    return status == NrrStatus.CONFIRMED.value


__all__ = [
    "classification_of",
    "read_json",
    "read_bytes",
    "stage_json",
    "stage_bytes",
    "source_floor",
    "assert_model_safe",
    "register_staged",
    "write_json_artifact",
    "is_confirmed",
]
