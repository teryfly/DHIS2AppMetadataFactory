"""S3 errors.

Every class here is a :class:`FactoryError` subclass that pins one error code and
adds nothing else, so S1 maps it to a REST response with the same mechanism it uses
for S2 and S4 (00 doc 6.5).  Retryability comes from the registry, not from the
class: ``error_catalog.is_retryable(code)`` is what decides whether a Temporal
activity should back off (overall SRS 19.2.2).

``LLM_ERROR``, ``LLM_TIMEOUT`` and ``POLICY_DENIED`` are already registered as
owned by S3 in S0's built-in catalogue, so they are deliberately *not* repeated in
:data:`factory_intelligence.registries.AI_ERROR_CODES`.
"""

from __future__ import annotations

from factory_foundation.api import FactoryError


class AiError(FactoryError):
    """Base for every S3 failure."""

    code = "AI_ERROR"


class FeatureNotEnabled(AiError):
    """A capability scheduled for a later milestone was called (SRS 12)."""

    code = "AI_FEATURE_NOT_ENABLED"


class AgentRunInProgress(AiError):
    """The same idempotency key is still being executed by another attempt.

    AI-AC-05 says a repeated call must continue the same run rather than restart it.
    When the first call has not finished there is no result to hand back, and S1's
    ``AgentRunStatus`` is a strict six-member enum, so inventing a status is not an
    option either.  Raising a retryable error lets the activity back off and re-enter
    the same run — the same resolution S2 chose for ``BP_IN_PROGRESS``.
    """

    code = "AI_IN_PROGRESS"


class RunNotResumable(AiError):
    """Resume was requested for a run that is not waiting for input (AI-RT-015)."""

    code = "AI_RUN_NOT_RESUMABLE"


class ScannedDocumentUnsupported(AiError):
    """AI-INP-003a: a PDF or Word file with no text layer is outside MVP.

    Failing loudly is the requirement: the alternative is a silently empty NRR.
    """

    code = "SCANNED_DOCUMENT_UNSUPPORTED"


class ParserDependencyMissing(AiError):
    """The library for a mandatory input format is not installed (AI-INP-002).

    Never downgraded to an empty result, for the same reason as
    :class:`ScannedDocumentUnsupported`.
    """

    code = "AI_PARSER_DEPENDENCY_MISSING"


class PolicyDeniedError(AiError):
    """Raised only where a run result cannot carry the denial (e.g. ``explain_issues``).

    A normal agent run *returns* ``POLICY_DENIED`` instead (AI-RT-017); the error code
    exists for the read-only and side-channel paths where a status is not part of the
    contract.
    """

    code = "POLICY_DENIED"


class PolicyRelaxationDenied(AiError):
    """AI-POL-005: a relaxation was asked for that would send PHI_PII to a model."""

    code = "AI_POLICY_RELAXATION_DENIED"


class ContextTooLarge(AiError):
    """AI-POL-003: the context exceeds the model's budget.

    Truncating instead would be the "静默截断" the SRS forbids, so this is an error.
    """

    code = "AI_CONTEXT_TOO_LARGE"


class ContextContainsPhi(AiError):
    """A caller tried to add a PHI_PII item to a model context (AC-POL-02)."""

    code = "AI_CONTEXT_CONTAINS_PHI"


class OutputInvalid(AiError):
    """The model output failed its Schema after the one corrective retry (AI-RT-011)."""

    code = "AI_OUTPUT_INVALID"


class ConstraintViolation(AiError):
    """The output passed the Schema but broke an agent-level prohibition (AI-RT-012).

    Treated as a Schema failure by the runtime; this class is what the checks raise
    internally and what a caller sees when the violation is not retryable.
    """

    code = "AI_CONSTRAINT_VIOLATION"


class PatchRejected(AiError):
    """S2 refused the patch (AI-RT-013) and the corrective retry did not fix it."""

    code = "AI_PATCH_REJECTED"


class PromptActivationBlocked(AiError):
    """AI-REG-003: no evaluation pass, or no registered regression pass."""

    code = "AI_PROMPT_ACTIVATION_BLOCKED"


class PromptNotFound(AiError):
    """A declared prompt name/version is not in the registry (AI-REG-001)."""

    code = "AI_PROMPT_NOT_FOUND"


class PromptNotActivated(AiError):
    """AI-REG-002: an agent ran with no activated prompt, which is a deployment step nobody
    finished rather than a model fault.

    The runtime refuses it instead of degrading to an empty placeholder: a run that answers
    from an inert, un-activated prompt still spends a model call, still produces output, and
    gives a reviewer no signal that governance never took effect.  A refusal is the honest
    answer, and it names the agent so whoever owns the deployment can finish it.
    """

    code = "AI_PROMPT_NOT_ACTIVATED"


class ProviderUnavailable(AiError):
    """No model could be reached for this run (AI-POL-007, AI-LLM-003)."""

    code = "AI_PROVIDER_UNAVAILABLE"


class GovernanceActionUnknown(AiError):
    """``governance()`` was called with an action this build does not implement."""

    code = "AI_GOVERNANCE_ACTION_UNKNOWN"


class EvalSetMissing(AiError):
    """AI-EVAL-001: the agent has no evaluation set at that version."""

    code = "AI_EVAL_SET_MISSING"


class BudgetExceeded(AiError):
    """AI-COST-002: raised when a caller needs an error rather than a run status."""

    code = "AI_BUDGET_EXCEEDED"


class BudgetInvalid(AiError):
    """AI-COST-002: a budget statement that cannot be compared against recorded cost.

    A limit is only meaningful next to the unit prices it is compared with, and a
    threshold outside ``(0, 1)`` would either warn on every run or never warn at all, so
    the shape is refused before the row is written rather than half-obeyed afterwards.
    """

    code = "AI_BUDGET_INVALID"


__all__ = [
    "AiError",
    "AgentRunInProgress",
    "BudgetExceeded",
    "BudgetInvalid",
    "ConstraintViolation",
    "ContextContainsPhi",
    "ContextTooLarge",
    "EvalSetMissing",
    "FeatureNotEnabled",
    "GovernanceActionUnknown",
    "PatchRejected",
    "ParserDependencyMissing",
    "PolicyDeniedError",
    "PolicyRelaxationDenied",
    "PromptActivationBlocked",
    "PromptNotFound",
    "PromptNotActivated",
    "ProviderUnavailable",
    "RunNotResumable",
    "ScannedDocumentUnsupported",
    "OutputInvalid",
]
