"""S2 enumerations.

Value sets come from the overall SRS and the S2 SRS; the ordering of
``DesignStage`` and the Disposition semantics table are behaviour-defining
(BP-IMP-003 computes "this stage and every later stage", BP-REG-002 decides
payload membership and managed scope).
"""

from __future__ import annotations

import enum


@enum.unique
class Section(str, enum.Enum):
    """Blueprint top-level sections (overall SRS 5.1, S2 SRS 2)."""

    PROJECT = "project"
    REQUIREMENTS = "requirements"
    DOMAIN = "domain"
    USE_CASES = "useCases"
    ACTORS = "actors"
    ORGANIZATION_MODEL = "organizationModel"
    ACCESS_MODEL = "accessModel"
    ARCHITECTURE = "architecture"
    MAPPING = "mapping"
    REGISTRY = "registry"
    PAGES = "pages"
    API_INTENTS = "apiIntents"
    UI_INTENTS = "uiIntents"
    RULES = "rules"
    FIXTURES = "fixtures"
    ENVIRONMENT_CONSTRAINTS = "environmentConstraints"
    GENERATION_POLICY = "generationPolicy"


#: Every section must be present in a Blueprint document.
ALL_SECTIONS: tuple[Section, ...] = tuple(Section)

#: Sections that only a SYSTEM-authored change may touch (appendix A: project is
#: writable only by initialisation and delivery-mode upgrade).
SYSTEM_ONLY_SECTIONS: frozenset[Section] = frozenset({Section.PROJECT})

#: Sections frozen by ARCHITECTURE_GATE: touching them afterwards requires an
#: approved Change Request ID (BP-DRF-005.5, BP-BR-07).
MAPPING_FROZEN_SECTIONS: frozenset[Section] = frozenset(
    {Section.MAPPING, Section.REGISTRY, Section.ARCHITECTURE}
)


@enum.unique
class DesignStage(str, enum.Enum):
    """Workflow stages, in design-line order (00 doc 3.1)."""

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


#: Canonical stage order; impact sets contain the originating stage and every
#: later stage (BP-IMP-003, overall SRS FR-WF-003).
STAGE_ORDER: tuple[DesignStage, ...] = tuple(DesignStage)

#: The design-line stages a working draft can be scoped to.
DRAFT_STAGES: tuple[DesignStage, ...] = (
    DesignStage.REQUIREMENTS,
    DesignStage.DOMAIN,
    DesignStage.MAPPING,
    DesignStage.PAGE_DESIGN,
)

#: Ordering used for "has mapping been frozen" comparisons.
_STAGE_RANK: dict[DesignStage, int] = {s: i for i, s in enumerate(STAGE_ORDER)}


def stage_rank(stage: DesignStage | str) -> int:
    return _STAGE_RANK[DesignStage(stage)]


def stages_from(stage: DesignStage) -> list[DesignStage]:
    """Return ``stage`` and every later stage (impact set computation)."""
    return [s for s in STAGE_ORDER if _STAGE_RANK[s] >= _STAGE_RANK[stage]]


@enum.unique
class DeliveryMode(str, enum.Enum):
    """A = metadata-only delivery on standard DHIS2 apps; B = custom front-end app."""

    A = "A"
    B = "B"


@enum.unique
class ProjectType(str, enum.Enum):
    GREENFIELD = "GREENFIELD"
    BROWNFIELD = "BROWNFIELD"


@enum.unique
class Disposition(str, enum.Enum):
    """Registry entry disposition (overall SRS 5.5.1)."""

    CREATE = "CREATE"
    UPDATE = "UPDATE"
    REUSE = "REUSE"
    REFERENCE = "REFERENCE"
    EXTERNAL = "EXTERNAL"
    DEPRECATED = "DEPRECATED"


#: disposition -> (enters import payload, belongs to managed scope).
#: DEPRECATED enters the payload only through an approved change request, which
#: the compiler models separately as a deletion candidate (BP-CMP-026).
DISPOSITION_SEMANTICS: dict[Disposition, tuple[bool, bool]] = {
    Disposition.CREATE: (True, True),
    Disposition.UPDATE: (True, True),
    Disposition.REUSE: (False, False),
    Disposition.REFERENCE: (False, False),
    Disposition.EXTERNAL: (False, False),
    Disposition.DEPRECATED: (False, True),
}


