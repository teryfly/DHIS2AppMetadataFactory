"""Core ORM models.

Importing this package registers every ``core`` table on
:data:`factory_foundation.models.base.CoreBase.metadata` so migrations and the
cross-schema FK checker see them.  Other subsystems import their own schema
bases and add tables there.
"""

from factory_foundation.models.artifact import (
    ArtifactRow,
    ArtifactSourceRow,
    ArtifactStagingRow,
)
from factory_foundation.models.audit import AuditChainStateRow, AuditEventRow
from factory_foundation.models.base import (
    AiBase,
    BpBase,
    CoreBase,
    CpBase,
    DxBase,
)
from factory_foundation.models.idempotency import IdempotencyRecordRow
from factory_foundation.models.registry import (
    ArtifactTypeRow,
    AuditEventTypeRow,
    ErrorCodeRow,
    IdPrefixRow,
)

__all__ = [
    "AiBase",
    "BpBase",
    "CoreBase",
    "CpBase",
    "DxBase",
    "ArtifactRow",
    "ArtifactSourceRow",
    "ArtifactStagingRow",
    "AuditEventRow",
    "AuditChainStateRow",
    "IdempotencyRecordRow",
    "ArtifactTypeRow",
    "AuditEventTypeRow",
    "ErrorCodeRow",
    "IdPrefixRow",
]
