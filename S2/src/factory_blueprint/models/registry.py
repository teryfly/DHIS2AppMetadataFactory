"""``bp.registry_entry`` — normalized Registry index per Blueprint version.

Rebuildable from the version's ``registry`` section (BP-VER-002).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from factory_blueprint.models.base import BpTable


class RegistryEntryRow(BpTable):
    __tablename__ = "registry_entry"

    project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    blueprint_version: Mapped[str] = mapped_column(String(32), primary_key=True)
    business_key: Mapped[str] = mapped_column(Text, primary_key=True)

    dhis2_type: Mapped[str] = mapped_column(String(64), nullable=False)
    #: CREATE/UPDATE carry the S2-allocated UID, identical across environments (D-02)
    uid: Mapped[str | None] = mapped_column(String(11), nullable=True)
    code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    disposition: Mapped[str] = mapped_column(String(16), nullable=False)
    managed: Mapped[bool] = mapped_column(nullable=False)
    controlled_fields: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    decision_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # observation baseline (BP-REG-004)
    source_environment: Mapped[str | None] = mapped_column(String(64), nullable=True)
    observed_last_updated: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    observed_hash: Mapped[str | None] = mapped_column(String(71), nullable=True)
    discovery_result_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        # BP-REG-005: one concept one UID, one UID one concept
        UniqueConstraint(
            "project_id", "blueprint_version", "uid", name="uq_bp_registry_uid_per_version"
        ),
        # BP-REG-009: code unique per type within the project
        UniqueConstraint(
            "project_id",
            "blueprint_version",
            "dhis2_type",
            "code",
            name="uq_bp_registry_code_per_version",
        ),
        Index("ix_bp_registry_type", "project_id", "blueprint_version", "dhis2_type"),
        Index("ix_bp_registry_disposition", "project_id", "blueprint_version", "disposition"),
        Index("ix_bp_registry_uid", "uid"),
        Index("ix_bp_registry_decision", "project_id", "blueprint_version", "decision_id"),
    )
