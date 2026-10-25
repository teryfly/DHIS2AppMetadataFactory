"""S3 enumerations.

Two of these value sets are *contracts with other subsystems* and may not be
widened unilaterally:

* :class:`AgentRunStatus` — S1 converts the run result with ``AgentRunStatus(result.status)``
  (``factory_control/services/workflow.py:352``) against a strict six-member enum, so
  returning any other string raises ``ValueError`` inside S1's own transaction.
  :class:`RunState` is the *database* state and carries a seventh value, ``RUNNING``:
  a run that is still executing is not a result, so it is reported by raising a
  retryable error rather than by inventing a status (AI-RT-017, AI-RT-020).
* :class:`QuestionSeverity` — S1's ``QuestionSeverity`` accepts exactly these three,
  and HIGH is what makes a question blocking (AI-RT-015).
"""

from __future__ import annotations

import enum

from factory_foundation.api import DataClassification


@enum.unique
class ProviderClass(str, enum.Enum):
    """Overall SRS 20.2 — how far a provider may take data out of the Factory."""

    LOCAL_ONLY = "LOCAL_ONLY"
    PRIVATE_NETWORK = "PRIVATE_NETWORK"
    APPROVED_EXTERNAL = "APPROVED_EXTERNAL"
    UNRESTRICTED = "UNRESTRICTED"


#: Ordering used only to *report* which of two classes is stricter.  The effective
#: policy is computed by set intersection (policy/engine.py), never by ranking, so
#: "strictest" cannot drift from a hand-maintained number (AI-POL-004).
PROVIDER_CLASS_RANK: dict[ProviderClass, int] = {
    ProviderClass.UNRESTRICTED: 0,
    ProviderClass.APPROVED_EXTERNAL: 1,
    ProviderClass.PRIVATE_NETWORK: 2,
    ProviderClass.LOCAL_ONLY: 3,
}


def as_provider_class(value: object) -> ProviderClass:
    """Accept the member, its own value, or the JSON spelling of it.

    ``str(ProviderClass.LOCAL_ONLY)`` is ``"ProviderClass.LOCAL_ONLY"`` — a str-mixin enum keeps
    ``Enum.__str__`` — so ``ProviderClass(str(member))`` does *not* round-trip. Every caller
    that reads a provider class out of a payload, a row or an argument goes through here
    instead, because the failure otherwise appears as "unknown provider class" for a value the
    caller already holds.
    """
    if isinstance(value, ProviderClass):
        return value
    declared = getattr(value, "value", None)
    return ProviderClass(str(declared if declared is not None else value))


#: AI-POL-001 (overall SRS 20.1): the classification -> sendable-classes matrix.
#: Read as a **ceiling**, not as the starting state: AI-POL-002 separately requires that
#: the system *default* denies external providers, and an organisation may only widen
#: toward this table, never past it.  Collapsing the two into one constant — as the SRS's
#: wording "默认可发送" invites — makes every legal relaxation a recorded no-op, because
#: there would be nothing left below the ceiling to widen to.
POLICY_MATRIX: dict[DataClassification, frozenset[ProviderClass]] = {
    DataClassification.PUBLIC: frozenset(
        {
            ProviderClass.LOCAL_ONLY,
            ProviderClass.PRIVATE_NETWORK,
            ProviderClass.APPROVED_EXTERNAL,
            ProviderClass.UNRESTRICTED,
        }
    ),
    DataClassification.INTERNAL: frozenset(
        {
            ProviderClass.LOCAL_ONLY,
            ProviderClass.PRIVATE_NETWORK,
            ProviderClass.APPROVED_EXTERNAL,
        }
    ),
    DataClassification.CONFIDENTIAL: frozenset(
        {ProviderClass.LOCAL_ONLY, ProviderClass.PRIVATE_NETWORK}
    ),
    DataClassification.RESTRICTED: frozenset({ProviderClass.LOCAL_ONLY}),
    DataClassification.PHI_PII: frozenset(),
}

