"""S1 domain enumerations.

Values follow the S1 SRS: stage states (CP-WF-003), workflow states (CP-WF-023),
Deployment Run states (CP-WF-019), Gate types and states (CP-GATE-001/003),
Question lifecycle (CP-Q-003), Change Request states (CP-CR-002), project
milestones (CP-WF-024), bound-object types (SRS 2), notification types
(CP-NTF-001) and the event type list (总体 SRS 19.2.3 plus CP-EVT-007 additions).
"""

from __future__ import annotations

import enum


class StrEnum(str, enum.Enum):  # noqa: UP042 - str mixin is what the cp columns and DTOs store
    """``str`` enum whose value is the member name, all upper-case by convention."""

    @classmethod
    def values(cls) -> tuple[str, ...]:
        return tuple(m.value for m in cls)


@enum.unique
class StageType(StrEnum):
    """Design main line plus per-Run steps and delivery (00 doc 3.1)."""

    REQUIREMENTS = "REQUIREMENTS"
    DOMAIN = "DOMAIN"
    DISCOVERY = "DISCOVERY"
    MAPPING = "MAPPING"
    PAGE_DESIGN = "PAGE_DESIGN"
    BASELINE_COMPILE = "BASELINE_COMPILE"
    BIND_COMPILE = "BIND_COMPILE"
    STATIC_VALIDATION = "STATIC_VALIDATION"
    DHIS2_VALIDATE = "DHIS2_VALIDATE"
    COMMIT = "COMMIT"
    POST_VERIFY = "POST_VERIFY"
    FIXTURE_IMPORT = "FIXTURE_IMPORT"
    HANDOFF = "HANDOFF"


#: The environment-independent design line, in order.
DESIGN_STAGES: tuple[StageType, ...] = (
    StageType.REQUIREMENTS,
    StageType.DOMAIN,
    StageType.DISCOVERY,
    StageType.MAPPING,
    StageType.PAGE_DESIGN,
    StageType.BASELINE_COMPILE,
)

#: The fixed per-environment Run steps, in order (CP-WF-021 for the tail).
RUN_STAGES: tuple[StageType, ...] = (
    StageType.BIND_COMPILE,
    StageType.STATIC_VALIDATION,
    StageType.DHIS2_VALIDATE,
    StageType.COMMIT,
    StageType.POST_VERIFY,
    StageType.FIXTURE_IMPORT,
)

#: Stages whose execution is automatic — no agent, no human submission
#: (附录 A 触发说明).
AUTO_STAGES: frozenset[StageType] = frozenset(
    {
        StageType.DISCOVERY,
        StageType.BASELINE_COMPILE,
        StageType.BIND_COMPILE,
        StageType.STATIC_VALIDATION,
        StageType.DHIS2_VALIDATE,
        StageType.POST_VERIFY,
        StageType.FIXTURE_IMPORT,
    }
)

#: Stages backed by an S3 agent, hence open to the manual-authoring path
#: (CP-WF-029).
AGENT_STAGES: frozenset[StageType] = frozenset(
    {StageType.REQUIREMENTS, StageType.DOMAIN, StageType.MAPPING, StageType.PAGE_DESIGN}
)

#: Stages that end in a design Gate (CP-GATE-001).
DESIGN_GATE_STAGES: frozenset[StageType] = frozenset(
    {StageType.REQUIREMENTS, StageType.DOMAIN, StageType.MAPPING, StageType.PAGE_DESIGN}
)


@enum.unique
class StageStatus(StrEnum):
    """CP-WF-003."""

    LOCKED = "LOCKED"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING_INPUT = "WAITING_INPUT"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    PASSED = "PASSED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    INVALIDATED = "INVALIDATED"
    SKIPPED = "SKIPPED"
    MANUAL_CHECK = "MANUAL_CHECK"


#: Stage states that mean "this stage still needs somebody to act".
WAITING_STAGE_STATES: frozenset[StageStatus] = frozenset(
    {StageStatus.WAITING_INPUT, StageStatus.WAITING_APPROVAL}
)

