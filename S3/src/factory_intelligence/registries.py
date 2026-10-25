"""S3 registries: artifact types, audit event types, error codes, ID prefixes.

00 doc 6.10 requires every library subsystem to register these at start-up, and S0
rejects anything unregistered: an unknown artifact type cannot be created, an unknown
audit event type cannot be written (``require_registered``), an unknown error code is
not retryable, and an unregistered ID prefix cannot be minted.  A capability that
forgets to register here fails at *runtime* in the caller's transaction, so the four
tables below are kept in lock-step with the code by
``tests/unit/test_architecture.py``.
"""

from __future__ import annotations

#: Artifact types S3 creates.  ``NRR`` and ``AGENT_OUTPUT`` are already declared in
#: S0's initial catalogue with owner ``["S3"]``; they are restated so S3 owns them
#: explicitly rather than by S0's goodwill (SRS 1.2, 6.6).
AI_ARTIFACT_TYPES: dict[str, list[str]] = {
    "NRR": ["S3"],
    "AGENT_OUTPUT": ["S3"],
}

#: Sizes are generous but finite: AI-NFR-06 allows a 100 MB input, and a model
#: context artifact or an agent output has no business being that large.
AI_ARTIFACT_MAX_SIZES: dict[str, int] = {
    "NRR": 32 * 1024 * 1024,
    "AGENT_OUTPUT": 32 * 1024 * 1024,
}

#: The sixteen events of SRS appendix A, verbatim.  Names must satisfy S0's
#: ``^[a-z][a-z0-9_]*\\.[a-z0-9_]+\\.[a-z0-9_]+$``.
AI_AUDIT_EVENT_TYPES: dict[str, str] = {
    "ai.input.parsed": "S3",
    "ai.input.parse_failed": "S3",
    "ai.agent.started": "S3",
    "ai.agent.completed": "S3",
    "ai.agent.failed": "S3",
    "ai.agent.waiting_input": "S3",
    "ai.agent.resumed": "S3",
    "ai.agent.cancelled": "S3",
    "ai.model.called": "S3",
    "ai.policy.denied": "S3",
    "ai.policy.relaxed": "S3",
    "ai.prompt.activated": "S3",
    "ai.model.registered": "S3",
    "ai.budget.updated": "S3",
    "ai.desensitization.created": "S3",
    "ai.project.data_deleted": "S3",
}

#: ``{code: (owner, retryable)}`` — retryability follows overall SRS 19.2.2: a
#: transport-class or contention failure may be retried by the caller's workflow, a
#: schema, policy, budget or approval failure may not.
#:
#: ``LLM_ERROR``, ``LLM_TIMEOUT`` and ``POLICY_DENIED`` are already owned by S3 in
#: S0's built-in catalogue and are intentionally not repeated here.
AI_ERROR_CODES: dict[str, tuple[str, bool]] = {
    # SRS 12: a capability from a later milestone was called
    "AI_FEATURE_NOT_ENABLED": ("S3", False),
    # AI-AC-05: the same key is still executing elsewhere; back off and re-enter
    "AI_IN_PROGRESS": ("S3", True),
    "AI_RUN_NOT_RESUMABLE": ("S3", False),
    # AI-INP-003a: named exactly as the SRS names it
    "SCANNED_DOCUMENT_UNSUPPORTED": ("S3", False),
    "AI_PARSER_DEPENDENCY_MISSING": ("S3", False),
    # AI-INP-002/003: dispatch found no deterministic parser and the caller refused the
    # model-assisted path, so the input is reported instead of parsed by guessing
    "AI_PARSER_UNMATCHED": ("S3", False),
    # AI-INP-003a's reasoning again: bytes a chosen deterministic parser cannot read at all
    # are a failure, never a silently empty NRR
    "AI_PARSER_INPUT_INVALID": ("S3", False),
    "AI_POLICY_RELAXATION_DENIED": ("S3", False),
    # AI-POL-003: the answer is to refuse, not to truncate
    "AI_CONTEXT_TOO_LARGE": ("S3", False),
    "AI_CONTEXT_CONTAINS_PHI": ("S3", False),
    "AI_OUTPUT_INVALID": ("S3", False),
    "AI_CONSTRAINT_VIOLATION": ("S3", False),
    "AI_PATCH_REJECTED": ("S3", False),
    "AI_PROMPT_ACTIVATION_BLOCKED": ("S3", False),
    "AI_PROMPT_NOT_FOUND": ("S3", False),
    # AI-REG-002: an agent ran with no activated prompt -- a deployment gap, never a model fault
    "AI_PROMPT_NOT_ACTIVATED": ("S3", False),
    "AI_PROVIDER_UNAVAILABLE": ("S3", True),
    "AI_GOVERNANCE_ACTION_UNKNOWN": ("S3", False),
    "AI_EVAL_SET_MISSING": ("S3", False),
    "AI_BUDGET_EXCEEDED": ("S3", False),
    # AI-COST-002: a limit/threshold/currency statement that cannot be compared with cost
    "AI_BUDGET_INVALID": ("S3", False),
    "AI_ERROR": ("S3", False),
}

#: ID prefixes (00 doc 6.7: type-prefixed ULID).  ``ptch`` is deliberately absent:
#: a Blueprint patch belongs to S2's namespace, and S3 supplies one as the author.
AI_ID_PREFIXES: dict[str, str] = {
    "aipr": "S3",  # InputParse
    "anre": "S3",  # NrrEntry (AI-INP-015's query index)
    "arun": "S3",  # AgentRun
    "arct": "S3",  # AgentRunContext
    "apdc": "S3",  # PolicyDecision
    "amcl": "S3",  # ModelCall
    "aexg": "S3",  # LlmExchange (parse diagnosis log)
    "acht": "S3",  # ChatSession (AI-CHAT-003)
    "apvd": "S3",  # Provider
    "amdl": "S3",  # Model
    "apvr": "S3",  # PromptVersion
    "aeval": "S3",  # EvalRun
    "aecr": "S3",  # EvalCaseResult
    "abus": "S3",  # Budget
    "adsn": "S3",  # DesensitizationRecord
    "achs": "S3",  # ChatSession
    "aplax": "S3",  # PolicyRelaxation
    "acnf": "S3",  # ConfirmationRecord
    "aqcn": "S3",  # question activation / local-id sequence
    "argr": "S3",  # regression reference (AI-REG-003)
}

#: Idempotency scopes (00 doc 6.4).  Scopes keep S3's keys out of S1's and S2's
#: namespace in the shared ``core.idempotency_record`` table.
class IdemScope:
    INPUT_PARSE = "ai.input.parse"
    AGENT_START = "ai.agent.start"
    AGENT_RESUME = "ai.agent.resume"
    EXPLAIN_ISSUES = "ai.agent.explain"
    EVALUATION_RUN = "ai.eval.run"
    DESENSITIZE = "ai.desensitize"
    STAGE_CHAT = "ai.chat.turn"


__all__ = [
    "AI_ARTIFACT_TYPES",
    "AI_ARTIFACT_MAX_SIZES",
    "AI_AUDIT_EVENT_TYPES",
    "AI_ERROR_CODES",
    "AI_ID_PREFIXES",
    "IdemScope",
]