#: AI-POL-002's default, and the policy the system starts a project with: both external
#: classes unavailable to every level, so that "APPROVED_EXTERNAL" is something an
#: organisation must reach for deliberately and have audited.  Everything here is a
#: subset of :data:`POLICY_MATRIX`, which is what makes widening legal (asserted in
#: ``tests/unit/test_policy_matrix.py``).
SYSTEM_DEFAULT_MATRIX: dict[DataClassification, frozenset[ProviderClass]] = {
    level: (
        frozenset({ProviderClass.LOCAL_ONLY, ProviderClass.PRIVATE_NETWORK})
        if level is not DataClassification.RESTRICTED
        else frozenset({ProviderClass.LOCAL_ONLY})
    )
    if level is not DataClassification.PHI_PII
    else frozenset()
    for level in (
        DataClassification.PUBLIC,
        DataClassification.INTERNAL,
        DataClassification.CONFIDENTIAL,
        DataClassification.RESTRICTED,
        DataClassification.PHI_PII,
    )
}


@enum.unique
class AgentType(str, enum.Enum):
    """The eight agents of the AI-AG table (SRS 3.3)."""

    REQUIREMENTS = "REQUIREMENTS"
    DOMAIN = "DOMAIN"
    MAPPING = "MAPPING"
    METADATA_DESIGN = "METADATA_DESIGN"
    API = "API"
    UI = "UI"
    FIXTURE = "FIXTURE"
    REVIEW = "REVIEW"


@enum.unique
class AgentRunStatus(str, enum.Enum):
    """AI-RT-017 — the six results a caller may branch on.  Exhaustive on purpose."""

    COMPLETED = "COMPLETED"
    WAITING_INPUT = "WAITING_INPUT"
    FAILED = "FAILED"
    POLICY_DENIED = "POLICY_DENIED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    CANCELLED = "CANCELLED"


@enum.unique
class RunState(str, enum.Enum):
    """``ai.agent_run.status`` — :class:`AgentRunStatus` plus the non-result ``RUNNING``."""

    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    WAITING_INPUT = "WAITING_INPUT"
    FAILED = "FAILED"
    POLICY_DENIED = "POLICY_DENIED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    CANCELLED = "CANCELLED"


#: A row in one of these states is finished: its stored result may be replayed to a
#: caller that repeats the idempotency key (AI-AC-05).
TERMINAL_STATES: frozenset[RunState] = frozenset(
    {
        RunState.COMPLETED,
        RunState.FAILED,
        RunState.POLICY_DENIED,
        RunState.BUDGET_EXCEEDED,
        RunState.CANCELLED,
    }
)


@enum.unique
class ParseStatus(str, enum.Enum):
    """Result of :func:`parse_input` (SRS 7, AI-INP-006/007/003a)."""

    PARSED = "PARSED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    FAILED = "FAILED"
    POLICY_DENIED = "POLICY_DENIED"


