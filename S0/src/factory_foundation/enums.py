"""Shared enumerations for the foundation.

Value sets come from the overall SRS; classification ordering is used by the
artifact service to enforce FD-ART-008 / FR-POL-001.
"""

from __future__ import annotations

import enum


@enum.unique
class DataClassification(str, enum.Enum):
    """Overall SRS 20.1, ordered low → high (00 doc, S0 SRS 2)."""

    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    RESTRICTED = "RESTRICTED"
    PHI_PII = "PHI_PII"

    @property
    def rank(self) -> int:
        return _CLASSIFICATION_RANK[self]

    def at_least(self, other: DataClassification) -> bool:
        return self.rank >= other.rank

    @classmethod
    def max_of(cls, items: list[DataClassification]) -> DataClassification:
        if not items:
            return cls.PUBLIC
        return max(items, key=lambda c: c.rank)


_CLASSIFICATION_RANK: dict[DataClassification, int] = {
    DataClassification.PUBLIC: 0,
    DataClassification.INTERNAL: 1,
    DataClassification.CONFIDENTIAL: 2,
    DataClassification.RESTRICTED: 3,
    DataClassification.PHI_PII: 4,
}


@enum.unique
class ArtifactStatus(str, enum.Enum):
    """FR-DATA-002."""

    DRAFT = "DRAFT"
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    SUPERSEDED = "SUPERSEDED"


@enum.unique
class OperatorType(str, enum.Enum):
    USER = "USER"
    SYSTEM = "SYSTEM"


@enum.unique
class IdempotencyStatus(str, enum.Enum):
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


#: Idempotency scope whose records must never be expiry-deleted (FD-IDEM-003,
#: FD-BR-08): DHIS2 execution class.
DHIS2_EXECUTION_SCOPE = "dx.execution"