def enters_payload(disposition: Disposition | str) -> bool:
    return DISPOSITION_SEMANTICS[Disposition(disposition)][0]


def is_managed(disposition: Disposition | str) -> bool:
    return DISPOSITION_SEMANTICS[Disposition(disposition)][1]


#: Dispositions whose entry must record an observation baseline (BP-REG-004).
OBSERVED_DISPOSITIONS: frozenset[Disposition] = frozenset(
    {Disposition.UPDATE, Disposition.REUSE, Disposition.REFERENCE}
)

#: Dispositions that need a UID assigned by S2 (CREATE and UPDATE are identical
#: across all environments — D-02).
UID_BEARING_DISPOSITIONS: frozenset[Disposition] = frozenset(
    {Disposition.CREATE, Disposition.UPDATE}
)


@enum.unique
class RegistryEntryStatus(str, enum.Enum):
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"


@enum.unique
class ChangeLevel(str, enum.Enum):
    COSMETIC = "Cosmetic"
    MINOR = "Minor"
    MAJOR = "Major"


#: Ordering high > low: the change level of a draft is the highest of all its
#: individual changes (BP-IMP-002).
_CHANGE_LEVEL_RANK: dict[ChangeLevel, int] = {
    ChangeLevel.COSMETIC: 0,
    ChangeLevel.MINOR: 1,
    ChangeLevel.MAJOR: 2,
}


def change_level_rank(level: ChangeLevel | str) -> int:
    return _CHANGE_LEVEL_RANK[ChangeLevel(level)]


def max_change_level(levels: list[ChangeLevel]) -> ChangeLevel:
    if not levels:
        return ChangeLevel.COSMETIC
    return max(levels, key=change_level_rank)


@enum.unique
class DraftStatus(str, enum.Enum):
    OPEN = "OPEN"
    FROZEN = "FROZEN"
    PROMOTED = "PROMOTED"
    RESET = "RESET"


#: A project has at most one *active* draft at a time (BP-DRF-001).
ACTIVE_DRAFT_STATUSES: frozenset[DraftStatus] = frozenset(
    {DraftStatus.OPEN, DraftStatus.FROZEN}
)


@enum.unique
class PatchStatus(str, enum.Enum):
    PROPOSED = "PROPOSED"
    REJECTED = "REJECTED"
    APPLIED = "APPLIED"
    DISCARDED = "DISCARDED"


@enum.unique
class AuthorKind(str, enum.Enum):
    """Patch author kind.  SYSTEM covers schema migrations and mode upgrades."""

    AGENT = "AGENT"
    USER = "USER"
    SYSTEM = "SYSTEM"


@enum.unique
class IssueLevel(str, enum.Enum):
    """Validation issue severity (overall SRS 13.1)."""

    BLOCKING = "BLOCKING"
    WARNING = "WARNING"
    INFO = "INFO"


@enum.unique
class VersionStatus(str, enum.Enum):
    """Blueprint version index status."""

    CURRENT = "CURRENT"
    APPROVED = "APPROVED"


@enum.unique
class BuildKind(str, enum.Enum):
    BASELINE = "BASELINE"
    ENVIRONMENT = "ENVIRONMENT"
    HANDOFF = "HANDOFF"


@enum.unique
class BuildStatus(str, enum.Enum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@enum.unique
class UidAllocationStatus(str, enum.Enum):
    """A retired UID is never reused, by anyone, ever (BP-REG-006, FR-REG-004)."""

    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"
    DEPRECATED = "DEPRECATED"


@enum.unique
class BindingResolution(str, enum.Enum):
    AUTO = "AUTO"
    MANUAL = "MANUAL"


@enum.unique
class ImpactLevel(str, enum.Enum):
    """Decision impact level (BP-DEC-004)."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


@enum.unique
class Tier(str, enum.Enum):
    """Metadata type coverage tier (overall SRS 6.1)."""

    TIER_1 = "1"
    TIER_2 = "2"
    TIER_3 = "3"