@enum.unique
class QuestionSeverity(str, enum.Enum):
    """AI-RT-015.  HIGH is always blocking, whatever the model claimed."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


QUESTION_SEVERITY_RANK: dict[QuestionSeverity, int] = {
    QuestionSeverity.LOW: 0,
    QuestionSeverity.MEDIUM: 1,
    QuestionSeverity.HIGH: 2,
}


@enum.unique
class ItemSourceKind(str, enum.Enum):
    """Where a context item came from, which decides how it is fenced (AI-RT-030)."""

    SYSTEM_RULES = "SYSTEM_RULES"
    STAGE_RULES = "STAGE_RULES"
    APPROVED_ENTRY = "APPROVED_ENTRY"
    INPUT_ARTIFACT = "INPUT_ARTIFACT"
    REGISTRY_ENTRY = "REGISTRY_ENTRY"
    OPEN_QUESTION = "OPEN_QUESTION"
    USER_ANSWER = "USER_ANSWER"
    VALIDATION_ERRORS = "VALIDATION_ERRORS"
    SIMILAR_OBJECTS = "SIMILAR_OBJECTS"


#: The kinds whose content originated outside the Factory and is therefore data,
#: never instructions (AI-RT-030, AI-BR-07).
UNTRUSTED_KINDS: frozenset[ItemSourceKind] = frozenset(
    {
        ItemSourceKind.INPUT_ARTIFACT,
        ItemSourceKind.APPROVED_ENTRY,
        ItemSourceKind.REGISTRY_ENTRY,
        ItemSourceKind.SIMILAR_OBJECTS,
        ItemSourceKind.OPEN_QUESTION,
    }
)


@enum.unique
class NrrStatus(str, enum.Enum):
    """Mirrors S2's ``requirements.items.status`` (AI-INP-005: the NRR schema *is*
    the requirements section schema, so this vocabulary is not ours to invent)."""

    CANDIDATE = "CANDIDATE"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"


@enum.unique
class ParserType(str, enum.Enum):
    """AI-INP-002.  Whether a parser is deterministic is fixed by this table, which
    is how AI-INP-003 ("formats a deterministic parser can handle must not be handed
    to an LLM") is enforced by dispatch rather than by discipline."""

    MERMAID = "MERMAID"
    PLANTUML = "PLANTUML"
    CSV = "CSV"
    XLSX = "XLSX"
    MARKDOWN = "MARKDOWN"
    DOCX = "DOCX"
    PDF = "PDF"
    IMAGE = "IMAGE"
    DHIS2_METADATA = "DHIS2_METADATA"
    SOURCE_CODE = "SOURCE_CODE"
    OPENAPI = "OPENAPI"


#: Parsers that never call a model.  Anything absent from this set is model-assisted
#: and therefore requires human review (AI-INP-002).
DETERMINISTIC_PARSERS: frozenset[ParserType] = frozenset(
    {
        ParserType.MERMAID,
        ParserType.PLANTUML,
        ParserType.CSV,
        ParserType.XLSX,
        ParserType.DHIS2_METADATA,
    }
)


@enum.unique
class ModelCallOutcome(str, enum.Enum):
    OK = "OK"
    ERROR = "ERROR"


@enum.unique
class ProviderKind(str, enum.Enum):
    """How a model call is served.  ``MOCK`` and ``REPLAY`` never touch a network
    (AI-MOCK-001, AI-LLM-006) and are what lets the whole suite run offline."""

    LIVE = "LIVE"
    REPLAY = "REPLAY"
    MOCK = "MOCK"


@enum.unique
class Milestone(str, enum.Enum):
    """Delivery milestone a capability was scheduled for (SRS 12).  Used by
    :mod:`factory_intelligence.feature` to gate M3/M4 work in place of removing it."""

    M1 = "M1"
    M2 = "M2"
    M3 = "M3"
    M4 = "M4"
    M5 = "M5"


#: What this build delivers.  SRS 12 puts the API/UI/Fixture agents and in-stage
#: chat in M3, and desensitisation and budget enforcement in M4.
CURRENT_MILESTONE: Milestone = Milestone.M2

_MILESTONE_RANK: dict[Milestone, int] = {m: i for i, m in enumerate(Milestone)}


def milestone_at_least(milestone: Milestone | str) -> bool:
    """True when ``milestone`` has already been delivered in this build."""
    return _MILESTONE_RANK[Milestone(milestone)] <= _MILESTONE_RANK[CURRENT_MILESTONE]


@enum.unique
class PolicyResult(str, enum.Enum):
    ALLOWED = "ALLOWED"
    DENIED = "DENIED"


@enum.unique
class BudgetState(str, enum.Enum):
    """AI-COST-002/003 — reported alongside every run result, never a status of its own."""

    UNSET = "UNSET"
    OK = "OK"
    WARNING = "WARNING"
    EXCEEDED = "EXCEEDED"
