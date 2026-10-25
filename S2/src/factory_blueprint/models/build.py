"""``bp.build_record`` and ``bp.validation_run``.

The build record is the reproducibility contract (BP-CMP-003, BP-NFR-08): every
compatibility dimension is a column so a reproduce run can assert them, while
``inputs``/``build_context``/``output_hashes`` keep the exact byte-level recipe.

S4 probe results are recorded here (``s4_probe``) and never inside an artifact:
they are not a function of the compilation inputs, so letting them into artifact
bytes would break byte-level reproduction across deployments (AC-DATA-01).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from factory_blueprint.enums import BuildKind, BuildStatus
from factory_blueprint.models.base import BpTable


class BuildRecordRow(BpTable):
    __tablename__ = "build_record"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default=BuildKind.BASELINE.value)
    blueprint_version: Mapped[str] = mapped_column(String(32), nullable=False)
    blueprint_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    environment_key: Mapped[str | None] = mapped_column(String(64), nullable=True)

    #: [{role, artifactId, type, artifactHash}] — idMap-rewritable on import
    inputs: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    build_context: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    #: the caller-supplied parameters that changed this product (FR-CMP-004 replay)
    compile_parameters: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )

    blueprint_schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    compiler_version: Mapped[str] = mapped_column(String(32), nullable=False)
    template_version: Mapped[str] = mapped_column(String(32), nullable=False)
    descriptor_version: Mapped[str] = mapped_column(String(32), nullable=False)
    profile_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    openapi_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    package_template_version: Mapped[str | None] = mapped_column(String(32), nullable=True)

    output_artifact_ids: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    output_hashes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    package_hash: Mapped[str | None] = mapped_column(String(71), nullable=True)
    #: hash of the normalized build-record body, compared by reproduce()
    records_hash: Mapped[str | None] = mapped_column(String(71), nullable=True)
    #: hash of (kind, version, environment, build context) — the dedup key that lets a
    #: retried compilation with an identical idempotency key return the first result
    recipe_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=BuildStatus.COMPLETED.value
    )
    s4_probe: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        # recipe_hash is a lookup key, not a uniqueness rule: compiling the same
        # version with the same context again is required by AC-REG-01, and repeat
        # suppression belongs to the idempotency store instead
        Index("ix_bp_build_record_recipe", "project_id", "recipe_hash"),
        Index("ix_bp_build_record_project_created", "project_id", "created_at"),
    )


class ValidationRunRow(BpTable):
    __tablename__ = "validation_run"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    build_id: Mapped[str] = mapped_column(String(64), nullable=False)
    report_artifact_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    blocking_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warning_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    info_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: structural hash of the sorted issue list, so an identical run is comparable
    issues_digest: Mapped[str] = mapped_column(String(71), nullable=False)

    __table_args__ = (Index("ix_bp_validation_run_build", "build_id"),)
