"""Object-level Diff as an API call (S2 SRS 7.2).

The comparison itself is pure (``diff.object_diff``).  Comparing two *versions* has to
read two artifact bodies, so ``diff_versions`` is external I/O; comparing a *draft* to
its own base does not, because ``prepare_draft_base`` already put both documents on the
draft row — which is why ``diff_draft`` is a plain read, as chapter 7 says it is.
"""

from __future__ import annotations

from typing import Any

from factory_foundation.api import external_io, unit_of_work
from sqlalchemy.orm import Session

from factory_blueprint import artifacts
from factory_blueprint.diff import object_diff
from factory_blueprint.draft import service as draft_service
from factory_blueprint.dto.diff import DiffReport
from factory_blueprint.version import service as version_service


@external_io
def diff_versions(
    project_id: str,
    from_version: str,
    to_version: str,
    sections: list[str] | None = None,
) -> DiffReport:
    """Object-level Diff of two approved versions, Registry included (BP-DIFF-001)."""
    before, after = _documents(project_id, from_version, to_version)
    return object_diff.compare_documents(
        project_id, from_version, to_version, before, after, sections=sections
    )


def diff_draft(
    draft_id: str,
    sections: list[str] | None = None,
    *,
    session: Session | None = None,
) -> DiffReport:
    """What this design stage changed, object by object, against the draft's base.

    BP-DIFF-003: the review view shows added, updated (field level), unchanged,
    reference-only and removed, and the stage Gate is approved on exactly that view.
    """
    info = draft_service.get_draft(draft_id, session=session)
    base = draft_service.draft_base_document(draft_id, session=session)
    current = draft_service.draft_document(draft_id, session=session)
    return object_diff.compare_documents(
        info.project_id,
        info.base_version,
        draft_id,
        base,
        current,
        sections=sections,
        kind="DRAFT_VS_BASE",
    )


def _documents(
    project_id: str, from_version: str, to_version: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    with unit_of_work() as session:
        from_row = version_service.get_version(session, project_id, from_version)
        to_row = version_service.get_version(session, project_id, to_version)
    before, _ = artifacts.read_json(from_row.artifact_id)
    after, _ = artifacts.read_json(to_row.artifact_id)
    return before, after