#: Terminal-for-this-run states that release the single-RUNNING slot (CP-WF-006).
ACTIVE_STAGE_STATES: frozenset[StageStatus] = frozenset(
    {StageStatus.RUNNING, StageStatus.WAITING_INPUT, StageStatus.WAITING_APPROVAL, StageStatus.MANUAL_CHECK}
)


@enum.unique
class WorkflowStatus(StrEnum):
    """CP-WF-023 derivation targets, plus the stored CANCELLED marker."""

    ACTIVE = "ACTIVE"
    WAITING_USER = "WAITING_USER"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    COMPLETED = "COMPLETED"
    PAUSED = "PAUSED"
    CANCELLED = "CANCELLED"


@enum.unique
class RunStatus(StrEnum):
    """CP-WF-019."""

    CREATED = "CREATED"
    IN_PROGRESS = "IN_PROGRESS"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    COMMITTED = "COMMITTED"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    MANUAL_CHECK = "MANUAL_CHECK"


#: Run states that count as "an in-progress Run occupies this environment"
#: (CP-PRJ-004, CP-WF-018, CP-BR-12).
OPEN_RUN_STATES: frozenset[RunStatus] = frozenset(
    {
        RunStatus.CREATED,
        RunStatus.IN_PROGRESS,
        RunStatus.WAITING_APPROVAL,
        RunStatus.MANUAL_CHECK,
    }
)

#: Run states that mean COMMIT has happened or is underway and must not be
#: cancelled out from under the executor (CP-WF-011).
COMMIT_REACHED_RUN_STATES: frozenset[RunStatus] = frozenset(
    {RunStatus.COMMITTED, RunStatus.VERIFIED}
)


@enum.unique
class GateType(StrEnum):
    """CP-GATE-001."""

    REQUIREMENT_GATE = "REQUIREMENT_GATE"
    DOMAIN_GATE = "DOMAIN_GATE"
    ARCHITECTURE_GATE = "ARCHITECTURE_GATE"
    DESIGN_GATE = "DESIGN_GATE"
    PACKAGE_GATE = "PACKAGE_GATE"
    DEPLOY_GATE = "DEPLOY_GATE"
    HANDOFF_GATE = "HANDOFF_GATE"

    @property
    def is_design_gate(self) -> bool:
        """Design Gates bind a working draft and promote a Blueprint version on approval."""
        return self in _DESIGN_GATES

    @property
    def stage(self) -> StageType:
        """The stage this Gate closes (CP-GATE-001 position column)."""
        return _GATE_STAGE[self.value]


_DESIGN_GATES: frozenset[GateType] = frozenset(
    {
        GateType.REQUIREMENT_GATE,
        GateType.DOMAIN_GATE,
        GateType.ARCHITECTURE_GATE,
        GateType.DESIGN_GATE,
    }
)

_GATE_STAGE: dict[str, StageType] = {
    GateType.REQUIREMENT_GATE.value: StageType.REQUIREMENTS,
    GateType.DOMAIN_GATE.value: StageType.DOMAIN,
    GateType.ARCHITECTURE_GATE.value: StageType.MAPPING,
    GateType.DESIGN_GATE.value: StageType.PAGE_DESIGN,
    GateType.PACKAGE_GATE.value: StageType.STATIC_VALIDATION,
    GateType.DEPLOY_GATE.value: StageType.COMMIT,
    GateType.HANDOFF_GATE.value: StageType.HANDOFF,
}


@enum.unique
class GateStatus(StrEnum):
    """CP-GATE-003."""

    WAITING = "WAITING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    INVALIDATED = "INVALIDATED"


@enum.unique
class RefType(StrEnum):
    """Bound-object types (SRS 2 "绑定对象")."""

    ARTIFACT = "ARTIFACT"
    BLUEPRINT_DRAFT = "BLUEPRINT_DRAFT"
    BLUEPRINT_VERSION = "BLUEPRINT_VERSION"
    ENVIRONMENT = "ENVIRONMENT"
    EXECUTION = "EXECUTION"


