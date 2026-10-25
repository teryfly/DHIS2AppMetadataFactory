"""``ai`` schema tables.  Importing this package registers every table on
:class:`~factory_foundation.api.AiBase.metadata`, which is what the migration and the
cross-schema foreign-key check operate on.
"""

from __future__ import annotations

from factory_intelligence.models.base import AiTable, TimestampMixin
from factory_intelligence.models.call import ModelCallRow
from factory_intelligence.models.cost import BudgetRow
from factory_intelligence.models.deferred import ChatSessionRow, DesensitizationRecordRow
from factory_intelligence.models.eval import EvalCaseResultRow, EvalRunRow
from factory_intelligence.models.exchange import LlmExchangeRow
from factory_intelligence.models.nrr import NrrEntryRow
from factory_intelligence.models.parse import InputParseRow
from factory_intelligence.models.policy import (
    OrgPolicyRow,
    PolicyDecisionRow,
    PolicyRelaxationRow,
)
from factory_intelligence.models.registry import (
    ModelRow,
    PromptActivationRow,
    PromptVersionRow,
    ProviderRow,
    RegressionRefRow,
)
from factory_intelligence.models.run import (
    AgentRunContextRow,
    AgentRunRow,
    ConfirmationRecordRow,
)

#: Every table :func:`factory_intelligence.cleanup.delete_project_data` may remove rows
#: from (AI-DEL-001).  Absent on purpose: ``model_call`` (AI-DEL-001 keeps the usage
#: records, which are aggregate values with no content, and its immutability trigger would
#: refuse the deletion anyway); ``policy_relaxation``, the registries and ``budget``
#: (organisation-level, not project data); and ``eval_run`` with ``eval_case_result`` —
#: an evaluation is of a prompt version against a model, not of a project, and deleting it
#: would erase the very evidence AI-REG-003 gates an activation on.
DELETABLE_TABLES: tuple[str, ...] = (
    "agent_run_context",
    "confirmation_record",
    "agent_run",
    "policy_decision",
    "nrr_entry",
    "input_parse",
    "llm_exchange",
    "chat_session",
    "desensitization_record",
)

__all__ = [
    "AiTable",
    "TimestampMixin",
    "InputParseRow",
    "NrrEntryRow",
    "LlmExchangeRow",
    "AgentRunRow",
    "AgentRunContextRow",
    "ConfirmationRecordRow",
    "PolicyDecisionRow",
    "OrgPolicyRow",
    "PolicyRelaxationRow",
    "ModelCallRow",
    "ProviderRow",
    "ModelRow",
    "PromptVersionRow",
    "PromptActivationRow",
    "RegressionRefRow",
    "EvalRunRow",
    "EvalCaseResultRow",
    "BudgetRow",
    "ChatSessionRow",
    "DesensitizationRecordRow",
    "DELETABLE_TABLES",
]
