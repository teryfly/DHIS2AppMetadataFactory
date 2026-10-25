"""Resolving a reference to a data classification (AI-POL-006).

The engine in :mod:`factory_intelligence.policy.engine` is pure; this is its only
input/output half, kept apart so the purity guard on the engine is a one-file rule.

AI-POL-006 gives two sources of truth: an artifact carries its own classification, and a
Blueprint entry takes the classification of the artifact behind the version or draft it
belongs to.  The draft case has one wrinkle worth stating: S2's ``DraftInfo`` reports a
content hash and no artifact id, so a draft resolves through its **base version's**
artifact and says so in the returned label.  That is the conservative reading — a draft
inherits the classification of what it started from, and material a user adds to a draft
arrives as an artifact with its own classification the next time it is read.
"""

from __future__ import annotations

from collections.abc import Sequence

from factory_foundation.api import (
    DataClassification,
    NotFoundError,
    get_artifact,
)
from sqlalchemy.orm import Session

from factory_intelligence.deps import get_blueprint
from factory_intelligence.dto.request import ContextItemRef
from factory_intelligence.enums import ItemSourceKind
from factory_intelligence.policy.model import ContextItem

ARTIFACT_PREFIX = "artifact:"
BLUEPRINT_PREFIX = "blueprint:"
DRAFT_MARKER = "draft:"


def resolve(
    refs: Sequence[ContextItemRef | str],
    *,
    project_id: str,
    session: Session | None = None,
) -> list[ContextItem]:
    """Turn references into classified context items.

    An explicit ``classification`` on the reference wins: it is what the context builder
    uses after it has already read the artifact, and re-reading the metadata for every
    item of a hundred-item context would be the cost AI-NFR-01 exists to avoid.
    """
    items: list[ContextItem] = []
    for ref in refs:
        parsed = ref if isinstance(ref, ContextItemRef) else ContextItemRef(ref=ref)
        classification = _resolve_one(
            parsed, project_id=project_id, session=session
        )
        items.append(
            ContextItem(
                ref=parsed.ref,
                classification=classification,
                kind=ItemSourceKind.INPUT_ARTIFACT,
                label=parsed.label or parsed.ref,
            )
        )
    return items


def _resolve_one(
    ref: ContextItemRef, *, project_id: str, session: Session | None
) -> DataClassification:
    if ref.classification:
        return DataClassification(ref.classification)
    if ref.artifact_id:
        return get_artifact(ref.artifact_id, session=session).data_classification
    if ref.is_blueprint:
        return _blueprint_classification(ref.ref, project_id=project_id)
    raise NotFoundError(
        f"cannot classify context reference {ref.ref!r}: expected 'artifact:<id>' or "
        "'blueprint:<version>' or 'blueprint:draft:<id>'",
        details={"ref": ref.ref},
    )


def _blueprint_classification(ref: str, *, project_id: str) -> DataClassification:
    target = ref[len(BLUEPRINT_PREFIX) :]
    blueprint = get_blueprint()
    if target.startswith(DRAFT_MARKER):
        draft_id = target[len(DRAFT_MARKER) :]
        draft = blueprint.draft(draft_id)
        if draft.project_id != project_id:
            raise NotFoundError(
                f"draft {draft_id} belongs to another project", details={"draftId": draft_id}
            )
        # A draft's only classified ancestor is the version it was opened from; content a
        # user adds to it arrives as artifacts with their own classification (see the
        # module docstring).
        base = blueprint.version(project_id, draft.base_version)
        return get_artifact(base.artifact_id).data_classification
    if target in ("", "latest", "current"):
        return get_artifact(blueprint.current_version(project_id).artifact_id).data_classification
    version_row = blueprint.version(project_id, target)
    return get_artifact(version_row.artifact_id).data_classification


__all__ = ["resolve", "ARTIFACT_PREFIX", "BLUEPRINT_PREFIX", "DRAFT_MARKER"]
