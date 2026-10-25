"""Rebuildable lookup indexes: businessKey/operationKey placement and dependency edges.

00 doc 4.1 lists "页面键等索引" and "依赖图索引" as part of ``bp``; both are
derived from version content and are dropped and rebuilt on promote/import.
"""

from __future__ import annotations

from sqlalchemy import Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from factory_blueprint.models.base import BpTable


class BusinessKeyIndexRow(BpTable):
    __tablename__ = "business_key_index"

    project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    blueprint_version: Mapped[str] = mapped_column(String(32), primary_key=True)
    section: Mapped[str] = mapped_column(String(32), primary_key=True)
    key_kind: Mapped[str] = mapped_column(String(24), primary_key=True)
    entry_key: Mapped[str] = mapped_column(String(255), primary_key=True)

    #: JSON pointer of the entry inside the Blueprint document
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    dhis2_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    uid: Mapped[str | None] = mapped_column(String(11), nullable=True)
    decision_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: pageKey an entry belongs to (apiIntents / uiIntents are page-scoped)
    page_key: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        Index("ix_bp_key_index_lookup", "project_id", "blueprint_version", "key_kind", "entry_key"),
        Index("ix_bp_key_index_page", "project_id", "blueprint_version", "page_key"),
        Index("ix_bp_key_index_decision", "project_id", "blueprint_version", "decision_id"),
    )


class DependencyEdgeRow(BpTable):
    """Field-level reference edge between two Registry entries (BP-TD-006)."""

    __tablename__ = "dependency_edge"

    project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    blueprint_version: Mapped[str] = mapped_column(String(32), primary_key=True)
    from_business_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    to_business_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    field: Mapped[str] = mapped_column(String(128), primary_key=True)

    from_dhis2_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    to_dhis2_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: ref | refs | implicit | sharing | attribute
    edge_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="ref")

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "blueprint_version",
            "from_business_key",
            "to_business_key",
            "field",
            name="uq_bp_dep_edge",
        ),
        # reverse traversal (impact analysis, deletion safety) needs the target side
        Index("ix_bp_dep_edge_to", "project_id", "blueprint_version", "to_business_key"),
    )
