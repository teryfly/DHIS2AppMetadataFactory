"""``bp.environment_binding`` — per-environment UID and observation baseline.

Bindings are keyed by (project, version, environment, businessKey) and are *not*
part of the version content, so the same approved version can be deployed to
several environments (D-02, BP-BR-14).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from factory_blueprint.enums import BindingResolution
from factory_blueprint.models.base import BpTable


class EnvironmentBindingRow(BpTable):
    __tablename__ = "environment_binding"

    project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    blueprint_version: Mapped[str] = mapped_column(String(32), primary_key=True)
    environment_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    business_key: Mapped[str] = mapped_column(String(255), primary_key=True)

    uid: Mapped[str] = mapped_column(String(11), nullable=False)
    observed_last_updated: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    observed_hash: Mapped[str | None] = mapped_column(String(71), nullable=True)
    discovery_result_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resolution: Mapped[str] = mapped_column(
        String(16), nullable=False, default=BindingResolution.AUTO.value
    )
    resolved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    note: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    #: set by import: the target deployment's environments differ (BP-EXP-002)
    pending_reparse: Mapped[bool] = mapped_column(nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "blueprint_version",
            "environment_key",
            "uid",
            name="uq_bp_binding_env_uid",
        ),
        Index("ix_bp_binding_env", "project_id", "blueprint_version", "environment_key"),
        Index("ix_bp_binding_uid", "uid"),
    )