@enum.unique
class QuestionStatus(StrEnum):
    """CP-Q-003."""

    OPEN = "OPEN"
    ANSWERED = "ANSWERED"
    WITHDRAWN = "WITHDRAWN"
    SUPERSEDED = "SUPERSEDED"


@enum.unique
class QuestionSeverity(StrEnum):
    """CP-Q-002."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


@enum.unique
class QuestionSourceKind(StrEnum):
    """CP-Q-001 — who raised the question."""

    AGENT = "AGENT"
    PATCH_CONFLICT = "PATCH_CONFLICT"
    USER = "USER"


@enum.unique
class ChangeRequestStatus(StrEnum):
    """CP-CR-002."""

    DRAFT = "DRAFT"
    ANALYZED = "ANALYZED"
    APPROVED = "APPROVED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


@enum.unique
class MilestoneStatus(StrEnum):
    """CP-WF-024, derived from the current Blueprint version."""

    DRAFT = "DRAFT"
    DESIGN_APPROVED = "DESIGN_APPROVED"
    PACKAGE_APPROVED = "PACKAGE_APPROVED"
    DHIS2_VALIDATED = "DHIS2_VALIDATED"
    DEPLOYED = "DEPLOYED"
    VERIFIED = "VERIFIED"
    HANDED_OFF = "HANDED_OFF"


@enum.unique
class DeliveryMode(StrEnum):
    """2.5 — S1 only mirrors the value to decide SKIPPED stages; semantics live in S2 (D-15)."""

    A = "A"
    B = "B"


@enum.unique
class EnvironmentClass(StrEnum):
    """S4 environment category used for PRODUCTION-class rules (CP-WF-020, CP-GATE-012)."""

    DEV = "DEV"
    TEST = "TEST"
    UAT = "UAT"
    PRODUCTION = "PRODUCTION"


PRODUCTION_CLASS = EnvironmentClass.PRODUCTION


@enum.unique
class NotificationType(StrEnum):
    """CP-NTF-001."""

    GATE_PENDING = "GATE_PENDING"
    GATE_REMINDER = "GATE_REMINDER"
    GATE_APPROVED = "GATE_APPROVED"
    GATE_REJECTED = "GATE_REJECTED"
    GATE_INVALIDATED = "GATE_INVALIDATED"
    QUESTION_CREATED = "QUESTION_CREATED"
    STAGE_FAILED = "STAGE_FAILED"
    STAGE_BLOCKED = "STAGE_BLOCKED"
    STAGE_MANUAL_CHECK = "STAGE_MANUAL_CHECK"
    EXECUTION_COMPLETED = "EXECUTION_COMPLETED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    BUDGET_THRESHOLD = "BUDGET_THRESHOLD"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    CREDENTIAL_EXPIRING = "CREDENTIAL_EXPIRING"
    AUDIT_INTEGRITY_FAILED = "AUDIT_INTEGRITY_FAILED"


@enum.unique
class NotificationChannel(StrEnum):
    """CP-NTF-002."""

    IN_APP = "IN_APP"
    EMAIL = "EMAIL"
    WEBHOOK = "WEBHOOK"


@enum.unique
class DeliveryStatus(StrEnum):
    """CP-NTF-002投递结果."""

    PENDING = "PENDING"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"


@enum.unique
class WarningDisposition(StrEnum):
    """CP-GATE-008 — each WARNING is individually marked at approval time."""

    ACCEPTED = "ACCEPTED"
    MUST_FIX = "MUST_FIX"


@enum.unique
class ApprovalDecision(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


@enum.unique
class ManualCheckOutcome(StrEnum):
    """附录 A: MANUAL_CHECK resolves only to PASSED or FAILED, with a note."""

    PASSED = "PASSED"
    FAILED = "FAILED"


@enum.unique
class AgentRunStatus(StrEnum):
    """S3 run result statuses (S3 AI-RT-017), mirrored so S1 can branch on them."""

    COMPLETED = "COMPLETED"
    WAITING_INPUT = "WAITING_INPUT"
    FAILED = "FAILED"
    POLICY_DENIED = "POLICY_DENIED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    CANCELLED = "CANCELLED"


@enum.unique
class ExecutionStatus(StrEnum):
    """S4 execution statuses (DX-EXE-035 / CP-WF-008)."""

    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    NEEDS_MANUAL_CHECK = "NEEDS_MANUAL_CHECK"


@enum.unique
class ChangeLevel(StrEnum):
    """S2 change level for a new Blueprint version (D-04, CP-WF-015 rule 5)."""

    COSMETIC = "Cosmetic"
    MINOR = "Minor"
    MAJOR = "Major"

    @property
    def is_major(self) -> bool:
        return self is ChangeLevel.MAJOR


@enum.unique
class CapabilityFreshness(StrEnum):
    """S4 ``check_capability_freshness`` outcomes (DX-CAP-006, CP-WF-022)."""

    VALID = "VALID"
    EXPIRED = "EXPIRED"
    VERSION_CHANGED = "VERSION_CHANGED"


#: Event types: 总体 SRS 19.2.3 list plus the CP-EVT-007 additions.
EVENT_TYPES: tuple[str, ...] = (
    "workflow.started",
    "workflow.paused",
    "workflow.resumed",
    "workflow.completed",
    "workflow.failed",
    "workflow.updated",
    "stage.started",
    "stage.waiting_input",
    "stage.waiting_approval",
    "stage.passed",
    "stage.failed",
    "stage.blocked",
    "stage.invalidated",
    "stage.manual_check",
    "stage.rerun",
    "agent.started",
    "agent.completed",
    "agent.progress",
    "question.created",
    "question.answered",
    "gate.created",
    "gate.approved",
    "gate.rejected",
    "gate.invalidated",
    "artifact.created",
    "artifact.superseded",
    "validation.completed",
    "execution.started",
    "execution.progress",
    "execution.completed",
    "execution.failed",
    "deployment_run.created",
    "deployment_run.completed",
    "deployment_run.failed",
    "deployment_run.cancelled",
    "deployment_run.outdated",
    "change_request.updated",
    "notification.created",
    "stream.reset",
)

#: Payload of these events must never carry RESTRICTED-or-higher content (CP-EVT-006).
EVENT_PAYLOAD_MAX_CLASSIFICATION = "CONFIDENTIAL"


#: The stages that have a design dialogue at all (AI-CHAT-001): S3's ``DesignStage``
#: vocabulary, which is narrower than ``DESIGN_STAGES`` above.  DISCOVERY and BASELINE_COMPILE
#: are design-line stages with no draft to discuss, so a turn asked there is a 404 naming the
#: stages that do answer rather than an assistant with nothing to talk about.
DIALOGUE_STAGE_TYPES = frozenset({"REQUIREMENTS", "DOMAIN", "MAPPING", "PAGE_DESIGN"})

#: Stage states a conversation may join (CP-WF-003).  LOCKED and SKIPPED are the two that may
#: not: one is finished and closed, the other is a stage this delivery mode does not run.
#: RUNNING and WAITING_INPUT are in, because asking *why* a run is stuck is the main reason the
#: panel is open.
CHATABLE_STAGE_STATUSES = frozenset(
    {
        StageStatus.READY.value,
        StageStatus.RUNNING.value,
        StageStatus.WAITING_INPUT.value,
        StageStatus.WAITING_APPROVAL.value,
        StageStatus.PASSED.value,
        StageStatus.FAILED.value,
        StageStatus.BLOCKED.value,
        StageStatus.INVALIDATED.value,
        StageStatus.MANUAL_CHECK.value,
    }
)
