"""Artifact type catalog (FD-ART-002).

S0 owns the catalog table ``core.artifact_type``.  Each subsystem registers the
types it owns at start-up; a type may have several owners and repeated
registration merges owner lists (e.g. DIFF_REPORT → S2 and S4).  Creating a type
whose declared creator is not an owner raises ``ARTIFACT_ERROR``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from factory_foundation.common.errors import ArtifactError
from factory_foundation.db.uow import resolve_session, writable_session
from factory_foundation.models.registry import ArtifactTypeRow

# Initial type → owner subsystems (FD-ART-002 table).
INITIAL_ARTIFACT_CATALOG: dict[str, list[str]] = {
    "BUSINESS_INPUT": ["S1"],
    "NRR": ["S3"],
    "AGENT_OUTPUT": ["S3"],
    "BLUEPRINT": ["S2"],
    "METADATA_PACKAGE": ["S2"],
    "DATASTORE_CHANGESET": ["S2"],
    "OPENAPI": ["S2"],
    "UI_SPECIFICATION": ["S2"],
    "FIXTURE_PACKAGE": ["S2"],
    "VALIDATION_REPORT": ["S2"],
    "FRONTEND_PACKAGE": ["S2"],
    "MANIFEST": ["S2"],
    "DHIS2_IMPORT_REPORT": ["S4"],
    "SNAPSHOT": ["S4"],
    "EXECUTION_PLAN": ["S4"],
    "EXECUTION_REPORT": ["S4"],
    "POST_VERIFICATION_REPORT": ["S4"],
    "CAPABILITY_PROFILE": ["S4"],
    "DISCOVERY_RESULT": ["S4"],
    "DIFF_REPORT": ["S2", "S4"],
    "CHANGE_IMPACT_REPORT": ["S1"],
    "PROJECT_EXPORT": ["S1"],
    "HANDOFF_CHECK_REPORT": ["S2"],
}


def register_artifact_types(
    type_owners: Mapping[str, Sequence[str]],
    *,
    max_sizes: Mapping[str, int] | None = None,
    session: Session | None = None,
) -> None:
    """Merge ``type_owners`` (type → owner subsystem codes) into the catalog.

    New types are inserted; existing types get their owner lists unioned.  A
    ``max_size`` is applied only where supplied (FD-ART-006: unset = unlimited).
    """
    with writable_session(session) as sess:
        for type_name, owners in type_owners.items():
            row = sess.get(ArtifactTypeRow, type_name)
            new_owners = sorted({*(owners or []), *(row.owner_subsystems if row else [])})
            if row is None:
                row = ArtifactTypeRow(
                    type=type_name,
                    owner_subsystems=new_owners,
                    max_size=(max_sizes or {}).get(type_name),
                )
                sess.add(row)
            else:
                row.owner_subsystems = new_owners
                if max_sizes and type_name in max_sizes:
                    row.max_size = max_sizes[type_name]
        sess.flush()


def list_artifact_types(session: Session | None = None) -> list[tuple[str, list[str], int | None]]:
    with resolve_session(session) as sess:
        rows = sess.execute(select(ArtifactTypeRow).order_by(ArtifactTypeRow.type)).scalars().all()
        return [(r.type, list(r.owner_subsystems), r.max_size) for r in rows]


def get_type_entry(type_name: str, session: Session | None = None) -> ArtifactTypeRow | None:
    with resolve_session(session) as sess:
        return sess.get(ArtifactTypeRow, type_name)


def assert_type_owner(
    type_name: str, creator_subsystem: str, session: Session | None = None
) -> ArtifactTypeRow:
    """Validate the creator owns the type and return the catalog row.

    Raises ``ARTIFACT_ERROR`` when the type is unknown or the creator is not one
    of its allowed owners (FD-ART-002, FD-BR-03, FD-AC-05).
    """
    with resolve_session(session) as sess:
        row = sess.get(ArtifactTypeRow, type_name)
        if row is None:
            raise ArtifactError(
                f"unregistered artifact type: {type_name}",
                details={"type": type_name},
            )
        if creator_subsystem not in row.owner_subsystems:
            raise ArtifactError(
                f"subsystem {creator_subsystem} is not an owner of artifact type {type_name} "
                f"(allowed: {row.owner_subsystems})",
                details={
                    "type": type_name,
                    "creator": creator_subsystem,
                    "owners": row.owner_subsystems,
                },
            )
        return row
