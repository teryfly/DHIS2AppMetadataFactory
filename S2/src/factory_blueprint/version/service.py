"""Transactional access to the Blueprint version index."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from factory_foundation.api import NotFoundError, current_session
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from factory_blueprint.enums import ChangeLevel, VersionStatus
from factory_blueprint.models.version import BlueprintVersionRow
from factory_blueprint.version.numbering import Version


def insert_version(
    session: Session,
    *,
    version_id: str,
    project_id: str,
    version: str,
    parent_version: str | None,
    schema_version: int,
    change_level: ChangeLevel | str,
    content_hash: str,
    artifact_id: str,
    source_artifact_ids: Sequence[str],
    decision_ids: Sequence[str],
    created_by: str,
    promoted_from_stage: str | None,
    promoted_from_draft_id: str | None,
) -> BlueprintVersionRow:
    """Add the new version row and retire the previous CURRENT marker (BP-DRF-001)."""
    session.execute(
        update(BlueprintVersionRow)
        .where(
            BlueprintVersionRow.project_id == project_id,
            BlueprintVersionRow.status == VersionStatus.CURRENT.value,
        )
        .values(status=VersionStatus.APPROVED.value)
    )
    row = BlueprintVersionRow(
        id=version_id,
        project_id=project_id,
        version=version,
        parent_version=parent_version,
        schema_version=schema_version,
        change_level=ChangeLevel(change_level).value,
        content_hash=content_hash,
        artifact_id=artifact_id,
        status=VersionStatus.CURRENT.value,
        source_artifact_ids=list(source_artifact_ids),
        decision_ids=list(decision_ids),
        promoted_from_stage=promoted_from_stage,
        promoted_from_draft_id=promoted_from_draft_id,
        created_by=created_by,
    )
    session.add(row)
    session.flush()
    return row


def get_current(session: Session, project_id: str) -> BlueprintVersionRow | None:
    return session.execute(
        select(BlueprintVersionRow).where(
            BlueprintVersionRow.project_id == project_id,
            BlueprintVersionRow.status == VersionStatus.CURRENT.value,
        )
    ).scalar_one_or_none()


def require_current(session: Session, project_id: str) -> BlueprintVersionRow:
    row = get_current(session, project_id)
    if row is None:
        raise NotFoundError(
            f"project {project_id} has no Blueprint version yet",
            details={"projectId": project_id},
        )
    return row


def get_version(session: Session, project_id: str, version: str) -> BlueprintVersionRow:
    row = session.execute(
        select(BlueprintVersionRow).where(
            BlueprintVersionRow.project_id == project_id,
            BlueprintVersionRow.version == version,
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(
            f"unknown Blueprint version {version} for project {project_id}",
            details={"projectId": project_id, "version": version},
        )
    return row


def list_versions(
    session: Session, project_id: str, *, limit: int = 50, offset: int = 0
) -> tuple[list[BlueprintVersionRow], int]:
    """Newest first by stored version number (BP-VER-006 keeps all history)."""
    total = session.execute(
        select(func.count()).select_from(BlueprintVersionRow).where(
            BlueprintVersionRow.project_id == project_id
        )
    ).scalar_one()
    rows = list(
        session.execute(
            select(BlueprintVersionRow)
            .where(BlueprintVersionRow.project_id == project_id)
            .order_by(
                BlueprintVersionRow.created_at.desc(), BlueprintVersionRow.version.desc()
            )
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return rows, int(total)


def versions_since(session: Session, project_id: str, from_version: str) -> list[str]:
    """All versions after ``from_version``, oldest first — S1's invalidation range."""
    rows, _total = list_versions(session, project_id, limit=100000)
    start = Version.parse(from_version)
    ordered = sorted((row for row in rows), key=lambda row: Version.parse(row.version))
    return [row.version for row in ordered if Version.parse(row.version) > start]


def delete_project_versions(session: Session, project_id: str) -> int:
    statement = select(func.count()).select_from(BlueprintVersionRow).where(
        BlueprintVersionRow.project_id == project_id
    )
    count = int(session.execute(statement).scalar_one())
    session.query(BlueprintVersionRow).filter(
        BlueprintVersionRow.project_id == project_id
    ).delete(synchronize_session=False)
    return count


def created_at_text(row: BlueprintVersionRow) -> str:
    moment: datetime = row.created_at
    return moment.isoformat()


def session_or_current(session: Session | None = None) -> Session:
    return session if session is not None else current_session()
