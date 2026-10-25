"""Per-schema declarative bases (FD-DB-002).

Each subsystem defines its tables on the base bound to its own schema so the
tables land in ``core`` / ``cp`` / ``bp`` / ``ai`` / ``dx`` automatically
(00 doc 4.1, D-08).  S0 only owns ``core``; the other bases exist so that the
cross-schema foreign-key checker (FD-DB-006) can see tables from every schema
that have been registered in the process, and so a subsystem can subclass the
matching base directly.
"""

from __future__ import annotations

from typing import ClassVar

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

from factory_foundation.common.errors import Schema

# Naming convention keeps migration diffs deterministic.
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def _md(schema: str) -> MetaData:
    return MetaData(schema=schema, naming_convention=NAMING_CONVENTION)


class CoreBase(DeclarativeBase):
    """Schema ``core`` — owned by S0."""

    metadata: ClassVar[MetaData] = _md(Schema.CORE.value)


class CpBase(DeclarativeBase):
    """Schema ``cp`` — owned by S1."""

    metadata: ClassVar[MetaData] = _md(Schema.CP.value)


class BpBase(DeclarativeBase):
    """Schema ``bp`` — owned by S2."""

    metadata: ClassVar[MetaData] = _md(Schema.BP.value)


class AiBase(DeclarativeBase):
    """Schema ``ai`` — owned by S3."""

    metadata: ClassVar[MetaData] = _md(Schema.AI.value)


class DxBase(DeclarativeBase):
    """Schema ``dx`` — owned by S4."""

    metadata: ClassVar[MetaData] = _md(Schema.DX.value)


#: All schema bases, used by the FK checker and test fixtures.
SCHEMA_BASES: dict[str, type[DeclarativeBase]] = {
    Schema.CORE.value: CoreBase,
    Schema.CP.value: CpBase,
    Schema.BP.value: BpBase,
    Schema.AI.value: AiBase,
    Schema.DX.value: DxBase,
}

ALL_METADATA: list[MetaData] = [b.metadata for b in SCHEMA_BASES.values()]


def register_schema_base(schema: str, base: type[DeclarativeBase]) -> None:
    """Allow a subsystem to register its own base for FK checking (00 doc 4.2)."""
    SCHEMA_BASES[schema] = base
    if base.metadata not in ALL_METADATA:
        ALL_METADATA.append(base.metadata)
